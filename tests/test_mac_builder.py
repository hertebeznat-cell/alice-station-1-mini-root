import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from station_root import mac_builder
from station_root.dependencies import platform_tools_archive


class MacBuilderTests(unittest.TestCase):
    def test_google_macos_archive_selection(self):
        xml = (
            b"""<sdk><remotePackage path="platform-tools"><archives><archive>
        <host-os>macosx</host-os><complete><size>123</size><checksum type="sha256">"""
            + b"a" * 64
            + b"""</checksum>
        <url>platform-tools-darwin.zip</url></complete></archive></archives></remotePackage></sdk>"""
        )
        archive = platform_tools_archive(xml, "macosx")
        self.assertEqual(archive["bytes"], 123)
        self.assertEqual(archive["url"], "https://dl.google.com/android/repository/platform-tools-darwin.zip")

    @patch("station_root.mac_builder.ready", return_value=True)
    @patch("station_root.mac_builder.docker_binary", return_value="/usr/bin/docker")
    def test_builder_confines_paths_and_disables_network(self, _docker, _ready):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "folder with spaces"
            work.mkdir()
            command = mac_builder.command(work, work / "backup.bin", work / "keys/adbkey.pub", work / "root.ubi")
            self.assertIn("--network", command)
            self.assertEqual(command[command.index("--network") + 1], "none")
            self.assertIn(str(work.resolve()) + ":/data", command)
            self.assertEqual(command[-3:], ["/data/backup.bin", "/data/keys/adbkey.pub", "/data/root.ubi"])
            with self.assertRaises(ValueError):
                mac_builder.command(work, work / "other/backup.bin", work / "keys/adbkey.pub", work / "root.ubi")

    @patch("station_root.mac_builder.ready", return_value=False)
    @patch("station_root.mac_builder.docker_binary", return_value="/usr/bin/docker")
    def test_missing_builder_fails_before_write(self, _docker, _ready):
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            mac_builder.command("/src", "/data/a", "/data/keys/adbkey.pub", "/data/b")
