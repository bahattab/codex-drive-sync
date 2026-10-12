import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from codex_drive_sync.archives import export_archive,search_archive
from codex_drive_sync.portable import export_workspace
from codex_drive_sync.cli import main,dispatch,parser,require_private_path,save_settings,require_review,remember_private_path,require_public_root
from codex_drive_sync.mcp import serve


class InterfaceTests(unittest.TestCase):
    def test_read_only_engine_reports_pending_journal_without_recovery(self):
        from codex_drive_sync.engine import Engine
        from codex_drive_sync.providers import LocalProvider
        from codex_drive_sync.security import Cipher
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); project=p/'project'; project.mkdir(); (project/'work.txt').write_text('independent work')
            provider=LocalProvider(p/'transport'); state=p/'state'
            first=Engine(state,provider,Cipher(b'x'*32)); first.add_project(project,'demo')
            (state/'journal.json').write_text('{"unsafe":"would fail recovery"}')
            read=Engine(state,provider,None,recover_on_init=False)
            self.assertTrue(read.status()['recovery_pending'])
            self.assertEqual((project/'work.txt').read_text(),'independent work')
            self.assertTrue((state/'journal.json').exists())

    def test_pair_import_inside_mapped_project_rejected_before_import(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); home=p/'home'; (home/'engine').mkdir(parents=True)
            (home/'engine/state.json').write_text(json.dumps({'projects':{'demo':str(p/'project')}}))
            args=parser().parse_args(['--home',str(home),'pair','import','--file',str(p/'project/arbitrary.json')])
            with patch('codex_drive_sync.cli.secret_store'),patch('codex_drive_sync.security.import_recovery') as importer:
                with self.assertRaises(ValueError): dispatch(args)
                importer.assert_not_called()

    def test_known_private_file_blocks_future_project_or_transport(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); home=p/'home'
            remember_private_path(home,p/'formerly-private/arbitrary.json')
            with self.assertRaises(ValueError): require_public_root(home,p/'formerly-private')
            require_public_root(home,p/'public')

    def test_recovery_and_client_path_cannot_be_in_selected_project(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); home=p/'home'; (home/'engine').mkdir(parents=True)
            (home/'engine/state.json').write_text(json.dumps({'projects':{'demo':str(p/'project')}}))
            with self.assertRaises(ValueError): require_private_path(home,p/'project/arbitrary.json')
            save_settings(home,{'transport':str(p/'transport')})
            with self.assertRaises(ValueError): require_private_path(home,p/'transport/anything.json')
            require_private_path(home,p/'private/recovery.json')

    def test_first_inventory_review_invalidated_by_new_mapping(self):
        class E:
            def inventory(self): return {'projects':{'a':{'test.txt':'hash'}}}
            def status(self): return {'projects':{'a':'local-path'}}
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); save_settings(p,{'approved_inventory':'old','approved_projects':{'different':'old'}})
            with self.assertRaises(ValueError): require_review(p,E())

    def test_archive_preserves_and_checks_integrity(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); raw=b'{"role":"user","content":"hello"}\n{"role":"assistant","content":"world"}\n'
            (p/'a.jsonl').write_bytes(raw)
            export_archive(p/'a.jsonl',p/'out')
            self.assertEqual((p/'out/conversation.jsonl').read_bytes(),raw)
            self.assertIn('world',(p/'out/context.md').read_text('utf-8'))
            self.assertEqual(search_archive(p/'out','world')['matching_records'],[2])
            (p/'out/conversation.jsonl').write_bytes(b'corrupted')
            with self.assertRaises(ValueError): search_archive(p/'out','world')

    def test_settings_secrets_trust_paths_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); root=p/'codex'; root.mkdir()
            (root/'config.toml').write_text('model="test-model"\napi_key="PRIVATE"\napproval_policy="never"\npath="C:/private"\n',encoding='utf-8')
            skill=root/'skills/demo'; skill.mkdir(parents=True); (skill/'SKILL.md').write_text('demo'); (skill/'.env').write_text('PRIVATE')
            export_workspace(root,p/'out',['demo'])
            self.assertEqual(json.loads((p/'out/shared-settings.json').read_text()),{'model':'test-model'})
            self.assertFalse((p/'out/skills/demo/.env').exists())
            self.assertTrue((p/'out/skills/demo/SKILL.md').exists())

    def test_cli_error_redacts_raw_exception(self):
        with patch('codex_drive_sync.cli.dispatch',side_effect=RuntimeError('PRIVATE_ACCESS_TOKEN')),patch('sys.stdout',new_callable=io.StringIO) as out:
            self.assertEqual(main(['status']),1)
            self.assertNotIn('PRIVATE_ACCESS_TOKEN',out.getvalue())

    def test_mcp_initialization_and_tool_schema(self):
        requests=[{'jsonrpc':'2.0','id':1,'method':'initialize'},{'jsonrpc':'2.0','id':2,'method':'tools/list'}]
        with patch('sys.stdin',io.StringIO('\n'.join(map(json.dumps,requests))+'\n')),patch('sys.stdout',new_callable=io.StringIO) as out:
            serve(Path('.'))
            replies=[json.loads(x) for x in out.getvalue().splitlines()]
            self.assertIn('protocolVersion',replies[0]['result'])
            self.assertEqual(len(replies[1]['result']['tools']),3)

if __name__=='__main__': unittest.main()
