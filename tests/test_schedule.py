import plistlib
from pathlib import Path

from jobcopilot import schedule


def test_plist_contains_schedule_and_path(monkeypatch, tmp_path):
    monkeypatch.setattr(schedule.shutil, "which", lambda name: "/Users/me/.local/bin/claude")
    plist = plistlib.loads(schedule.plist_xml("/x/.venv/bin/jobcopilot", tmp_path, 8, 30).encode())
    assert plist["Label"] == "com.jobcopilot.daily"
    assert plist["StartCalendarInterval"] == {"Hour": 8, "Minute": 30}
    assert plist["ProgramArguments"] == ["/x/.venv/bin/jobcopilot", "--home", str(tmp_path), "run"]
    assert plist["WorkingDirectory"] == str(tmp_path)
    assert plist["StandardOutPath"] == str(tmp_path / "logs" / "run.log")
    assert "/Users/me/.local/bin" in plist["EnvironmentVariables"]["PATH"].split(":")
