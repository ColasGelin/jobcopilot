import plistlib
from pathlib import Path

from jobcopilot.agent.launchd import LABEL, plist_xml


def test_plist_runs_agent_with_dashboard():
    data = plistlib.loads(plist_xml("/opt/homebrew/bin/uv", Path("/repo"), "http://localhost:8077").encode())
    args = data["ProgramArguments"]
    assert data["Label"] == LABEL == "com.jobcopilot.agent"
    assert args[0] == "/opt/homebrew/bin/uv" and "agent" in args and args[args.index("--dashboard") + 1] == "http://localhost:8077"
    assert "--extra" in args and "browser" in args and "/repo" in args
    assert data["RunAtLoad"] is True and data["KeepAlive"] is True and data["WorkingDirectory"] == "/repo"
    assert data["StandardOutPath"].endswith(".jobcopilot/agent.log")
    assert data["EnvironmentVariables"]["PYTHONUNBUFFERED"] == "1"  # log lines show up as they happen
