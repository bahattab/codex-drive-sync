"""Explicit staging of selected skills, safe settings and definition inventories."""
import json
from pathlib import Path
import re
import shutil
import tomllib
from .discovery import portable_inventory

SHARED_KEYS=('model','model_reasoning_effort','model_verbosity')
DENIED={'.git','node_modules','__pycache__','.venv','auth.json','credentials.json','token.json','tokens.json','secrets.json','secrets','client_secret.json','recovery.json','recovery-key.json'}


def export_workspace(codex_home,output,skills):
    root=Path(codex_home).resolve(); target=Path(output).resolve()
    if target.is_relative_to(root) or root.is_relative_to(target): raise ValueError('Staging must be separate from Codex home')
    if target.exists(): raise ValueError('Use new staging directory')
    # Validate selected trees in full before creating a destination.
    selected=[]
    for name in skills:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}',name): raise ValueError('Unsafe skill name')
        p=root/'skills'/name
        if not (p/'SKILL.md').is_file(): raise ValueError('Skill missing')
        for item in [p,*p.rglob('*')]:
            if item.is_symlink() or (hasattr(item,'is_junction') and item.is_junction()): raise ValueError('Links cannot be exported')
            if item.is_file() and item.stat().st_size>32*1024*1024: raise ValueError('Skill file exceeds limit')
        selected.append(p)
    config={}
    p=root/'config.toml'
    if p.exists():
        data=tomllib.loads(p.read_text('utf-8'))
        config={k:data[k] for k in SHARED_KEYS if k in data and isinstance(data[k],str) and len(data[k])<100 and re.fullmatch(r'[A-Za-z0-9._-]+',data[k])}
    target.mkdir(parents=True)
    excluded=[]
    try:
        for p in selected:
            for item in p.rglob('*'):
                rel=item.relative_to(p)
                if any(part.lower() in DENIED or part.lower().startswith(('.env','client_secret','credentials')) or part.lower().endswith(('.key','.pem','.p12','.pfx')) for part in rel.parts):
                    excluded.append(rel.as_posix()); continue
                dest=target/'skills'/p.name/rel
                if item.is_dir(): dest.mkdir(parents=True,exist_ok=True)
                elif item.is_file(): dest.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(item,dest)
        (target/'shared-settings.json').write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
        inventory=portable_inventory(root)
        (target/'inventory.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')
        (target/'ACTIVATION.md').write_text('# تفعيل يدوي\n\nراجع المهارات المنقولة قبل تثبيتها؛ لا تنفذ سكربتاتها تلقائيًا. shared-settings.json يسمح فقط بإعدادات نموذج غير سرية؛ ادمج القيم يدويًا مع إبقاء تجاوزات الجهاز. inventory.json تعريفات فقط؛ استخدم آلية plugin add الموثقة محليًا، ثم تحقق من التثبيت والاعتماديات وربط الحساب والعمل كلًا على حدة. لا تُنقل الثقة أو الأذونات أو بيانات الدخول.\n',encoding='utf-8')
    except Exception:
        # Preserve partially staged output for review; never activate it.
        (target/'INCOMPLETE').write_text('Do not activate this incomplete export',encoding='utf-8')
        raise
    return {'staged_skills':[p.name for p in selected],'settings':list(config),'excluded':excluded,'activation':'manual review required','plugin_packages_installed':False}
