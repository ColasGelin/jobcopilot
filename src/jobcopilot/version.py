"""The version shown in the dashboard footer: the commit and build time, written to VERSION by the Docker build.

`python -m jobcopilot.version <path to .git>` prints it; the build mounts the checkout's .git for this (no git needed)."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

VERSION_FILE = Path(__file__).parent / "VERSION"


def commit(git: Path) -> str:
    """The short commit .git/HEAD points to, whether its branch ref is loose or packed."""
    head = (git / "HEAD").read_text(encoding="utf-8").strip()
    if not head.startswith("ref: "):
        return head[:7]  # detached HEAD
    ref = head[5:]
    if (git / ref).exists():
        return (git / ref).read_text(encoding="utf-8").strip()[:7]
    for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
        if line.endswith(" " + ref):
            return line.split()[0][:7]
    raise ValueError(f"{ref} not found in {git}")


def describe(git: Path, now: datetime) -> str:
    return f"{commit(git)} · built {now:%d %b %H:%M}"


def running() -> str | None:
    """The VERSION of this build, or None when run from a checkout (no Docker build)."""
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


if __name__ == "__main__":
    print(describe(Path(sys.argv[1]), datetime.now().astimezone()))
