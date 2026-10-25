import unittest

from station_root.image_builder import patch_adb_activation


class ActivationVariantTests(unittest.TestCase):
    def script(self, branch):
        return ('#!/bin/sh\nif [ "$ARG_CMD" = "activate" ]; then\n'
                '    /etc/init.d/S89usbgadget start\nelse\n' + branch + '\nfi\n')

    def test_stock_deactivation_is_removed(self):
        source = self.script('    killall adbd\n    mv "${ADBD}" "${ADBD_BACKUP}"\n'
                             '    sync\n    echo "`date`: Stop adb" >> "${ADB_LOG}"')
        result, variant = patch_adb_activation(source)
        self.assertEqual(variant, 'stock-deactivation')
        self.assertNotIn('killall adbd', result)
        self.assertNotIn('mv "${ADBD}"', result)
        self.assertIn('/etc/init.d/S89usbgadget start', result)

    def test_owners_previous_persistent_patch_is_recognized(self):
        source = self.script('    echo "`date`: Ignore adb deactivation (persistent USB ADB)" >> "${ADB_LOG}"')
        result, variant = patch_adb_activation(source)
        self.assertEqual(variant, 'legacy-persistent')
        self.assertIn('Keep persistent USB ADB', result)
        self.assertIn('/etc/init.d/S89usbgadget start', result)
        self.assertNotIn('killall adbd', result)

    def test_unrecognized_log_only_branch_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'activate_adb'):
            patch_adb_activation(self.script('    echo "something else"'))

    def test_legacy_message_with_additional_commands_is_rejected(self):
        source = self.script('    echo "`date`: Ignore adb deactivation (persistent USB ADB)" >> "${ADB_LOG}"\n    killall adbd')
        with self.assertRaisesRegex(RuntimeError, 'activate_adb'):
            patch_adb_activation(source)

    def test_legacy_branch_does_not_hide_an_extra_daemon_rename(self):
        source = self.script('    echo "`date`: Ignore adb deactivation (persistent USB ADB)" >> "${ADB_LOG}"')
        source = source.replace('    /etc/init.d/S89usbgadget start',
                                '    mv "${ADBD}" "${ADBD_BACKUP}"\n    /etc/init.d/S89usbgadget start')
        with self.assertRaisesRegex(RuntimeError, 'activate_adb'):
            patch_adb_activation(source)

    def test_current_patch_is_not_treated_as_a_clean_rollback_source(self):
        with self.assertRaisesRegex(RuntimeError, 'already patched'):
            patch_adb_activation(self.script('    echo "`date`: Keep persistent USB ADB" >> "${ADB_LOG}"'))


if __name__ == '__main__':
    unittest.main()
