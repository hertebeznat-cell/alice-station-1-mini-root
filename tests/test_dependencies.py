import io
import json
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from station_root.environment import discover_distros, installed_distro, probe_distro, RestartRequired
from station_root.dependencies import platform_tools_archive, extract_tools, Dependencies
from station_root.workflow import Workflow


class DiscoveryTests(unittest.TestCase):
    def test_renamed_distro_is_detected_from_os_release(self):
        with patch("station_root.environment.run_capture", return_value=(0, 'NAME="Anything"\nID=ubuntu\nID_LIKE=debian\n', "")):
            self.assertEqual(probe_distro("my-builder"), "compatible")

    def test_appliance_is_not_started(self):
        with patch("station_root.environment.run_capture") as runner:
            self.assertEqual(probe_distro("docker-desktop"), "unsupported")
            runner.assert_not_called()

    def test_default_and_saved_distro_priority(self):
        with patch("station_root.environment.probe_distro", return_value="compatible"), \
             patch("station_root.environment.default_distro", return_value="Work Linux"):
            names = ["Debian", "Work Linux"]
            self.assertEqual(discover_distros(names=names)["selected"], "Work Linux")
            self.assertEqual(discover_distros("Debian", names)["selected"], "Debian")
            self.assertEqual(discover_distros("removed", names)["selected"], "Work Linux")

    def test_timeout_does_not_trigger_another_distro_install(self):
        with patch("station_root.environment.probe_distro", return_value="unknown"):
            with self.assertRaises(RuntimeError):
                installed_distro(names=["Ubuntu"])


class DownloadTests(unittest.TestCase):
    def xml(self, url="platform-tools.zip", checksum="f" * 40):
        return f'''<repository xmlns="urn:test"><remotePackage path="platform-tools"><channelRef ref="channel-0"/>
        <archives><archive><host-os>windows</host-os><complete><url>{url}</url><size>100</size>
        <checksum type="sha1">{checksum}</checksum></complete></archive></archives></remotePackage></repository>'''

    def test_official_platform_tools_metadata(self):
        item = platform_tools_archive(self.xml(), "windows")
        self.assertEqual(item["url"], "https://dl.google.com/android/repository/platform-tools.zip")
        self.assertEqual(item["digest"], "f" * 40)

    def test_metadata_cannot_redirect_to_another_host(self):
        with self.assertRaises(ValueError):
            platform_tools_archive(self.xml("https://untrusted.example/a.zip"), "windows")

    def test_invalid_checksum_is_rejected(self):
        with self.assertRaises(ValueError):
            platform_tools_archive(self.xml(checksum="not-a-checksum"), "windows")

    def test_zip_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            archive = Path(d) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("platform-tools/../../escape.exe", "bad")
            with self.assertRaises(ValueError):
                extract_tools(archive, Path(d) / "output")
            self.assertFalse((Path(d) / "escape.exe").exists())


class ReadinessTests(unittest.TestCase):
    def test_anchor_continues_during_host_wait_and_stops_before_transfer(self):
        flow = Workflow(log=lambda message: None)
        flow.transport = Mock()
        value = flow.host_operation(lambda: (time.sleep(0.55), 42)[1])
        self.assertEqual(value, 42)
        calls = flow.transport.keepalive.call_count
        self.assertGreaterEqual(calls, 1)
        time.sleep(0.45)
        self.assertEqual(flow.transport.keepalive.call_count, calls)

    def test_host_wait_does_not_hide_a_lost_anchor(self):
        flow = Workflow(log=lambda message: None)
        flow.transport = Mock()
        flow.transport.keepalive.side_effect = OSError('disconnected')
        with self.assertRaisesRegex(RuntimeError, 'USB connection lost'):
            flow.host_operation(lambda: time.sleep(0.55))

    def test_setup_failure_never_unlocks_or_writes_ready_marker(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"STATION_ROOT_DATA_DIR": d}), \
             patch("station_root.dependencies.sys.platform", "win32"), \
             patch("station_root.dependencies.find_adb", return_value="adb"), \
             patch("station_root.dependencies.install_driver") as installer:
            workflow = Mock()
            workflow.ensure_environment.side_effect = RestartRequired("restart")
            manager = Dependencies(workflow, Path(d))
            with self.assertRaises(RestartRequired):
                manager.install({})
            installer.assert_not_called()
            workflow.connect.assert_not_called()
            self.assertFalse(manager.marker.exists())

    def test_failed_usb_validation_does_not_mark_ready(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"STATION_ROOT_DATA_DIR": d}), \
             patch("station_root.dependencies.sys.platform", "win32"), \
             patch("station_root.dependencies.find_adb", return_value="adb"), \
             patch("station_root.dependencies.driver_registered", return_value=True), \
             patch("station_root.dependencies.install_driver"):
            workflow = Mock()
            workflow.connect.side_effect = TimeoutError("No speaker")
            manager = Dependencies(workflow, Path(d))
            with self.assertRaises(TimeoutError):
                manager.install({})
            self.assertFalse(manager.marker.exists())

    def test_stale_ready_file_is_not_enough_without_a_driver(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"STATION_ROOT_DATA_DIR": d}), \
             patch("station_root.dependencies.sys.platform", "win32"), \
             patch("station_root.dependencies.find_adb", return_value="adb"), \
             patch("station_root.dependencies.discover_distros", return_value={"selected": "Linux"}), \
             patch("station_root.dependencies.builder_ready", return_value=True), \
             patch("station_root.dependencies.make_backend"), \
             patch("station_root.dependencies.driver_registered", return_value=False):
            manager = Dependencies(Mock(), Path(d))
            manager.marker.write_text(json.dumps({"usb_verified": True, "adb_driver_staged": True}))
            self.assertFalse(manager.inspect({})["ready"])


if __name__ == "__main__": unittest.main()
