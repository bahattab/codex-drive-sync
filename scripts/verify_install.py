"""Behavioral Windows installed-package check with synthetic local data only."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid
from codex_drive_sync.security import SecretStore


def verify(python):
    namespace='codex-drive-sync-install-test-'+uuid.uuid4().hex
    store=SecretStore(service=namespace)
    env=os.environ.copy(); env['CODEX_DRIVE_SYNC_SECRET_SERVICE']=namespace
    with tempfile.TemporaryDirectory() as d:
        root=Path(d); home=root/'state'; project=root/'project'; project.mkdir()
        (project/'ملف.txt').write_text('synthetic installed acceptance',encoding='utf-8')
        env['CODEX_DRIVE_SYNC_HOME']=str(home)
        cmd=[str(python),'-m','codex_drive_sync.cli']
        def run(*args):
            p=subprocess.run(cmd+list(args),capture_output=True,text=True,encoding='utf-8',env={**env,'PYTHONUTF8':'1'},timeout=70)
            if p.returncode: raise RuntimeError('Installed command failed: '+args[0])
            return json.loads(p.stdout)
        try:
            run('configure','--provider','local','--transport',str(root/'transport'))
            run('projects','add','--id','synthetic','--path',str(project))
            preview=run('sync','--dry-run')
            run('sync','--approve-inventory',preview['approval_digest'])
            run('service','start','--interval','10')
            assert run('service','health')['running']
            assert run('service','stop')['stopped']
            assert not run('service','health')['running']
            req={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'synthetic','version':'1'}}}
            p=subprocess.run(cmd+['mcp'],input=json.dumps(req)+'\n',capture_output=True,text=True,env=env,timeout=10)
            assert p.returncode==0 and json.loads(p.stdout)['result']['serverInfo']['name']=='codex-drive-sync'
            assert (project/'ملف.txt').read_text('utf-8')=='synthetic installed acceptance'
            return {'installed_cli_local_sync':'PASS','actual_worker_start_health_stop':'PASS','installed_stdio_mcp':'PASS','project_preserved':'PASS','test_namespace_cleanup':'performed in finally'}
        finally:
            if (home/'service-lease.json').exists():
                subprocess.run(cmd+['service','stop'],env=env,capture_output=True,timeout=50)
            store.delete('encryption-key'); store.delete('oauth')


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--python',type=Path,required=True); a=p.parse_args()
    try: print(json.dumps(verify(a.python),ensure_ascii=False,indent=2))
    except Exception as exc:
        print(json.dumps({'installed_acceptance':'FAIL','error_type':type(exc).__name__,'sensitive_details':'omitted'}))
        raise SystemExit(1)
