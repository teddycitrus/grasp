"""Install and remove grasp's marked blocks in .git/hooks without touching anything else."""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from grasp.diff import git

START, END = "# >>> grasp >>>", "# <<< grasp <<<"
BLOCKS = {
    "pre-commit": """# Installed by `grasp init`. Remove this block or run `grasp uninstall` to disable.
if command -v grasp >/dev/null 2>&1; then
  if [ -n "$GRASP_TTY" ]; then
    grasp hook pre-commit || exit $?
  elif (exec </dev/tty) 2>/dev/null; then
    GRASP_TTY=1 grasp hook pre-commit </dev/tty || exit $?
  else
    grasp hook pre-commit </dev/null || exit $?
  fi
else
  echo "grasp: command not found on PATH, skipping" >&2
fi""",
    "post-commit": """# Installed by `grasp init`. Logs commits made with --no-verify.
if command -v grasp >/dev/null 2>&1; then
  grasp hook post-commit </dev/null || true
fi""",
}
SHELLS = re.compile(r"^#!\s*(/usr/bin/env\s+)?(/\S*/)?(sh|bash|zsh|dash|ksh)\b")


class HookError(Exception):
    pass


def hooks_dir(repo) -> Path:
    root = Path(git("rev-parse", "--show-toplevel", cwd=repo).strip())
    custom = git("config", "--get", "core.hooksPath", cwd=repo, check=False).strip()
    if custom:
        p = Path(os.path.expanduser(custom))
        return p if p.is_absolute() else root / p
    return Path(git("rev-parse", "--absolute-git-dir", cwd=repo).strip()) / "hooks"


def _block(name: str) -> str:
    return f"{START}\n{BLOCKS[name]}\n{END}\n"


def _strip_block(text: str) -> str:
    return re.sub(rf"\n?{re.escape(START)}.*?{re.escape(END)}\n?", "\n", text, flags=re.S)


def _check_appendable(path: Path, text: str) -> None:
    first = text.splitlines()[0] if text else ""
    if not SHELLS.match(first):
        raise HookError(f"{path} is not a shell script ({first or 'no shebang'}). Add this line to it yourself:\n"
                        f"    grasp hook {path.name}")
    code = [ln.strip() for ln in _strip_block(text).splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if code and re.match(r"^(exit|exec)\b", code[-1]):
        raise HookError(f"{path} ends with `{code[-1]}`, so an appended block would never run. Add this line "
                        f"before it yourself:\n    grasp hook {path.name}")


def install(repo) -> list[str]:
    """Write or update the grasp block in each hook. Returns a line per hook describing what happened."""
    d = hooks_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    plans = []
    for name in BLOCKS:
        path = d / name
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if START in text:
            new, action = re.sub(rf"{re.escape(START)}.*?{re.escape(END)}\n?", lambda _: _block(name), text, flags=re.S), "updated"
        elif text:
            _check_appendable(path, text)
            new, action = text.rstrip("\n") + "\n\n" + _block(name), "appended to existing hook"
        else:
            new, action = "#!/bin/sh\n" + _block(name), "created"
        plans.append((path, new, action))
    for path, new, _ in plans:  # only write once every hook is known to be safe
        path.write_text(new, encoding="utf-8", newline="\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return [f"{path}: {action}" for path, _, action in plans]


def uninstall(repo) -> list[str]:
    d, out = hooks_dir(repo), []
    for name in BLOCKS:
        path = d / name
        if not path.exists() or START not in (text := path.read_text(encoding="utf-8")):
            continue
        rest = _strip_block(text).strip()
        if rest in ("", "#!/bin/sh"):
            path.unlink()
            out.append(f"{path}: removed")
        else:
            path.write_text(rest + "\n", encoding="utf-8", newline="\n")
            out.append(f"{path}: grasp block removed, rest of hook kept")
    return out
