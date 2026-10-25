import os
import subprocess
import unittest
from unittest.mock import Mock, patch, call

from station_root.environment import (discover_distros, builder_ready, WSLUnavailable,
                                      external_process)
from station_root.workflow import Workflow


class WSLSelectionTests(unittest.TestCase):
    def test_usable_saved_ubuntu_does_not_start_unrelated_crb(self):
        with patch('station_root.environment.default_distro', return_value='crb'), \
             patch('station_root.environment.probe_distro', return_value='compatible') as probe:
            found = discover_distros('Ubuntu', ['Ubuntu', 'crb'])
        self.assertEqual(found['selected'], 'Ubuntu')
        self.assertEqual(found['unchecked'], ['crb'])
        probe.assert_called_once_with('Ubuntu')

    def test_root_reuses_selected_builder_without_rediscovering_distros(self):
        flow = Workflow(log=lambda message: None)
        flow.setup = Mock()
        with patch('station_root.workflow.sys.platform', 'win32'), \
             patch('station_root.workflow.installed_distro') as discovery, \
             patch('station_root.workflow.builder_ready', return_value=True) as check:
            config = flow.ensure_environment({'distro': 'Ubuntu'}, setup_if_missing=False)
        self.assertEqual(config['distro'], 'Ubuntu')
        self.assertEqual(check.call_args.args[0], 'Ubuntu')
        discovery.assert_not_called()
        flow.setup.assert_not_called()

    def test_slow_wsl_launch_retries_same_distro(self):
        with patch('station_root.environment.run_capture', side_effect=[
                subprocess.TimeoutExpired('wsl', 90),
                (0, 'STATION_BUILDER_READY\n', '')]) as runner:
            self.assertTrue(builder_ready('Ubuntu', strict=True))
        self.assertEqual(runner.call_count, 2)
        self.assertTrue(all(c.args[0][2] == 'Ubuntu' for c in runner.call_args_list))

    def test_explicit_missing_packages_are_not_a_launch_failure(self):
        with patch('station_root.environment.run_capture', return_value=(0, 'STATION_BUILDER_MISSING\n', '')) as runner:
            self.assertFalse(builder_ready('Ubuntu', strict=True))
        runner.assert_called_once()

    def test_wsl_launch_error_keeps_details_and_never_starts_backup_or_setup(self):
        flow = Workflow(log=lambda message: None)
        flow.adb_binary, flow.backup, flow.setup, flow.connect = Mock(), Mock(), Mock(), Mock()
        with patch('station_root.workflow.sys.platform', 'win32'), \
             patch('station_root.environment.run_capture', return_value=(1, '', 'Wsl/Service/ExampleFailure')):
            with self.assertRaisesRegex(WSLUnavailable, 'Wsl/Service/ExampleFailure'):
                flow.install({'distro': 'Ubuntu', 'work': '.'})
        flow.setup.assert_not_called()
        flow.backup.assert_not_called()
        flow.connect.assert_not_called()

    def test_root_does_not_reinstall_packages_if_builder_was_removed(self):
        flow = Workflow(log=lambda message: None)
        flow.setup = Mock()
        with patch('station_root.workflow.sys.platform', 'win32'), \
             patch('station_root.workflow.builder_ready', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'dependency setup'):
                flow.ensure_environment({'distro': 'Ubuntu'}, setup_if_missing=False)
        flow.setup.assert_not_called()


class FrozenProcessTests(unittest.TestCase):
    def invoke(self, failure=False):
        kernel = Mock()
        kernel.SetDllDirectoryW.return_value = 1
        with patch('station_root.environment.sys.platform', 'win32'), \
             patch('station_root.environment.sys.frozen', True, create=True), \
             patch('station_root.environment.sys._MEIPASS', r'C:\Temp\_MEI123', create=True), \
             patch.dict(os.environ, {'PATH': r'C:\Temp\_MEI123;C:\Windows;C:\Temp\_MEI123\lib;C:\Temp\_MEI123-other'}), \
             patch('station_root.environment.ctypes.WinDLL', return_value=kernel, create=True), \
             patch('station_root.environment.subprocess.Popen') as spawn:
            if failure:
                spawn.side_effect = OSError('cannot launch')
                with self.assertRaises(OSError):
                    external_process(['wsl.exe', '--status'])
            else:
                external_process(['wsl.exe', '--status'])
            env = spawn.call_args.kwargs['env']
        self.assertEqual(kernel.SetDllDirectoryW.call_args_list,
                         [call(None), call(r'C:\Temp\_MEI123')])
        self.assertEqual(env['PATH'], r'C:\Windows;C:\Temp\_MEI123-other')

    def test_frozen_app_starts_wsl_with_system_dll_search_path(self):
        self.invoke()

    def test_app_dll_path_is_restored_if_subprocess_start_fails(self):
        self.invoke(failure=True)


if __name__ == '__main__':
    unittest.main()
