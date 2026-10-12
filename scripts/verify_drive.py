"""Live non-sensitive Drive smoke test after explicit OAuth connect.

This is two temporary local environments, never two actual computers.
Encrypted synthetic objects remain in a unique test namespace for inspection.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import uuid
from codex_drive_sync.engine import Engine
from codex_drive_sync.providers import GoogleDriveProvider
from codex_drive_sync.security import Cipher,SecretStore


class Namespace:
    def __init__(self,provider,prefix): self.provider,self.prefix=provider,prefix
    def put(self,name,data): return self.provider.put(self.prefix+name,data)
    def get(self,name): return self.provider.get(self.prefix+name)
    def list(self,prefix=''): return [x[len(self.prefix):] for x in self.provider.list(self.prefix+prefix)]


def verify():
    store=SecretStore()
    transport=Namespace(GoogleDriveProvider(store),'acceptance/'+uuid.uuid4().hex+'/')
    cipher=Cipher.from_store(store)
    with tempfile.TemporaryDirectory() as d:
        root=Path(d); a=root/'project-a'; b=root/'different-project-b'; a.mkdir(); b.mkdir()
        (a/'تجربة.txt').write_text('synthetic Drive acceptance',encoding='utf-8')
        (a/'binary.bin').write_bytes(bytes(range(256))*4)
        first=Engine(root/'state-a',transport,cipher,'smoke-a'); second=Engine(root/'state-b',transport,cipher,'smoke-b')
        first.add_project(a,'synthetic'); second.add_project(b,'synthetic')
        prepared=first.prepare(); received=second.receive(prepared['snapshot_id'])
        assert received['conflicts']==[]
        for name in ('تجربة.txt','binary.bin'):
            assert hashlib.sha256((a/name).read_bytes()).digest()==hashlib.sha256((b/name).read_bytes()).digest()
        (b/'تجربة.txt').write_text('synthetic return from temporary environment',encoding='utf-8')
        second.sync(); first.sync()
        assert (a/'تجربة.txt').read_bytes()==(b/'تجربة.txt').read_bytes()
        snapshot_count=len(first.snapshots()); first.sync()
        assert len(first.snapshots())==snapshot_count
        return {'live_google_drive_encrypted_roundtrip':'PASS','arabic_binary_integrity':'PASS','incremental_return_and_idempotency':'PASS','temporary_local_environments':2,'actual_two_computers':'NOT RUN','native_conversation_import':'BLOCKED','synthetic_objects_retained':True}


if __name__=='__main__':
    try: print(json.dumps(verify(),ensure_ascii=False,indent=2))
    except Exception as exc:
        print(json.dumps({'live_google_drive':'FAIL','error_type':type(exc).__name__,'sensitive_details':'omitted'}))
        raise SystemExit(1)
