"""What the git hooks run: pick questions, ask them, grade, log, allow or block."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from grasp import config, llm, static
from grasp.diff import (git, identifiers, load_ignore, meaningful_lines, prepare, question_count, redact,
                        staged_diff, staged_source)
from grasp.llm import BackendError
from grasp.questions import Question

CHAINS = {"auto": ["local", "cloud", "static"], "local": ["local", "static"], "cloud": ["cloud", "static"],
          "static": ["static"]}
IDK = {"", "idk", "i don't know", "i dont know", "dunno", "no idea", "?", "skip"}
SELF = {"y": "pass", "yes": "pass", "p": "partial", "partial": "partial", "n": "fail", "no": "fail"}


class IO:
    """Terminal I/O. The hook script sets GRASP_TTY=1 when it has reattached stdin to the terminal."""

    def __init__(self, inp=None, out=None, interactive: bool | None = None):
        self.inp, self.out = inp or sys.stdin, out or sys.stdout
        if interactive is None:
            interactive = os.environ.get("GRASP_TTY") == "1" or (hasattr(self.inp, "isatty") and self.inp.isatty())
        self.interactive = interactive

    def say(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    def line(self, prompt: str = "") -> str:
        self.out.write(prompt)
        self.out.flush()
        s = self.inp.readline()
        if not s:
            raise EOFError
        return s.rstrip("\r\n")

    def answer(self) -> str:
        self.say('Answer (finish with an empty line; type "idk" if unsure):')
        lines = []
        try:
            while (s := self.line("> ")).strip():
                lines.append(s)
        except EOFError:
            pass
        return "\n".join(lines).strip()

    def choose(self, prompt: str, options: dict[str, str], default: str) -> str:
        for _ in range(3):
            try:
                s = self.line(prompt + " ").strip().lower()
            except EOFError:
                return default
            if s in options:
                return options[s]
            if not s:
                return default
        return default


def _git_dir(root) -> Path:
    return Path(git("rev-parse", "--absolute-git-dir", cwd=root).strip())


def in_progress(root) -> str | None:
    d = _git_dir(root)
    for name in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
        if (d / name).exists():
            return name
    return None


def _marker(root) -> Path:
    return _git_dir(root) / "grasp-verified"


def _allow(root) -> int:
    """Record the tree being committed so post-commit can tell this commit went through the hook."""
    try:
        _marker(root).write_text(git("write-tree", cwd=root).strip(), encoding="utf-8")
    except Exception:
        pass
    return 0


def _block(root) -> int:
    _marker(root).unlink(missing_ok=True)
    return 1


def generate(files, source, n, cfg, env, avoid=(), transport=None, io=None):
    """Walk the backend chain. Returns (questions, {name: backend}, label of what was used, errors)."""
    errors, backends, idents = [], {}, identifiers(files)
    for name in CHAINS[cfg["backend"]]:
        if name == "static":
            return static.generate(files, source, n, avoid, idents), backends, "static", errors
        b = llm.from_config(name, cfg, env, transport)
        if b is None:
            if cfg["backend"] == "cloud":
                errors.append("cloud: no API key (set cloud.api_key_env and that env var)")
            continue
        if name == "local" and (h := b.health()) != "ok":
            errors.append(f"local: {h} at {b.base_url}")
            if h == "no-model" and io:
                io.say(f"grasp: model {b.model} is not pulled. Run: ollama pull {b.model}")
            continue
        try:
            qs = b.generate(files, n, avoid)
        except BackendError as e:
            errors.append(str(e))
            continue
        backends[name] = b
        label = name
        if len(qs) < n:
            qs += static.generate(files, source, n - len(qs), list(avoid) + [q.text for q in qs], idents)
            label = f"{name}+static"
        return qs, backends, label, errors
    return [], backends, "none", errors


def decide(mode: str, grades: list[str]) -> bool:
    if mode == "advisory":
        return True
    if mode == "strict":
        return all(g == "pass" for g in grades)
    return sum(g in ("pass", "partial") for g in grades) > len(grades) / 2


def _self_grade(io: IO, q: Question, ans: str) -> tuple[str, str]:
    io.say("Checklist (honor system: static grading cannot judge free text, so you grade yourself):")
    for p in q.rubric:
        io.say(f"  - {p}")
    weak = static.weak_check(q, ans)
    if weak:
        return "fail", weak
    grade = io.choose("Did your answer cover these? [y/partial/n]", SELF, "fail")
    return grade, "Self-graded. A good answer covers: " + "; ".join(q.rubric)


def ask(io: IO, q: Question, i: int, total: int, backends: dict, errors: list) -> dict:
    io.say(f"\nQuestion {i} of {total}  [{q.file}]")
    if q.snippet:
        io.say(q.snippet)
    io.say(f"\n{q.text}")
    ans = io.answer()
    if ans.lower() in IDK:
        grade, feedback, by = "fail", "Counted as a miss. A good answer covers: " + "; ".join(q.rubric), "idk"
    else:
        result, by = None, q.source
        if q.source in backends:
            try:
                result = backends[q.source].grade(q, ans)
            except BackendError as e:
                errors.append(str(e))
                io.say("(grader unavailable, switching to self-grading)")
        if result:
            grade, feedback = result["grade"], result["feedback"]
        else:
            (grade, feedback), by = _self_grade(io, q, ans), "self"
    io.say(f"-> {grade}. {feedback}")
    return {**q.to_log(), "answer": redact(ans), "grade": grade, "feedback": feedback, "graded_by": by}


def session(root, files, n, lines, cfg, env, io, base, transport=None) -> int:
    def source(path, side):
        return staged_source(path, side, cwd=root)
    avoid: list[str] = []
    mode = cfg["mode"]
    while True:
        t0 = time.monotonic()
        qs, backends, used, errors = generate(files, source, n, cfg, env, avoid, transport, io)
        if not qs:
            config.log({**base, "result": "error" if errors else "skip", "reason": "no questions for this diff",
                        "errors": errors})
            io.say("grasp: no questions could be generated for this diff; allowing commit (logged).")
            return _allow(root)
        honor = " (honor system)" if any(q.source == "static" for q in qs) else ""
        io.say(f"\ngrasp: {lines} meaningful lines changed. {len(qs)} question{'s' if len(qs) > 1 else ''}, "
               f"backend: {used}{honor}, mode: {mode}.")
        results = [ask(io, q, i, len(qs), backends, errors) for i, q in enumerate(qs, 1)]
        grades = [r["grade"] for r in results]
        ok = decide(mode, grades)
        passed = decide("strict" if mode == "strict" else "normal", grades)
        result = "pass" if passed else "advisory-miss" if mode == "advisory" else "blocked"
        config.log({**base, "backend": used, "questions": results, "result": result, "errors": errors,
                    "seconds": round(time.monotonic() - t0, 1)})
        summary = ", ".join(grades)
        if ok:
            io.say(f"\ngrasp: {summary}. Commit allowed." + ("" if passed else " (advisory mode, review the feedback above)"))
            return _allow(root)
        rule = "every answer must pass" if mode == "strict" else "more than half must pass or be partial"
        io.say(f"\ngrasp: {summary}. Commit blocked ({mode} mode: {rule}).")
        choice = io.choose("[r]etry with new questions, [b]ypass this once, or [a]bort? (default: abort)",
                           {"r": "retry", "retry": "retry", "b": "bypass", "bypass": "bypass", "a": "abort",
                            "abort": "abort"}, "abort")
        if choice == "retry":
            avoid += [q.text for q in qs]
            continue
        if choice == "bypass":
            config.log({**base, "result": "skip", "reason": "bypass after block"})
            io.say("grasp: bypassed, logged as a skip.")
            return _allow(root)
        io.say("grasp: commit aborted. Your changes are still staged.")
        return _block(root)


def pre_commit(repo=".", io: IO | None = None, env=None, cfg=None, transport=None) -> int:
    env = os.environ if env is None else env
    io = io or IO()
    root = repo
    try:
        root = git("rev-parse", "--show-toplevel", cwd=repo).strip()
        cfg = cfg or config.load()
        base = {"repo": root}
        if env.get("GRASP_SKIP") == "1":
            names = git("diff", "--cached", "--name-only", cwd=root).split()
            config.log({**base, "files": names, "result": "skip", "reason": "GRASP_SKIP=1"})
            io.say("grasp: skipped (GRASP_SKIP=1), logged as a skip.")
            return _allow(root)
        if in_progress(root):
            return _allow(root)
        files, _ = prepare(staged_diff(root), load_ignore(root))
        lines = meaningful_lines(files)
        n = question_count(lines, int(cfg["threshold"]))
        if n == 0:
            return _allow(root)
        base |= {"files": [f.path for f in files], "lines": lines, "mode": cfg["mode"]}
        if env.get("CI", "").lower() not in ("", "0", "false") or not io.interactive:
            config.log({**base, "result": "skip", "reason": "non-interactive"})
            return _allow(root)
        return session(root, files, n, lines, cfg, env, io, base, transport)
    except KeyboardInterrupt:
        io.say("\ngrasp: interrupted, commit not made.")
        return 1
    except Exception as e:  # fail open: never lose a commit to a grasp bug
        try:
            config.log({"repo": str(root), "result": "error", "reason": f"{type(e).__name__}: {e}"})
        except Exception:
            pass
        io.say(f"grasp: internal error ({type(e).__name__}: {e}). Allowing commit; the error is logged.")
        return _allow(root)


def post_commit(repo=".") -> int:
    """Log commits that skipped the pre-commit hook (git commit --no-verify)."""
    try:
        root = git("rev-parse", "--show-toplevel", cwd=repo).strip()
        marker = _marker(root)
        verified = marker.exists() and marker.read_text(encoding="utf-8").strip() == git(
            "rev-parse", "HEAD^{tree}", cwd=root).strip()
        marker.unlink(missing_ok=True)
        if verified or in_progress(root):
            return 0
        files = git("diff-tree", "--no-commit-id", "--name-only", "-r", "--root", "HEAD", cwd=root).split()
        config.log({"repo": root, "files": files, "result": "skip", "reason": "no-verify",
                    "commit": git("rev-parse", "HEAD", cwd=root).strip()})
    except Exception:
        pass
    return 0
