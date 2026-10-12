"""Immutable local objects and Google Drive appDataFolder transport.

Google protocol references: developers.google.com/identity/protocols/oauth2/native-app
and developers.google.com/workspace/drive/api/guides/appdata.
"""
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import time
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser
import requests

SCOPE = "https://www.googleapis.com/auth/drive.appdata"
TOKEN_URL = "https://oauth2.googleapis.com/token"
MAX_OBJECT_BYTES = 34 * 1024 * 1024


def _reparse(path):
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


class ProviderError(RuntimeError):
    pass


def safe_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,239}", name):
        raise ProviderError("Unsafe object name")
    if any(part in ("", ".", "..") for part in name.split("/")):
        raise ProviderError("Unsafe object name")
    return name


class LocalProvider:
    def __init__(self, root):
        original = Path(root).absolute()
        if any(_reparse(item) for item in [original, *original.parents]):
            raise ProviderError("Reparse point in provider root")
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, name):
        path = self.root.joinpath(*safe_name(name).split("/"))
        if any(_reparse(item) for item in [path, *path.parents] if item.is_relative_to(self.root)):
            raise ProviderError("Reparse point in object store")
        if not path.resolve().is_relative_to(self.root):
            raise ProviderError("Object escapes provider root")
        return path

    def put(self, name, data):
        if len(data) > MAX_OBJECT_BYTES:
            raise ProviderError("Object exceeds transport size limit")
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".upload-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if self.get(name) != data:
                    raise ProviderError("Immutable object already exists with different content")
        finally:
            Path(temporary).unlink(missing_ok=True)

    def get(self, name):
        with self._path(name).open("rb") as stream:
            data = stream.read(MAX_OBJECT_BYTES + 1)
        if len(data) > MAX_OBJECT_BYTES:
            raise ProviderError("Object exceeds transport size limit")
        return data

    def list(self, prefix=""):
        if prefix:
            safe_name(prefix.rstrip("/"))
        result = []
        for path in self.root.rglob("*"):
            if _reparse(path):
                raise ProviderError("Reparse point in object store")
            if path.is_file() and not path.name.startswith(".upload-"):
                name = path.relative_to(self.root).as_posix()
                safe_name(name)
                if name.startswith(prefix):
                    result.append(name)
        return sorted(result)


def _token_request(session, data):
    try:
        response = session.post(TOKEN_URL, data=data, timeout=30)
        document = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ProviderError("OAuth token request failed") from exc
    if response.status_code != 200 or not document.get("access_token"):
        raise ProviderError("OAuth token rejected; reconnect explicitly")
    if "scope" in document and SCOPE not in document["scope"].split():
        raise ProviderError("Required Drive appdata permission was not granted")
    return document


def connect(client_file, store, *, timeout=180, session=None, opener=None):
    """Only this explicit command opens the user's browser."""
    session = session or requests.Session()
    opener = opener or webbrowser.open
    try:
        with Path(client_file).open("rb") as stream:
            raw_client = stream.read(65537)
        if len(raw_client) > 65536:
            raise ValueError("client configuration too large")
        client = json.loads(raw_client)["installed"]
        client_id = client["client_id"]
        client_secret = client["client_secret"]
        if not all(isinstance(value, str) and 0 < len(value) <= 4096 for value in (client_id, client_secret)):
            raise ValueError("invalid client schema")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ProviderError("Provide a local Google Desktop OAuth client JSON file") from exc
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def log_message(self, *args):
            pass

        def do_GET(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            valid = parsed.path == "/callback" and len(query.get("state", [])) == 1 and hmac.compare_digest(query["state"][0], state)
            if not valid:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Invalid authorization response")
                return
            if query.get("error"):
                result["error"] = True
            elif len(query.get("code", [])) == 1:
                result["code"] = query["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Authorization received. Return to Codex Drive Sync.")

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        redirect = f"http://127.0.0.1:{server.server_port}/callback"
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({"client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE, "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent select_account"})
        if not opener(url):
            raise ProviderError("Could not open authorization browser")
        deadline = time.monotonic() + timeout
        while not result and time.monotonic() < deadline:
            server.timeout = min(1, max(0.01, deadline - time.monotonic()))
            server.handle_request()
    if not result.get("code"):
        raise ProviderError("Authorization denied or timed out")
    tokens = _token_request(session, {"client_id": client_id, "client_secret": client_secret, "code": result["code"], "code_verifier": verifier, "redirect_uri": redirect, "grant_type": "authorization_code"})
    if not tokens.get("refresh_token"):
        raise ProviderError("No refresh token received; reconnect with consent")
    store.set("oauth", json.dumps({"client_id": client_id, "client_secret": client_secret, "refresh_token": tokens["refresh_token"]}))


def disconnect(store, revoke=False, *, session=None):
    value = store.get("oauth")
    if revoke and value:
        session = session or requests.Session()
        response = session.post("https://oauth2.googleapis.com/revoke", data={"token": json.loads(value)["refresh_token"]}, timeout=30)
        if response.status_code not in (200, 400):
            raise ProviderError("Token revocation failed")
    store.delete("oauth")


class GoogleDriveProvider:
    def __init__(self, store, *, session=None, sleep=time.sleep, retries=4):
        self.store, self.session, self.sleep = store, session or requests.Session(), sleep
        self.retries = max(0, min(retries, 6))
        self.access_token = None

    def _refresh(self):
        value = self.store.get("oauth")
        if not value:
            raise ProviderError("Google Drive is disconnected; run connect explicitly")
        data = json.loads(value)
        tokens = _token_request(self.session, {**data, "grant_type": "refresh_token"})
        self.access_token = tokens["access_token"]

    def _request(self, method, url, **kwargs):
        if not self.access_token:
            self._refresh()
        refreshed = False
        extra_headers = kwargs.pop("headers", {})
        for attempt in range(self.retries + 2):
            try:
                response = self.session.request(method, url, headers={"Authorization": f"Bearer {self.access_token}", **extra_headers}, timeout=30, **kwargs)
            except requests.RequestException as exc:
                # Upload outcome may be uncertain; never blindly retry creates.
                if method != "GET" or attempt >= self.retries:
                    raise ProviderError("Drive transport failed; outcome may require verification") from exc
                self.sleep(min(2 ** attempt, 16))
                continue
            if response.status_code == 401 and not refreshed:
                self._refresh()
                refreshed = True
                continue
            quota = response.status_code == 429
            if response.status_code == 403:
                try:
                    quota = any(e.get("reason") in ("rateLimitExceeded", "userRateLimitExceeded") for e in response.json().get("error", {}).get("errors", []))
                except ValueError:
                    pass
            if (quota or response.status_code >= 500) and method == "GET" and attempt < self.retries:
                self.sleep(min(2 ** attempt, 16))
                continue
            if not 200 <= response.status_code < 300:
                raise ProviderError(f"Drive request rejected (HTTP {response.status_code})")
            return response
        raise ProviderError("Drive retry budget exhausted")

    def _files(self, name=None):
        params = {"spaces": "appDataFolder", "pageSize": 1000, "fields": "nextPageToken,files(id,name)", "q": "trashed = false"}
        if name is not None:
            params["q"] += f" and name = '{safe_name(name)}'"
        files = []
        seen = set()
        while True:
            document = self._request("GET", "https://www.googleapis.com/drive/v3/files", params=params).json()
            files.extend(document.get("files", []))
            page = document.get("nextPageToken")
            if not page:
                return files
            if page in seen:
                raise ProviderError("Drive pagination loop")
            seen.add(page)
            params["pageToken"] = page

    def _download(self, identifier):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            raise ProviderError("Invalid Drive object ID")
        response = self._request("GET", f"https://www.googleapis.com/drive/v3/files/{identifier}", params={"alt": "media"}, stream=True)
        try:
            output = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                if len(output) + len(chunk) > MAX_OBJECT_BYTES:
                    raise ProviderError("Object exceeds transport size limit")
                output.extend(chunk)
            return bytes(output)
        except requests.RequestException as exc:
            raise ProviderError("Drive download interrupted") from exc
        finally:
            response.close()

    def get(self, name):
        entries = self._files(name)
        if not entries:
            raise FileNotFoundError(name)
        first = self._download(entries[0]["id"])
        authenticated_plaintext = None
        for entry in entries[1:]:
            other = self._download(entry["id"])
            if other != first:
                # Concurrent clients use independent AES-GCM nonces. Only hash-addressed
                # objects may reconcile ciphertext variants after local authentication.
                match = re.search(r"(?:^|/)(?:blobs|snapshots)/([0-9a-f]{64})$", name)
                encoded_key = self.store.get("encryption-key")
                if match is None or encoded_key is None:
                    raise ProviderError("Duplicate Drive names contain different data")
                try:
                    from .security import Cipher, SecurityError
                    cipher = Cipher(base64.b64decode(encoded_key, validate=True))
                    if authenticated_plaintext is None:
                        authenticated_plaintext = cipher.decrypt(first)
                        if hashlib.sha256(authenticated_plaintext).hexdigest() != match.group(1):
                            raise ProviderError("Duplicate object plaintext hash mismatch")
                    plaintext = cipher.decrypt(other)
                    if hashlib.sha256(plaintext).hexdigest() != match.group(1) or plaintext != authenticated_plaintext:
                        raise ProviderError("Duplicate object plaintext hash mismatch")
                except (SecurityError, ValueError, TypeError) as exc:
                    raise ProviderError("Duplicate object authentication failed") from exc
        return first

    def put(self, name, data):
        safe_name(name)
        if len(data) > MAX_OBJECT_BYTES:
            raise ProviderError("Object exceeds transport size limit")
        if self._files(name):
            if self.get(name) != data:
                raise ProviderError("Immutable object already exists with different content")
            return
        boundary = "cds_" + secrets.token_hex(24)
        metadata = json.dumps({"name": name, "parents": ["appDataFolder"]}).encode()
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode() + metadata + f"\r\n--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n".encode() + data + f"\r\n--{boundary}--\r\n".encode())
        self._request("POST", "https://www.googleapis.com/upload/drive/v3/files", params={"uploadType": "multipart", "fields": "id"}, data=body, headers={"Content-Type": f"multipart/related; boundary={boundary}"})
        if self.get(name) != data:
            raise ProviderError("Drive upload verification failed")

    def list(self, prefix=""):
        if prefix:
            safe_name(prefix.rstrip("/"))
        names = {safe_name(entry["name"]) for entry in self._files()}
        return sorted(name for name in names if name.startswith(prefix))
