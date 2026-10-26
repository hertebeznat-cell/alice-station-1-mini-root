from __future__ import annotations
import json
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from .workflow import Workflow, resource_root
from .dependencies import Dependencies, default_workspace
from .i18n import tr, translate_message


class App:
    def __init__(self, root):
        self.root = root
        self.settings_path = Path(os.environ.get("STATION_ROOT_CONFIG_DIR", Path.home() / ".config/StationMiniRoot")) / "settings.json"
        try:
            settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
        self.language = settings.get("language", "ru") if settings.get("language", "ru") in ("ru", "en") else "ru"
        self.events, self.jobs = queue.Queue(), queue.Queue()
        self.busy, self.connected, self.dependencies_ready = False, False, False
        self.inspect_pending = True
        self.raw_logs = []
        self.last_progress = 0
        self.workflow = Workflow(self.log, self.progress)
        self.workflow.phase = lambda phase: self.events.put(("phase", phase))
        self.dependencies = Dependencies(self.workflow, resource_root())
        self.values = {key: tk.StringVar(value=settings.get(key, default)) for key, default in {
            "work": str(default_workspace()), "adb": "", "distro": "", "builder_python": ""}.items()}
        self.frame = None
        root.title("Station Mini Root 0.3.11 · YNDX-0004")
        root.geometry("840x640")
        root.minsize(760, 580)
        style = ttk.Style()
        if "clam" in style.theme_names(): style.theme_use("clam")
        style.configure("TButton", padding=(14, 12))
        style.configure("Title.TLabel", font=("Segoe UI", 24, "bold"))
        self.build_ui()
        self.thread = threading.Thread(target=self.worker, daemon=True)
        self.thread.start()
        self.jobs.put(("inspect", self.config()))
        root.after(100, self.poll)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    def text(self, text):
        return tr(text, self.language)

    def config(self):
        return {**{key: value.get() for key, value in self.values.items()},
                "block_updates": True, "_managed_dependencies": True}

    def save_settings(self):
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps({"language": self.language,
            **{key: value.get() for key, value in self.values.items()}}, ensure_ascii=False, indent=2), encoding="utf-8")

    def build_ui(self):
        if self.frame: self.frame.destroy()
        self.frame = frame = ttk.Frame(self.root, padding=24)
        frame.pack(fill="both", expand=True)
        header = ttk.Frame(frame)
        header.pack(fill="x")
        ttk.Label(header, text="Station Mini Root", style="Title.TLabel").pack(side="left")
        self.language_choice = ttk.Combobox(header, state="readonly", width=13, values=("Русский", "English"))
        self.language_choice.set("Русский" if self.language == "ru" else "English")
        self.language_choice.pack(side="right")
        self.language_choice.bind("<<ComboboxSelected>>", self.change_language)
        ttk.Label(frame, text=self.text("Yandex Station Mini 1 · YNDX-0004 · Windows / Linux")).pack(anchor="w", pady=(6, 12))
        ttk.Label(frame, text=self.text("First install dependencies. Then choose root or restore. All paths are automatic."), wraplength=780).pack(anchor="w")
        self.discovery_text = tk.StringVar(value=self.text("Looking for installed tools and WSL..."))
        ttk.Label(frame, textvariable=self.discovery_text, wraplength=780, foreground="#4a6678").pack(anchor="w", pady=10)
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(4, 12))
        self.buttons = []
        for column, (label, command) in enumerate((("1. Install all dependencies", "dependencies"),
                ("2. Install root", "install"), ("3. Restore backup", "restore"))):
            button = ttk.Button(actions, text=self.text(label), command=lambda c=command: self.start(c))
            button.grid(row=0, column=column, sticky="ew", padx=(0, 8))
            actions.columnconfigure(column, weight=1)
            self.buttons.append(button)
        ttk.Label(frame, text=self.text("Root installation includes a backup and disables automatic Yandex updates."), wraplength=780).pack(anchor="w")
        ttk.Label(frame, text=self.text("Homebrew is installed automatically. The stock Alice service stays enabled."), wraplength=780).pack(anchor="w", pady=(4, 0))
        self.homebrew_button = ttk.Button(frame, text=self.text("Update Homebrew only (ADB root)"),
                                         command=lambda: self.start("homebrew"))
        self.homebrew_button.pack(anchor="w", pady=(5, 0))
        ttk.Label(frame, text=self.text("Do not disconnect power during writing or read-back verification."), wraplength=780,
                  foreground="#945400").pack(anchor="w", pady=(5, 12))
        self.status = tk.StringVar(value=self.text("Looking for installed tools and WSL...") if self.inspect_pending else self.text("Ready"))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Label(row, textvariable=self.status, wraplength=570).pack(side="left")
        self.cancel_button = ttk.Button(row, text=self.text("Cancel waiting"), state="disabled", command=self.workflow.cancel.set)
        self.cancel_button.pack(side="right")
        self.bar = ttk.Progressbar(frame, maximum=100)
        self.bar.pack(fill="x", pady=8)
        self.output = ScrolledText(frame, height=10, state="disabled", font=("Consolas", 10), background="#16212c", foreground="#d7e5ee")
        self.output.pack(fill="both", expand=True)
        self.render_logs()
        self.set_busy(self.busy)
        if hasattr(self, "last_inspection"): self.show_inspection(self.last_inspection)

    def change_language(self, event=None):
        self.language = "ru" if self.language_choice.get() == "Русский" else "en"
        self.save_settings()
        self.build_ui()

    def set_busy(self, busy):
        self.busy = busy
        self.buttons[0]["state"] = "disabled" if busy or self.inspect_pending else "normal"
        for button in self.buttons[1:]:
            button["state"] = "normal" if self.dependencies_ready and not busy and not self.inspect_pending else "disabled"
        self.homebrew_button["state"] = "disabled" if busy or self.inspect_pending else "normal"
        self.language_choice["state"] = "disabled" if busy else "readonly"

    def show_inspection(self, result):
        self.last_inspection = result
        for name, value in result["config"].items():
            if name in self.values: self.values[name].set(value)
        self.dependencies_ready = result["ready"]
        self.inspect_pending = False
        distro = result["config"].get("distro", "")
        tools = self.text("Dependencies ready") if result["ready"] else self.text("Install dependencies to unlock root and restore")
        self.discovery_text.set(tools + (" · WSL: " + distro if distro else ""))
        self.status.set(self.text("Ready"))
        self.set_busy(self.busy)
        self.save_settings()

    def log(self, text): self.events.put(("log", str(text)))

    def render_logs(self):
        self.output["state"] = "normal"
        self.output.delete("1.0", "end")
        for stamp, text in self.raw_logs: self.output.insert("end", stamp + translate_message(text, self.language) + "\n")
        self.output.see("end")
        self.output["state"] = "disabled"

    def progress(self, current, total):
        now = time.monotonic()
        if current == total or now - self.last_progress > 0.1:
            self.events.put(("progress", (current, total)))
            self.last_progress = now

    def start(self, command):
        if self.busy or self.inspect_pending: return
        if command not in ("dependencies", "homebrew") and not self.dependencies_ready:
            messagebox.showinfo(self.text("Dependencies"), self.text("Install dependencies to unlock root and restore"))
            return
        config = self.config()
        if command == "homebrew":
            if not messagebox.askyesno(self.text("Update Homebrew only (ADB root)"),
                    self.text("Connect the rooted speaker in normal ADB mode. Only Homebrew will be updated; system firmware and plugin settings are preserved.")):
                return
        if command == "install":
            if not messagebox.askyesno(self.text("Install root"), self.text("Connected device is Mini 1 (YNDX-0004). Continue?") + "\n\n" +
                    self.text("The app will save a backup, build and write the image, verify it, reboot and check root.")): return
        if command == "restore":
            try: stored = json.loads((Path(config["work"]) / "session.json").read_text(encoding="utf-8"))
            except (OSError, ValueError): stored = {}
            if stored.get("restore_eligible"):
                config["use_session"] = True
            else:
                backup = filedialog.askopenfilename(title=self.text("Restore selected backup…"), initialdir=config["work"],
                    filetypes=[("System backup", "*.bin"), ("All files", "*.*")])
                if not backup: return
                config["backup"] = backup
            if not messagebox.askyesno(self.text("3. Restore backup"), self.text("Restore this system backup?")): return
        self.workflow.cancel.clear()
        self.set_busy(True)
        self.status.set(self.text("Operation is running"))
        self.jobs.put((command, config))

    def worker(self):
        while True:
            try: command, config = self.jobs.get(timeout=0.5)
            except queue.Empty:
                try: self.workflow.heartbeat()
                except Exception as exc:
                    self.log("USB connection lost: " + str(exc))
                    self.workflow.close()
                    self.events.put(("connected", False))
                continue
            if command == "shutdown":
                self.workflow.close()
                return
            try:
                if command == "inspect":
                    self.events.put(("inspection", self.dependencies.inspect(config)))
                    continue
                if command == "dependencies":
                    self.events.put(("dependency_complete", self.dependencies.install(config)))
                elif command == "install": self.workflow.install(config)
                elif command == "homebrew": self.workflow.update_homebrew(config)
                elif command == "restore":
                    if config.get("use_session"): self.workflow.unroot(config)
                    else:
                        if self.workflow.transport is None: self.workflow.connect()
                        self.workflow.restore(config, config["backup"])
                        self.workflow.reboot()
                self.events.put(("done", None))
            except Exception as exc:
                self.log("ERROR: " + str(exc))
                if command == "dependencies": self.events.put(("dependency_failed", None))
                self.events.put(("inspect_error" if command == "inspect" else "error", str(exc)))
            finally: self.events.put(("connected", self.workflow.transport is not None))

    def poll(self):
        while True:
            try: event, value = self.events.get_nowait()
            except queue.Empty: break
            if event == "log":
                stamp = time.strftime("%H:%M:%S ")
                self.raw_logs.append((stamp, value))
                self.output["state"] = "normal"
                self.output.insert("end", stamp + translate_message(value, self.language) + "\n")
                self.output.see("end")
                self.output["state"] = "disabled"
            elif event == "progress":
                current, total = value
                self.bar["value"] = 100 * current / total
                self.status.set(f"{100 * current / total:.0f}%")
            elif event == "inspection": self.show_inspection(value)
            elif event == "dependency_complete": self.show_inspection({"config": value, "ready": True})
            elif event == "dependency_failed":
                self.dependencies_ready = False
                if hasattr(self, "last_inspection"):
                    self.last_inspection = {**self.last_inspection, "ready": False}
                self.set_busy(self.busy)
            elif event == "inspect_error":
                self.inspect_pending = False
                self.dependencies_ready = False
                self.discovery_text.set(self.text("Install dependencies to unlock root and restore"))
                self.set_busy(False)
            elif event == "connected": self.connected = value
            elif event == "phase": self.cancel_button["state"] = "normal" if value == "waiting" else "disabled"
            elif event in ("done", "error"):
                self.set_busy(False)
                self.status.set(self.text("Ready" if event == "done" else "Error — see log"))
                self.cancel_button["state"] = "disabled"
                if event == "error": messagebox.showerror(self.text("Operation stopped"), translate_message(value, self.language))
        self.root.after(100, self.poll)

    def on_close(self):
        if self.busy:
            messagebox.showinfo(self.text("Operation is running"), self.text("Wait for completion. USB waiting can be cancelled."))
            return
        if self.connected and not messagebox.askyesno(self.text("USB anchor"), self.text("Close the application and release the USB anchor?")): return
        self.save_settings()
        self.jobs.put(("shutdown", {}))
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
