import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from station_root.dependencies import Dependencies
from station_root.usb_transport import BackendAdapter, Transport, USBBackendError, make_backend


class USBWindowsTests(unittest.TestCase):
    def test_connection_uses_fresh_context_after_driver_setup(self):
        cached = Mock()
        with patch('usb.backend.libusb1.get_backend', return_value=cached), \
             patch('usb.backend.libusb1._LibUSB', side_effect=[Mock(), Mock()]) as factory:
            first, second = make_backend(), make_backend()
        self.assertIsNot(first.backend, second.backend)
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(factory.call_args.args, (cached.lib,))

    def test_actual_pyusb_open_error_preserves_operation(self):
        # Use the real PyUSB Device / resource manager, not a mocked Transport.
        backend = Mock()
        fields = ('bLength bDescriptorType bcdUSB bDeviceClass bDeviceSubClass '
                  'bDeviceProtocol bMaxPacketSize0 bcdDevice iManufacturer iProduct '
                  'iSerialNumber bNumConfigurations address bus port_number speed').split()
        descriptor = SimpleNamespace(**dict.fromkeys(fields, 1),
                                     idVendor=0x1b8e, idProduct=0xc003, port_numbers=(1,))
        backend.enumerate_devices.return_value = [object()]
        backend.get_device_descriptor.return_value = descriptor
        backend.open_device.side_effect = NotImplementedError('Operation not supported or unimplemented on this platform')
        with patch('station_root.usb_transport.make_backend', return_value=BackendAdapter(backend)):
            with self.assertRaises(USBBackendError) as error:
                Transport.wait(threading.Event(), log=lambda message: None, seconds=1)
        self.assertEqual(error.exception.operation, 'open_device')
        backend.ctrl_transfer.assert_not_called()
        backend.bulk_write.assert_not_called()

    def test_usb_error_codes_other_than_not_supported_are_preserved(self):
        from usb.core import USBError
        backend = Mock()
        error = USBError('Access denied', error_code=-3, errno=13)
        backend.open_device.side_effect = error
        with self.assertRaises(USBError) as caught:
            BackendAdapter(backend).open_device(object())
        self.assertIs(caught.exception, error)

    def dependency_run(self, errors):
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {'STATION_ROOT_DATA_DIR': folder}), \
             patch('station_root.dependencies.sys.platform', 'win32'), \
             patch('station_root.dependencies.find_adb', return_value='adb'), \
             patch('station_root.dependencies.driver_registered', return_value=True), \
             patch('station_root.dependencies.install_driver') as install:
            workflow = Mock()
            workflow.connect.side_effect = errors
            manager = Dependencies(workflow, Path(folder))
            try:
                manager.install({})
                return workflow.connect.call_count, [call.args[1] for call in install.call_args_list], manager.marker.exists()
            except USBBackendError as error:
                return workflow.connect.call_count, [call.args[1] for call in install.call_args_list], manager.marker.exists(), error.operation

    def test_open_failure_repairs_boot_driver_once_then_validates_usb(self):
        self.assertEqual(self.dependency_run([USBBackendError('open_device'), None]),
                         (2, ['adb', 'boot'], True))

    def test_second_open_failure_never_unlocks_or_retries_forever(self):
        self.assertEqual(self.dependency_run([USBBackendError('open_device'), USBBackendError('open_device')]),
                         (2, ['adb', 'boot'], False, 'open_device'))

    def test_transfer_failure_does_not_trigger_driver_replacement(self):
        self.assertEqual(self.dependency_run([USBBackendError('ctrl_transfer')]),
                         (1, ['adb'], False, 'ctrl_transfer'))


if __name__ == '__main__':
    unittest.main()
