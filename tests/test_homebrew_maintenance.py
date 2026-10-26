import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from station_root.workflow import Workflow

class HomebrewMaintenanceTests(unittest.TestCase):
    def test_update_only_never_captures_builds_or_flashes(self):
        with tempfile.TemporaryDirectory() as folder:
            key=Path(folder)/'keys/adbkey';key.parent.mkdir();key.write_text('test-only')
            flow=Workflow(log=lambda _:None)
            flow.verify=Mock(return_value='speaker');flow.finish_homebrew=Mock()
            flow.connect=Mock();flow.backup=Mock();flow.prepare=Mock();flow.flash=Mock();flow.ensure_environment=Mock()
            with patch('station_root.dependencies.find_adb',return_value='adb'),patch('station_root.workflow.homebrew.resources'):
                flow.update_homebrew({'work':folder})
            flow.finish_homebrew.assert_called_once_with({'work':folder,'adb':'adb','serial':'speaker'})
            for operation in [flow.connect,flow.backup,flow.prepare,flow.flash,flow.ensure_environment]:operation.assert_not_called()
    def test_missing_original_key_never_starts_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            flow=Workflow(log=lambda _:None);flow.verify=Mock();flow.finish_homebrew=Mock()
            with patch('station_root.dependencies.find_adb',return_value='adb'):
                with self.assertRaisesRegex(RuntimeError,'private key is missing'):flow.update_homebrew({'work':folder})
            flow.verify.assert_not_called();flow.finish_homebrew.assert_not_called()
