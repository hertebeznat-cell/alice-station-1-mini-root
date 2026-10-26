import json
import tempfile
import unittest
from pathlib import Path

from station_root.image_builder import disable_ota, KNOWN_NOUPDATE_SCRIPT


class ExistingNoupdateTests(unittest.TestCase):
    def fixture(self, folder):
        root = Path(folder)
        vendor = root / 'system/vendor/quasar'
        vendor.mkdir(parents=True)
        tasks = [
            {'name': 'maind', 'path': '/system/vendor/quasar/maind', 'arg': ['--yandexmini']},
            {'name': 'audiod', 'path': '/system/vendor/quasar/maind', 'arg': ['--audiod']},
        ]
        launch = {'nodes': [{'name': 'main', 'root': '.', 'tasks': tasks}]}
        (vendor / 'launch.json').write_text(json.dumps(launch), encoding='utf-8')
        (vendor / 'device_ota_update.sh').write_bytes(KNOWN_NOUPDATE_SCRIPT)
        (vendor / 'a01localupdate').write_bytes(b'original-updater')
        return root, vendor, launch

    def test_known_noupdate_keeps_services_and_blocks_remaining_entrypoints(self):
        with tempfile.TemporaryDirectory() as folder:
            root, vendor, launch = self.fixture(folder)
            result = disable_ota(root)
            self.assertEqual(json.loads((vendor / 'launch.json').read_text()), launch)
            self.assertEqual(result['source_policy'], 'noupdate-v1')
            self.assertEqual(result['removed_tasks'], [])
            self.assertEqual(len(result['blocked_entrypoints']), 2)
            for name in ['device_ota_update.sh', 'a01localupdate']:
                self.assertIn('exit 78', (vendor / name).read_text())

    def test_comment_is_not_enough_when_script_contains_extra_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            root, vendor, _ = self.fixture(folder)
            script = vendor / 'device_ota_update.sh'
            content = KNOWN_NOUPDATE_SCRIPT + b'updater --apply\n'
            script.write_bytes(content)
            original = (vendor / 'launch.json').read_bytes()
            with self.assertRaises(RuntimeError):
                disable_ota(root)
            self.assertEqual(script.read_bytes(), content)
            self.assertEqual((vendor / 'launch.json').read_bytes(), original)

    def test_other_updater_task_is_not_accepted_as_preblocked(self):
        with tempfile.TemporaryDirectory() as folder:
            root, vendor, launch = self.fixture(folder)
            launch['nodes'][0]['tasks'].append({'name': 'helper', 'path': '/system/vendor/quasar/updater', 'arg': []})
            (vendor / 'launch.json').write_text(json.dumps(launch), encoding='utf-8')
            with self.assertRaises(RuntimeError):
                disable_ota(root)
            self.assertEqual((vendor / 'a01localupdate').read_bytes(), b'original-updater')

    def test_empty_unknown_launcher_still_stops_even_with_matching_stub(self):
        with tempfile.TemporaryDirectory() as folder:
            root, vendor, _ = self.fixture(folder)
            (vendor / 'launch.json').write_text('{"tasks": []}', encoding='utf-8')
            with self.assertRaises(RuntimeError):
                disable_ota(root)


if __name__ == '__main__':
    unittest.main()
