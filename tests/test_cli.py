import shutil

from grasp import cli, config
from grasp.evaluate import FIXTURES, run_eval


def test_choice_matches_uppercase_options_case_insensitively(monkeypatch):
    replies = iter(["openai_api_key"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(replies))
    assert cli._choice("Which key", ["ANTHROPIC_API_KEY", "OPENAI_API_KEY"], "ANTHROPIC_API_KEY", False) == "OPENAI_API_KEY"


def test_choice_gives_up_on_bad_input_instead_of_looping(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "nonsense")
    assert cli._choice("Mode", config.MODES, "normal", False) == "normal"


def test_eval_ignores_bytecode_from_installs(tmp_path):
    cases = tmp_path / "cases"
    shutil.copytree(FIXTURES, cases)
    junk = cases / "py_new_branch" / "after" / "__pycache__"
    junk.mkdir()
    (junk / "config.cpython-314.pyc").write_bytes(b"\x00\xde\xad\xbe\xef")
    lines = []
    assert run_eval(cases, "static", config.load(), {}, out=lines.append) == 0
    assert "13 cases, 26 questions, 0 failed checks" in lines[-1]


def test_init_outside_repo_fails_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "--yes"]) == 1
    assert "inside a git repository" in capsys.readouterr().out


def test_log_reports_weak_spots_and_skips(capsys):
    config.log({"repo": "/r", "result": "blocked", "backend": "static",
                "questions": [{"kind": "error", "grade": "fail"}, {"kind": "branch", "grade": "pass"}]})
    config.log({"repo": "/r", "result": "skip", "reason": "no-verify"})
    assert cli.main(["log"]) == 0
    out = capsys.readouterr().out
    assert "error handling (1 of 1 not fully passed)" in out and "no-verify: 1" in out
