import os

import pytest
from conftest import git

from grasp import hook


def hooks(repo):
    return repo / ".git" / "hooks"


def test_fresh_install_creates_both_hooks(repo):
    hook.install(repo)
    for name in ("pre-commit", "post-commit"):
        text = (hooks(repo) / name).read_text()
        assert text.startswith("#!/bin/sh\n") and text.count(hook.START) == 1 and hook.END in text
        if os.name != "nt":
            assert os.access(hooks(repo) / name, os.X_OK)
    assert "grasp hook pre-commit" in (hooks(repo) / "pre-commit").read_text()


def test_install_is_idempotent(repo):
    hook.install(repo)
    first = (hooks(repo) / "pre-commit").read_text()
    assert "updated" in hook.install(repo)[0]
    assert (hooks(repo) / "pre-commit").read_text() == first


def test_existing_shell_hook_is_kept_and_restored(repo):
    original = "#!/usr/bin/env bash\nset -e\nnpm run lint\n"
    (hooks(repo) / "pre-commit").write_text(original)
    lines = hook.install(repo)
    text = (hooks(repo) / "pre-commit").read_text()
    assert "appended" in lines[0]
    assert text.startswith(original) and text.count(hook.START) == 1
    hook.uninstall(repo)
    assert (hooks(repo) / "pre-commit").read_text() == original
    assert not (hooks(repo) / "post-commit").exists()


@pytest.mark.parametrize("content", [
    "#!/bin/sh\nnpm test\nexit 0\n",
    "#!/usr/bin/env bash\nexec python -m pre_commit hook-impl --hook-type=pre-commit\n",
    "#!/usr/bin/env python3\nprint('hi')\n",
    "no shebang at all\n",
])
def test_unsafe_existing_hook_is_left_alone(repo, content):
    (hooks(repo) / "pre-commit").write_text(content)
    with pytest.raises(hook.HookError) as e:
        hook.install(repo)
    assert "grasp hook pre-commit" in str(e.value)
    assert (hooks(repo) / "pre-commit").read_text() == content
    assert not (hooks(repo) / "post-commit").exists(), "nothing is written unless every hook is safe"


def test_respects_core_hooks_path(repo):
    git(repo, "config", "core.hooksPath", ".husky")
    hook.install(repo)
    assert (repo / ".husky" / "pre-commit").exists()
    assert not (hooks(repo) / "pre-commit").exists()


def test_uninstall_without_hooks_is_a_noop(repo):
    assert hook.uninstall(repo) == []
