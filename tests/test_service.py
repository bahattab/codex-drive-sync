import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_drive_sync.service import control, _alive


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def put(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding='utf-8')

    def test_live_process_probe(self):
        self.assertTrue(_alive(os.getpid()))
        self.assertFalse(_alive(None))
        self.assertFalse(_alive(-1))

    def test_health_requires_matching_live_owner(self):
        self.put('service-health.json', {'pid': 123, 'token': 'new'})
        self.assertFalse(control(self.root, 'health')['running'])
        self.put('service-lease.json', {'pid': 123, 'token': 'old'})
        with patch('codex_drive_sync.service._alive', return_value=True):
            self.assertFalse(control(self.root, 'health')['running'])
        self.put('service-lease.json', {'pid': 123, 'token': 'new'})
        with patch('codex_drive_sync.service._alive', return_value=True):
            self.assertTrue(control(self.root, 'health')['running'])
        with patch('codex_drive_sync.service._alive', return_value=False):
            self.assertFalse(control(self.root, 'health')['running'])

    def test_stop_cleans_dead_lease_and_preserves_data(self):
        self.put('service-lease.json', {'pid': 123, 'token': 'x'})
        (self.root / 'project.txt').write_text('keep')
        with patch('codex_drive_sync.service._alive', return_value=False):
            result = control(self.root, 'stop')
        self.assertTrue(result['stopped'])
        self.assertFalse((self.root / 'service-lease.json').exists())
        self.assertEqual((self.root / 'project.txt').read_text(), 'keep')

    def test_stop_timeout_fails_closed(self):
        self.put('service-lease.json', {'pid': 123, 'token': 'x'})
        with patch('codex_drive_sync.service._alive', return_value=True), patch('codex_drive_sync.service.time.monotonic', side_effect=[0, 46]):
            with self.assertRaisesRegex(RuntimeError, 'preserved'):
                control(self.root, 'stop')
        self.assertTrue((self.root / 'service-lease.json').exists())

    def test_start_refuses_stale_lease_without_spawning(self):
        self.put('service-lease.json', {'pid': 123, 'token': 'x'})
        with patch('codex_drive_sync.service.subprocess.Popen') as spawn:
            with self.assertRaises(ValueError):
                control(self.root, 'start')
            spawn.assert_not_called()

    def test_run_preserves_stop_during_initialization(self):
        root = self.root
        class Engine:
            def sync(self):
                raise AssertionError('Stop request must prevent synchronization')
        def build(_):
            self.assertTrue((root / 'service-lease.json').exists())
            (root / 'service-stop').write_text('stop')
            return Engine()
        with patch('codex_drive_sync.cli.build_engine', side_effect=build), patch('codex_drive_sync.cli.require_review'):
            self.assertTrue(control(root, 'run', 10)['stopped'])
        self.assertFalse((root / 'service-lease.json').exists())
        self.assertFalse(control(root, 'health')['running'])

    def test_initialization_failure_releases_lease(self):
        with patch('codex_drive_sync.cli.build_engine', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                control(self.root, 'run', 10)
        self.assertFalse((self.root / 'service-lease.json').exists())

    def test_worker_logs_no_sensitive_error_details(self):
        root = self.root
        class Engine:
            def sync(self):
                (root / 'service-stop').write_text('stop')
                raise RuntimeError('TEST_PRIVATE_TOKEN')
        with patch('codex_drive_sync.cli.build_engine', return_value=Engine()), patch('codex_drive_sync.cli.require_review'):
            control(root, 'run', 10)
        logs = (root / 'service-events.jsonl').read_text()
        self.assertNotIn('TEST_PRIVATE_TOKEN', logs)
        self.assertIn('RuntimeError', logs)
