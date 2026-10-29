import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from station_mini_root import check_usb


class USBDiagnosticTests(unittest.TestCase):
    def test_diagnostic_loads_runtime_without_accessing_any_device(self):
        backend = SimpleNamespace(backend=SimpleNamespace(lib=SimpleNamespace(_name="bundled-libusb")))
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("station_root.usb_transport.make_backend", return_value=backend),
            patch("usb.core.find") as find,
        ):
            path = Path(temporary) / "usb.json"
            self.assertEqual(check_usb(path), 0)
            report = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(report["ready"])
            self.assertEqual(report["library"], "bundled-libusb")
            find.assert_not_called()

    def test_missing_runtime_is_a_failed_diagnostic_with_original_error(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("station_root.usb_transport.make_backend", side_effect=RuntimeError("missing test library")),
        ):
            path = Path(temporary) / "usb.json"
            self.assertEqual(check_usb(path), 1)
            report = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(report["ready"])
            self.assertEqual(report["error"], "missing test library")
