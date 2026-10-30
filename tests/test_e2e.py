"""End-to-end runs of the pre-commit logic against real temp repos, with scripted terminal input."""
import json

import httpx
import pytest
from conftest import FIXTURES, commit_file, fake_io, git, stage_file

from grasp import config, run
from grasp.diff import parse_diff
from grasp.evaluate import FIXTURES as EVAL

BEFORE = (EVAL / "py_new_branch" / "before" / "config.py").read_text()
AFTER = (EVAL / "py_new_branch" / "after" / "config.py").read_text()
# Covers kind words for every static template, so it always clears the weak check.
GOOD = "If it is true the function returns early or raises an error, otherwise the default value is used and it continues."


def setup(repo, before=BEFORE, after=AFTER, path="config.py"):
    commit_file(repo, path, before)
    stage_file(repo, path, after)


def cfg(**kw):
    c = config.load()
    c.update({"backend": "static", "threshold": 5, **kw})
    return c


def answers(*grades, text=GOOD):
    out = []
    for g in grades:
        out += ["idk", ""] if g == "idk" else [text, "", g]
    return out


def last_log():
    return config.read_log()[-1]


def test_static_pass(repo):
    setup(repo)
    io = fake_io(*answers("y", "y"))
    assert run.pre_commit(repo, io, env={}, cfg=cfg()) == 0
    entry = last_log()
    assert entry["result"] == "pass" and entry["backend"] == "static" and len(entry["questions"]) == 2
    assert all(q["graded_by"] == "self" for q in entry["questions"])
    out = io.out.getvalue()
    assert "honor system" in out and "Question 2 of 2" in out and "Commit allowed" in out


def test_strict_blocks_on_failing_answers(repo):
    setup(repo)
    io = fake_io(*answers("idk", "idk"), "a")
    assert run.pre_commit(repo, io, env={}, cfg=cfg(mode="strict")) == 1
    assert last_log()["result"] == "blocked"
    assert "Commit blocked" in io.out.getvalue()


def test_strict_needs_full_pass_but_normal_accepts_partial(repo):
    setup(repo)
    assert run.pre_commit(repo, fake_io(*answers("partial", "partial"), "a"), env={}, cfg=cfg(mode="strict")) == 1
    assert run.pre_commit(repo, fake_io(*answers("partial", "partial")), env={}, cfg=cfg(mode="normal")) == 0


def test_normal_mode_needs_majority(repo):
    setup(repo)
    assert run.pre_commit(repo, fake_io(*answers("y", "idk"), "a"), env={}, cfg=cfg(mode="normal")) == 1


def test_retry_asks_fresh_questions(repo):
    setup(repo)
    io = fake_io(*answers("idk", "idk"), "r", *answers("y", "y"))
    assert run.pre_commit(repo, io, env={}, cfg=cfg(mode="strict")) == 0
    first, second = config.read_log()[-2:]
    assert first["result"] == "blocked" and second["result"] == "pass"
    assert not {q["text"] for q in first["questions"]} & {q["text"] for q in second["questions"]}


def test_bypass_after_block_is_logged_as_skip(repo):
    setup(repo)
    assert run.pre_commit(repo, fake_io(*answers("idk", "idk"), "b"), env={}, cfg=cfg(mode="strict")) == 0
    assert last_log()["result"] == "skip" and last_log()["reason"] == "bypass after block"


def test_advisory_never_blocks(repo):
    setup(repo)
    io = fake_io(*answers("idk", "idk"))
    assert run.pre_commit(repo, io, env={}, cfg=cfg(mode="advisory")) == 0
    assert last_log()["result"] == "advisory-miss"
    assert "A good answer covers" in io.out.getvalue()


def test_grasp_skip(repo):
    setup(repo)
    io = fake_io()
    assert run.pre_commit(repo, io, env={"GRASP_SKIP": "1"}, cfg=cfg()) == 0
    assert last_log()["result"] == "skip" and last_log()["reason"] == "GRASP_SKIP=1"
    assert last_log()["files"] == ["config.py"]


def test_non_tty_skips(repo):
    setup(repo)
    io = fake_io(interactive=False)
    assert run.pre_commit(repo, io, env={}, cfg=cfg()) == 0
    assert last_log()["reason"] == "non-interactive" and io.out.getvalue() == ""


def test_ci_skips(repo):
    setup(repo)
    assert run.pre_commit(repo, fake_io(), env={"CI": "true"}, cfg=cfg()) == 0
    assert last_log()["reason"] == "non-interactive"


def test_merge_in_progress_skips_silently(repo):
    setup(repo)
    (repo / ".git" / "MERGE_HEAD").write_text(git(repo, "rev-parse", "HEAD"))
    io = fake_io()
    assert run.pre_commit(repo, io, env={}, cfg=cfg()) == 0
    assert io.out.getvalue() == "" and config.read_log() == []


def test_fail_open_on_internal_error(repo, monkeypatch):
    setup(repo)
    monkeypatch.setattr(run, "generate", lambda *a, **k: 1 / 0)
    io = fake_io()
    assert run.pre_commit(repo, io, env={}, cfg=cfg()) == 0
    assert last_log()["result"] == "error" and "ZeroDivisionError" in last_log()["reason"]
    assert "Allowing commit" in io.out.getvalue()


def test_fail_open_when_no_questions_possible(repo):
    commit_file(repo, "notes.md", "a\n")
    stage_file(repo, "notes.md", "".join(f"line {i} of prose\n" for i in range(30)))
    io = fake_io()
    assert run.pre_commit(repo, io, env={}, cfg=cfg()) == 0
    assert (last_log()["result"], last_log()["reason"]) == ("skip", "no questions for this diff")


@pytest.mark.parametrize("fixture", ["comment_only.diff", "import_only.diff", "whitespace_only.diff"])
def test_noise_only_commits_skip_silently(repo, fixture):
    """Rebuild each fixture as a real staged change, then run the hook with the default threshold."""
    for f in parse_diff((FIXTURES / fixture).read_text()):
        old = "".join(ln[1:] + "\n" for h in f.hunks for ln in h.lines if ln[0] in " -")
        new = "".join(ln[1:] + "\n" for h in f.hunks for ln in h.lines if ln[0] in " +")
        commit_file(repo, f.path, old)
        stage_file(repo, f.path, new)
    io = fake_io()
    assert run.pre_commit(repo, io, env={}, cfg=cfg(threshold=15)) == 0
    assert io.out.getvalue() == "" and config.read_log() == []


def test_twenty_line_logic_change_asks_two_questions(repo):
    (f,) = parse_diff((FIXTURES / "logic_20.diff").read_text())
    old = "".join(ln[1:] + "\n" for h in f.hunks for ln in h.lines if ln[0] in " -")
    new = "".join(ln[1:] + "\n" for h in f.hunks for ln in h.lines if ln[0] in " +")
    setup(repo, old, new, f.path)
    io = fake_io(*answers("y", "y"))
    assert run.pre_commit(repo, io, env={}, cfg=cfg(threshold=15)) == 0
    assert "20 meaningful lines" in io.out.getvalue() and "Question 2 of 2" in io.out.getvalue()
    assert "Question 3" not in io.out.getvalue()


def mock_llm(questions, grades):
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen2.5-coder:7b"}]})
        body = json.loads(request.content)
        grading = body["messages"][0]["content"].startswith("You grade")
        reply = grades.pop(0) if grading else {"questions": questions}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})
    return httpx.MockTransport(handler)


LLM_QS = [
    {"file": "config.py", "anchor": "parse_config", "kind": "branch",
     "question": "In `parse_config`, what happens to a caller passing a missing file now that `path.exists()` is checked?",
     "rubric": ["Returns an empty dict instead of raising"]},
    {"file": "config.py", "anchor": "merge", "kind": "branch",
     "question": "In `merge`, what ends up in `result` for a key whose override `value` is None?",
     "rubric": ["The key is removed"]},
]


def test_mocked_llm_pass_and_fail(repo):
    setup(repo)
    t = mock_llm(LLM_QS, [{"grade": "pass", "feedback": "Right."}, {"grade": "partial", "feedback": "Also removed."}])
    io = fake_io("It returns {} early.", "", "The key gets popped.", "")
    assert run.pre_commit(repo, io, env={}, cfg=cfg(backend="local"), transport=t) == 0
    entry = last_log()
    assert entry["backend"] == "local" and [q["grade"] for q in entry["questions"]] == ["pass", "partial"]
    assert [q["graded_by"] for q in entry["questions"]] == ["local", "local"]

    t = mock_llm(LLM_QS, [{"grade": "fail", "feedback": "No."}, {"grade": "fail", "feedback": "No."}])
    io = fake_io("wrong", "", "wrong", "", "a")
    assert run.pre_commit(repo, io, env={}, cfg=cfg(backend="local", mode="strict"), transport=t) == 1
    assert last_log()["result"] == "blocked"


def test_grader_failure_falls_back_to_self_grading(repo):
    setup(repo)
    t = mock_llm(LLM_QS, ["not json", "still not json", "nope", "nope"])
    ans = "If the path is missing it returns an empty dict, and a None value removes the key."
    io = fake_io(ans, "", "y", ans, "", "y")
    assert run.pre_commit(repo, io, env={}, cfg=cfg(backend="local"), transport=t) == 0
    assert [q["graded_by"] for q in last_log()["questions"]] == ["self", "self"]
    assert "switching to self-grading" in io.out.getvalue()


@pytest.mark.parametrize("backend", ["static", "auto"])
def test_offline_static_end_to_end(repo, no_network, backend):
    """With every socket blocked, static (and auto, which falls through to it) still works end to end."""
    setup(repo)
    io = fake_io(*answers("y", "y"))
    assert run.pre_commit(repo, io, env={"ANTHROPIC_API_KEY": "x"}, cfg=cfg(backend=backend)) == 0
    entry = last_log()
    assert entry["result"] == "pass" and entry["backend"] == "static"
    if backend == "auto":
        assert entry["errors"] and entry["errors"][0].startswith("local: down")
