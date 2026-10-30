"""`grasp eval`: run the question generator over fixture diffs and print every question with automatic checks."""
from __future__ import annotations

import difflib
import time
from pathlib import Path

from grasp.diff import CONTEXT, identifiers, meaningful_lines, prepare, question_count
from grasp.questions import anchors, problems

FIXTURES = Path(__file__).parent / "eval_fixtures"


def load_case(case: Path):
    """A case is a directory with before/ and after/ trees. Returns (filtered files, source function)."""
    before, after = case / "before", case / "after"
    rels = sorted({p.relative_to(d).as_posix() for d in (before, after) if d.is_dir() for p in d.rglob("*")
                   if p.is_file() and "__pycache__" not in p.parts})  # installs byte-compile the fixtures

    def source(rel: str, side: str) -> str | None:
        p = (before if side == "old" else after) / rel
        return p.read_text(encoding="utf-8") if p.is_file() else None

    chunks = []
    for rel in rels:
        a, b = source(rel, "old"), source(rel, "new")
        lines = [(t if t.endswith("\n") else t + "\n").splitlines(keepends=True) if t else [] for t in (a, b)]
        chunks.append(f"diff --git a/{rel} b/{rel}\n" + ("new file mode 100644\n" if a is None else "")
                      + ("deleted file mode 100644\n" if b is None else ""))
        chunks += difflib.unified_diff(lines[0], lines[1], f"a/{rel}" if a is not None else "/dev/null",
                                       f"b/{rel}" if b is not None else "/dev/null", n=CONTEXT)
    files, _ = prepare("".join(chunks))
    return files, source


def run_eval(path: Path | None, backend: str, cfg: dict, env, show_snippets: bool = False, out=print) -> int:
    from grasp.run import generate
    root = Path(path) if path else FIXTURES
    cases = sorted(d for d in root.iterdir() if (d / "before").is_dir() or (d / "after").is_dir())
    total = bad = short = 0
    started = time.monotonic()
    for case in cases:
        files, source = load_case(case)
        lines = meaningful_lines(files)
        n = max(2, question_count(lines, int(cfg["threshold"])))
        t0 = time.monotonic()
        qs, _, used, errors = generate(files, source, n, {**cfg, "backend": backend}, env)
        idents = identifiers(files)
        out(f"\n== {case.name}  [{', '.join(f.path for f in files)}]  {lines} meaningful lines, "
            f"backend {used}, {time.monotonic() - t0:.1f}s")
        for e in errors:
            out(f"   backend error: {e}")
        if len(qs) < n:
            short += 1
            out(f"   only {len(qs)} of {n} questions generated")
        for i, q in enumerate(qs, 1):
            total += 1
            probs = problems(q, idents)
            bad += bool(probs)
            out(f"  Q{i} [{q.kind}, {q.source}] {q.text}")
            for p in q.rubric:
                out(f"       - {p}")
            out(f"       checks: {'ok' if not probs else 'FAIL: ' + '; '.join(probs)}"
                f"  (anchors: {', '.join(anchors(q.text, idents)) or 'none'})")
            if show_snippets and q.snippet:
                out("\n".join("       | " + ln for ln in q.snippet.splitlines()))
    out(f"\n{len(cases)} cases, {total} questions, {bad} failed checks, {short} cases short of questions, "
        f"{time.monotonic() - started:.1f}s total")
    return 1 if bad or short else 0
