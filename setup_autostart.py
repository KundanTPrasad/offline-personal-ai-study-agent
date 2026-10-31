import os
from pathlib import Path
import subprocess
import sys

STARTUP_DIR = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
SHORTCUT_PATH = STARTUP_DIR / "StudyAgent.lnk"
PROJECT_DIR = Path(__file__).resolve().parent
APP_PY = PROJECT_DIR / "app.py"
PYTHONW = Path(sys.executable).parent / "pythonw.exe"


def enable():
    if not PYTHONW.exists():
        print(f"Error: pythonw.exe not found at {PYTHONW}")
        return False

    ps_script = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut('{SHORTCUT_PATH}')
$Shortcut.TargetPath = '{PYTHONW}'
$Shortcut.Arguments = '"{APP_PY}"'
$Shortcut.WorkingDirectory = '{PROJECT_DIR}'
$Shortcut.Description = 'Offline Personal AI Study Agent'
$Shortcut.Save()
"""
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, text=True)
    if res.returncode == 0 and SHORTCUT_PATH.exists():
        print(f"Auto-start ENABLED successfully!\nShortcut created at: {SHORTCUT_PATH}")
        return True
    else:
        print(f"Failed to create shortcut: {res.stderr}")
        return False


def disable():
    if SHORTCUT_PATH.exists():
        SHORTCUT_PATH.unlink()
        print(f"Auto-start DISABLED! Shortcut removed from: {SHORTCUT_PATH}")
    else:
        print("Auto-start is already disabled.")


def status():
    if SHORTCUT_PATH.exists():
        print(f"Auto-start is currently ENABLED ({SHORTCUT_PATH})")
    else:
        print("Auto-start is currently DISABLED.")


if __name__ == "__main__":
    choice = sys.argv[1].lower() if len(sys.argv) > 1 else "enable"
    if choice == "enable":
        enable()
    elif choice == "disable":
        disable()
    elif choice == "status":
        status()
    else:
        print("Usage: python setup_autostart.py [enable|disable|status]")

