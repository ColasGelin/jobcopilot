"""Install the daily run as a macOS launchd agent."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

LABEL = "com.jobcopilot.daily"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def plist_xml(exe: str, home: Path, hour: int, minute: int) -> str:
    claude = shutil.which("claude")
    path_dirs = ([str(Path(claude).parent)] if claude else []) + ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    log = str(home / "logs" / "run.log")
    plist = {
        "Label": LABEL,
        "ProgramArguments": [exe, "--home", str(home), "run"],
        "WorkingDirectory": str(home),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "EnvironmentVariables": {"PATH": ":".join(path_dirs), "HOME": str(Path.home())},
    }
    return plistlib.dumps(plist).decode()


def install(exe: str, home: Path, hour: int, minute: int) -> Path:
    (home / "logs").mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist_xml(exe, home, hour, minute))
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(PLIST_PATH)], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(PLIST_PATH)], check=True)
    return PLIST_PATH


def uninstall() -> None:
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(PLIST_PATH)], capture_output=True)
    PLIST_PATH.unlink(missing_ok=True)
