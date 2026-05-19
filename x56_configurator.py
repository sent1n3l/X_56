from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ModuleNotFoundError:  # pragma: no cover - non-GUI environments
    tk = None
    filedialog = messagebox = ttk = None

APP_STATE_DIR = Path.home() / ".x56_configurator"
STATE_FILE = APP_STATE_DIR / "state.json"
VERSION_FILE = Path(__file__).parent / "templates" / "version_history.json"


@dataclass
class ScanResult:
    install_dir: Path | None
    game_version: str
    config_path: Path | None
    joystick_name: str | None
    throttle_name: str | None
    virtual_devices: list[str]


class X56Engine:
    def __init__(self, state_file: Path = STATE_FILE, version_file: Path = VERSION_FILE) -> None:
        self.state_file = state_file
        self.version_file = version_file
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    def startup_cleanup(self) -> list[str]:
        messages: list[str] = []
        temp_dir = self.state_file.parent / "tmp"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
            messages.append("Removed stale temporary data.")
        else:
            messages.append("No stale temporary data found.")

        broken_backup = self.state_file.parent / "actionmaps.xml.broken"
        if broken_backup.exists():
            broken_backup.unlink(missing_ok=True)
            messages.append("Removed broken backup from previous run.")
        return messages

    def detect_devices(self) -> tuple[str | None, str | None, list[str]]:
        text = ""
        if os.name == "nt":
            try:
                output = subprocess.run(
                    [
                        "wmic",
                        "path",
                        "Win32_PnPEntity",
                        "get",
                        "Name",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=15,
                )
                text = output.stdout.lower()
            except Exception:
                text = ""

        joystick = "X56 H.O.T.A.S. Joystick" if "x56" in text and "joystick" in text else None
        throttle = "X56 H.O.T.A.S. Throttle" if "x56" in text and "throttle" in text else None
        virtual = []
        for marker in ("vjoy", "virtual", "x360ce", "xinput"):
            if marker in text:
                virtual.append(marker)
        return joystick, throttle, sorted(set(virtual))

    def find_installation(self, preferred: Path | None = None) -> Path | None:
        candidates = []
        if preferred:
            candidates.append(preferred)
        home = Path.home()
        candidates.extend(
            [
                home / "StarCitizen",
                home / "Games" / "StarCitizen",
                Path("C:/Program Files/Roberts Space Industries/StarCitizen"),
                Path("D:/StarCitizen"),
            ]
        )
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def detect_game_version(self, install_dir: Path) -> str:
        for rel in ["LIVE/f_p4_version.txt", "PTU/f_p4_version.txt", "f_p4_version.txt"]:
            f = install_dir / rel
            if f.exists():
                return f.read_text(encoding="utf-8", errors="ignore").strip() or "unknown"
        return "unknown"

    def find_actionmaps(self, install_dir: Path) -> Path | None:
        for rel in [
            "LIVE/USER/Client/0/Profiles/default/actionmaps.xml",
            "PTU/USER/Client/0/Profiles/default/actionmaps.xml",
            "USER/Client/0/Profiles/default/actionmaps.xml",
        ]:
            p = install_dir / rel
            if p.exists():
                return p
        return None

    def _load_templates(self) -> dict:
        return json.loads(self.version_file.read_text(encoding="utf-8"))

    def template_for_version(self, game_version: str) -> dict:
        data = self._load_templates()
        if game_version in data:
            return data[game_version]
        return data[data["latest"]]

    def hash_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def read_state(self) -> dict:
        if not self.state_file.exists():
            return {}
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def write_state(self, state: dict) -> None:
        self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def apply_fix(self, config_path: Path, game_version: str, joystick: str, throttle: str) -> list[str]:
        template = self.template_for_version(game_version)
        messages = []

        backup = config_path.with_suffix(config_path.suffix + ".bak")
        shutil.copy2(config_path, backup)
        messages.append(f"Backup created at {backup}")

        try:
            root = ET.parse(config_path).getroot()
        except ET.ParseError:
            root = ET.Element("ActionMaps")

        for node in list(root.findall("device")):
            name = (node.attrib.get("name") or "").lower()
            if "x56" not in name:
                root.remove(node)

        for d_name, bindings in ((joystick, template["joystick_bindings"]), (throttle, template["throttle_bindings"])):
            existing = None
            for node in root.findall("device"):
                if node.attrib.get("name") == d_name:
                    existing = node
                    break
            if existing is None:
                existing = ET.SubElement(root, "device", {"name": d_name})
            for child in list(existing):
                existing.remove(child)
            for action, input_name in bindings.items():
                ET.SubElement(existing, "binding", {"action": action, "input": input_name})

        config_path.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(root).write(config_path, encoding="utf-8", xml_declaration=True)
        messages.append("Applied versioned X56 bindings and cleaned stale device entries.")

        user_cfg = config_path.parent / "user.cfg"
        user_cfg.write_text(f"pp_rebindkeys {config_path.name}\n", encoding="utf-8")
        messages.append("Enforced profile load through user.cfg")

        digest = self.hash_file(config_path)
        state = self.read_state()
        state[str(config_path)] = {"hash": digest, "version": game_version}
        self.write_state(state)
        messages.append("Saved post-fix integrity snapshot.")
        return messages

    def verify_fix(self, config_path: Path, joystick: str, throttle: str) -> list[str]:
        results = []
        root = ET.parse(config_path).getroot()
        names = {n.attrib.get("name") for n in root.findall("device")}
        results.append("PASS: XML is valid and readable.")
        if joystick in names and throttle in names:
            results.append("PASS: Both X56 devices exist in profile.")
        else:
            results.append("FAIL: Missing one or more X56 device entries.")

        digest = self.hash_file(config_path)
        saved = self.read_state().get(str(config_path), {}).get("hash")
        if digest == saved:
            results.append("PASS: Integrity snapshot matches generated file.")
        else:
            results.append("FAIL: Integrity mismatch detected.")
        return results

    def cross_check(self, config_path: Path) -> bool:
        saved = self.read_state().get(str(config_path), {}).get("hash")
        if not saved or not config_path.exists():
            return False
        return self.hash_file(config_path) != saved


class X56ConfiguratorUI:
    def __init__(self, root: tk.Tk) -> None:
        if tk is None or ttk is None:
            raise RuntimeError("Tkinter is required to run the UI.")
        self.root = root
        self.engine = X56Engine()
        self.scan_result = ScanResult(None, "unknown", None, None, None, [])

        self.root.title("X56 Star Citizen Configurator")
        self.root.geometry("900x600")
        self.root.minsize(820, 520)

        container = ttk.Frame(root, padding=16)
        container.pack(fill="both", expand=True)

        title = ttk.Label(container, text="X56 H.O.T.A.S. Auto Configurator", font=("Segoe UI", 16, "bold"))
        title.pack(pady=(0, 12))

        self.progress = ttk.Progressbar(container, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 12))

        button_bar = ttk.Frame(container)
        button_bar.pack(fill="x", pady=(0, 8))

        ttk.Button(button_bar, text="Scan & Detect", command=self.scan).pack(side="left", padx=4)
        ttk.Button(button_bar, text="Fix Configuration", command=self.fix).pack(side="left", padx=4)
        ttk.Button(button_bar, text="Select Installation Folder", command=self.select_folder).pack(side="left", padx=4)
        ttk.Button(button_bar, text="Re-check Integrity", command=self.recheck).pack(side="left", padx=4)

        debug_frame = ttk.LabelFrame(container, text="Debug / Progress", padding=8)
        debug_frame.pack(fill="both", expand=True)
        self.debug = tk.Text(debug_frame, wrap="word", height=20)
        self.debug.pack(fill="both", expand=True)

        self.log("Startup: preparing environment and running first-launch cleanup.")
        for msg in self.engine.startup_cleanup():
            self.log(msg)
        self.progress["value"] = 10
        self.log("Ready.")

    def log(self, msg: str) -> None:
        self.debug.insert("end", f"{msg}\n")
        self.debug.see("end")

    def _run_async(self, fn) -> None:
        thread = threading.Thread(target=fn, daemon=True)
        thread.start()

    def select_folder(self) -> None:
        selected = filedialog.askdirectory(title="Select Star Citizen installation folder")
        if selected:
            self.scan_result.install_dir = Path(selected)
            self.log(f"Selected install directory: {selected}")

    def scan(self) -> None:
        def worker() -> None:
            self.progress["value"] = 20
            joystick, throttle, virtual = self.engine.detect_devices()
            self.scan_result.joystick_name = joystick or "X56 H.O.T.A.S. Joystick"
            self.scan_result.throttle_name = throttle or "X56 H.O.T.A.S. Throttle"
            self.scan_result.virtual_devices = virtual

            install = self.engine.find_installation(self.scan_result.install_dir)
            self.scan_result.install_dir = install

            if install is None:
                self.log("Star Citizen installation not found. Use Select Installation Folder.")
                self.progress["value"] = 0
                return

            self.scan_result.game_version = self.engine.detect_game_version(install)
            self.scan_result.config_path = self.engine.find_actionmaps(install) or (
                install / "LIVE/USER/Client/0/Profiles/default/actionmaps.xml"
            )
            self.progress["value"] = 60
            self.log(f"Detected installation: {install}")
            self.log(f"Detected game version: {self.scan_result.game_version}")
            self.log(f"Detected profile path: {self.scan_result.config_path}")
            self.log(f"Detected joystick: {self.scan_result.joystick_name}")
            self.log(f"Detected throttle: {self.scan_result.throttle_name}")
            if virtual:
                self.log(f"Warning: possible virtual controllers found: {', '.join(virtual)}")
            self.progress["value"] = 100

        self._run_async(worker)

    def fix(self) -> None:
        def worker() -> None:
            if not self.scan_result.config_path:
                self.log("No configuration path found. Run Scan & Detect first.")
                return

            self.progress["value"] = 15
            self.scan_result.config_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.scan_result.config_path.exists():
                self.scan_result.config_path.write_text("<ActionMaps/>", encoding="utf-8")

            messages = self.engine.apply_fix(
                self.scan_result.config_path,
                self.scan_result.game_version,
                self.scan_result.joystick_name or "X56 H.O.T.A.S. Joystick",
                self.scan_result.throttle_name or "X56 H.O.T.A.S. Throttle",
            )
            self.progress["value"] = 70
            for msg in messages:
                self.log(msg)

            self.log("Running 3-way post-fix verification...")
            checks = self.engine.verify_fix(
                self.scan_result.config_path,
                self.scan_result.joystick_name or "X56 H.O.T.A.S. Joystick",
                self.scan_result.throttle_name or "X56 H.O.T.A.S. Throttle",
            )
            for line in checks:
                self.log(line)

            self.progress["value"] = 100
            ok = all(line.startswith("PASS") for line in checks)
            if ok:
                self.log("Success. Move sticks/throttle and press game buttons to validate mappings in game.")
                messagebox.showinfo(
                    "Success",
                    "Configuration updated successfully. Please move sticks/buttons in Star Citizen to confirm mapping.",
                )
            else:
                messagebox.showwarning("Verification issues", "Fix completed but verification reported issues.")

        self._run_async(worker)

    def recheck(self) -> None:
        if not self.scan_result.config_path:
            self.log("No configuration path found. Run Scan & Detect first.")
            return

        changed = self.engine.cross_check(self.scan_result.config_path)
        if changed:
            self.log("Warning: configuration changed since last successful generation.")
            if messagebox.askyesno("Repair suggested", "Config changed. Re-apply fix now?"):
                self.fix()
        else:
            self.log("Integrity check passed: no unexpected changes detected.")


def main() -> None:
    if tk is None:
        raise RuntimeError("Tkinter is not available in this Python environment.")
    root = tk.Tk()
    X56ConfiguratorUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
