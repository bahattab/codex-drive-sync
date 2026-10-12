"""Mock/local acceptance; live Google authorization and Drive tests are NOT RUN."""
import base64
import hashlib
import json
import threading
from urllib.parse import parse_qs, urlparse
import requests
import unittest
import tempfile
from pathlib import Path


def raises(exception, match=None):
    case = unittest.TestCase()
    return case.assertRaisesRegex(exception, match) if match else case.assertRaises(exception)
from codex_drive_sync.providers import LocalProvider, GoogleDriveProvider, ProviderError, connect
from codex_drive_sync.security import Cipher, SecretStore, SecurityError, export_recovery, import_recovery


class MemoryStore:
    def __init__(self): self.values = {}
    def get(self, name): return self.values.get(name)
    def set(self, name, value): self.values[name] = value
    def delete(self, name): self.values.pop(name, None)


def test_local_immutable_and_traversal(tmp_path):
    provider = LocalProvider(tmp_path)
    provider.put("objects/one", b"one")
    provider.put("objects/one", b"one")
    assert provider.get("objects/one") == b"one"
    assert provider.list("objects/") == ["objects/one"]
    with raises(ProviderError): provider.put("objects/one", b"two")
    for name in ("../secret", "/absolute", "a//b", "C:/secret", "a/../b"):
        with raises(ProviderError): provider.put(name, b"x")


def test_cipher_integrity_and_recovery(tmp_path):
    store = MemoryStore()
    cipher = Cipher.from_store(store)
    encrypted = cipher.encrypt(b"private")
    assert cipher.decrypt(encrypted) == b"private"
    with raises(SecurityError): cipher.decrypt(encrypted[:-1] + bytes([encrypted[-1] ^ 1]))
    with raises(SecurityError): Cipher(b"x" * 32).decrypt(encrypted)
    recovery = tmp_path / "offline-recovery.json"
    export_recovery(store, recovery)
    other = MemoryStore()
    import_recovery(other, recovery)
    assert Cipher.from_store(other).decrypt(encrypted) == b"private"
    with raises(FileExistsError): export_recovery(store, recovery)
    different = MemoryStore()
    Cipher.from_store(different)
    with raises(SecurityError): import_recovery(different, recovery)


def test_plaintext_backend_rejected():
    class Plaintext: pass
    with raises(SecurityError): SecretStore(backend=Plaintext())


class Response:
    def __init__(self, status=200, document=None, content=b""):
        self.status_code, self.document, self.content = status, document or {}, content
    def json(self): return self.document
    def iter_content(self, chunk_size):
        yield self.content
    def close(self): pass


def test_oauth_pkce_state_and_token(tmp_path):
    path = tmp_path / "client.json"
    path.write_text(json.dumps({"installed": {"client_id": "test-client", "client_secret": "test-secret"}}))
    store = MemoryStore()
    query = {}
    threads = []
    class Session:
        def post(self, url, data, timeout):
            challenge = base64.urlsafe_b64encode(hashlib.sha256(data["code_verifier"].encode()).digest()).rstrip(b"=").decode()
            assert challenge == query["code_challenge"][0]
            assert data["code"] == "test-code"
            return Response(document={"access_token": "test-access", "refresh_token": "test-refresh"})
    def opener(url):
        query.update(parse_qs(urlparse(url).query))
        assert query["code_challenge_method"] == ["S256"]
        assert query["prompt"] == ["consent select_account"]
        def callback():
            redirect = query["redirect_uri"][0]
            assert requests.get(redirect, params={"state": "incorrect", "code": "bad"}, timeout=5).status_code == 400
            assert requests.get(redirect, params={"state": query["state"][0], "code": "test-code"}, timeout=5).status_code == 200
        thread = threading.Thread(target=callback)
        threads.append(thread)
        thread.start()
        return True
    connect(path, store, timeout=5, opener=opener, session=Session())
    for thread in threads: thread.join()
    assert json.loads(store.get("oauth"))["refresh_token"] == "test-refresh"


def test_oauth_token_rejected(tmp_path):
    from codex_drive_sync.providers import _token_request
    class Session:
        def post(self, *args, **kwargs): return Response(400, {"error": "invalid_grant", "error_description": "sensitive"})
    with raises(ProviderError, match="reconnect") as result: _token_request(Session(), {})
    assert "sensitive" not in str(result.exception)


def test_duplicates_fail_closed():
    provider = GoogleDriveProvider(MemoryStore())
    provider._files = lambda name=None: [{"id": "a", "name": "one"}, {"id": "b", "name": "one"}]
    provider._download = lambda identifier: identifier.encode()
    with raises(ProviderError, match="Duplicate"): provider.get("one")
    provider._download = lambda identifier: b"same"
    assert provider.get("one") == b"same"


def test_retry_budget_and_upload_not_retried():
    calls = []
    class Session:
        def request(self, *args, **kwargs):
            calls.append(args)
            return Response(429)
    provider = GoogleDriveProvider(MemoryStore(), session=Session(), sleep=lambda seconds: None, retries=2)
    provider.access_token = "test-token"
    with raises(ProviderError): provider._request("GET", "https://example.invalid")
    assert len(calls) == 3
    calls.clear()
    with raises(ProviderError): provider._request("POST", "https://example.invalid")
    assert len(calls) == 1


class ProviderTests(unittest.TestCase):
    def test_duplicate_envelopes_same_authenticated_plaintext(self):
        store = MemoryStore()
        cipher = Cipher.from_store(store)
        plaintext = b"concurrent content"
        digest = hashlib.sha256(plaintext).hexdigest()
        provider = GoogleDriveProvider(store)
        provider._files = lambda name: [{"id": "a"}, {"id": "b"}]
        variants = {"a": cipher.encrypt(plaintext), "b": cipher.encrypt(plaintext)}
        self.assertNotEqual(variants["a"], variants["b"])
        provider._download = variants.__getitem__
        for name in ("blobs/" + digest, "namespace/snapshots/" + digest):
            self.assertEqual(cipher.decrypt(provider.get(name)), plaintext)
        variants["b"] = cipher.encrypt(b"different")
        with self.assertRaisesRegex(ProviderError, "hash mismatch"):
            provider.get("blobs/" + digest)
        variants["b"] = cipher.encrypt(plaintext)
        with self.assertRaisesRegex(ProviderError, "hash mismatch"):
            provider.get("blobs/" + "0" * 64)
        variants["b"] = variants["b"][:-1] + bytes([variants["b"][-1] ^ 1])
        with self.assertRaisesRegex(ProviderError, "authentication failed"):
            provider.get("blobs/" + digest)

    def test_duplicate_decryption_never_generates_key(self):
        store = MemoryStore()
        provider = GoogleDriveProvider(store)
        provider._files = lambda name: [{"id": "a"}, {"id": "b"}]
        provider._download = lambda identifier: identifier.encode()
        with self.assertRaises(ProviderError):
            provider.get("namespace/blobs/" + "0" * 64)
        self.assertEqual(store.values, {})

    def test_local_size_bounds(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory, patch("codex_drive_sync.providers.MAX_OBJECT_BYTES", 4):
            provider = LocalProvider(directory)
            with self.assertRaisesRegex(ProviderError, "size limit"):
                provider.put("too-big", b"12345")
            (Path(directory) / "too-big").write_bytes(b"12345")
            with self.assertRaisesRegex(ProviderError, "size limit"):
                provider.get("too-big")

    def test_stream_size_bounds(self):
        from unittest.mock import patch
        provider = GoogleDriveProvider(MemoryStore())
        provider._request = lambda *args, **kwargs: Response(content=b"12345")
        with patch("codex_drive_sync.providers.MAX_OBJECT_BYTES", 4):
            with self.assertRaisesRegex(ProviderError, "size limit"):
                provider._download("test-id")

    def test_local_reparse_rejected(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            provider = LocalProvider(directory)
            with patch("codex_drive_sync.providers._reparse", return_value=True):
                with self.assertRaisesRegex(ProviderError, "Reparse"):
                    provider.get("object")

    def test_oauth_client_size_and_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "client.json"
            for data in (b"x" * 65537, b'{"installed":{"client_id":2,"client_secret":"test"}}'):
                path.write_bytes(data)
                with self.assertRaisesRegex(ProviderError, "local Google Desktop"):
                    connect(path, MemoryStore(), opener=lambda url: self.fail("Invalid client opened browser"))

    def test_disconnected_does_not_open_browser(self):
        provider = GoogleDriveProvider(MemoryStore())
        with self.assertRaisesRegex(ProviderError, "disconnected"):
            provider.list()

    def test_expired_access_refreshes_once(self):
        calls = []
        class Session:
            def post(self, *args, **kwargs):
                return Response(document={"access_token": "renewed"})
            def request(self, *args, **kwargs):
                calls.append(kwargs["headers"]["Authorization"])
                return Response(401 if len(calls) == 1 else 200)
        store = MemoryStore()
        store.set("oauth", json.dumps({"client_id": "test", "client_secret": "test", "refresh_token": "test"}))
        provider = GoogleDriveProvider(store, session=Session())
        provider.access_token = "expired"
        provider._request("GET", "https://example.invalid")
        self.assertEqual(calls, ["Bearer expired", "Bearer renewed"])

    def test_permission_denied_not_retried(self):
        calls = []
        class Session:
            def request(self, *args, **kwargs):
                calls.append(True)
                return Response(403, {"error": {"errors": [{"reason": "insufficientPermissions"}]}})
        provider = GoogleDriveProvider(MemoryStore(), session=Session())
        provider.access_token = "test"
        with self.assertRaisesRegex(ProviderError, "403"):
            provider._request("GET", "https://example.invalid")
        self.assertEqual(len(calls), 1)

    def test_oauth_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "client.json"
            path.write_text(json.dumps({"installed": {"client_id": "test", "client_secret": "test"}}))
            store = MemoryStore()
            with self.assertRaisesRegex(ProviderError, "timed out"):
                connect(path, store, timeout=0.01, opener=lambda url: True)
            self.assertIsNone(store.get("oauth"))

    def test_local(self):
        with tempfile.TemporaryDirectory() as directory:
            test_local_immutable_and_traversal(Path(directory))

    def test_cipher(self):
        with tempfile.TemporaryDirectory() as directory:
            test_cipher_integrity_and_recovery(Path(directory))

    def test_backend(self):
        test_plaintext_backend_rejected()

    def test_oauth(self):
        with tempfile.TemporaryDirectory() as directory:
            test_oauth_pkce_state_and_token(Path(directory))

    def test_token_error(self):
        test_oauth_token_rejected(None)

    def test_duplicates(self):
        test_duplicates_fail_closed()

    def test_retries(self):
        test_retry_budget_and_upload_not_retried()
