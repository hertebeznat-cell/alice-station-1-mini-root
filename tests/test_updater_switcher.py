import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from station_root.image_builder import disable_ota, KNOWN_UPDATER_SWITCHER_SHA256


class UpdaterSwitcherTests(unittest.TestCase):
    def fixture(self, folder):
        root = Path(folder)
        vendor = root / 'system/vendor/quasar'
        vendor.mkdir(parents=True)
        tasks = [
            {'name': 'mediad', 'path': '/system/vendor/quasar/maind', 'arg': ['--mediad'], 'envs': ['MALLOC_ARENA_MAX=2'], 'order': 3},
            {'name': 'maind', 'path': '/system/vendor/quasar/maind', 'arg': ['--platformd'], 'order': 1},
            {'name': 'updater_switcher', 'path': '/system/vendor/quasar/updater_switcher',
             'arg': ['/data/quasar/use_updater', '/system/vendor/quasar/maind', '/system/vendor/quasar/updater'], 'order': 3},
            {'name': 'telemetry', 'path': '/system/vendor/quasar/maind', 'arg': ['--telemetry'], 'order': 0},
        ]
        data = {'nodes': [{'name': 'main', 'tasks': tasks}], 'unrelated': {'keep': True}}
        (vendor / 'launch.json').write_text(json.dumps(data), encoding='utf-8')
        (vendor / 'device_ota_update.sh').write_text('#!/bin/sh\napply_packed_ota "$1"\n', encoding='utf-8')
        (vendor / 'a01localupdate').write_bytes(b'original-updater')
        (vendor / 'updater_switcher').write_bytes(b'unit-test-binary-not-the-firmware')
        return root, vendor, data

    def assert_unchanged_on_failure(self, root, vendor):
        before = {p.name: p.read_bytes() for p in vendor.iterdir() if not p.is_symlink()}
        with self.assertRaises(RuntimeError):
            disable_ota(root)
        after = {p.name: p.read_bytes() for p in vendor.iterdir() if not p.is_symlink()}
        self.assertEqual(before, after)

    def test_verified_switcher_removed_without_changing_voice_services(self):
        with tempfile.TemporaryDirectory() as folder:
            root, vendor, data = self.fixture(folder)
            expected = copy.deepcopy(data)
            expected['nodes'][0]['tasks'].pop(2)
            binary = (vendor / 'updater_switcher').read_bytes()
            # The proprietary firmware binary is not distributed as a fixture.
            # Its real fingerprint is exercised by the full private-image check.
            with patch('station_root.image_builder.sha256', return_value=KNOWN_UPDATER_SWITCHER_SHA256):
                result = disable_ota(root)
            self.assertEqual(json.loads((vendor / 'launch.json').read_text()), expected)
            self.assertEqual(result['removed_tasks'], ['updater_switcher'])
            self.assertEqual(result['source_policy'], 'stock-updater-switcher-v1')
            self.assertEqual((vendor / 'updater_switcher').read_bytes(), binary)
            self.assertEqual(len(result['blocked_entrypoints']), 2)
            self.assertIn('exit 78', (vendor / 'device_ota_update.sh').read_text())
            self.assertIn('exit 78', (vendor / 'a01localupdate').read_text())

    def test_unknown_binary_is_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root, vendor, _ = self.fixture(folder)
            self.assert_unchanged_on_failure(root, vendor)

    def test_changed_arguments_or_missing_platformd_are_rejected(self):
        for change in ['flag', 'legacy', 'updater', 'platformd']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                root, vendor, data = self.fixture(folder)
                tasks = data['nodes'][0]['tasks']
                if change == 'platformd':
                    tasks[1]['arg'] = ['--unknown-main']
                else:
                    tasks[2]['arg'][{'flag': 0, 'legacy': 1, 'updater': 2}[change]] = '/unknown'
                (vendor / 'launch.json').write_text(json.dumps(data), encoding='utf-8')
                with patch('station_root.image_builder.sha256', return_value=KNOWN_UPDATER_SWITCHER_SHA256):
                    self.assert_unchanged_on_failure(root, vendor)

    def test_additional_unrecognized_updater_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root, vendor, data = self.fixture(folder)
            data['nodes'][0]['tasks'].append({'name': 'other', 'path': '/system/vendor/quasar/updater', 'arg': []})
            (vendor / 'launch.json').write_text(json.dumps(data), encoding='utf-8')
            with patch('station_root.image_builder.sha256', return_value=KNOWN_UPDATER_SWITCHER_SHA256):
                self.assert_unchanged_on_failure(root, vendor)

    def test_missing_or_symlinked_switcher_is_rejected(self):
        for symlink in [False, True]:
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as folder:
                root, vendor, _ = self.fixture(folder)
                binary = vendor / 'updater_switcher'
                binary.unlink()
                if symlink:
                    target = vendor / 'different-binary'
                    target.write_bytes(b'different')
                    try:
                        binary.symlink_to(target)
                    except OSError:
                        continue  # Linux run covers links on Windows without link privileges.
                with patch('station_root.image_builder.sha256', return_value=KNOWN_UPDATER_SWITCHER_SHA256):
                    self.assert_unchanged_on_failure(root, vendor)


if __name__ == '__main__':
    unittest.main()
