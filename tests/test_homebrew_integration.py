import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from station_root import homebrew


class HomebrewBundleTests(unittest.TestCase):
    def test_resources_reject_non_arm64_binary(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "alice-homebrew"
            (root / "scripts").mkdir(parents=True)
            (root / "dist").mkdir()
            for name in ("install-speaker.py", "start-speaker.sh", "S98alice-homebrew"):
                (root / "scripts" / name).write_text("")
            (root / "dist/alice-homebrew-linux-arm64").write_bytes(b"not an ARM64 ELF")
            with self.assertRaisesRegex(RuntimeError, "Linux ARM64"):
                homebrew.resources(folder)

    def test_deployment_only_installs_homebrew_and_its_own_hook(self):
        module = types.SimpleNamespace(main=Mock(return_value="http://192.0.2.10:8088"))
        loader = Mock()
        spec = types.SimpleNamespace(loader=loader)
        with patch.object(homebrew, "resources", return_value=(Path("installer.py"), Path("arm64"))), \
             patch.object(homebrew.importlib.util, "spec_from_file_location", return_value=spec), \
             patch.object(homebrew.importlib.util, "module_from_spec", return_value=module):
            url = homebrew.install("bundle", {"adb": "adb", "work": "workspace", "serial": "speaker"})
        self.assertEqual(url, "http://192.0.2.10:8088")
        args = module.main.call_args.args[0]
        self.assertIn("--install", args)
        self.assertIn("--autostart", args)
        self.assertNotIn("--disable-alice", args)
        self.assertNotIn("--remove-apps", args)
        self.assertEqual(args[args.index("--serial") + 1], "speaker")
