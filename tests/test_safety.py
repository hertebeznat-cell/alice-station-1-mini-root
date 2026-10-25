import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from station_root import usb_transport as usb
from station_root.image_builder import validate_public_key
from station_root.workflow import Workflow, validate_image


class SafetyTests(unittest.TestCase):
    def test_reset_accepts_expected_usb_disconnect(self):
        from usb.core import USBError
        device = Mock()
        device.ctrl_transfer.side_effect = USBError("device gone", errno=19)
        usb.Transport(device).reboot()

    def test_reset_does_not_hide_other_usb_errors(self):
        from usb.core import USBError
        device = Mock()
        device.ctrl_transfer.side_effect = USBError("I/O error", errno=5)
        with self.assertRaises(USBError):
            usb.Transport(device).reboot()
    def test_only_unlocked_known_tpl_is_accepted(self):
        usb.Transport.validate_identify(bytes.fromhex("0007001000000000"))
        usb.Transport.validate_identify(bytes.fromhex("0008001000000000"))
        for ident in (b"", bytes.fromhex("0007000000000000"),
                      bytes.fromhex("0007001001000000")):
            with self.subTest(ident=ident), self.assertRaises(RuntimeError):
                usb.Transport.validate_identify(ident)

    def test_public_key_rejects_private_key(self):
        with tempfile.TemporaryDirectory() as d:
            key = Path(d) / "key"
            key.write_text("-----BEGIN PRIVATE KEY-----\nsecret")
            with self.assertRaises(ValueError):
                validate_public_key(key)

    def test_invalid_image_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            image = Path(d) / "bad.ubi"
            image.write_bytes(b"UBI#")
            image.with_suffix(".json").write_text(json.dumps({"partition": "bootloader"}))
            with self.assertRaises(ValueError):
                validate_image(image)

    def test_different_firmware_is_backed_up_but_never_written(self):
        flow = Workflow(log=lambda s: None)
        flow.transport = Mock()
        flow.backup = Mock(return_value=(Path("saved.bin"), "different-firmware"))
        with patch("station_root.workflow.validate_image", return_value={"image_sha256": "test", "source_sha256": "expected"}):
            with self.assertRaises(ValueError):
                flow.flash({"work": "."})
        flow.backup.assert_called_once()
        flow.transport.write_system.assert_not_called()

    def test_truncated_backup_is_marked_partial(self):
        with tempfile.TemporaryDirectory() as d:
            flow = Workflow(log=lambda s: None)
            flow.transport = Mock()
            def broken(output, progress):
                output.write_bytes(b"partial data")
                raise OSError("USB lost")
            flow.transport.read_system.side_effect = broken
            with self.assertRaises(OSError):
                flow.backup(d)
            self.assertEqual(len(list(Path(d).glob("*.partial"))), 1)
            self.assertEqual(list(Path(d).glob("*.json")), [])

    def test_read_handles_fragmented_bulk_packets(self):
        with tempfile.TemporaryDirectory() as d, patch.object(usb, "SYSTEM_SIZE", 8):
            device = Mock()
            device.ctrl_transfer.return_value = struct.pack("<IIII", 0xEFE8, 8, 0, 0)
            transport = usb.Transport(device)
            transport.keepalive = Mock()
            transport.command = Mock()
            endpoint = Mock()
            endpoint.read.side_effect = [b"abc", b"defgh"]
            transport.endpoints = Mock(return_value=(endpoint, Mock()))
            output = Path(d) / "backup.bin"
            self.assertEqual(transport.read_system(output), hashlib.sha256(b"abcdefgh").hexdigest())
            self.assertEqual(output.read_bytes(), b"abcdefgh")
            transport.command.assert_called_once_with("upload store system normal 0x8")

    def test_read_refuses_overflow_block(self):
        with tempfile.TemporaryDirectory() as d, patch.object(usb, "SYSTEM_SIZE", 8):
            device = Mock()
            device.ctrl_transfer.return_value = struct.pack("<IIII", 0xEFE8, 9, 0, 0)
            transport = usb.Transport(device)
            transport.keepalive = Mock()
            transport.command = Mock()
            endpoint = Mock()
            transport.endpoints = Mock(return_value=(endpoint, Mock()))
            with self.assertRaises(RuntimeError):
                transport.read_system(Path(d) / "backup.bin")
            endpoint.read.assert_not_called()

    def test_write_checks_ack_and_never_erases_other_partitions(self):
        with tempfile.TemporaryDirectory() as d, patch.object(usb, "SYSTEM_SIZE", 0x10000):
            image = Path(d) / "image.ubi"
            image.write_bytes(b"\x01\x00\x00\x00" * 16384)
            device = Mock()
            device.ctrl_transfer.return_value = b"success\0"
            device.read.return_value = b"OK!!\0"
            endpoint = Mock()
            endpoint.write.return_value = 0x10000
            transport = usb.Transport(device)
            transport.keepalive = Mock()
            transport.command = Mock()
            transport.endpoints = Mock(return_value=(Mock(), endpoint))
            transport.write_system(image)
            self.assertEqual([c.args[0] for c in transport.command.call_args_list],
                             ["disk_initial 0", "download get_status"])
            self.assertEqual(device.ctrl_transfer.call_args_list[0].args[4],
                             b"download store system normal 0x10000\0")
            request = device.ctrl_transfer.call_args_list[2].args[4]
            values = struct.unpack("<IIIIHH", request[:20])
            self.assertEqual(values, (0, 65536, 0, 16384, 0xEF, 512))

    def test_bad_ack_aborts_before_success_status(self):
        with tempfile.TemporaryDirectory() as d, patch.object(usb, "SYSTEM_SIZE", 0x10000):
            image = Path(d) / "image.ubi"
            image.write_bytes(b"\0" * 0x10000)
            device = Mock()
            device.ctrl_transfer.return_value = b"success\0"
            device.read.return_value = b"ERROR"
            endpoint = Mock()
            endpoint.write.return_value = 0x10000
            transport = usb.Transport(device)
            transport.keepalive = Mock()
            transport.command = Mock()
            transport.endpoints = Mock(return_value=(Mock(), endpoint))
            with self.assertRaises(RuntimeError):
                transport.write_system(image)
            self.assertEqual([c.args[0] for c in transport.command.call_args_list], ["disk_initial 0"])

    def test_backup_never_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / "backup.bin"
            output.write_bytes(b"precious")
            transport = usb.Transport(Mock())
            transport.keepalive = Mock()
            transport.endpoints = Mock(return_value=(Mock(), Mock()))
            transport.command = Mock()
            with self.assertRaises(FileExistsError):
                transport.read_system(output)
            transport.command.assert_not_called()
            self.assertEqual(output.read_bytes(), b"precious")


if __name__ == "__main__":
    unittest.main()
