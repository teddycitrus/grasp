"""Real `git commit` runs in temp repos, with the installed `grasp` command on PATH."""
import os
import shutil
import subprocess
import sysconfig

import pytest
from conftest import commit_file, stage_file

from grasp import config

SCRIPTS = sysconfig.get_path("scripts")
pytestmark = pytest.mark.skipif(not shutil.which("grasp", path=SCRIPTS), reason="grasp is not installed in this env")


def env(**extra):
    e = dict(os.environ, PATH=SCRIPTS + os.pathsep + os.environ["PATH"], CI="1")
    e.pop("GRASP_TTY", None)
    e.update(extra)
    return e


def sh(repo, *args, **extra):
    cmd = [shutil.which(args[0], path=SCRIPTS) or args[0], *args[1:]]  # Windows ignores env PATH for lookup
    return subprocess.run(cmd, cwd=repo, env=env(**extra), capture_output=True, text=True)


@pytest.fixture
def installed(repo):
    r = sh(repo, "grasp", "init", "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    c = config.load()
    c.update(backend="static", threshold=1)
    config.save(c)
    commit_file(repo, "app.py", "def f(x):\n    return x\n")
    stage_file(repo, "app.py", "def f(x, y=0):\n    if x is None:\n        return y\n    return x + y\n")
    return repo


def test_init_installs_hooks(installed):
    assert "grasp hook pre-commit" in (installed / ".git" / "hooks" / "pre-commit").read_text()
    assert config.config_path().exists()


def test_grasp_skip_commit_is_logged(installed):
    r = sh(installed, "git", "commit", "-m", "skip", GRASP_SKIP="1")
    assert r.returncode == 0, r.stderr
    log = config.read_log()
    assert [(e["result"], e["reason"]) for e in log] == [("skip", "GRASP_SKIP=1")]


def test_no_verify_commit_is_logged(installed):
    r = sh(installed, "git", "commit", "--no-verify", "-m", "nv")
    assert r.returncode == 0, r.stderr
    (entry,) = config.read_log()
    assert (entry["result"], entry["reason"], entry["files"]) == ("skip", "no-verify", ["app.py"])


def test_non_interactive_commit_passes_and_is_not_counted_as_no_verify(installed):
    r = sh(installed, "git", "commit", "-m", "ci")
    assert r.returncode == 0, r.stderr
    assert [e["reason"] for e in config.read_log()] == ["non-interactive"]


def test_existing_hook_still_runs(repo, tmp_path):
    marker = tmp_path / "ran.txt"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f'#!/bin/sh\necho ran > "{marker.as_posix()}"\n')
    hook.chmod(0o755)
    assert sh(repo, "grasp", "init", "--yes").returncode == 0
    commit_file(repo, "a.py", "x = 1\n")
    stage_file(repo, "a.py", "x = 2\n")
    assert sh(repo, "git", "commit", "-m", "m").returncode == 0
    assert marker.exists()


def test_uninstall_command(installed):
    r = sh(installed, "grasp", "uninstall")
    assert r.returncode == 0 and "removed" in r.stdout
    assert not (installed / ".git" / "hooks" / "pre-commit").exists()
