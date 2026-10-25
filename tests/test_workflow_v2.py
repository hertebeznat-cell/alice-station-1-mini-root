import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, call

from station_root.workflow import Workflow
from station_root.image_builder import disable_ota, patch_rootfs
from station_root.environment import decode_output, select_distro, prepare_environment, RestartRequired
from station_root.i18n import tr, translate_message, PAIRS


class UpdatesTests(unittest.TestCase):
    def fixture(self, folder):
        root = Path(folder)
        vendor = root / "system/vendor/quasar"
        vendor.mkdir(parents=True)
        launch = {"nodes": [{"name": "main", "tasks": [
            {"name": "updatesd", "arg": ["--updatesd"]},
            {"name": "maind", "arg": ["--platformd"]},
            {"name": "mediad", "arg": ["--mediad"]}]}]}
        (vendor / "launch.json").write_text(json.dumps(launch))
        (vendor / "device_ota_update.sh").write_text("#!/bin/sh\napply_packed_ota file\nreboot\n")
        (vendor / "a01localupdate").write_bytes(b"ELF-original-updater")
        return root, vendor

    def test_ota_block_preserves_all_non_update_tasks(self):
        with tempfile.TemporaryDirectory() as d:
            root, vendor = self.fixture(d)
            result = disable_ota(root)
            tasks = json.loads((vendor / "launch.json").read_text())["nodes"][0]["tasks"]
            self.assertEqual([task["name"] for task in tasks], ["maind", "mediad"])
            self.assertEqual(result["removed_tasks"], ["updatesd"])
            for name in ("device_ota_update.sh", "a01localupdate"):
                self.assertIn("exit 78", (vendor / name).read_text())
                self.assertNotIn("reboot", (vendor / name).read_text())

    def test_unknown_launcher_does_not_claim_ota_block(self):
        with tempfile.TemporaryDirectory() as d:
            root, vendor = self.fixture(d)
            (vendor / "launch.json").write_text('{"tasks": []}')
            with self.assertRaises(RuntimeError):
                disable_ota(root)
            self.assertIn("apply_packed_ota", (vendor / "device_ota_update.sh").read_text())

    @unittest.skipIf(os.name == "nt", "Linux rootfs symlinks")
    def test_symlink_updater_does_not_overwrite_shared_binary(self):
        with tempfile.TemporaryDirectory() as d:
            root, vendor = self.fixture(d)
            updater = vendor / "a01localupdate"
            updater.unlink()
            shared = vendor / "maind"
            shared.write_bytes(b"KEEP-MULTICALL-BINARY")
            updater.symlink_to(shared.name)
            disable_ota(root)
            self.assertEqual(shared.read_bytes(), b"KEEP-MULTICALL-BINARY")
            self.assertFalse(updater.is_symlink())


class PipelineTests(unittest.TestCase):
    def test_prepare_accepts_a_non_allowlisted_source_hash(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            source = work / "other-firmware.bin"
            with source.open("wb") as stream:
                stream.truncate(80 * 1024 * 1024)
            keys = work / "keys"
            keys.mkdir()
            (keys / "adbkey").write_text("test-only")
            (keys / "adbkey.pub").write_text("test-only")
            flow = Workflow(log=lambda text: None)
            flow.adb_binary = Mock(return_value="adb")
            flow._build_process = Mock()
            digest = "f" * 64
            with patch("station_root.workflow.sys.platform", "linux"), \
                 patch("station_root.workflow.sha256", return_value=digest), \
                 patch("station_root.workflow.validate_public_key"), \
                 patch("station_root.workflow.validate_image", return_value={"source_sha256": digest}):
                output = flow.prepare({"source": str(source), "work": d})
            self.assertIn(digest[:16], output.name)
            flow._build_process.assert_called_once()

    def pipeline(self, folder):
        flow = Workflow(log=lambda text: None)
        flow.transport = Mock()
        steps = []
        flow.adb_binary = Mock(return_value="adb")
        flow.ensure_environment = Mock(side_effect=lambda cfg, **kwargs: steps.append("environment"))
        flow.backup = Mock(side_effect=lambda work: (steps.append("backup") or (Path(folder) / "original.bin", "f" * 64)))
        flow.prepare = Mock(side_effect=lambda cfg: (steps.append("build") or Path(folder) / "image.ubi"))
        flow.flash = Mock(side_effect=lambda *args, **kwargs: steps.append("flash"))
        flow.reboot = Mock(side_effect=lambda: steps.append("reboot"))
        flow.verify = Mock(side_effect=lambda *args, **kwargs: (steps.append("root") or "test-speaker"))
        bundle = patch("station_root.workflow.homebrew.resources")
        bundle.start()
        self.addCleanup(bundle.stop)
        deployment = patch("station_root.workflow.homebrew.install", side_effect=lambda *args: (steps.append("homebrew") or "http://192.0.2.10:8088"))
        flow.homebrew_deploy_mock = deployment.start()
        self.addCleanup(deployment.stop)
        return flow, steps

    def test_single_click_order_and_persistent_rollback_record(self):
        with tempfile.TemporaryDirectory() as d:
            flow, steps = self.pipeline(d)
            with patch("station_root.workflow.validate_image", return_value={"source_sha256": "f" * 64, "ota_disabled": True}):
                flow.install({"work": d})
            self.assertEqual(steps, ["environment", "backup", "build", "flash", "reboot", "root", "homebrew"])
            flow.backup.assert_called_once()
            self.assertEqual(flow.flash.call_args.kwargs["original"][1], "f" * 64)
            session = json.loads((Path(d) / "session.json").read_text())
            self.assertTrue(session["restore_eligible"])
            self.assertEqual(session["state"], "root-and-homebrew-verified")
            self.assertEqual(session["homebrew_url"], "http://192.0.2.10:8088")
            self.assertEqual(session["source_sha256"], "f" * 64)

    def test_build_failure_keeps_backup_and_never_flashes(self):
        with tempfile.TemporaryDirectory() as d:
            flow, steps = self.pipeline(d)
            flow.prepare.side_effect = RuntimeError("unsupported layout")
            with self.assertRaises(RuntimeError):
                flow.install({"work": d})
            flow.flash.assert_not_called()
            flow.reboot.assert_not_called()
            session = json.loads((Path(d) / "session.json").read_text())
            self.assertFalse(session.get("restore_eligible", False))
            self.assertIn("pending_backup", session)

    def test_homebrew_failure_retries_without_reflashing(self):
        with tempfile.TemporaryDirectory() as d:
            flow, steps = self.pipeline(d)
            flow.homebrew_deploy_mock.side_effect = RuntimeError("disk full")
            with patch("station_root.workflow.validate_image", return_value={"source_sha256": "f" * 64, "ota_disabled": True}):
                with self.assertRaisesRegex(RuntimeError, "ADB root is confirmed"):
                    flow.install({"work": d})
            state = json.loads((Path(d) / "session.json").read_text())
            self.assertEqual(state["state"], "homebrew-pending")
            self.assertEqual(state["serial"], "test-speaker")
            steps.clear()
            flow.homebrew_deploy_mock.side_effect = lambda *args: (steps.append("homebrew") or "http://192.0.2.10:8088")
            flow.install({"work": d})
            self.assertEqual(steps, ["root", "homebrew"])
            self.assertEqual(flow.flash.call_count, 1)

    def test_missing_homebrew_bundle_never_captures_or_flashes(self):
        with tempfile.TemporaryDirectory() as d:
            flow, _ = self.pipeline(d)
            with patch("station_root.workflow.homebrew.resources", side_effect=RuntimeError("bundle missing")):
                with self.assertRaisesRegex(RuntimeError, "bundle missing"):
                    flow.install({"work": d})
            flow.backup.assert_not_called()
            flow.flash.assert_not_called()

    def test_failed_reinstall_preserves_previous_unroot_target(self):
        with tempfile.TemporaryDirectory() as d:
            flow, _ = self.pipeline(d)
            flow.save_session(d, restore_eligible=True, original_backup="pristine.bin", source_sha256="old")
            flow.prepare.side_effect = RuntimeError("already patched")
            with self.assertRaises(RuntimeError):
                flow.install({"work": d})
            session = json.loads((Path(d) / "session.json").read_text())
            self.assertTrue(session["restore_eligible"])
            self.assertEqual(session["original_backup"], "pristine.bin")
            self.assertEqual(session["source_sha256"], "old")

    def test_mismatched_build_source_never_flashes(self):
        with tempfile.TemporaryDirectory() as d:
            flow, _ = self.pipeline(d)
            with patch("station_root.workflow.validate_image", return_value={"source_sha256": "wrong"}):
                with self.assertRaises(ValueError):
                    flow.install({"work": d})
            flow.flash.assert_not_called()

    def test_setup_restart_does_not_capture_or_backup(self):
        with tempfile.TemporaryDirectory() as d:
            flow, _ = self.pipeline(d)
            flow.ensure_environment.side_effect = RestartRequired("restart")
            flow.connect = Mock()
            with self.assertRaises(RestartRequired):
                flow.install({"work": d})
            flow.backup.assert_not_called()
            flow.connect.assert_not_called()

    def test_unroot_restores_saved_original_and_reboots(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            original = work / "original.bin"
            original.write_bytes(b"ORIGINAL")
            from station_root.image_builder import sha256
            flow = Workflow(log=lambda text: None)
            flow.transport = Mock()
            flow.restore, flow.reboot = Mock(), Mock()
            flow.save_session(work, restore_eligible=True, original_backup=str(original), source_sha256=sha256(original))
            flow.unroot({"work": d})
            flow.restore.assert_called_once_with({"work": d}, original)
            flow.reboot.assert_called_once()

    def test_changed_rollback_file_is_never_written(self):
        with tempfile.TemporaryDirectory() as d:
            flow = Workflow(log=lambda text: None)
            original = Path(d) / "backup.bin"
            original.write_bytes(b"CHANGED")
            flow.save_session(d, restore_eligible=True, original_backup=str(original), source_sha256="wrong")
            flow.restore = Mock()
            with self.assertRaises(ValueError):
                flow.unroot({"work": d})
            flow.restore.assert_not_called()


class EnvironmentTests(unittest.TestCase):
    def test_utf16_wsl_output(self):
        self.assertEqual(decode_output("Debian\r\nUbuntu".encode("utf-16-le")), "Debian\r\nUbuntu")

    def test_does_not_select_docker_appliances(self):
        self.assertEqual(select_distro(["docker-desktop", "Ubuntu-24.04"]), "Ubuntu-24.04")
        self.assertEqual(select_distro(["docker-desktop"]), "")
        self.assertEqual(select_distro(["Ubuntu", "Debian"]), "Debian")
        with self.assertRaises(RuntimeError):
            select_distro(["Debian"], "missing")

    def test_missing_wsl_installs_features_then_stops_for_restart(self):
        runner = Mock()
        with patch("station_root.environment.sys.platform", "win32"), \
             patch("station_root.environment.list_distros", return_value=[]), \
             patch("station_root.environment.shutil.which", return_value=None), \
             patch("station_root.environment.elevate_wsl_install", return_value=0) as elevate:
            with self.assertRaises(RestartRequired):
                prepare_environment({}, lambda text: None, runner, Path("."))
            elevate.assert_called_once()
            runner.assert_not_called()

    def test_existing_distro_uses_selected_root_builder(self):
        runner = Mock()
        with patch("station_root.environment.sys.platform", "win32"), \
             patch("station_root.environment.list_distros", return_value=["Debian"]), \
             patch("station_root.environment.probe_distro", return_value="compatible"), \
             patch("station_root.environment.run_capture", return_value=(0, "/mnt/c/setup.sh", "")):
            result = prepare_environment({}, lambda text: None, runner, Path("."))
        self.assertEqual(result, "Debian")
        self.assertEqual(runner.call_args.args[0], ["wsl.exe", "--distribution", "Debian", "--user", "root", "--exec", "bash", "/mnt/c/setup.sh"])


class LanguageTests(unittest.TestCase):
    def test_both_directions_preserve_dynamic_paths(self):
        ru = translate_message("Reading system (80 MiB), backup: C:/Workspace/test.bin", "ru")
        self.assertIn("Чтение system", ru)
        en = translate_message(ru, "en")
        self.assertEqual(en, "Reading system (80 MiB), backup: C:/Workspace/test.bin")

    def test_ui_primary_controls_are_translated(self):
        for phrase in ("Install root", "Return without root", "Setup WSL / builder", "Disable Yandex automatic updates"):
            self.assertNotEqual(tr(phrase, "ru"), phrase)
            self.assertEqual(tr(phrase, "en"), phrase)


if __name__ == "__main__":
    unittest.main()
