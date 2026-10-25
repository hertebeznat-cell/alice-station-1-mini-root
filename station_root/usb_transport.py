# SPDX-License-Identifier: MIT
# Optimus transport follows pyamlboot (Neil Armstrong / BayLibre, 2018).
# Modifications copyright (c) 2026 TrueKris and Station Mini Root contributors.
# Full MIT permission notice: licenses/pyamlboot-MIT.txt.
"""Optimus subset, following the local pyamlboot and partition dump scripts.

Only the system partition can be written. No erase-all or bootloader commands.
"""
from __future__ import annotations

import struct
import time
from functools import wraps
from pathlib import Path

SYSTEM_SIZE = 80 * 1024 * 1024
STOCK_SHA256 = "ce21c54bd9fdde0ab1489429a503d07fb4c8be3c8fc78a5b18e48dd283d32f6f"


class USBBackendError(RuntimeError):
    def __init__(self, operation):
        self.operation = operation
        super().__init__("USB operation is not supported: " + operation +
                         " (LIBUSB_ERROR_NOT_SUPPORTED). Reconnect the speaker and run dependency setup again.")


class BackendAdapter:
    # The stock WorldCup interface does not answer GET_CONFIGURATION reliably.
    # This is the compatibility behavior used by the local usb_backend.py.
    def __init__(self, backend):
        self.backend = backend

    def get_configuration(self, handle):
        return 1

    def __getattr__(self, name):
        value = getattr(self.backend, name)
        if not callable(value):
            return value
        @wraps(value)
        def call(*args, **kwargs):
            try:
                return value(*args, **kwargs)
            except NotImplementedError as exc:
                # PyUSB translates libusb -12 into NotImplementedError, not
                # USBError. Preserve the failing operation for driver repair.
                raise USBBackendError(name) from exc
        return call


def make_backend():
    import usb.backend.libusb1
    try:
        import libusb_package
        backend = usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)
    except ImportError:
        backend = usb.backend.libusb1.get_backend()
    if backend is None:
        raise RuntimeError("Не найден libusb. Установите зависимости из README.")
    # PyUSB caches a native context on first import. Setup may replace the
    # Windows driver afterwards; keep the cached DLL, but enumerate in a new
    # context for each connection attempt. Never reset a context with handles.
    return BackendAdapter(usb.backend.libusb1._LibUSB(backend.lib))


class Transport:
    def __init__(self, device):
        self.device = device
        self._epin = self._epout = None

    @classmethod
    def wait(cls, cancel, log=print, seconds=120):
        import usb.core
        import usb.util
        backend = make_backend()
        deadline = time.monotonic() + seconds
        log("Ожидаю 1B8E:C003. Теперь подключите USB-кабель, кнопки не нужны.")
        last_error = None
        while time.monotonic() < deadline:
            if cancel.is_set():
                raise InterruptedError("Ожидание отменено")
            devices = list(usb.core.find(find_all=True, idVendor=0x1B8E,
                                        idProduct=0xC003, backend=backend))
            if len(devices) > 1:
                raise RuntimeError("Подключите только одну станцию в режиме WorldCup.")
            if devices:
                transport = cls(devices[0])
                try:
                    ident = transport.identify()
                    transport.validate_identify(ident)
                    # Start communicating immediately, before the boot window closes.
                    transport.command("    low_power")
                    log(f"TPL захвачен: {ident.hex(' ')}. Якорь: USB-опрос каждые 0,5 с.")
                    return transport
                except usb.core.USBError as exc:
                    last_error = str(exc)
                    usb.util.dispose_resources(devices[0])
                except Exception:
                    usb.util.dispose_resources(devices[0])
                    raise
            cancel.wait(0.05)
        raise TimeoutError("Станция не обнаружена за 120 с. Проверьте USB-драйвер и кабель. "
                           + (last_error or ""))

    @staticmethod
    def validate_identify(ident):
        if len(ident) < 5 or ident[3] != 0x10 or ident[4] != 0:
            raise RuntimeError(f"Неподдерживаемый/заблокированный загрузчик: {ident.hex()}. "
                               "Нужен незаблокированный TPL, stage=0x10.")

    def identify(self):
        return bytes(self.device.ctrl_transfer(0xC0, 0x20, 0, 0, 8, timeout=1000))

    def keepalive(self):
        self.validate_identify(self.identify())

    def close(self):
        import usb.util
        usb.util.dispose_resources(self.device)

    def endpoints(self):
        import usb.util
        if self._epin is None:
            interface = self.device.get_active_configuration()[(0, 0)]
            self._epin = usb.util.find_descriptor(interface, custom_match=lambda ep:
                usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN)
            self._epout = usb.util.find_descriptor(interface, custom_match=lambda ep:
                usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT)
            if self._epin is None or self._epout is None:
                raise RuntimeError("Не найдены USB bulk endpoints")
        return self._epin, self._epout

    def command(self, command, timeout=15000):
        payload = command.encode("ascii") + b"\0"
        if len(payload) >= 128:
            raise ValueError("Слишком длинная команда TPL")
        self.device.ctrl_transfer(0x40, 0x34, 0, 2, payload, timeout=1000)
        deadline = time.monotonic() + timeout / 1000
        while True:
            response = bytes(self.device.read(0x81, 512, timeout=timeout)).rstrip(b"\0")
            if response.startswith(b"Continue:34"):
                if time.monotonic() > deadline:
                    raise TimeoutError(command)
                time.sleep(0.1)
                continue
            if response != b"success":
                raise RuntimeError(f"TPL отклонил {command!r}: {response!r}")
            return

    def read_system(self, output: Path, progress=lambda n, total: None):
        """Chunked 0x33 handshake exactly as alice_dump_partition.py."""
        import hashlib
        self.keepalive()
        digest = hashlib.sha256()
        received = 0
        epin, _ = self.endpoints()
        # Reserve destination before starting a transfer; never replace a backup.
        with output.open("xb") as stream:
            self.command(f"upload store system normal 0x{SYSTEM_SIZE:x}")
            while received < SYSTEM_SIZE:
                request = bytes(self.device.ctrl_transfer(0xC0, 0x33, 0, 0, 16, timeout=5000))
                if len(request) != 16:
                    raise RuntimeError("Повреждён заголовок чтения")
                magic, length, sequence, checksum = struct.unpack("<IIII", request)
                if magic != 0xEFE8 or not 0 < length <= 0x10000 or received + length > SYSTEM_SIZE:
                    raise RuntimeError("Некорректный размер блока чтения")
                block = bytearray()
                while len(block) < length:
                    part = bytes(epin.read(min(0x1000, length - len(block)), timeout=5000))
                    if not part or len(part) > length - len(block):
                        raise RuntimeError("Пустой/слишком длинный USB-пакет")
                    block.extend(part)
                stream.write(block)
                digest.update(block)
                received += length
                progress(received, SYSTEM_SIZE)
        return digest.hexdigest()

    def write_system(self, image: Path, progress=lambda n, total: None):
        """Normal full UBI container, not a naked UBIFS/rootfs download."""
        if image.stat().st_size != SYSTEM_SIZE:
            raise ValueError("Образ должен быть ровно 80 МиБ")
        self.keepalive()
        _, epout = self.endpoints()
        self.command("disk_initial 0", timeout=60000)
        payload = f"download store system normal 0x{SYSTEM_SIZE:x}\0".encode("ascii")
        self.device.ctrl_transfer(0x40, 0x30, 0, 1, payload, timeout=1000)
        status = bytes(self.device.ctrl_transfer(0xC0, 0x31, 0, 0, 64,
                                                timeout=60000)).rstrip(b"\0")
        if status != b"success":
            raise RuntimeError(f"TPL отклонил download: {status!r}")
        sent = 0
        with image.open("rb") as stream:
            for seq in range(SYSTEM_SIZE // 0x10000):
                data = stream.read(0x10000)
                if len(data) != 0x10000:
                    raise RuntimeError("Образ изменился во время записи")
                checksum = sum(struct.unpack("<16384I", data)) & 0xFFFFFFFF
                control = struct.pack("<IIIIHH", 0, len(data), seq, checksum, 0xEF, 0x200)
                self.device.ctrl_transfer(0x40, 0x32, 1, 0xFFFF,
                                          control.ljust(32, b"\0"), timeout=5000)
                if epout.write(data, timeout=15000) != len(data):
                    raise RuntimeError("Неполная USB-запись. Не перезагружайте станцию.")
                deadline = time.monotonic() + 60
                while True:
                    ack = bytes(self.device.read(0x81, 512, timeout=60000))
                    if ack.startswith(b"Continue:32") and time.monotonic() < deadline:
                        continue
                    if not ack.startswith(b"OK!!"):
                        raise RuntimeError(f"Ошибка подтверждения блока {seq}: {ack[:80]!r}")
                    break
                sent += len(data)
                progress(sent, SYSTEM_SIZE)
        self.command("download get_status", timeout=60000)

    def reboot(self):
        # A disconnect during reset is expected; other USB errors still fail.
        import usb.core
        try:
            self.device.ctrl_transfer(0x40, 0x34, 0, 2, b"reset\0", timeout=1000)
        except usb.core.USBError as exc:
            if exc.errno != 19 and getattr(exc, "backend_error_code", None) != -4:
                raise
