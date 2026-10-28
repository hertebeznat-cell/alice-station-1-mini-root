"""Local, per-run logs. No upload or account/config collection."""
from datetime import datetime
import platform
import threading
from pathlib import Path


def header(version):
    return f"Station Mini Root {version}\nOS: {platform.platform()}\n\n"


class Journal:
    def __init__(self, folder, version):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / ("station-mini-root-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".log")
        self.path.write_text(header(version), encoding="utf-8")
        self.lock = threading.Lock()

    def append(self, text):
        with self.lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(datetime.now().strftime("%H:%M:%S ") + str(text) + "\n")
