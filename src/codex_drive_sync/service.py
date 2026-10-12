"""Polling daemon controlled by private local files, no public HTTP listener."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time


def _atomic(p,obj):
    tmp=p.with_suffix('.tmp'); tmp.write_text(json.dumps(obj),encoding='utf-8'); os.replace(tmp,p)


def _alive(pid):
    if not isinstance(pid,int) or pid<1: return False
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x100000,False,pid)
        if not handle: return False
        try: return kernel.WaitForSingleObject(handle,0)==258
        finally: kernel.CloseHandle(handle)
    try: os.kill(pid,0); return True
    except OSError: return False


def control(root,action,interval=60):
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    lease=root/'service-lease.json'; heartbeat=root/'service-health.json'; stop=root/'service-stop'
    if action=='health':
        if not heartbeat.exists() or not lease.exists(): return {'running':False}
        try:
            v=json.loads(heartbeat.read_text('utf-8')); owner=json.loads(lease.read_text('utf-8'))
        except (OSError,ValueError): return {'running':False}
        return {**v,'running':v.get('pid')==owner.get('pid') and v.get('token')==owner.get('token') and _alive(owner.get('pid'))}
    if action=='stop':
        stop.write_text('stop',encoding='ascii')
        deadline=time.monotonic()+45
        while lease.exists():
            owner=json.loads(lease.read_text('utf-8'))
            if not _alive(owner.get('pid')):
                lease.unlink(missing_ok=True); break
            if time.monotonic()>deadline: raise RuntimeError('Worker still running; installation must be preserved')
            time.sleep(.1)
        return {'stop_requested':True,'stopped':True,'projects_preserved':True}
    if action=='start':
        if control(root,'health')['running']: return {'running':True}
        if lease.exists(): raise ValueError('Stale lease: verify no worker then remove service-lease.json')
        stop.unlink(missing_ok=True)
        cmd=[sys.executable,'-m','codex_drive_sync.cli','--home',str(root),'service','run','--interval',str(interval)]
        kwargs={'stdin':subprocess.DEVNULL,'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL}
        if os.name=='nt': kwargs['creationflags']=subprocess.CREATE_NO_WINDOW
        subprocess.Popen(cmd,**kwargs)
        for _ in range(30):
            if heartbeat.exists() and control(root,'health')['running']: return {'started':True}
            time.sleep(.1)
        raise RuntimeError('Worker did not report healthy')
    if interval<10 or interval>86400: raise ValueError('Interval outside bounds')
    from .cli import build_engine,require_review
    token=secrets.token_hex(16)
    with lease.open('x',encoding='utf-8') as f: json.dump({'pid':os.getpid(),'token':token},f)
    try:
        engine=build_engine(root); require_review(root,engine)
        _atomic(heartbeat,{'pid':os.getpid(),'token':token,'heartbeat':time.time(),'interval':interval,'last_result':'starting'})
        while not stop.exists():
            result='PASS'
            try: engine.sync()
            except Exception as exc: result=type(exc).__name__
            _atomic(heartbeat,{'pid':os.getpid(),'token':token,'heartbeat':time.time(),'interval':interval,'last_result':result})
            with (root/'service-events.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps({'time':time.time(),'event':'sync','result':result})+'\n')
            for _ in range(interval*2):
                if stop.exists(): break
                time.sleep(.5)
    finally:
        lease.unlink(missing_ok=True)
        _atomic(heartbeat,{'heartbeat':0,'last_result':'stopped','interval':interval})
    return {'stopped':True}
