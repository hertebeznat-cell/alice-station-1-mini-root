"""ADB access to a rooted speaker with the workspace's private key."""

from __future__ import annotations

import os
import socket
import subprocess
import contextlib

# The user's normal ADB server. This app reuses it but never restarts or stops it.
STANDARD_PORT = 5037
# A private server started with the workspace key when the standard one does
# not already see the speaker. Opening a second server on the same legacy USB
# interface can hang, so the standard server is always tried first.
PRIVATE_PORT = 5038
PROBE_TIMEOUT = 5
COMMAND_TIMEOUT = 20

# Hide the console window of child processes on Windows.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_hidden(args, **kwargs):
    return subprocess.run(args, creationflags=NO_WINDOW, **kwargs)


def port_open(port):
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def is_station(compatible):
    """Station Mini 1 reports an Amlogic board in /proc/device-tree/compatible."""
    return "amlogic" in compatible.lower()


def device_serials(listing):
    return [
        line.split()[0] for line in listing.splitlines()[1:] if len(line.split()) >= 2 and line.split()[1] == "device"
    ]


class AdbSession:
    def __init__(self, adb, key, port):
        self.adb, self.port = adb, port
        self.env = os.environ.copy()
        self.env["ADB_VENDOR_KEYS"] = str(key)
        self.env.pop("ADB_SERVER_SOCKET", None)
        if port == PRIVATE_PORT:
            self.env["ADB_SERVER_SOCKET"] = f"tcp:localhost:{PRIVATE_PORT}"

    @classmethod
    def open(cls, adb, key, serial=""):
        """Use the standard server if it already sees an authorized speaker."""
        standard = cls(adb, key, STANDARD_PORT)
        if port_open(STANDARD_PORT) and standard._sees_station(serial):
            return standard
        if port_open(PRIVATE_PORT):
            raise RuntimeError(f"Порт {PRIVATE_PORT} уже занят. Освободите его перед проверкой root.")
        return cls(adb, key, PRIVATE_PORT)

    @property
    def private(self):
        return self.port == PRIVATE_PORT

    def _sees_station(self, serial):
        try:
            for candidate in device_serials(self._call(["devices", "-l"], PROBE_TIMEOUT).stdout):
                if serial and candidate != serial:
                    continue
                identity = self._call(["-s", candidate, "shell", "cat /proc/device-tree/compatible"], PROBE_TIMEOUT)
                if identity.returncode == 0 and is_station(identity.stdout):
                    return True
        except (OSError, subprocess.TimeoutExpired):
            pass
        return False

    def _call(self, args, timeout):
        return run_hidden(
            [self.adb, "-P", str(self.port), *args], env=self.env, text=True, capture_output=True, timeout=timeout
        )

    def run(self, *args):
        result = self._call(list(args), COMMAND_TIMEOUT)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Ошибка ADB")
        return result.stdout

    def shell(self, serial, command):
        return self.run("-s", serial, "shell", command)

    def stations(self, serials):
        """Serials among several devices that are Station Mini 1 boards."""
        found = []
        for candidate in serials:
            try:
                if is_station(self.shell(candidate, "cat /proc/device-tree/compatible")):
                    found.append(candidate)
            except RuntimeError:
                pass
        return found

    def close(self):
        """Stop only the private server this session may have started."""
        if self.private:
            with contextlib.suppress(Exception):
                self.run("kill-server")
