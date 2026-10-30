import pytest
from conftest import make_diff

from grasp import static
from grasp.diff import identifiers, prepare
from grasp.evaluate import FIXTURES, load_case
from grasp.questions import Question, anchors, problems

CASES = sorted(d.name for d in FIXTURES.iterdir() if d.is_dir())


def kinds(case):
    files, source = load_case(FIXTURES / case)
    return [(f.kind, f.fn, f.data) for f in static.facts_for(files, source)]


def find(case, kind, **data):
    return [d for k, fn, d in kinds(case) if k == kind and all(d.get(x) == v for x, v in data.items())]


def test_new_branch_guard_and_param():
    assert find("py_new_branch", "new_branch", cond="not path.exists()", exit=("return", "{}"))
    assert find("py_new_branch", "new_branch", cond="value is None", exit=("continue", ""))
    assert find("py_new_branch", "new_param", param="strict", default="False")


def test_removed_call():
    assert find("py_removed_call", "removed_call", call="self.cache.clear")
    assert find("py_removed_call", "changed_condition", old="key in self.cache", new="key not in self.cache")


def test_changed_defaults_and_new_param():
    assert find("py_changed_default", "changed_default", param="timeout", old="10", new="30")
    assert find("py_changed_default", "changed_default", param="retries", old="0", new="3")
    assert find("py_changed_default", "new_param", param="max_delay", default="8")


def test_error_handling():
    assert find("py_error_handling", "new_except", exc="KeyError", reraise=False)
    assert find("py_error_handling", "new_raise", exc="ValueError")
    assert find("py_new_loop", "new_except", exc="Exception", reraise=True)


def test_condition_pairing_uses_similarity():
    assert find("py_changed_condition", "changed_condition", old="customer.age > 25", new="customer.age >= 25")
    assert find("py_changed_condition", "new_branch", cond="customer.banned")
    assert find("py_changed_condition", "changed_condition", old="days_late <= 0", new="days_late <= grace_days")


def test_removed_branch_and_constant():
    assert find("py_removed_branch", "removed_branch", cond="user.last_reminder == today")
    assert find("py_changed_constant", "changed_constant", name="MAX_UPLOAD_MB", old="10", new="25")


def test_regex_extractor_for_js_go_ts():
    assert find("js_auth", "removed_call", call="auditLog.record")
    assert find("js_auth", "changed_condition", old="req.user.role === role", new="roles.includes(req.user.role)")
    assert find("js_auth", "new_branch", cond='!header.startsWith("Bearer ")')
    assert find("go_error_handling", "new_branch", cond="id <= 0")
    assert find("ts_changed_default", "changed_default", param="attempts", old="3", new="5")
    assert find("ts_changed_default", "new_param", param="shouldRetry")


def test_python_syntax_error_falls_back_to_regex():
    before = "def f(x):\n    return x\n"
    after = "def f(x):\n    if x is None:\n        return 0\n    return x +\n"
    files, _ = prepare(make_diff("bad.py", before, after))
    found = static.facts_for(files, lambda p, side: before if side == "old" else after)
    assert any(f.kind == "new_branch" and f.data["cond"] == "x is None" for f in found)


@pytest.mark.parametrize("case", CASES)
def test_every_question_names_a_real_identifier_and_passes_checks(case):
    files, source = load_case(FIXTURES / case)
    idents = identifiers(files)
    qs = static.generate(files, source, 3)
    assert len(qs) >= 2
    for q in qs:
        assert anchors(q.text, idents), q.text
        assert problems(q, idents) == [], q.text
        assert q.snippet and len(q.snippet.splitlines()) <= 25 + 6
        assert q.source == "static"


def test_loop_questions_never_ask_about_an_empty_fixed_range():
    before = "def pick(xs):\n    return xs[0]\n"
    after = ("def pick(xs):\n    for name in ['a', 'b']:\n        log(name)\n    for _ in range(5):\n"
             "        if ok(xs):\n            return xs[0]\n    return None\n")
    files, _ = prepare(make_diff("p.py", before, after))
    found = [f for f in static.facts_for(files, lambda p, side: before if side == "old" else after) if f.kind == "new_loop"]
    assert [f.data["iter"] for f in found] == ["range(5)"], "literal lists are skipped"
    text, _, _ = static._render(found[0])
    assert "at most 5 times" in text and "empty" not in text


def test_c_style_loop_asks_about_its_bound():
    files, _ = prepare(make_diff("r.js", "function go(n) {\n  return 0;\n}\n",
                                 "function go(n) {\n  for (let i = 0; i < n; i++) {\n    step(i);\n  }\n  return 0;\n}\n"))
    (loop,) = [f for f in static.regex_facts(files[0], None) if f.kind == "new_loop"]
    assert "`n` is 0" in static._render(loop)[0]


def test_avoid_gives_fresh_questions():
    files, source = load_case(FIXTURES / "py_new_branch")
    first = static.generate(files, source, 2)
    second = static.generate(files, source, 2, avoid=[q.text for q in first])
    assert second and not {q.text for q in first} & {q.text for q in second}


def test_weak_check():
    q = Question("In `parse_config`, ...", ["x"], "a.py", "parse_config", concepts=["path", "exists", "return"])
    assert static.weak_check(q, "idk lol") is not None
    assert static.weak_check(q, "It does something useful for users here") is not None
    assert static.weak_check(q, "When the path is missing it returns an empty dict early.") is None
