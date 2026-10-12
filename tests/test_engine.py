import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from codex_drive_sync.engine import Engine, SyncError, _encode, _safe_path


class MemoryProvider:
    def __init__(self):
        self.objects = {}
        self.fail = None
    def put(self, name, data):
        if self.fail and name.startswith(self.fail):
            raise OSError("injected transport failure")
        self.objects[name] = data
    def get(self, name):
        return self.objects[name]
    def list(self, prefix):
        return sorted(n for n in self.objects if n.startswith(prefix))


class TestCipher:
    """Authenticated test envelope. Production uses AES-GCM from security.py."""
    def encrypt(self, data):
        return b"ENC" + hashlib.sha256(data).digest() + bytes(b ^ 173 for b in data)
    def decrypt(self, data):
        raw = bytes(b ^ 173 for b in data[35:])
        if data[:3] != b"ENC" or hashlib.sha256(raw).digest() != data[3:35]:
            raise SyncError("test authentication failed")
        return raw


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.transport = MemoryProvider()
        self.cipher = TestCipher()
        self.a = self.make("a")
        self.b = self.make("b")
    def tearDown(self):
        self.temp.cleanup()
    def make(self, name):
        root = self.base / name
        root.mkdir()
        engine = Engine(self.base / ("state-" + name), self.transport, self.cipher, name)
        engine.add_project(root, "project")
        return engine
    def write(self, name, text, filename="hello.txt"):
        target = self.base / name / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    def read(self, name, filename="hello.txt"):
        return (self.base / name / filename).read_text("utf-8")
    def paired(self):
        self.write("a", "original")
        self.a.sync()
        self.b.sync()
        self.a.sync()

    def test_initial_pair_and_ordinary_edit(self):
        self.paired()
        self.assertEqual(self.read("b"), "original")
        self.write("a", "updated")
        self.a.sync()
        self.b.sync()
        self.assertEqual(self.read("b"), "updated")
        self.assertFalse(self.b.conflicts())

    def test_first_pair_preserves_extra_files_and_conflicting_versions(self):
        self.write("a", "left")
        self.write("b", "right")
        self.write("b", "only right", "extra.txt")
        self.a.sync()
        self.b.sync()
        self.assertEqual(self.read("b"), "right")
        self.assertEqual(self.read("b", "extra.txt"), "only right")
        self.assertEqual(len(self.b.conflicts()), 1)
        self.a.sync()
        self.assertEqual(self.read("a", "extra.txt"), "only right")
        self.assertEqual(len(self.a.conflicts()), 1)

    def test_offline_edit_conflict_and_resolution_propagation(self):
        self.paired()
        self.write("a", "alpha")
        self.write("b", "beta")
        self.a.prepare()
        self.b.prepare()
        self.b.sync()
        conflict = self.b.conflicts()[0]
        self.assertEqual(len(conflict["variants"]), 2)
        self.b.resolve(conflict["conflict_id"], "local")
        self.a.sync()
        self.assertEqual(self.read("a"), "beta")
        self.assertFalse(self.a.conflicts())

    def test_delete_propagates_after_pairing(self):
        self.paired()
        (self.base / "a" / "hello.txt").unlink()
        self.a.sync()
        self.b.sync()
        self.assertFalse((self.base / "b" / "hello.txt").exists())

    def test_delete_edit_conflict_preserves_edited_content(self):
        self.paired()
        (self.base / "a" / "hello.txt").unlink()
        self.write("b", "edited offline")
        self.a.prepare()
        self.b.sync()
        self.assertEqual(self.read("b"), "edited offline")
        self.assertIn(None, self.b.conflicts()[0]["variants"])

    def test_independent_offline_changes_merge(self):
        self.paired()
        self.write("a", "left", "left.txt")
        self.write("b", "right", "right.txt")
        self.a.prepare()
        self.b.sync()
        self.a.sync()
        self.assertEqual(self.read("a", "right.txt"), "right")
        self.assertEqual(self.read("b", "left.txt"), "left")
        self.assertFalse(self.a.conflicts())

    def test_no_manifest_publication_on_blob_failure(self):
        self.write("a", "payload")
        self.transport.fail = "blobs/"
        with self.assertRaises(OSError):
            self.a.prepare()
        self.assertEqual(self.transport.list("snapshots/"), [])
        self.assertIsNone(self.a.status()["head"])

    def test_transport_has_no_cleartext_content_or_local_paths(self):
        self.write("a", "VERY PRIVATE CONTENT")
        self.a.sync()
        for payload in self.transport.objects.values():
            self.assertNotIn(b"VERY PRIVATE CONTENT", payload)
            self.assertNotIn(str(self.base).encode(), payload)
        manifest = self.a._manifest(self.a.status()["head"])
        self.assertNotIn(str(self.base), json.dumps(manifest))

    def test_dry_run_does_not_write_project_state_or_transport(self):
        self.paired()
        self.write("a", "change")
        self.a.prepare()
        files_before = {str(p): p.read_bytes() for p in (self.base / "state-b").rglob("*") if p.is_file()}
        objects_before = dict(self.transport.objects)
        result = self.b.sync(dry_run=True)
        self.assertTrue(result["changes"])
        self.assertEqual(self.read("b"), "original")
        self.assertEqual(objects_before, self.transport.objects)
        self.assertEqual(files_before, {str(p): p.read_bytes() for p in (self.base / "state-b").rglob("*") if p.is_file()})

    def test_restore_rolls_back_on_write_failure(self):
        self.write("a", "old")
        old = self.a.prepare()["snapshot_id"]
        self.write("a", "new")
        self.write("a", "extra", "extra.txt")
        latest = self.a.prepare()["snapshot_id"]
        def fault(point):
            if point == "file:0":
                raise OSError("injected disk failure")
        self.a._fault_hook = fault
        with self.assertRaises(OSError):
            self.a.restore(old)
        self.assertEqual(self.read("a"), "new")
        self.assertEqual(self.read("a", "extra.txt"), "extra")
        self.assertEqual(self.a.status()["head"], latest)
        self.assertFalse(self.a.journal.exists())
        self.assertTrue(list((self.base / "state-a" / "backups").rglob("0")))

    def test_crash_recovery_reverts_interrupted_restore(self):
        self.write("a", "old")
        old = self.a.prepare()["snapshot_id"]
        self.write("a", "new")
        latest = self.a.prepare()["snapshot_id"]
        def fault(point):
            if point == "file:0":
                raise KeyboardInterrupt()
        self.a._fault_hook = fault
        with self.assertRaises(KeyboardInterrupt):
            self.a.restore(old)
        self.assertTrue(self.a.journal.exists())
        recovered = Engine(self.base / "state-a", self.transport, self.cipher, "a")
        self.assertEqual(self.read("a"), "new")
        self.assertEqual(recovered.status()["head"], latest)
        self.assertFalse(recovered.journal.exists())

    def test_committed_crash_keeps_completed_restore(self):
        self.write("a", "old")
        old = self.a.prepare()["snapshot_id"]
        self.write("a", "new")
        self.a.prepare()
        def fault(point):
            if point == "commit":
                raise KeyboardInterrupt()
        self.a._fault_hook = fault
        with self.assertRaises(KeyboardInterrupt):
            self.a.restore(old)
        recovered = Engine(self.base / "state-a", self.transport, self.cipher, "a")
        self.assertEqual(self.read("a"), "old")
        self.assertFalse(recovered.journal.exists())

    def test_restore_dry_run_and_receive_preserve_untracked_extras(self):
        self.write("a", "old")
        old = self.a.prepare()["snapshot_id"]
        self.write("b", "extra", "extra.txt")
        self.b.receive(old)
        self.assertEqual(self.read("b", "extra.txt"), "extra")
        self.write("b", "new")
        result = self.b.restore(old, dry_run=True)
        self.assertEqual(len(result["changes"]), 2)
        self.assertEqual(self.read("b"), "new")
        self.b.restore(old)
        self.assertEqual(self.read("b"), "old")
        self.assertFalse((self.base / "b" / "extra.txt").exists())

    def test_unsafe_paths_and_secret_exclusions(self):
        for name in ("../escape", "/absolute", "C:/escape", "folder/../escape", "NUL.txt", "hello.", "a\\b", "a//b", "x:y"):
            with self.subTest(name=name), self.assertRaises(SyncError):
                _safe_path(name)
        self.write("a", "SECRET", ".env")
        self.write("a", "SECRET", "token.json")
        self.write("a", "public", "hello.txt")
        inventory = self.a.inventory()
        self.assertEqual(set(inventory["projects"]["project"]), {"hello.txt"})

    def test_symlink_rejected_and_original_untouched(self):
        target = self.base / "outside"
        target.write_text("original")
        try:
            (self.base / "a" / "link").symlink_to(target)
        except OSError:
            self.skipTest("Creating symlinks needs platform permission")
        with self.assertRaises(SyncError):
            self.a.prepare()
        self.assertEqual(target.read_text(), "original")
        self.assertFalse(self.transport.objects)

    def test_corrupt_blob_fails_before_overwrite(self):
        self.paired()
        self.write("a", "update")
        snapshot = self.a.prepare()["snapshot_id"]
        digest = self.a._manifest(snapshot)["projects"]["project"]["hello.txt"]
        self.transport.objects["blobs/" + digest] = self.cipher.encrypt(b"wrong payload")
        with self.assertRaises(SyncError):
            self.b.receive(snapshot)
        self.assertEqual(self.read("b"), "original")

    def test_malicious_manifest_rejected_before_write(self):
        item = {"version": 1, "device": "x", "parents": [], "projects": {"project": {"../outside": "0" * 64}}, "conflicts": {}}
        raw = _encode(item)
        sid = hashlib.sha256(raw).hexdigest()
        self.transport.objects["snapshots/" + sid] = self.cipher.encrypt(raw)
        with self.assertRaises(SyncError):
            self.b.receive(sid)
        self.assertFalse((self.base / "outside").exists())

    def test_state_must_be_outside_project(self):
        with self.assertRaises(SyncError):
            self.a.add_project(self.base, "other")
        with self.assertRaises(SyncError):
            self.a.add_project(self.base / "a", "other")

    def test_restore_is_causal_and_propagates_to_other_device(self):
        self.paired()
        old = self.a.status()["head"]
        self.write("a", "new")
        self.a.sync()
        self.b.sync()
        latest = self.a.status()["head"]
        restored = self.a.restore(old)
        self.assertNotEqual(restored["snapshot_id"], old)
        self.assertIn(latest, self.a._manifest(restored["snapshot_id"])["parents"])
        self.b.sync()
        self.a.sync()
        self.assertEqual(self.read("a"), "original")
        self.assertEqual(self.read("b"), "original")

    def test_recovery_preserves_independent_post_crash_edit(self):
        self.write("a", "old")
        old = self.a.prepare()["snapshot_id"]
        self.write("a", "new")
        self.a.prepare()
        def fault(point):
            if point == "file:0":
                raise KeyboardInterrupt()
        self.a._fault_hook = fault
        with self.assertRaises(KeyboardInterrupt):
            self.a.restore(old)
        self.write("a", "third party edit")
        with self.assertRaises(SyncError):
            Engine(self.base / "state-a", self.transport, self.cipher, "a")
        self.assertEqual(self.read("a"), "third party edit")
        self.assertTrue(self.a.journal.exists())

    def test_remote_casefold_file_directory_collision_rejected(self):
        with self.assertRaises(SyncError):
            self.a._validate_files({"project": {"Hello": "0" * 64, "hello/file": "0" * 64}})

    def test_real_aes_gcm_and_immutable_local_provider(self):
        from codex_drive_sync.providers import LocalProvider
        from codex_drive_sync.security import Cipher
        provider = LocalProvider(self.base / "real-transport")
        cipher = Cipher(os.urandom(32))
        a = Engine(self.base / "real-state-a", provider, cipher, "real-a")
        b = Engine(self.base / "real-state-b", provider, cipher, "real-b")
        a.add_project(self.base / "a", "project")
        b.add_project(self.base / "b", "project")
        self.write("a", "مرحبا بالعالم", "مجلد/نص.txt")
        first = a.sync()["snapshot_id"]
        b.sync()
        a.sync()
        b.sync()
        self.assertEqual(self.read("b", "مجلد/نص.txt"), "مرحبا بالعالم")
        envelope_before = provider.get("snapshots/" + first)
        a._put_plain("snapshots/" + first, cipher.decrypt(envelope_before))
        self.assertEqual(provider.get("snapshots/" + first), envelope_before)
        self.write("a", "نسخة ثانية", "مجلد/نص.txt")
        a.sync()
        b.sync()
        self.assertEqual(self.read("b", "مجلد/نص.txt"), "نسخة ثانية")

    def test_receive_same_path_preserves_local_and_remote_variants(self):
        self.write("a", "remote")
        sid = self.a.prepare()["snapshot_id"]
        self.write("b", "local")
        result = self.b.receive(sid)
        self.assertEqual(self.read("b"), "local")
        self.assertEqual(len(result["conflicts"]), 1)
        variants = self.b.conflicts()[0]["variants"]
        self.assertEqual({self.b._blob(v).decode() for v in variants}, {"local", "remote"})

    def test_configurable_exclusions_and_known_credentials(self):
        self.a.set_exclusions(["*.private", "ignored"])
        for filename in ("auth.json", "tokens.json", "client_secret_demo.json", "recovery-key.json", "note.private", "ignored/file.txt"):
            self.write("a", "SECRET", filename)
        self.write("a", "public")
        self.assertEqual(set(self.a.inventory()["projects"]["project"]), {"hello.txt"})
        sid = self.a.prepare()["snapshot_id"]
        self.assertEqual(set(self.a._manifest(sid)["projects"]["project"]), {"hello.txt"})

    def test_binary_content_and_file_size_limit(self):
        payload = bytes(range(256)) * 4096
        (self.base / "a" / "binary.bin").write_bytes(payload)
        self.a.sync()
        self.b.sync()
        self.assertEqual((self.base / "b" / "binary.bin").read_bytes(), payload)
        from unittest.mock import patch
        with patch("codex_drive_sync.engine.MAX_FILE_BYTES", 20), self.assertRaises(SyncError):
            self.a.prepare()

    def test_unknown_manifest_schema_rejected(self):
        item = {"version": 999, "device": "x", "parents": [], "projects": {}, "conflicts": {}}
        raw = _encode(item)
        sid = hashlib.sha256(raw).hexdigest()
        self.transport.objects["snapshots/" + sid] = self.cipher.encrypt(raw)
        with self.assertRaises(SyncError):
            self.b.receive(sid)

    def test_status_pending_changes_uses_local_baseline(self):
        self.write("a", "initial")
        self.assertEqual(self.a.status()["pending_changes"], 1)
        self.a.prepare()
        self.assertEqual(self.a.status()["pending_changes"], 0)
        self.write("a", "updated")
        self.assertEqual(self.a.status()["pending_changes"], 1)

    def test_resolved_conflict_does_not_return_from_unchanged_offline_branch(self):
        self.paired()
        self.write("a", "alpha")
        self.write("b", "beta")
        self.a.prepare()
        self.b.sync()
        self.a.sync()
        conflict = self.b.conflicts()[0]
        self.b.resolve(conflict["conflict_id"], "local")
        self.write("a", "unrelated offline edit", "extra.txt")
        self.a.prepare()
        self.a.sync()
        self.assertFalse(self.a.conflicts())
        self.assertEqual(self.read("a"), "beta")

    def test_apply_checks_unchanged_blob_before_snapshot_activation(self):
        self.paired()
        sid = self.b.status()["head"]
        item = self.b._manifest(sid)
        digest = item["projects"]["project"]["hello.txt"]
        self.transport.objects["blobs/" + digest] = self.cipher.encrypt(b"corrupt")
        before = self.b.state_file.read_bytes()
        with self.assertRaises(SyncError):
            with self.b._locked():
                current, _ = self.b._scan()
                self.b._apply(item["projects"], {}, sid, current, True, False)
        self.assertEqual(self.read("b"), "original")
        self.assertEqual(self.b.state_file.read_bytes(), before)
        self.assertFalse(self.b.journal.exists())

    def test_apply_checks_unselected_conflict_variant(self):
        self.write("a", "selected")
        sid = self.a.prepare()["snapshot_id"]
        item = self.a._manifest(sid)
        variant = hashlib.sha256(b"unselected").hexdigest()
        self.transport.objects["blobs/" + variant] = self.cipher.encrypt(b"corrupt")
        selected = item["projects"]["project"]["hello.txt"]
        conflict = {"0" * 64: {"project": "project", "path": "hello.txt", "variants": [selected, variant]}}
        before = self.a.state_file.read_bytes()
        with self.assertRaises(SyncError):
            with self.a._locked():
                current, _ = self.a._scan()
                self.a._apply(item["projects"], conflict, sid, current, True, False)
        self.assertEqual(self.read("a"), "selected")
        self.assertEqual(self.a.state_file.read_bytes(), before)

    def test_recovery_preflights_all_changes_before_rollback(self):
        self.write("a", "old first", "a.txt")
        self.write("a", "old last", "z.txt")
        old = self.a.prepare()["snapshot_id"]
        self.write("a", "new first", "a.txt")
        self.write("a", "new last", "z.txt")
        self.a.prepare()
        def fault(point):
            if point == "file:1":
                raise KeyboardInterrupt()
        self.a._fault_hook = fault
        with self.assertRaises(KeyboardInterrupt):
            self.a.restore(old)
        # Recovery visits z.txt first; it must not roll that back before noticing
        # the independent edit in a.txt later in its traversal.
        self.write("a", "independent edit", "a.txt")
        with self.assertRaises(SyncError):
            Engine(self.base / "state-a", self.transport, self.cipher, "a")
        self.assertEqual(self.read("a", "a.txt"), "independent edit")
        self.assertEqual(self.read("a", "z.txt"), "old last")
        self.assertTrue(self.a.journal.exists())

    def test_recovery_preflights_all_backup_integrity_before_rollback(self):
        self.write("a", "old first", "a.txt")
        self.write("a", "old last", "z.txt")
        old = self.a.prepare()["snapshot_id"]
        self.write("a", "new first", "a.txt")
        self.write("a", "new last", "z.txt")
        self.a.prepare()
        def fault(point):
            if point == "file:1":
                raise KeyboardInterrupt()
        self.a._fault_hook = fault
        with self.assertRaises(KeyboardInterrupt):
            self.a.restore(old)
        journal = json.loads(self.a.journal.read_text("utf-8"))
        backup = self.a.state_dir / "backups" / journal["transaction"] / journal["changes"][0]["before"]
        backup.write_bytes(self.cipher.encrypt(b"corrupted backup"))
        with self.assertRaises(SyncError):
            Engine(self.base / "state-a", self.transport, self.cipher, "a")
        self.assertEqual(self.read("a", "a.txt"), "old first")
        self.assertEqual(self.read("a", "z.txt"), "old last")
        self.assertTrue(self.a.journal.exists())

    def test_materialized_size_limit_counts_duplicate_content(self):
        from unittest.mock import patch
        payload = b"0123456789"
        digest = hashlib.sha256(payload).hexdigest()
        self.transport.objects["blobs/" + digest] = self.cipher.encrypt(payload)
        target = {"project": {"one.txt": digest, "two.txt": digest}}
        with patch("codex_drive_sync.engine.MAX_TOTAL_BYTES", 15), self.assertRaises(SyncError):
            with self.a._locked():
                self.a._apply(target, {}, "0" * 64, {"project": {}}, True, False)
        self.assertFalse((self.base / "a" / "one.txt").exists())

    def test_receive_propagates_tracked_deletion_after_pairing(self):
        self.write("a", "original")
        original = self.a.sync()["snapshot_id"]
        self.write("b", "initial extra", "extra.txt")
        self.b.receive(original)
        self.assertEqual(self.read("b", "extra.txt"), "initial extra")
        (self.base / "a" / "hello.txt").unlink()
        deleted = self.a.prepare()["snapshot_id"]
        before = self.b.state_file.read_bytes()
        preview = self.b.receive(deleted, dry_run=True)
        self.assertTrue(any(c["path"] == "hello.txt" and c["hash"] is None for c in preview["changes"]))
        self.assertEqual(self.b.state_file.read_bytes(), before)
        self.assertTrue((self.base / "b" / "hello.txt").exists())
        self.b.receive(deleted)
        self.assertFalse((self.base / "b" / "hello.txt").exists())
        self.assertEqual(self.read("b", "extra.txt"), "initial extra")
        self.a.sync()
        self.b.sync()
        self.assertFalse((self.base / "a" / "hello.txt").exists())
        self.assertFalse((self.base / "b" / "hello.txt").exists())

    def test_state_and_transport_cannot_overlap_before_creation(self):
        from codex_drive_sync.providers import LocalProvider
        provider = LocalProvider(self.base / "isolated-transport")
        nested = provider.root / "sensitive-state"
        with self.assertRaises(SyncError):
            Engine(nested, provider, self.cipher, "nested")
        self.assertFalse(nested.exists())
        with self.assertRaises(SyncError):
            Engine(provider.root, provider, self.cipher, "equal")
        with self.assertRaises(SyncError):
            Engine(self.base, provider, self.cipher, "ancestor")
        self.assertEqual(provider.list(), [])


if __name__ == "__main__":
    unittest.main()
