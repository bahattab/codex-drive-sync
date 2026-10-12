"""Encrypted, immutable project snapshots and conservative DAG reconciliation.

No Codex database is opened or modified. Snapshot ordering is ancestry, never
wall-clock time. A transport is an untrusted object store, not a local backup.
"""
from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path, PurePosixPath

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_FILES = 10000
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
EXCLUDED = {".git", "node_modules", ".venv", "venv", "__pycache__", ".codex", ".ssh", ".aws", ".azure", ".gnupg"}
SECRET_NAMES = {"credentials.json", "token.json", "tokens.json", "auth.json", "secrets.json", "id_rsa", "id_ed25519"}
_ID = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


class SyncError(RuntimeError):
    """The operation failed closed; consult status before retrying."""


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _safe_path(name):
    if not isinstance(name, str) or not name or len(name) > 1024 or "\\" in name:
        raise SyncError("Invalid portable relative path")
    parts = PurePosixPath(name).parts
    if name.startswith("/") or "/".join(parts) != name or any(
        p in (".", "..") or p[-1:] in (" ", ".") or _RESERVED.match(p)
        or any(ord(c) < 32 or c in '<>:"|?*' for c in p) or len(p) > 240
        for p in parts
    ):
        raise SyncError("Unsafe or nonportable path")
    return parts


def _excluded(parts):
    return any(p.lower() in EXCLUDED or p.lower() in SECRET_NAMES or p.lower() == ".env"
               or p.lower().startswith(".env.") or fnmatch.fnmatch(p.lower(), "client_secret*.json")
               or fnmatch.fnmatch(p.lower(), "*recovery*key*.json") or p.lower() == "recovery.json"
               or p.lower().endswith((".pem", ".key", ".p12", ".pfx"))
               for p in parts)


def _link(path):
    s = path.lstat()
    return stat.S_ISLNK(s.st_mode) or bool(getattr(s, "st_file_attributes", 0) & 0x400)


class Engine:
    def __init__(self, state_dir, provider, cipher, device_id=None):
        self.state_dir = Path(state_dir).absolute()
        self.provider, self.cipher = provider, cipher
        transport_root = getattr(provider, "root", None)
        if transport_root is not None:
            transport_root = Path(transport_root).resolve()
            resolved_state = self.state_dir.resolve()
            if resolved_state == transport_root or resolved_state in transport_root.parents or transport_root in resolved_state.parents:
                raise SyncError("Local state and transport must be outside one another")
        self.state_file = self.state_dir / "state.json"
        self.journal = self.state_dir / "journal.json"
        self._fault_hook = None  # Injected by tests, called at durable transaction boundaries.
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state = self._read_state()
        if device_id and self.state["device"] != device_id and self.state_file.exists():
            raise SyncError("State belongs to another device")
        if device_id:
            self.state["device"] = device_id
        if not _ID.fullmatch(self.state["device"]):
            raise SyncError("Invalid device id")
        with self._locked():
            self._recover()
            self._save()

    def _read_state(self):
        if self.state_file.exists():
            try:
                result = json.loads(self.state_file.read_text("utf-8"))
                if result.get("version") != 1:
                    raise ValueError()
                return result
            except (ValueError, OSError) as exc:
                raise SyncError("Local state is unreadable; recover from a local backup") from exc
        return {"version": 1, "device": uuid.uuid4().hex, "projects": {}, "head": None, "conflicts": {}}

    @contextlib.contextmanager
    def _locked(self):
        # An OS-held lock releases after a crash without deleting another process's lock.
        lock = self.state_dir / "lock"
        with open(lock, "a+b") as handle:
            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise SyncError("Another sync operation holds this state lock") from exc
            try:
                # Reload after obtaining the lock, preventing stale writers.
                if self.state_file.exists():
                    self.state = self._read_state()
                yield
            finally:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def _atomic(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
        try:
            with open(temp, "xb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def _save(self):
        self._atomic(self.state_file, _encode(self.state))

    def _is_excluded(self, parts):
        return _excluded(parts) or any(
            fnmatch.fnmatch("/".join(parts).casefold(), pattern.casefold())
            or any(fnmatch.fnmatch(part.casefold(), pattern.casefold()) for part in parts)
            for pattern in self.state.get("exclusions", [])
        )

    def set_exclusions(self, patterns):
        """Additional local globs; mandatory secret exclusions cannot be removed."""
        if not isinstance(patterns, list) or len(patterns) > 100 or any(
            not isinstance(p, str) or not p or len(p) > 240 or "\\" in p or ":" in p
            or p.startswith("/") or ".." in p.split("/") for p in patterns
        ):
            raise SyncError("Exclusions must be at most 100 safe relative glob patterns")
        with self._locked():
            self._recover()
            self.state["exclusions"] = sorted(set(patterns))
            self._save()
            return {"exclusions": self.state["exclusions"]}

    def _fault(self, point):
        if self._fault_hook:
            self._fault_hook(point)

    def _root(self, path):
        root = Path(path).absolute()
        if not root.is_dir() or _link(root):
            raise SyncError("Project must be an existing ordinary directory")
        for ancestor in root.parents:
            if _link(ancestor):
                raise SyncError("Project ancestry includes a link or reparse point")
        resolved = root.resolve()
        state = self.state_dir.resolve()
        if resolved == state or resolved in state.parents or state in resolved.parents:
            raise SyncError("Project and local state must be outside one another")
        transport = getattr(self.provider, "root", None)
        if transport:
            transport = Path(transport).resolve()
            if resolved == transport or resolved in transport.parents or transport in resolved.parents:
                raise SyncError("Project and transport must be outside one another")
        return resolved

    def add_project(self, path, project_id=None):
        with self._locked():
            self._recover()
            root = self._root(path)
            project_id = project_id or uuid.uuid4().hex
            if not _ID.fullmatch(project_id):
                raise SyncError("Invalid project id")
            for key, existing in self.state["projects"].items():
                other = Path(existing)
                if other == root or root in other.parents or other in root.parents:
                    if key == project_id and other == root:
                        return {"project_id": key, "path": str(root)}
                    raise SyncError("Overlapping or duplicate project mapping")
            if project_id in self.state["projects"]:
                raise SyncError("Project id already mapped; use map_project")
            self.state["projects"][project_id] = str(root)
            self._save()
            return {"project_id": project_id, "path": str(root)}

    def map_project(self, project_id, path):
        with self._locked():
            self._recover()
            old = self.state["projects"].pop(project_id, None)
            try:
                root = self._root(path)
                if not _ID.fullmatch(project_id):
                    raise SyncError("Invalid project id")
                for existing in self.state["projects"].values():
                    other = Path(existing)
                    if other == root or root in other.parents or other in root.parents:
                        raise SyncError("Overlapping project mapping")
                self.state["projects"][project_id] = str(root)
                self._save()
                return {"project_id": project_id, "path": str(root)}
            except BaseException:
                if old is not None:
                    self.state["projects"][project_id] = old
                raise

    def _scan(self):
        files, payloads, total = {}, {}, 0
        for project_id, root_name in sorted(self.state["projects"].items()):
            root = self._root(root_name)
            entries, folded = {}, set()
            for directory, dirs, names in os.walk(root, followlinks=False):
                # Links are rejected even if pointing to excluded paths.
                for name in list(dirs) + names:
                    candidate = Path(directory) / name
                    relative = candidate.relative_to(root).as_posix()
                    parts = _safe_path(relative)
                    if _link(candidate):
                        raise SyncError("Project includes a symbolic link or reparse point")
                    if self._is_excluded(parts):
                        if name in dirs:
                            dirs.remove(name)
                        continue
                    folded_name = relative.casefold()
                    if folded_name in folded:
                        raise SyncError("Case-insensitive path collision")
                    folded.add(folded_name)
                    if candidate.is_dir():
                        continue
                    if not stat.S_ISREG(candidate.stat().st_mode):
                        raise SyncError("Only regular files can be synchronized")
                    if candidate.stat().st_size > MAX_FILE_BYTES:
                        raise SyncError("File exceeds size limit")
                    with open(candidate, "rb") as f:
                        data = f.read(MAX_FILE_BYTES + 1)
                    if len(data) > MAX_FILE_BYTES:
                        raise SyncError("File exceeds size limit")
                    total += len(data)
                    if total > MAX_TOTAL_BYTES:
                        raise SyncError("Project set exceeds total size limit")
                    digest = _digest(data)
                    entries[relative] = digest
                    payloads[digest] = data
                    if sum(len(x) for x in files.values()) + len(entries) > MAX_FILES:
                        raise SyncError("Project set exceeds file count limit")
            files[project_id] = entries
        return files, payloads

    def inventory(self):
        with self._locked():
            files, payloads = self._scan()
            return {"projects": files, "files": sum(map(len, files.values())), "bytes": sum(map(len, payloads.values()))}

    def _validate_files(self, files):
        if not isinstance(files, dict):
            raise SyncError("Invalid snapshot project table")
        count = 0
        for project, entries in files.items():
            if not isinstance(project, str) or not _ID.fullmatch(project) or not isinstance(entries, dict):
                raise SyncError("Invalid snapshot project")
            folded, all_names = set(), {name.casefold() for name in entries}
            for path, digest in entries.items():
                parts = _safe_path(path)
                if _excluded(parts) or not isinstance(digest, str) or not _HASH.fullmatch(digest):
                    raise SyncError("Excluded file or invalid content hash in snapshot")
                if path.casefold() in folded:
                    raise SyncError("Case collision in snapshot")
                folded.add(path.casefold())
                if any("/".join(parts[:i]).casefold() in all_names for i in range(1, len(parts))):
                    raise SyncError("File/directory collision in snapshot")
                count += 1
        if count > MAX_FILES:
            raise SyncError("Snapshot exceeds file count limit")

    def _manifest(self, snapshot_id):
        if not isinstance(snapshot_id, str) or not _HASH.fullmatch(snapshot_id):
            raise SyncError("Invalid snapshot id")
        raw = self.cipher.decrypt(self.provider.get("snapshots/" + snapshot_id))
        if len(raw) > MAX_MANIFEST_BYTES or _digest(raw) != snapshot_id:
            raise SyncError("Snapshot integrity or size check failed")
        try:
            item = json.loads(raw)
            if item["version"] != 1 or not _ID.fullmatch(item["device"]):
                raise ValueError()
            parents = item["parents"]
            if not isinstance(parents, list) or len(parents) > 1000 or len(set(parents)) != len(parents) or any(not _HASH.fullmatch(x) for x in parents):
                raise ValueError()
            self._validate_files(item["projects"])
            conflicts = item.get("conflicts", {})
            if not isinstance(conflicts, dict) or len(conflicts) > MAX_FILES:
                raise ValueError()
            for cid, c in conflicts.items():
                if not _HASH.fullmatch(cid) or not _ID.fullmatch(c["project"]):
                    raise ValueError()
                _safe_path(c["path"])
                if _excluded(_safe_path(c["path"])) or not isinstance(c["variants"], list) or not 2 <= len(c["variants"]) <= 1000:
                    raise ValueError()
                for variant in c["variants"]:
                    if variant is not None and (not isinstance(variant, str) or not _HASH.fullmatch(variant)):
                        raise ValueError()
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise SyncError("Malformed snapshot manifest") from exc
        return item

    def _catalog(self):
        ids = self.provider.list("snapshots/")
        if len(ids) > 10000:
            raise SyncError("Snapshot catalog exceeds safety limit")
        catalog = {}
        for name in ids:
            if name.startswith("snapshots/"):
                sid = name[len("snapshots/"):]
                catalog[sid] = self._manifest(sid)
        for sid, item in catalog.items():
            if any(parent not in catalog for parent in item["parents"]):
                raise SyncError("Incomplete snapshot ancestry")
        # Explicit cycle check prevents malicious recursion and ancestry ambiguity.
        remaining, done = set(catalog), set()
        while remaining:
            ready = {sid for sid in remaining if set(catalog[sid]["parents"]) <= done}
            if not ready:
                raise SyncError("Snapshot ancestry contains a cycle")
            done.update(ready)
            remaining.difference_update(ready)
        return catalog

    def _publish(self, projects, payloads, parents, conflicts):
        self._validate_files(projects)
        # Every referenced blob is uploaded/verified before the commit manifest.
        hashes = {value for entries in projects.values() for value in entries.values()}
        hashes.update(v for c in conflicts.values() for v in c["variants"] if v is not None)
        for digest in sorted(hashes):
            if digest in payloads:
                self._put_plain("blobs/" + digest, payloads[digest])
            self._blob(digest)
        item = {"version": 1, "device": self.state["device"], "parents": sorted(set(parents)), "projects": projects, "conflicts": conflicts}
        raw = _encode(item)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise SyncError("Manifest exceeds size limit")
        sid = _digest(raw)
        self._put_plain("snapshots/" + sid, raw)
        self._manifest(sid)
        return sid

    def _put_plain(self, name, raw):
        # AES-GCM uses a fresh random nonce. Reuse an existing authenticated
        # envelope for a plaintext hash instead of violating immutability.
        try:
            existing = self.provider.get(name)
        except (FileNotFoundError, KeyError):
            existing = None
        if existing is not None:
            if self.cipher.decrypt(existing) != raw:
                raise SyncError("Existing immutable object has unexpected plaintext")
            return
        try:
            self.provider.put(name, self.cipher.encrypt(raw))
        except Exception:
            # A concurrent writer or uncertain network response may have stored
            # another valid envelope for exactly the same plaintext.
            try:
                existing = self.provider.get(name)
                if self.cipher.decrypt(existing) == raw:
                    return
            except Exception:
                pass
            raise

    def _blob(self, digest):
        raw = self.cipher.decrypt(self.provider.get("blobs/" + digest))
        if len(raw) > MAX_FILE_BYTES or _digest(raw) != digest:
            raise SyncError("Blob integrity or size check failed")
        return raw

    def _prepare(self):
        files, payloads = self._scan()
        head = self.state["head"]
        previous = self._manifest(head) if head else None
        projects = self._overlay_files(previous["projects"] if previous else {}, files)
        if previous and projects == previous["projects"] and self.state["conflicts"] == previous.get("conflicts", {}):
            return head
        sid = self._publish(projects, payloads, [head] if head else [], self.state["conflicts"])
        self.state["head"] = sid
        self.state["baseline"] = projects
        self._save()
        return sid

    def _overlay_files(self, previous, current):
        projects = dict(previous)
        for project, entries in current.items():
            preserved = {path: digest for path, digest in previous.get(project, {}).items()
                         if self._is_excluded(_safe_path(path))}
            preserved.update(entries)
            projects[project] = preserved
        return projects

    def prepare(self):
        with self._locked():
            self._recover()
            return {"snapshot_id": self._prepare()}

    def _ancestors(self, sid, catalog):
        found, pending = set(), [sid]
        while pending:
            candidate = pending.pop()
            if candidate not in found:
                found.add(candidate)
                pending.extend(catalog[candidate]["parents"])
        return found

    def _merge(self, catalog, heads, local_files):
        ancestors = [self._ancestors(h, catalog) for h in heads]
        common = set.intersection(*ancestors) if ancestors else set()
        # Criss-cross DAGs may have several maximal common ancestors. Require
        # unanimous baseline values instead of arbitrarily selecting an ancestor.
        maximal = [s for s in common if not any(s != other and s in self._ancestors(other, catalog) for other in common)]
        merged, conflicts = {}, {}
        for h in heads:
            conflicts.update(catalog[h].get("conflicts", {}))
        for cid in list(conflicts):
            if maximal and all(cid in catalog[s].get("conflicts", {}) for s in maximal):
                if any(cid not in catalog[h].get("conflicts", {}) for h in heads):
                    # Explicit resolution is a causal change, just like a file
                    # edit. Do not resurrect it from an unchanged offline branch.
                    del conflicts[cid]
        projects = set().union(*(set(catalog[h]["projects"]) for h in heads))
        for project in sorted(projects):
            paths = set().union(*(set(catalog[h]["projects"].get(project, {})) for h in heads))
            if maximal:
                paths.update(set().union(*(set(catalog[h]["projects"].get(project, {})) for h in maximal)))
            output = {}
            for path in sorted(paths):
                values = [catalog[h]["projects"].get(project, {}).get(path) for h in heads]
                baseline_values = {catalog[h]["projects"].get(project, {}).get(path) for h in maximal}
                baseline = next(iter(baseline_values)) if len(baseline_values) == 1 else None
                if maximal and len(baseline_values) == 1:
                    changed = set(values) - {baseline}
                else:
                    # First pairing cannot propagate deletions; absent means unknown.
                    changed = set(values) - {None}
                if len(changed) <= 1:
                    selected = next(iter(changed)) if changed else baseline
                else:
                    variants = sorted(changed, key=lambda v: (v is not None, v or ""))
                    cid = _digest(_encode([project, path, variants]))
                    conflicts[cid] = {"project": project, "path": path, "variants": variants}
                    selected = local_files.get(project, {}).get(path)
                    if selected not in changed:
                        selected = variants[-1]
                if selected is not None:
                    output[path] = selected
            merged[project] = output
        # Structural collisions cannot safely be materialized. Preserve all
        # branches remotely and fail before any local overwrite.
        self._validate_files(merged)
        return merged, conflicts, bool(maximal)

    def _target(self, project, relative):
        parts = _safe_path(relative)
        if self._is_excluded(parts):
            raise SyncError("Refusing to write an excluded file")
        root = self._root(self.state["projects"][project])
        candidate = root.joinpath(*parts)
        for node in [candidate, *candidate.parents]:
            if node == root.parent:
                break
            if node.exists() or node.is_symlink():
                if _link(node):
                    raise SyncError("Target includes a link or reparse point")
        if root not in candidate.parents:
            raise SyncError("Target escapes project")
        return candidate

    def _changes(self, target, current, allow_delete):
        changes = []
        for project in self.state["projects"]:
            if project not in target:
                continue
            desired = target[project]
            existing = current.get(project, {})
            for path in sorted(set(desired) | (set(existing) if allow_delete else set())):
                if self._is_excluded(_safe_path(path)):
                    continue
                if desired.get(path) != existing.get(path):
                    self._target(project, path)
                    changes.append({"project": project, "path": path, "hash": desired.get(path), "before_hash": existing.get(path)})
        return changes

    def _recover(self):
        if not self.journal.exists():
            return
        job = json.loads(self.journal.read_text("utf-8"))
        if job.get("phase") == "committed":
            self.journal.unlink()
            return
        attempted = job.get("attempted", len(job["changes"]))
        rollback = []
        # Preflight the entire rollback before its first write. A later
        # independent edit or damaged backup must not leave a half rollback.
        for change in reversed(job["changes"][:attempted]):
            dest = self._target(change["project"], change["path"])
            if change["before"] is None:
                raw = None
            else:
                backup = self.state_dir / "backups" / job["transaction"] / change["before"]
                raw = self.cipher.decrypt(backup.read_bytes())
                if _digest(raw) != change["before_hash"]:
                    raise SyncError("Recovery backup integrity check failed")
            observed = _digest(dest.read_bytes()) if dest.exists() else None
            if observed == change["before_hash"]:
                continue
            if observed != change["hash"]:
                raise SyncError("Recovery found an independently changed file; preserve it and the encrypted backups before manual recovery")
            rollback.append((dest, raw, observed))
        for dest, raw, observed in rollback:
            current = _digest(dest.read_bytes()) if dest.exists() else None
            if current != observed:
                raise SyncError("Project changed during recovery; encrypted backups retained")
            if raw is None:
                dest.unlink(missing_ok=True)
            else:
                self._atomic(dest, raw)
        self.state = job["state_before"]
        self._save()
        self.journal.unlink()

    def _apply(self, target, conflicts, sid, current, allow_delete, dry_run):
        changes = self._changes(target, current, allow_delete)
        # Verify the complete snapshot, including unchanged paths and every
        # unresolved variant, before activation or any project mutation.
        payloads, total = {}, 0
        hashes = {digest for entries in target.values() for digest in entries.values()}
        hashes.update(digest for conflict in conflicts.values() for digest in conflict["variants"] if digest is not None)
        for digest in sorted(hashes):
            payloads[digest] = self._blob(digest)
            total += len(payloads[digest])
            if total > MAX_TOTAL_BYTES:
                raise SyncError("Restore exceeds total size limit")
        logical_bytes = sum(len(payloads[digest]) for entries in target.values() for digest in entries.values())
        if logical_bytes > MAX_TOTAL_BYTES:
            raise SyncError("Snapshot materialized content exceeds total size limit")
        if dry_run:
            return {"snapshot_id": sid, "dry_run": True, "changes": changes, "conflicts": list(conflicts.values())}
        transaction = uuid.uuid4().hex
        backup_root = self.state_dir / "backups" / transaction
        backup_root.mkdir(parents=True)
        job = {"transaction": transaction, "phase": "applying", "state_before": self.state, "changes": [], "attempted": 0}
        for n, change in enumerate(changes):
            dest = self._target(change["project"], change["path"])
            before = None
            if dest.exists():
                if not dest.is_file():
                    raise SyncError("Target is not an ordinary file")
                raw = dest.read_bytes()
                if _digest(raw) != change["before_hash"]:
                    raise SyncError("Project changed during synchronization")
                before = str(n)
                self._atomic(backup_root / before, self.cipher.encrypt(raw))
            elif change["before_hash"] is not None:
                raise SyncError("Project changed during synchronization")
            job["changes"].append(dict(change, before=before))
        # Clone state to ensure the rollback image cannot alias mutable state.
        job["state_before"] = json.loads(_encode(self.state))
        self._atomic(self.journal, _encode(job))
        try:
            self._fault("journal")
            for n, change in enumerate(changes):
                dest = self._target(change["project"], change["path"])
                # Recheck content immediately before replacement/deletion.
                observed = _digest(dest.read_bytes()) if dest.exists() else None
                if observed != change["before_hash"]:
                    raise SyncError("Project changed during synchronization")
                job["attempted"] = n + 1
                self._atomic(self.journal, _encode(job))
                if change["hash"] is None:
                    dest.unlink(missing_ok=True)
                else:
                    self._atomic(dest, payloads[change["hash"]])
                self._fault("file:" + str(n))
            self.state["head"] = sid
            self.state["conflicts"] = conflicts
            self.state["last_successful_snapshot"] = sid
            self.state["baseline"] = target
            self._save()
            self._fault("state")
            job["phase"] = "committed"
            self._atomic(self.journal, _encode(job))
            self._fault("commit")
            self.journal.unlink()
        except Exception:
            self._recover()
            raise
        return {"snapshot_id": sid, "dry_run": False, "changes": changes, "conflicts": list(conflicts.values())}

    def sync(self, dry_run=False):
        with self._locked():
            if not dry_run:
                self._recover()
            current, payloads = self._scan()
            catalog = self._catalog()
            if dry_run:
                head = self.state["head"]
                projects = self._overlay_files(catalog[head]["projects"] if head in catalog else {}, current)
                virtual = {"version": 1, "device": self.state["device"], "parents": [head] if head else [], "projects": projects, "conflicts": self.state["conflicts"]}
                virtual_id = _digest(_encode(virtual))
                catalog[virtual_id] = virtual
            else:
                virtual_id = self._prepare()
                catalog = self._catalog()
            parents = {p for item in catalog.values() for p in item["parents"]}
            heads = sorted(set(catalog) - parents)
            merged, conflicts, paired = self._merge(catalog, heads, current)
            if dry_run:
                # Local virtual hashes need not exist in transport.
                changes = self._changes(merged, current, paired)
                return {"snapshot_id": None, "dry_run": True, "changes": changes, "conflicts": list(conflicts.values())}
            if len(heads) == 1 and catalog[heads[0]]["projects"] == merged and catalog[heads[0]].get("conflicts", {}) == conflicts:
                sid = heads[0]
            else:
                sid = self._publish(merged, payloads, heads, conflicts)
            return self._apply(merged, conflicts, sid, current, paired, False)

    def receive(self, snapshot_id, dry_run=False):
        with self._locked():
            if not dry_run:
                self._recover()
            catalog = self._catalog()
            if snapshot_id not in catalog:
                raise SyncError("Unknown snapshot")
            current, payloads = self._scan()
            head = self.state["head"]
            projects = self._overlay_files(catalog[head]["projects"] if head in catalog else {}, current)
            virtual = {"version": 1, "device": self.state["device"], "parents": [head] if head else [], "projects": projects, "conflicts": self.state["conflicts"]}
            vid = _digest(_encode(virtual))
            catalog[vid] = virtual
            merged, conflicts, paired = self._merge(catalog, [snapshot_id, vid], current)
            if dry_run:
                return {"snapshot_id": snapshot_id, "dry_run": True, "changes": self._changes(merged, current, paired), "conflicts": list(conflicts.values())}
            local_head = self._prepare()
            parents = sorted(set([snapshot_id, local_head]))
            sid = self._publish(merged, payloads, parents, conflicts)
            return self._apply(merged, conflicts, sid, current, paired, False)

    def restore(self, snapshot_id, dry_run=False):
        with self._locked():
            if not dry_run:
                self._recover()
            item = self._manifest(snapshot_id)
            current, _ = self._scan()
            # Restoring a known historical image is explicit deletion authorization.
            if dry_run:
                return self._apply(item["projects"], item.get("conflicts", {}), snapshot_id, current, True, True)
            # A restore is a new causal edit, so the next sync cannot immediately
            # select a newer descendant of the historical image and undo it.
            parent = self.state["head"]
            restored_id = self._publish(item["projects"], {}, [parent] if parent else [snapshot_id], item.get("conflicts", {}))
            result = self._apply(item["projects"], item.get("conflicts", {}), restored_id, current, True, False)
            result["restored_from"] = snapshot_id
            return result

    def resolve(self, conflict_id, choice):
        with self._locked():
            self._recover()
            if conflict_id not in self.state["conflicts"]:
                raise SyncError("Unknown conflict")
            conflict = self.state["conflicts"][conflict_id]
            current, _ = self._scan()
            if choice == "local":
                digest = current.get(conflict["project"], {}).get(conflict["path"])
            elif choice in ("delete", None):
                digest = None
            elif isinstance(choice, int) and not isinstance(choice, bool) and 0 <= choice < len(conflict["variants"]):
                digest = conflict["variants"][choice]
            elif choice in conflict["variants"]:
                digest = choice
            else:
                raise SyncError("Choice must be local, delete, a variant hash, or variant index")
            desired = json.loads(_encode(current))
            desired.setdefault(conflict["project"], {})
            if digest is None:
                desired[conflict["project"]].pop(conflict["path"], None)
            else:
                desired[conflict["project"]][conflict["path"]] = digest
            conflicts = dict(self.state["conflicts"])
            del conflicts[conflict_id]
            result = self._apply(desired, conflicts, self.state["head"], current, True, False)
            result["snapshot_id"] = self._prepare()
            return result

    def status(self):
        with self._locked():
            files, _ = self._scan()
            pending = 0
            for project, entries in files.items():
                baseline = self.state.get("baseline", {}).get(project, {})
                pending += sum(entries.get(path) != baseline.get(path)
                               for path in set(entries) | set(baseline)
                               if not self._is_excluded(_safe_path(path)))
            return {"device_id": self.state["device"], "head": self.state["head"], "last_successful_snapshot": self.state.get("last_successful_snapshot"), "pending_changes": pending, "projects": dict(self.state["projects"]), "exclusions": self.state.get("exclusions", []), "conflicts": len(self.state["conflicts"]), "recovery_pending": self.journal.exists()}

    def conflicts(self):
        with self._locked():
            return [dict(value, conflict_id=key) for key, value in sorted(self.state["conflicts"].items())]

    def snapshots(self):
        with self._locked():
            return [dict(snapshot_id=sid, device_id=item["device"], parents=item["parents"], files=sum(map(len, item["projects"].values())))
                    for sid, item in sorted(self._catalog().items())]
