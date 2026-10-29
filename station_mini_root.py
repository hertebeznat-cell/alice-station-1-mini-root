#!/usr/bin/env python3
import sys


def check_usb(report_path):
    """Load the USB runtime only: no enumeration, device opens or transfers."""
    import json
    from pathlib import Path
    from station_root import __version__
    from station_root.usb_transport import make_backend

    report = {
        "version": __version__,
        "ready": False,
        "frozen": bool(getattr(sys, "frozen", False)),
        "bundle_root": str(getattr(sys, "_MEIPASS", "")),
    }
    try:
        backend = make_backend()
        report.update(ready=True, library=str(backend.backend.lib._name))
    except Exception as exc:
        report["error"] = str(exc)
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ready"] else 1


def check_wsl(distro, report_path):
    """Read-only diagnostic also usable to test the frozen Windows executable."""
    import json
    from pathlib import Path
    from station_root.environment import builder_ready

    logs = []
    report = {"distro": distro, "ready": False, "logs": logs}
    try:
        report["ready"] = builder_ready(distro, strict=True, log=logs.append)
    except Exception as exc:
        report["error"] = str(exc)
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--check-usb":
        raise SystemExit(check_usb(sys.argv[2]))
    if len(sys.argv) == 4 and sys.argv[1] == "--check-wsl":
        raise SystemExit(check_wsl(sys.argv[2], sys.argv[3]))
    from station_root.gui import main

    main()
