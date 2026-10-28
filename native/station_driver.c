/* Station Mini USB driver helper. LGPL-3.0-or-later, linked with libwdi.
 * Only these device IDs are supported; no generic device selection is exposed.
 * Build instructions and upstream source are in native/README.md.
 */
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <shlwapi.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <io.h>
#include "libwdi.h"

static char *utf8(const wchar_t *value) {
    int n = WideCharToMultiByte(CP_UTF8, 0, value, -1, NULL, 0, NULL, NULL);
    char *out = (char *)calloc((size_t)n, 1);
    if (out) WideCharToMultiByte(CP_UTF8, 0, value, -1, out, n, NULL, NULL);
    return out;
}

static int install_one(struct wdi_device_info *device, const char *dest, int adb) {
    struct wdi_options_prepare_driver prepare = {0};
    struct wdi_options_install_driver install = {0};
    prepare.driver_type = WDI_WINUSB;
    prepare.vendor_name = "Station Mini";
    if (adb) prepare.device_guid = "{F72FE0D4-CBCB-407D-8814-9ED673D0DD6B}";
    const char *inf = adb ? "station_mini_adb.inf" : "station_mini_boot.inf";
    install.pending_install_timeout = 120000;
    fprintf(stderr, "Target %04x:%04x MI=%d current driver=%s\n", device->vid, device->pid,
            device->is_composite ? device->mi : -1, device->driver ? device->driver : "none");
    fflush(stderr);
    int result = wdi_prepare_driver(device, dest, inf, &prepare);
    fprintf(stderr, "wdi_prepare_driver: code=%d %s\n", result, wdi_strerror(result));
    fflush(stderr);
    if (result == WDI_SUCCESS) {
        result = wdi_install_driver(device, dest, inf, &install);
        fprintf(stderr, "wdi_install_driver: code=%d %s\n", result, wdi_strerror(result));
    }
    fprintf(stderr, "%04x:%04x MI=%d: %s\n", device->vid, device->pid,
            device->is_composite ? device->mi : -1, wdi_strerror(result));
    return result;
}

int wmain(int argc, wchar_t **argv) {
    /* Modes: boot installs/stages the boot device. adb installs only an ADB
       interface matching this speaker, or stages its stock MI_02 if absent. */
    if ((argc != 3 && argc != 4) || (wcscmp(argv[1], L"boot") && wcscmp(argv[1], L"adb"))) return 64;
    if (argc == 4) {
        if (!_wfreopen(argv[3], L"wb", stderr)) return 66;
        _dup2(_fileno(stderr), _fileno(stdout));
        setvbuf(stderr, NULL, _IONBF, 0);
        setvbuf(stdout, NULL, _IONBF, 0);
    }
    wdi_set_log_level(WDI_LOG_LEVEL_INFO);
    int adb = !wcscmp(argv[1], L"adb");
    char *dest = utf8(argv[2]);
    if (!dest) return 65;
    struct wdi_device_info *list = NULL, *p;
    struct wdi_options_create_list options = {0};
    options.list_all = TRUE;
    int found = 0, result = 0;
    if (wdi_create_list(&list, &options) == WDI_SUCCESS) {
        for (p = list; p != NULL; p = p->next) {
            int matches = !adb ? (p->vid == 0x1b8e && p->pid == 0xc003 && !p->is_composite) :
                (p->vid == 0x18d1 && p->pid == 0x4e26 && p->is_composite &&
                 p->compatible_id && StrStrIA(p->compatible_id, "Class_ff&SubClass_42&Prot_01"));
            if (matches) {
                found = 1;
                result = install_one(p, dest, adb);
                if (result) break;
            }
        }
        wdi_destroy_list(list);
    }
    if (!found) {
        struct wdi_device_info device = {0};
        device.vid = adb ? 0x18d1 : 0x1b8e;
        device.pid = adb ? 0x4e26 : 0xc003;
        device.is_composite = adb;
        device.mi = adb ? 2 : 0;
        device.desc = adb ? "Station Mini ADB" : "Station Mini USB Boot";
        result = install_one(&device, dest, adb);
    }
    free(dest);
    fprintf(stderr, "STATION_DRIVER_RESULT=%d\n", result);
    fflush(stderr);
    return result ? 1 : 0;
}
