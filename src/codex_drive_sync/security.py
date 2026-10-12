"""Local OS credential storage and authenticated snapshot encryption."""
import base64
import json
import os
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecurityError(RuntimeError):
    pass


class SecretStore:
    def __init__(self, service="codex-drive-sync", backend=None):
        import keyring
        self.backend = backend if backend is not None else keyring.get_keyring()
        allowed = ("keyring.backends.Windows", "keyring.backends.macOS", "keyring.backends.SecretService", "keyring.backends.kwallet")
        if not self.backend.__class__.__module__.startswith(allowed):
            raise SecurityError("A secure OS credential backend is required; plaintext fallback is forbidden")
        self.service = service

    def get(self, name):
        return self.backend.get_password(self.service, name)

    def set(self, name, value):
        self.backend.set_password(self.service, name, value)

    def delete(self, name):
        if self.get(name) is not None:
            self.backend.delete_password(self.service, name)


class Cipher:
    MAGIC = b"CDS1"
    def __init__(self, key):
        if len(key) != 32:
            raise SecurityError("Encryption key must be 32 bytes")
        self.key = key
        self.aes = AESGCM(key)

    @classmethod
    def from_store(cls, store):
        encoded = store.get("encryption-key")
        if encoded is None:
            encoded = base64.b64encode(os.urandom(32)).decode("ascii")
            store.set("encryption-key", encoded)
        try:
            return cls(base64.b64decode(encoded, validate=True))
        except (ValueError, TypeError) as exc:
            raise SecurityError("Invalid local encryption key") from exc

    def encrypt(self, data):
        nonce = os.urandom(12)
        return self.MAGIC + nonce + self.aes.encrypt(nonce, data, self.MAGIC)

    def decrypt(self, data):
        from cryptography.exceptions import InvalidTag
        if len(data) < 32 or data[:4] != self.MAGIC:
            raise SecurityError("Invalid encrypted envelope")
        try:
            return self.aes.decrypt(data[4:16], data[16:], self.MAGIC)
        except InvalidTag as exc:
            raise SecurityError("Encrypted object integrity check failed") from exc


def export_recovery(store, path):
    """Explicit sensitive recovery export. Caller must keep this file offline."""
    key = store.get("encryption-key")
    if key is None:
        raise SecurityError("No encryption key to export")
    Cipher(base64.b64decode(key, validate=True))
    target = Path(path)
    # Exclusive creation prevents replacing a recovery key accidentally.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump({"format": "codex-drive-sync-recovery-v1", "key": key}, stream)
        stream.flush()
        os.fsync(stream.fileno())


def import_recovery(store, path):
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("format") != "codex-drive-sync-recovery-v1":
        raise SecurityError("Unsupported recovery format")
    key = document.get("key")
    Cipher(base64.b64decode(key, validate=True))
    existing = store.get("encryption-key")
    if existing and existing != key:
        raise SecurityError("Refusing to replace an existing different encryption key")
    store.set("encryption-key", key)
