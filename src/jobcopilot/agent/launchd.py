"""Run the agent at login on macOS (LaunchAgent), from the repo checkout with uv."""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

LABEL = "com.jobcopilot.agent"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG = Path.home() / ".jobcopilot" / "agent.log"


def plist_xml(uv: str, repo: Path, dashboard: str) -> str:
    path_dirs = [str(Path(uv).parent), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    plist = {
        "Label": LABEL,
        "ProgramArguments": [uv, "run", "--project", str(repo), "--extra", "browser", "jobcopilot", "agent", "--dashboard", dashboard],
        "WorkingDirectory": str(repo),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
        "EnvironmentVariables": {"PATH": ":".join(path_dirs), "HOME": str(Path.home()), "PYTHONUNBUFFERED": "1"},
    }
    return plistlib.dumps(plist).decode()


def install(uv: str, repo: Path, dashboard: str) -> Path:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist_xml(uv, repo, dashboard))
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(PLIST_PATH)], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(PLIST_PATH)], check=True)
    return PLIST_PATH


def uninstall() -> None:
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(PLIST_PATH)], capture_output=True)
    PLIST_PATH.unlink(missing_ok=True)
