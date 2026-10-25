#!/usr/bin/env python3
import sys


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
    if len(sys.argv) == 4 and sys.argv[1] == "--check-wsl":
        raise SystemExit(check_wsl(sys.argv[2], sys.argv[3]))
    from station_root.gui import main
    main()
