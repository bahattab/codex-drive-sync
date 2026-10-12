"""Local CLI. No implicit Codex state imports or account authorization."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from . import __version__


def home():
    return Path(os.environ.get('CODEX_DRIVE_SYNC_HOME', str(Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share')) / 'codex-drive-sync')))


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def secret_store():
    from .security import SecretStore
    return SecretStore(service=os.environ.get('CODEX_DRIVE_SYNC_SECRET_SERVICE','codex-drive-sync'))


def settings(root):
    p = root / 'settings.json'
    return json.loads(p.read_text('utf-8')) if p.exists() else {}


def save_settings(root, value):
    root.mkdir(parents=True, exist_ok=True)
    p = root / 'settings.json'
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(value), encoding='utf-8')
    os.replace(tmp, p)


def require_private_path(root,path):
    target=Path(path).resolve()
    cfg=settings(root)
    candidates=[]
    state=root/'engine'/'state.json'
    if state.exists(): candidates.extend(Path(x).resolve() for x in json.loads(state.read_text('utf-8')).get('projects',{}).values())
    if cfg.get('transport'): candidates.append(Path(cfg['transport']).resolve())
    if any(target==base or target.is_relative_to(base) for base in candidates):
        raise ValueError('ملف الاعتماد أو الاسترداد يجب أن يبقى خارج المشاريع وموفر النقل.')


def remember_private_path(root,path):
    cfg=settings(root)
    cfg['private_paths']=sorted(set(cfg.get('private_paths',[])+[str(Path(path).resolve())]))
    save_settings(root,cfg)


def require_public_root(root,path):
    candidate=Path(path).resolve()
    for private in settings(root).get('private_paths',[]):
        secret=Path(private).resolve()
        if secret==candidate or secret.is_relative_to(candidate):
            raise ValueError('المجلد يحتوي ملف اعتماد أو استرداد معروف؛ لا يمكن تمكين نقله.')


def build_engine(root):
    from .engine import Engine
    from .security import SecretStore, Cipher
    from .providers import LocalProvider, GoogleDriveProvider
    cfg = settings(root)
    state=root/'engine'/'state.json'
    if state.exists():
        for project in json.loads(state.read_text('utf-8')).get('projects',{}).values(): require_public_root(root,project)
    if cfg.get('transport'): require_public_root(root,cfg['transport'])
    store = secret_store()
    if cfg.get('provider') == 'local':
        provider = LocalProvider(Path(cfg['transport']))
    elif cfg.get('provider') == 'google':
        provider = GoogleDriveProvider(store)
    else:
        raise ValueError('اختر الموفر باستخدام configure؛ ثم راجع sync --dry-run.')
    return Engine(root / 'engine', provider, Cipher.from_store(store))


def inventory_digest(engine):
    inventory = engine.inventory()
    encoded = json.dumps(inventory, sort_keys=True, ensure_ascii=False, default=str).encode()
    return inventory, hashlib.sha256(encoded).hexdigest()


def require_review(root, engine, digest=None):
    inventory, current = inventory_digest(engine)
    cfg = settings(root)
    if digest:
        if digest != current:
            raise ValueError('تغير الجرد؛ أعد sync --dry-run وراجع البصمة الجديدة.')
        cfg['approved_inventory'] = current
        cfg['approved_projects'] = engine.status()['projects']
        save_settings(root, cfg)
    if not cfg.get('approved_inventory') or cfg.get('approved_projects') != engine.status()['projects']:
        raise ValueError('راجع sync --dry-run ثم مرر --approve-inventory مع البصمة قبل أول رفع.')


def parser():
    p = argparse.ArgumentParser(description='مزامنة ملفات مشفرة؛ استيراد محادثات Codex الأصلي معطل.')
    p.add_argument('--home', type=Path, default=home())
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor')
    c = sub.add_parser('configure'); c.add_argument('--provider', choices=['local', 'google'], required=True); c.add_argument('--transport', type=Path)
    c = sub.add_parser('connect'); c.add_argument('--client-file', type=Path, required=True)
    c = sub.add_parser('disconnect'); c.add_argument('--revoke', action='store_true')
    c = sub.add_parser('projects'); c.add_argument('action', choices=['list','discover','add','map']); c.add_argument('--path', type=Path); c.add_argument('--id')
    c = sub.add_parser('sync'); c.add_argument('--dry-run', action='store_true'); c.add_argument('--approve-inventory')
    sub.add_parser('status')
    c = sub.add_parser('conflicts'); c.add_argument('action', choices=['list','resolve']); c.add_argument('--id'); c.add_argument('--choice',help='local, delete, variant SHA256 or zero-based index')
    c = sub.add_parser('snapshots'); c.add_argument('action', choices=['list'])
    c = sub.add_parser('restore'); c.add_argument('snapshot'); c.add_argument('--dry-run', action='store_true')
    c = sub.add_parser('handoff'); c.add_argument('action', choices=['prepare','receive']); c.add_argument('--snapshot'); c.add_argument('--dry-run', action='store_true'); c.add_argument('--approve-inventory')
    c = sub.add_parser('pair'); c.add_argument('action', choices=['export','import']); c.add_argument('--file', type=Path, required=True)
    c = sub.add_parser('service'); c.add_argument('action', choices=['start','stop','health','run']); c.add_argument('--interval', type=int, default=60)
    sub.add_parser('logs')
    sub.add_parser('uninstall')
    c = sub.add_parser('archive'); c.add_argument('--source', type=Path, required=True); c.add_argument('--output', type=Path, required=True)
    c = sub.add_parser('archive-search'); c.add_argument('--archive', type=Path, required=True); c.add_argument('--query', required=True)
    c = sub.add_parser('workspace-export'); c.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))); c.add_argument('--output',type=Path,required=True); c.add_argument('--skill',action='append',default=[])
    c = sub.add_parser('exclusions'); c.add_argument('--pattern',action='append',default=[])
    sub.add_parser('mcp')
    return p


def dispatch(a):
    root = a.home.resolve()
    if a.command == 'doctor':
        from .discovery import doctor
        return doctor()
    if a.command == 'configure':
        cfg = settings(root)
        if a.provider == 'local' and not a.transport:
            raise ValueError('--transport مطلوب للموفر المحلي.')
        if a.transport: require_public_root(root,a.transport)
        cfg.update(provider=a.provider)
        if a.transport: cfg['transport'] = str(a.transport.resolve())
        cfg.pop('approved_inventory', None)
        save_settings(root, cfg)
        return {'configured':a.provider,'required_action':'projects add ثم sync --dry-run'}
    if a.command in ('connect','disconnect','pair'):
        from .security import SecretStore, Cipher, export_recovery, import_recovery
        from .providers import connect, disconnect
        store = secret_store()
        if a.command == 'connect':
            require_private_path(root,a.client_file)
            remember_private_path(root,a.client_file)
            result = connect(a.client_file, store)
            cfg = settings(root); cfg['provider'] = 'google'; save_settings(root,cfg)
            return {'connected':True,'scope':'drive.appdata','real_transfer_verified':False}
        if a.command == 'disconnect': return disconnect(store, revoke=a.revoke)
        # Recovery files are explicit, local, private transfers; never upload them.
        if a.action == 'export':
            require_private_path(root,a.file)
            remember_private_path(root,a.file)
            Cipher.from_store(store)
            a.file.parent.mkdir(parents=True,exist_ok=True)
            export_recovery(store,a.file)
            return {'exported':True,'warning':'ملف استرداد حساس: انقله بوسيلة خاصة خارج Drive ثم احذفه بعد الاقتران.'}
        require_private_path(root,a.file)
        remember_private_path(root,a.file)
        import_recovery(store,a.file)
        return {'paired':True}
    if a.command == 'archive':
        from .archives import export_archive
        return export_archive(a.source,a.output)
    if a.command == 'archive-search':
        from .archives import search_archive
        return search_archive(a.archive,a.query)
    if a.command == 'workspace-export':
        from .portable import export_workspace
        return export_workspace(a.codex_home,a.output,a.skill)
    if a.command == 'service':
        from .service import control
        return control(root,a.action,a.interval)
    if a.command == 'mcp':
        from .mcp import serve
        serve(root); return None
    if a.command == 'logs':
        p = root/'service-events.jsonl'
        return {'events':[json.loads(x) for x in p.read_text('utf-8').splitlines()[-100:]] if p.exists() else []}
    if a.command == 'uninstall':
        from .service import control
        control(root,'stop',60)
        return {'service_stopped':True,'data_preserved':True,'next':'شغّل scripts/uninstall.ps1 لإزالة بيئة التطبيق دون حذف المشاريع أو اللقطات أو المفاتيح.'}
    if a.command=='projects' and a.action=='discover':
        from .discovery import registered_projects
        return registered_projects()
    engine = build_engine(root)
    if a.command == 'exclusions': return engine.set_exclusions(a.pattern)
    if a.command == 'projects':
        if a.action == 'list': return engine.inventory()
        if not a.path: raise ValueError('--path مطلوب.')
        require_public_root(root,a.path)
        if a.action == 'add': return engine.add_project(a.path,a.id)
        if not a.id: raise ValueError('--id مطلوب.')
        return engine.map_project(a.id,a.path)
    if a.command == 'sync':
        if a.dry_run:
            inventory,digest = inventory_digest(engine)
            return {'inventory':inventory,'approval_digest':digest,'exclusions':engine.status().get('exclusions',[]),'plan':engine.sync(dry_run=True)}
        require_review(root,engine,a.approve_inventory)
        return engine.sync()
    if a.command == 'status':
        result = engine.status()
        from .security import SecretStore
        connected=bool(secret_store().get('oauth'))
        return {'sync':result,'provider':settings(root).get('provider'),'google_authorization':'connected locally; live transfer not verified' if connected else 'disconnected','compatibility':'files only; sensitive Codex state import disabled','conversation_mode':'archived/context-exported only','native_import':'blocked','two_device_acceptance':'NOT RUN','required_action':'review first inventory, connect Google, map second device; native import remains blocked'}
    if a.command == 'conflicts':
        if a.action == 'list': return engine.conflicts()
        if not a.id or not a.choice: raise ValueError('--id و--choice مطلوبان.')
        choice=int(a.choice) if a.choice.isdecimal() else a.choice
        return engine.resolve(a.id,choice)
    if a.command == 'snapshots': return engine.snapshots()
    if a.command == 'restore': return engine.restore(a.snapshot,a.dry_run)
    if a.command == 'handoff':
        if a.action == 'prepare':
            if a.dry_run: return engine.sync(dry_run=True)
            require_review(root,engine,a.approve_inventory)
            return engine.prepare()
        if not a.snapshot: raise ValueError('--snapshot مطلوب.')
        return engine.receive(a.snapshot,a.dry_run)


def main(argv=None):
    try:
        result = dispatch(parser().parse_args(argv))
        if result is not None: emit(result)
        return 0
    except Exception as exc:
        # Exception strings from network, filesystem and parsers may contain secrets.
        emit({'error':type(exc).__name__,'message':'تعذر تنفيذ العملية. تحقق من الإعدادات والصلاحيات والتوافق؛ لم تُطبع تفاصيل قد تحتوي بيانات خاصة.'})
        return 1


if __name__ == '__main__':
    sys.exit(main())
