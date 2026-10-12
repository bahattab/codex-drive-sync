"""Read-only metadata inventory; never returns configuration secret values."""
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess


def doctor():
    root = Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
    report = {'platform':platform.system(),'python':platform.python_version(),'conversation_mode':'archive/context export; native import disabled','compatibility':'this program disables native import; no proven adapter','google_authorization':'use status/connect; browser sign-in is not authorization','locations':{},'databases':{}}
    for name in ('sessions','attachments','skills','plugins','config.toml','state_5.sqlite','thread_history_1.sqlite'):
        p=root/name
        report['locations'][name]={'exists':p.exists(),'kind':'directory' if p.is_dir() else 'file'}
    binary=shutil.which('codex')
    if binary:
        try:
            r=subprocess.run([binary,'--version'],capture_output=True,text=True,timeout=15,shell=False)
            report['path_cli_version']=r.stdout.strip()[:100] if r.returncode==0 else 'unavailable'
        except (OSError,subprocess.TimeoutExpired): report['path_cli_version']='unavailable'
    if platform.system()=='Windows':
        try:
            r=subprocess.run(['powershell.exe','-NoProfile','-Command','Get-AppxPackage -Name OpenAI.Codex | Select-Object -ExpandProperty Version'],capture_output=True,text=True,timeout=20)
            report['desktop_version']=r.stdout.strip()[:100] or 'unavailable'
        except (OSError,subprocess.TimeoutExpired): report['desktop_version']='unavailable'
    for p in root.glob('*.sqlite'):
        try:
            con=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
            report['databases'][p.name]={'tables':[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")],'wal_present':p.with_name(p.name+'-wal').exists()}
            con.close()
        except sqlite3.Error: report['databases'][p.name]={'inspection':'unavailable'}
    return report


def registered_projects():
    root=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
    p=root/'state_5.sqlite'
    if not p.exists(): return {'projects':[],'inspection':'database unavailable'}
    con=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
    try:
        con.execute('PRAGMA query_only=ON')
        columns={r[1] for r in con.execute('PRAGMA table_info(project_roots)')}
        if not {'project_id','path','position'}<=columns:
            return {'projects':[],'inspection':'unknown schema; automatic registration disabled'}
        rows=con.execute('SELECT project_id,path FROM project_roots ORDER BY project_id,position LIMIT 10000').fetchall()
        return {'projects':[{'codex_project_id':pid,'local_path':path,'enabled':False} for pid,path in rows],'inspection':'read-only local inventory; no automatic activation'}
    finally: con.close()


def portable_inventory(root):
    """Definitions only; no account sessions, config values, or execution trust."""
    root=Path(root)
    skills=[]
    for base in (root/'skills',):
        if base.exists():
            for p in base.glob('*/SKILL.md'):
                if not p.is_symlink(): skills.append({'name':p.parent.name,'mode':'definition-only'})
    plugins=[]
    cache=root/'plugins'/'cache'
    if cache.exists():
        for p in cache.glob('*/*/*/.codex-plugin/plugin.json'):
            try:
                obj=json.loads(p.read_text('utf-8'))
                plugins.append({k:obj[k] for k in ('name','version') if k in obj and isinstance(obj[k],str)})
            except (OSError,ValueError): pass
    counts={}
    for s in skills: counts[s['name']]=counts.get(s['name'],0)+1
    return {'skills':skills,'duplicate_names':[k for k,v in counts.items() if v>1],'plugins':plugins,'plugin_installation':'not performed','accounts_linked':False}
