import difflib
import io
import socket
import subprocess
from pathlib import Path

import pytest

from grasp.run import IO

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Keep config, log and git identity away from the real user environment."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg_data"))
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for k, v in {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "Test",
                 "GIT_COMMITTER_EMAIL": "t@example.com"}.items():
        monkeypatch.setenv(k, v)
    for k in ("GRASP_SKIP", "GRASP_TTY", "CI", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def no_network(monkeypatch):
    """Any attempt to open a socket fails, as on a machine with no network."""
    def blocked(*a, **k):
        raise OSError("network disabled for this test")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    return r


def commit_file(repo, path, text, msg="base"):
    p = Path(repo) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")
    git(repo, "add", path)
    git(repo, "commit", "-q", "--no-verify", "-m", msg)


def stage_file(repo, path, text):
    p = Path(repo) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")
    git(repo, "add", path)


def fake_io(*lines, interactive=True):
    return IO(io.StringIO("".join(line + "\n" for line in lines)), io.StringIO(), interactive)


def make_diff(path, before, after):
    """Git-style unified diff text for one file."""
    a = before.splitlines(keepends=True)
    b = after.splitlines(keepends=True)
    return f"diff --git a/{path} b/{path}\n" + "".join(difflib.unified_diff(a, b, f"a/{path}", f"b/{path}", n=6))
