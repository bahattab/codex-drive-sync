"""Reproducible synthetic file handoff on actual operator-selected devices.

Native conversation/UI acceptance remains a separate blocked gate.
"""
import argparse
import hashlib
import json
from pathlib import Path
import uuid
from codex_drive_sync.engine import Engine
from codex_drive_sync.providers import GoogleDriveProvider
from codex_drive_sync.security import Cipher,SecretStore
from verify_drive import Namespace


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    workspace=args.workspace.resolve(); workspace.mkdir(parents=True,exist_ok=True)
    project=workspace/'synthetic-project'; project.mkdir(exist_ok=True)
    store=SecretStore(); cipher=Cipher.from_store(store)
    if args.mode=='prepare-a':
        if any(project.iterdir()): raise ValueError('Use a new empty acceptance workspace')
        challenge={'schema':1,'prefix':'two-device/'+uuid.uuid4().hex+'/','project_id':'synthetic-two-device','conversation_mode':'BLOCKED'}
        (project/'تجربة.txt').write_text('synthetic device A',encoding='utf-8')
        (project/'binary.bin').write_bytes(bytes(range(256))*4)
    else:
        challenge=json.loads(args.challenge.read_text('utf-8-sig'))
        if challenge.get('schema')!=1 or not isinstance(challenge.get('prefix'),str) or not challenge['prefix'].startswith('two-device/'):
            raise ValueError('Unsupported challenge')
    transport=Namespace(GoogleDriveProvider(store),challenge['prefix'])
    engine=Engine(workspace/'state',transport,cipher)
    engine.map_project(challenge['project_id'],project)
    if args.mode=='prepare-a':
        challenge['initial_hashes']={p.name:digest(p) for p in project.iterdir()}
        challenge['snapshot_id']=engine.prepare()['snapshot_id']
    elif args.mode=='receive-b':
        received=engine.receive(challenge['snapshot_id'])
        if received['conflicts']: raise ValueError('Acceptance conflict: preserve both and inspect')
        for name,value in challenge['initial_hashes'].items():
            if name not in ('تجربة.txt','binary.bin') or digest(project/name)!=value: raise ValueError('Integrity mismatch')
        (project/'تجربة.txt').write_text('synthetic edited on device B',encoding='utf-8')
        challenge['return_snapshot_id']=engine.prepare()['snapshot_id']
        challenge['device_b_files']='PASS'
    else:
        received=engine.receive(challenge['return_snapshot_id'])
        if received['conflicts']: raise ValueError('Acceptance return conflict')
        if (project/'تجربة.txt').read_text('utf-8')!='synthetic edited on device B': raise ValueError('Return mismatch')
        if digest(project/'binary.bin')!=challenge['initial_hashes']['binary.bin']: raise ValueError('Binary mismatch')
        challenge['roundtrip_files']='PASS'
    args.output.write_text(json.dumps(challenge,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'step':args.mode,'result':'PASS','native_conversation':'BLOCKED','full_codex_workspace':'NOT PASSED','operator_must_confirm_actual_devices':True}


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--mode',choices=['prepare-a','receive-b','verify-a'],required=True); p.add_argument('--workspace',type=Path,required=True); p.add_argument('--challenge',type=Path); p.add_argument('--output',type=Path,required=True); a=p.parse_args()
    try:
        if a.mode!='prepare-a' and not a.challenge: raise ValueError('Challenge required')
        print(json.dumps(run(a),ensure_ascii=False,indent=2))
    except Exception as exc:
        print(json.dumps({'step':a.mode,'result':'FAIL','error_type':type(exc).__name__,'sensitive_details':'omitted'})); raise SystemExit(1)
