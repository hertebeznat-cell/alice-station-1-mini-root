"""Prepare a small Debian WSL builder; never modifies a station or removes a distro."""

from __future__ import annotations

import ctypes
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from .process_output import decode_output

MANAGED_PYTHON = "/opt/station-mini-root/venv/bin/python"
_launch_lock = threading.Lock()


class RestartRequired(RuntimeError):
    pass


class WSLUnavailable(RuntimeError):
    pass


def external_process(command, **kwargs):
    """Launch system tools without inheriting the frozen app's DLL search path."""
    env = dict(kwargs.get("env", os.environ))
    frozen = getattr(sys, "frozen", False)
    if frozen and sys.platform == "linux":
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = original
    kwargs["env"] = env
    if not frozen or sys.platform != "win32":
        return subprocess.Popen(command, **kwargs)
    import ntpath

    root = str(sys._MEIPASS)
    prefix = ntpath.normcase(ntpath.normpath(root))
    env["PATH"] = ";".join(
        part
        for part in env.get("PATH", "").split(";")
        if not (
            ntpath.normcase(ntpath.normpath(part)) == prefix
            or ntpath.normcase(ntpath.normpath(part)).startswith(prefix + "\\")
        )
    )
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    kernel.SetDllDirectoryW.restype = ctypes.c_int
    with _launch_lock:
        if not kernel.SetDllDirectoryW(None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return subprocess.Popen(command, **kwargs)
        finally:
            kernel.SetDllDirectoryW(root)


def run_capture(command, timeout=60):
    with external_process(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise
        return process.returncode, decode_output(stdout), decode_output(stderr)


def list_distros():
    if sys.platform != "win32" or not shutil.which("wsl.exe"):
        return []
    code, stdout, _ = run_capture(["wsl.exe", "--list", "--quiet"])
    if code:
        return []
    return [
        line.strip().lstrip("*").strip()
        for line in stdout.splitlines()
        if line.strip() and re.fullmatch(r"[\w. -]+", line.strip())
    ]


def default_distro():
    if sys.platform != "win32":
        return ""
    try:
        import winreg
        import uuid

        path = r"Software\Microsoft\Windows\CurrentVersion\Lxss"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            identifier = str(winreg.QueryValueEx(key, "DefaultDistribution")[0])
        uuid.UUID(identifier.strip("{}"))
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path + "\\" + identifier) as key:
            return str(winreg.QueryValueEx(key, "DistributionName")[0])
    except (ImportError, OSError, ValueError):
        return ""


def probe_distro(name):
    if name.lower().startswith(("docker-desktop", "rancher-desktop", "podman-machine")):
        return "unsupported"
    try:
        code, output, _ = run_capture(wsl_prefix(name) + ["cat", "/etc/os-release"], timeout=60)
        if code:
            return "unknown"
        fields = {}
        for line in output.splitlines():
            key, separator, value = line.partition("=")
            if separator and key in ("ID", "ID_LIKE"):
                fields[key] = " ".join(shlex.split(value))
        if not fields.get("ID"):
            return "unknown"
        family = set((fields.get("ID", "") + " " + fields.get("ID_LIKE", "")).lower().split())
        return "compatible" if family & {"debian", "ubuntu"} else "unsupported"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return "unknown"


def discover_distros(preferred="", names=None):
    names = list_distros() if names is None else list(names)
    systems, selected = {}, ""
    # A usable saved/default distro is enough. Starting an unrelated imported
    # distro can be slow or hang WSL, so do not probe it after finding a builder.
    for name in dict.fromkeys((preferred, default_distro(), *names)):
        if name not in names:
            continue
        systems[name] = probe_distro(name)
        if systems[name] == "compatible":
            selected = name
            break
    available = [name for name in names if systems.get(name) == "compatible"]
    return {
        "installed": names,
        "available": available,
        "selected": selected,
        "unknown": [name for name in names if systems.get(name) == "unknown"],
        "unchecked": [name for name in names if name not in systems],
    }


def installed_distro(preferred="", names=None):
    discovery = discover_distros(preferred, names)
    if not discovery["selected"] and discovery["unknown"]:
        raise RuntimeError(
            "Cannot inspect existing WSL distributions. Retry detection before installing another: "
            + ", ".join(discovery["unknown"])
        )
    return discovery["selected"]


def select_distro(names, preferred=""):
    if preferred:
        if preferred not in names:
            raise RuntimeError("Selected WSL distribution is not installed: " + preferred)
        return preferred
    # Do not provision unrelated Docker / appliance distributions.
    for name in names:
        if name.lower() == "debian":
            return name
    for name in names:
        if name.lower().startswith("ubuntu"):
            return name
    return ""


def run_elevated(executable, arguments, visible=False):
    """UAC is handled by Windows, not accepted programmatically."""
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    info = SHELLEXECUTEINFOW()
    info.cbSize, info.fMask = ctypes.sizeof(info), 0x40
    info.lpVerb, info.lpFile = "runas", executable
    info.lpParameters = subprocess.list2cmdline(arguments)
    info.nShow = 1 if visible else 0
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        raise OSError(ctypes.get_last_error(), "Windows declined installation / UAC")
    try:
        if kernel.WaitForSingleObject(info.hProcess, 30 * 60 * 1000) != 0:
            raise TimeoutError("Windows installer is still running. Reopen setup after it finishes.")
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value
    finally:
        kernel.CloseHandle(info.hProcess)


def elevate_wsl_install():
    return run_elevated("wsl.exe", ["--install", "--no-distribution", "--no-launch"])


def wsl_prefix(distro):
    if not distro:
        raise ValueError("A WSL distribution must be selected")
    return ["wsl.exe", "--distribution", distro, "--user", "root", "--exec"]


def prepare_environment(config, log, run_process, resources):
    """Called only after the user chooses Setup; returns the selected distro."""
    if sys.platform != "win32":
        raise RuntimeError("On Linux, run bash scripts/setup-linux.sh once, then use the application.")
    names = list_distros()
    if not names:
        status = run_capture(["wsl.exe", "--status"])[0] if shutil.which("wsl.exe") else 1
        if status:
            log("Installing WSL. Accept the Windows administrator prompt to continue.")
            code = elevate_wsl_install()
            if code not in (0, 3010):
                raise RuntimeError(f"WSL installer exited with code {code}")
            # Enabling optional Windows features can need reboot even on exit 0.
            raise RestartRequired(
                "WSL installation requested. Restart Windows if prompted, then run Setup again. "
                "No firmware operation has started."
            )
    selected = installed_distro(str(config.get("distro", "")).strip(), names)
    if not selected:
        log("Installing Debian for WSL: no desktop, builder packages only.")
        run_process(["wsl.exe", "--install", "--distribution", "Debian", "--no-launch", "--web-download"])
        names = list_distros()
        if "Debian" not in names:
            raise RestartRequired("Debian is not ready yet. Complete WSL installation and run Setup again.")
        selected = "Debian"
    prefix = wsl_prefix(selected)
    script = Path(resources) / "scripts" / "setup-wsl-builder.sh"
    code, converted, error = run_capture(prefix + ["wslpath", "-a", "-u", str(script)])
    if code:
        raise RuntimeError(error or converted)
    log("Preparing the Linux builder in WSL: " + selected)
    run_process(prefix + ["bash", converted.strip()])
    return selected


def builder_ready(distro, strict=False, log=lambda message: None):
    # Explicit result markers distinguish missing packages from a failed WSL
    # launch. A launch timeout must never trigger another apt/distro install.
    check = "import ubireader; import shutil; assert all(shutil.which(p) for p in ('mkfs.ubifs','ubinize','fakeroot'))"
    script = (
        "if test -x "
        + shlex.quote(MANAGED_PYTHON)
        + " && "
        + shlex.quote(MANAGED_PYTHON)
        + " -c "
        + shlex.quote(check)
        + "; then "
        "printf '\\nSTATION_BUILDER_READY\\n'; else printf '\\nSTATION_BUILDER_MISSING\\n'; fi"
    )
    log("Checking the selected WSL builder: " + distro)
    detail = ""
    for attempt in range(2):
        try:
            code, output, error = run_capture(wsl_prefix(distro) + ["/bin/sh", "-c", script], timeout=90)
            if code == 0 and "STATION_BUILDER_READY" in output.splitlines():
                return True
            if code == 0 and "STATION_BUILDER_MISSING" in output.splitlines():
                return False
            detail = (error.strip() or output.strip() or f"exit={code}")[-2000:]
        except subprocess.TimeoutExpired:
            detail = "WSL did not respond within 90 seconds"
        except OSError as exc:
            detail = str(exc)
        if attempt == 0:
            log("WSL did not respond correctly. Retrying the same distribution: " + distro)
    if strict:
        raise WSLUnavailable("Cannot start the selected WSL builder: " + distro + "\n" + detail)
    return False
