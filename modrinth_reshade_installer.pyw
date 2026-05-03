import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText


RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
MODRINTH_DIR = Path(os.environ["APPDATA"]) / "ModrinthApp"
DB_PATH = MODRINTH_DIR / "app.db"
PROFILES_DIR = MODRINTH_DIR / "profiles"
JAVA_VERSIONS_DIR = MODRINTH_DIR / "meta" / "java_versions"
PRESET_DIR = RESOURCE_DIR / "kyubus-current-preset"
RESHADE_HOME = "https://reshade.me/index.php"
HTTP_HEADERS = {"User-Agent": "ModrinthReShadeInstaller/1.0"}


def fetch_profiles():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT path, name, game_version, mod_loader, override_java_path
            FROM profiles
            ORDER BY name COLLATE NOCASE
            """
        ).fetchall()
    finally:
        conn.close()

    profiles = []
    for row in rows:
        profile_dir = PROFILES_DIR / row["path"]
        profiles.append(
            {
                "path_key": row["path"],
                "name": row["name"],
                "game_version": row["game_version"],
                "mod_loader": row["mod_loader"],
                "override_java_path": row["override_java_path"],
                "profile_dir": profile_dir,
            }
        )
    return profiles


def infer_java_major(game_version):
    match = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", game_version or "")
    if not match:
        return 17

    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3) or 0)

    if major >= 2:
        return 21
    if major == 1:
        if minor >= 21:
            return 21
        if minor == 20 and patch >= 5:
            return 21
        if minor >= 17:
            return 17
    return 8


def find_shared_java_runtime(java_major):
    candidates = sorted(JAVA_VERSIONS_DIR.glob(f"zulu{java_major}*"), key=lambda item: item.name, reverse=True)
    for candidate in candidates:
        javaw = candidate / "bin" / "javaw.exe"
        if javaw.exists():
            return javaw
    raise FileNotFoundError(f"Could not find a Modrinth Java {java_major} runtime under {JAVA_VERSIONS_DIR}")


def fetch_latest_reshade_download():
    request = urllib.request.Request(RESHADE_HOME, headers=HTTP_HEADERS)
    with urllib.request.urlopen(request) as response:
        html = response.read().decode("utf-8", errors="ignore")

    matches = re.findall(r'href="(/downloads/ReShade_Setup_([0-9.]+)\.exe)"', html)
    if not matches:
        raise RuntimeError("Could not locate the latest ReShade installer on the official site.")

    relative_path, version = matches[0]
    return version, urllib.parse.urljoin("https://reshade.me", relative_path)


def download_file(url, destination, log):
    destination.parent.mkdir(parents=True, exist_ok=True)
    log(f"Downloading {url}")
    request = urllib.request.Request(url, headers=HTTP_HEADERS)
    with urllib.request.urlopen(request) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)
    log(f"Saved installer to {destination}")


def resolve_target_java(profile, log):
    profile_dir = profile["profile_dir"]
    target_java = profile_dir / "reshade-java" / "bin" / "javaw.exe"
    if target_java.exists():
        log(f"Using existing profile-local Java runtime: {target_java}")
        return target_java

    override_java = profile.get("override_java_path")
    source_java = None
    if override_java:
        override_path = Path(override_java)
        if override_path.exists() and "reshade-java" not in override_path.parts:
            source_java = override_path
            log(f"Using profile override Java as the clone source: {source_java}")

    if source_java is None:
        java_major = infer_java_major(profile.get("game_version"))
        source_java = find_shared_java_runtime(java_major)
        log(f"Using shared Modrinth Java {java_major} runtime as the clone source: {source_java}")

    source_root = source_java.parent.parent
    target_root = profile_dir / "reshade-java"
    if target_root.exists():
        shutil.rmtree(target_root)

    log(f"Cloning Java runtime into {target_root}")
    shutil.copytree(source_root, target_root)
    if not target_java.exists():
        raise FileNotFoundError(f"Expected cloned Java runtime at {target_java}, but it was not created.")
    return target_java


def run_reshade_installer(installer_path, target_javaw, log):
    command = [str(installer_path), str(target_javaw), "--headless", "--api", "opengl"]
    log("Running the official ReShade installer in headless OpenGL mode")
    completed = subprocess.run(command, cwd=target_javaw.parent, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"ReShade installer exited with code {completed.returncode}.")

    opengl_dll = target_javaw.parent / "opengl32.dll"
    if not opengl_dll.exists():
        raise FileNotFoundError(f"ReShade install did not create {opengl_dll}")
    log(f"Verified ReShade hook file: {opengl_dll}")


def copy_preset_bundle(target_bin, log):
    if not PRESET_DIR.exists():
        raise FileNotFoundError(f"Bundled preset folder is missing: {PRESET_DIR}")

    log(f"Copying bundled Kyubus preset from {PRESET_DIR}")
    for item in PRESET_DIR.iterdir():
        destination = target_bin / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destination)


def update_profile_override(profile, new_javaw, log):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            UPDATE profiles
            SET override_java_path = ?, modified = ?
            WHERE path = ?
            """,
            (str(new_javaw), int(time.time() * 1000), profile["path_key"]),
        )
        conn.commit()
    finally:
        conn.close()
    log(f"Updated Modrinth override_java_path for {profile['name']}")


class InstallerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Modrinth ReShade Installer")
        self.root.geometry("860x620")
        self.root.minsize(760, 560)

        self.profiles = fetch_profiles()
        if not self.profiles:
            raise RuntimeError("No Modrinth profiles were found.")

        self.profile_index = {}
        self.selected_key = tk.StringVar()
        self.details_var = tk.StringVar()

        self._build_ui()
        self._populate_profiles()
        self._refresh_details()

    def _build_ui(self):
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill="both", expand=True)

        title = ttk.Label(
            container,
            text="Custom ReShade Installer for Modrinth Profiles",
            font=("Segoe UI", 16, "bold"),
        )
        title.pack(anchor="w")

        intro = ttk.Label(
            container,
            text=(
                "This downloads the latest official ReShade installer, lets you choose a Modrinth profile, "
                "creates a profile-local Java runtime, installs ReShade into it, copies the bundled Kyubus preset, "
                "and updates the profile Java override automatically."
            ),
            wraplength=800,
            justify="left",
        )
        intro.pack(anchor="w", pady=(8, 14))

        chooser = ttk.Frame(container)
        chooser.pack(fill="x")

        ttk.Label(chooser, text="Modrinth profile").pack(anchor="w")
        self.profile_combo = ttk.Combobox(
            chooser,
            textvariable=self.selected_key,
            state="readonly",
            width=72,
        )
        self.profile_combo.pack(fill="x", pady=(4, 8))
        self.profile_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_details())

        details = ttk.Label(
            chooser,
            textvariable=self.details_var,
            wraplength=800,
            justify="left",
        )
        details.pack(anchor="w", pady=(0, 10))

        button_row = ttk.Frame(container)
        button_row.pack(fill="x", pady=(0, 10))

        self.install_button = ttk.Button(
            button_row,
            text="Install ReShade to Selected Profile",
            command=self.install_selected_profile,
        )
        self.install_button.pack(side="left")

        self.open_button = ttk.Button(
            button_row,
            text="Open Installer Folder",
            command=lambda: subprocess.run(["explorer", str(APP_DIR)], check=False),
        )
        self.open_button.pack(side="left", padx=(8, 0))

        ttk.Label(container, text="Install log").pack(anchor="w")
        self.log_box = ScrolledText(container, height=22, wrap="word", font=("Consolas", 10))
        self.log_box.pack(fill="both", expand=True, pady=(4, 0))
        self.log_box.configure(state="disabled")

    def _populate_profiles(self):
        display_values = []
        for profile in self.profiles:
            label = f"{profile['name']}  |  {profile['game_version']}  |  {profile['mod_loader']}  |  {profile['path_key']}"
            display_values.append(label)
            self.profile_index[label] = profile
        self.profile_combo["values"] = display_values
        self.profile_combo.current(0)
        self.selected_key.set(display_values[0])

    def _refresh_details(self):
        profile = self.get_selected_profile()
        override_java = profile.get("override_java_path") or "None"
        details = (
            f"Profile folder: {profile['profile_dir']}\n"
            f"Current Java override: {override_java}\n"
            f"Bundled preset: {PRESET_DIR / 'kyubus-FrutigerAero-CRT.ini'}"
        )
        self.details_var.set(details)

    def get_selected_profile(self):
        label = self.selected_key.get()
        return self.profile_index[label]

    def set_busy(self, busy):
        state = "disabled" if busy else "normal"
        self.install_button.configure(state=state)
        self.profile_combo.configure(state="disabled" if busy else "readonly")

    def log(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{timestamp}] {message}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        self.root.update_idletasks()

    def install_selected_profile(self):
        profile = self.get_selected_profile()
        self.set_busy(True)
        try:
            self.log(f"Selected profile: {profile['name']} ({profile['path_key']})")
            version, download_url = fetch_latest_reshade_download()
            self.log(f"Latest official ReShade version detected: {version}")

            temp_dir = Path(tempfile.gettempdir()) / "modrinth_reshade_installer"
            installer_path = temp_dir / f"ReShade_Setup_{version}.exe"
            if installer_path.exists():
                self.log(f"Reusing downloaded installer: {installer_path}")
            else:
                download_file(download_url, installer_path, self.log)

            target_javaw = resolve_target_java(profile, self.log)
            run_reshade_installer(installer_path, target_javaw, self.log)
            copy_preset_bundle(target_javaw.parent, self.log)
            update_profile_override(profile, target_javaw, self.log)

            self.log("Install finished successfully")
            messagebox.showinfo(
                "Install complete",
                (
                    f"ReShade is installed for '{profile['name']}'.\n\n"
                    f"Java path:\n{target_javaw}\n\n"
                    "If Modrinth was open, restart it before launching the profile."
                ),
            )
            self._refresh_details()
        except Exception as exc:
            self.log(f"Install failed: {exc}")
            self.log(traceback.format_exc().strip())
            messagebox.showerror("Install failed", str(exc))
        finally:
            self.set_busy(False)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    InstallerApp().run()
