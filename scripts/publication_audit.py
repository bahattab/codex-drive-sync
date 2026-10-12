"""Allowlisted publication inventory and supplementary secret/privacy checks."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

ALLOWED_ROOTS={'src','tests','docs','scripts','plugin','.agents','.github'}
ALLOWED_FILES={'pyproject.toml','requirements.lock','README.md','TASK_PLAN.md','PROGRESS.md','DECISIONS.md','COMPATIBILITY.md','TEST_RESULTS.md','GATES.md','findings.md','.gitignore','REVIEW.md'}
PATTERNS=[r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',r'\bgh[pousr]_[A-Za-z0-9]{30,}',r'\bgithub_pat_[A-Za-z0-9_]{40,}',r'\bsk-(?:proj-)?[A-Za-z0-9_-]{30,}',r'\bAIza[A-Za-z0-9_-]{30,}',r'\b1//[A-Za-z0-9_-]{30,}',r'GOCSPX-[A-Za-z0-9_-]{20,}',r'C:[/\\]Users[/\\](?!Public\b|example\b)[^/\\\s]+',r'[0-9]{10,}-[a-z0-9]{20,}\.apps\.googleusercontent\.com']


def inventory(root):
    result=[]
    for p in root.rglob('*'):
        rel=p.relative_to(root)
        if any(x in {'.venv','.git','__pycache__','build','dist'} or x.endswith('.egg-info') for x in rel.parts): continue
        if not p.is_file(): continue
        if len(rel.parts)==1 and p.name not in ALLOWED_FILES: raise ValueError('Unexpected root file: '+p.name)
        if len(rel.parts)>1 and rel.parts[0] not in ALLOWED_ROOTS: raise ValueError('Unexpected publication directory')
        if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()): raise ValueError('Publication links forbidden')
        data=p.read_bytes(); content=data.decode('utf-8-sig')
        for pattern in PATTERNS:
            if re.search(pattern,content): raise ValueError('Potential private material in '+rel.as_posix())
        result.append({'path':rel.as_posix(),'size':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    return sorted(result,key=lambda x:x['path'])


def main():
    p=argparse.ArgumentParser(); p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]); p.add_argument('--zip',type=Path); p.add_argument('--json',action='store_true'); a=p.parse_args()
    root=a.root.resolve(); files=inventory(root)
    if a.zip:
        with zipfile.ZipFile(a.zip,'w',zipfile.ZIP_DEFLATED) as z:
            for f in files: z.write(root/f['path'],'codex-drive-sync/'+f['path'])
    print(json.dumps({'scan':'PASS','files':files if a.json else len(files),'scope':'allowlisted source only; no user state; detection is supplementary'},ensure_ascii=False,indent=2))


if __name__=='__main__': main()
