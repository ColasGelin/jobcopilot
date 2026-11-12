from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from jobcopilot import version
from jobcopilot.web import create_app


def git_dir(tmp_path, head="ref: refs/heads/main\n", loose=None, packed=None):
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text(head)
    if loose:
        (git / "refs" / "heads" / "main").write_text(loose + "\n")
    if packed:
        (git / "packed-refs").write_text(packed)
    return git


def test_commit_from_loose_branch_ref(tmp_path):
    assert version.commit(git_dir(tmp_path, loose="644b5d6aa0e1f00d")) == "644b5d6"


def test_commit_from_packed_refs(tmp_path):
    packed = "# pack-refs with: peeled fully-peeled sorted\n1111111aaaa refs/heads/old\n3f7762abbbb refs/heads/main\n"
    assert version.commit(git_dir(tmp_path, packed=packed)) == "3f7762a"


def test_commit_detached_head_and_missing_ref(tmp_path):
    assert version.commit(git_dir(tmp_path / "a", head="817f8ea0123\n")) == "817f8ea"
    with pytest.raises(ValueError):
        version.commit(git_dir(tmp_path / "b", packed="1111111 refs/heads/other\n"))


def test_describe_has_commit_and_build_time(tmp_path):
    assert version.describe(git_dir(tmp_path, loose="644b5d6aa"), datetime(2026, 11, 12, 11, 40)) == "644b5d6 · built 12 Nov 11:40"


def test_footer_shows_the_running_version(tmp_path, monkeypatch):
    monkeypatch.setattr(version, "VERSION_FILE", tmp_path / "VERSION")
    page = lambda: TestClient(create_app(tmp_path / "jobs.db"), base_url="http://localhost").get("/runs").text
    assert 'class="version"' not in page()  # run from a checkout: no footer
    (tmp_path / "VERSION").write_text("644b5d6 · built 12 Nov 11:40\n")
    assert "Version 644b5d6 · built 12 Nov 11:40" in page()
