import io
from contextlib import redirect_stdout
import json
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from station_root.process_output import decode_output, iter_output
from station_root.journal import Journal
from station_root.gui import App
from station_root.workflow import Workflow
from station_root.dependencies import install_driver
from station_root.image_builder import firmware_diagnostics, build, SYSTEM_SIZE


class Fragmented(io.BytesIO):
    def read1(self, _size):
        return self.read(1)


class SupportLogsTests(unittest.TestCase):
    def test_wsl_host_utf16_warning_and_linux_utf8_path_remain_readable(self):
        warning = "wsl: Ошибка запуска\r\n\r\n"
        linux = "/mnt/c/Users/Тест/system.bin\n"
        raw = warning.encode("utf-16-le") + linux.encode("utf-8")
        self.assertEqual(decode_output(raw), warning + linux)
        self.assertEqual("".join(iter_output(Fragmented(raw))), warning + linux)

    def test_utf16_russian_without_ascii_prefix_and_big_endian_bom(self):
        for raw in ["Ошибка\n".encode("utf-16-le"), b"\xfe\xff" + "Ошибка\n".encode("utf-16-be")]:
            self.assertEqual("".join(iter_output(Fragmented(raw))), "Ошибка\n")

    def test_real_child_failure_preserves_readable_output_and_exit_code(self):
        text = "Параметр WSL не поддерживается\r\n"
        data = text.encode("utf-16-le") + "Linux: ready\n".encode()
        logs = []
        flow = Workflow(log=logs.append)
        code = "import sys;sys.stdout.buffer.write(bytes.fromhex('" + data.hex() + "'));sys.exit(19)"
        with self.assertRaisesRegex(RuntimeError, "Exit code: 19") as error:
            flow._build_process([sys.executable, "-c", code])
        self.assertIn(text.strip(), logs)
        self.assertIn("Linux: ready", str(error.exception))
        self.assertNotIn("\x00", "".join(logs))

    def test_copy_log_includes_all_lines_not_only_the_visible_widget(self):
        app = App.__new__(App)
        app.raw_logs = [("12:00:00 ", "line " + str(i)) for i in range(1000)]
        app.language, app.root, app.status = "en", Mock(), Mock()
        app.copy_log()
        copied = app.root.clipboard_append.call_args.args[0]
        self.assertIn("line 0\n", copied)
        self.assertIn("line 999\n", copied)
        self.assertEqual(copied.count("12:00:00"), 1000)

    def test_automatic_journal_keeps_unicode_and_uses_unique_files(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = Journal(folder, "test"), Journal(folder, "test")
            first.append("Полный журнал")
            first.append("last line")
            self.assertNotEqual(first.path, second.path)
            text = first.path.read_text(encoding="utf-8")
            self.assertIn("Полный журнал", text)
            self.assertIn("last line", text)

    def test_native_driver_failure_exposes_details_and_keeps_log(self):
        with tempfile.TemporaryDirectory() as folder:
            resources = Path(folder) / "resources"
            resources.mkdir()
            helper = resources / "station-driver-helper.exe"
            helper.write_bytes(b"test helper fixture")
            helper.with_suffix(".json").write_text(
                json.dumps({"diagnostic_log": True, "sha256": hashlib.sha256(helper.read_bytes()).hexdigest()})
            )

            def elevated(_helper, args):
                Path(args[2]).write_text("wdi_install_driver: code=-9 pending\nSTATION_DRIVER_RESULT=-9\n")
                return 1

            logs = []
            with (
                patch("station_root.dependencies.sys.platform", "win32"),
                patch("station_root.dependencies.data_root", return_value=Path(folder) / "data"),
                patch("station_root.dependencies.run_elevated", side_effect=elevated),
            ):
                with self.assertRaisesRegex(RuntimeError, "exit=1"):
                    install_driver(Path(folder), "boot", logs.append)
            self.assertTrue(any("code=-9 pending" in line for line in logs))
            self.assertEqual(len(list((Path(folder) / "data/drivers/boot").glob("install-*.log"))), 1)

    def test_firmware_diagnostics_export_hash_checks_without_account_values(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            vendor = root / "system/vendor/quasar"
            vendor.mkdir(parents=True)
            secret = "PRIVATE_ACCOUNT_SENTINEL"
            (vendor / "quasar.cfg").write_text(secret)
            (vendor / "updater_switcher").write_bytes(b"unknown switcher")
            (vendor / "version_full").write_text("1.2.3.20261028")
            (vendor / "launch.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "name": "updater_switcher",
                                "path": "/system/vendor/quasar/updater_switcher",
                                "arg": [secret],
                            },
                        ]
                    }
                )
            )
            report = firmware_diagnostics(root, "0" * 64)
            self.assertFalse(report["switcher_hash_matches"])
            self.assertFalse(report["switcher_checks"][0]["arguments_match"])
            self.assertEqual(report["version_full"]["version"], "1.2.3.20261028")
            self.assertNotIn(secret, json.dumps(report))
            self.assertFalse(report["firmware_written"])

    def test_failed_patch_saves_diagnostics_without_producing_a_flash_image(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "backup.bin", Path(folder) / "root.ubi"
            with source.open("wb") as stream:
                stream.truncate(SYSTEM_SIZE)

            def extract(command, **_kwargs):
                root = Path(command[command.index("-o") + 1]) / "123/rootfs"
                vendor = root / "system/vendor/quasar"
                vendor.mkdir(parents=True)
                (vendor / "launch.json").write_text('{"tasks": []}')
                (vendor / "version_full").write_text("1.2.20261028")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with (
                patch("station_root.image_builder.sys.platform", "linux"),
                patch("station_root.image_builder.os.geteuid", return_value=0, create=True),
                patch("station_root.image_builder.inspect_layout", return_value={}),
                patch("station_root.image_builder.validate_public_key", return_value="public fixture"),
                patch("station_root.image_builder.shutil.which", return_value="tool"),
                patch("station_root.image_builder.subprocess.run", side_effect=extract) as child,
                patch("station_root.image_builder.patch_rootfs", side_effect=RuntimeError("unknown OTA")),
            ):
                with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "unknown OTA"):
                    build(source, Path(folder) / "fixture.pub", output)
            self.assertEqual(child.call_count, 1)  # Only extraction, no image creation or flashing.
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix(".json").exists())
            report = json.loads(output.with_suffix(".diagnostics.json").read_text(encoding="utf-8"))
            self.assertEqual(report["version_full"]["version"], "1.2.20261028")
            self.assertFalse(report["firmware_written"])


if __name__ == "__main__":
    unittest.main()
