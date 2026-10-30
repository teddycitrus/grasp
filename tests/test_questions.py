import pytest
from conftest import make_diff

from grasp.diff import identifiers, prepare
from grasp.questions import Question, problems

DIFF = make_diff("src/net.py",
                 "def fetch(url, retries=0):\n    return get(url)\n",
                 "def fetch(url, retries=3):\n    for attempt in range(retries + 1):\n        try:\n"
                 "            return get(url)\n        except TimeoutError:\n            cache.clear()\n    return None\n")
IDENTS = identifiers(prepare(DIFF)[0])


def q(text, rubric=("The function retries on timeouts",)):
    return Question(text, list(rubric), "src/net.py", "fetch", "other")


GOOD = "`fetch` now loops over `range(retries + 1)`. What does a caller get back if every attempt times out?"


def test_good_question_passes():
    assert problems(q(GOOD, ["It returns None after the last timeout"]), IDENTS) == []


@pytest.mark.parametrize("text,reason", [
    ("What does this change do?", "names no identifier"),
    ("Why did you make this change to `fetch`?", "banned phrase"),
    ("Explain this diff, focusing on `fetch` and `retries`.", "banned phrase"),
    ("What is the purpose of this change in `fetch`?", "banned phrase"),
    ("What happens when the request is slow and the server never answers?", "names no identifier"),
    ("What does `download_file` return when the server times out?", "names no identifier"),
    ("What is the name of the variable on line 3 of `fetch`?", "banned phrase"),
    ("What is the Big-O of `fetch` when `retries` is large?", "banned phrase"),
    ("Which other files call `fetch`, and do they pass `retries`?", "banned phrase"),
    ("What does `fetch` do?", "banned phrase"),
    ("What does the new `retries` parameter in `fetch` control?", "restates the code"),
    ("How does `fetch` use `range(retries + 1)` in the loop?", "restates the code"),
    ("What is the new default value for `retries` in `fetch`?", "banned phrase"),
    ("Why did the default for `retries` in `fetch` change from 0 to 3?", "banned phrase"),
    ("What does `cache.clear()` reset, and why is it important?", "banned phrase"),
    ("What does the new `if retries < 0` condition in `fetch` check for?", "restates the code"),
    ("What error is raised if `retries` is negative in `fetch`?", "banned phrase"),
    ("What change was made to `fetch` to allow retries?", "banned phrase"),
    ("`fetch`?", "length"),
    ("Why `fetch` " + "really " * 80 + "?", "length"),
])
def test_bad_questions_are_rejected(text, reason):
    found = problems(q(text), IDENTS)
    assert any(reason in p for p in found), (text, found)


def test_question_that_leaks_its_answer_is_rejected():
    leaky = q("Why does `fetch` retry so that transient network timeouts recover automatically?",
              ["Transient network timeouts recover automatically"])
    assert any("leaks" in p for p in problems(leaky, IDENTS))


@pytest.mark.parametrize("rubric", [[], ["a point", "b point", "c point", "d point"], ["x" * 201], ["ok"]])
def test_rubric_size_limits(rubric):
    assert any("rubric" in p for p in problems(q(GOOD, rubric), IDENTS))


def test_unknown_kind_rejected():
    bad = Question(GOOD, ["It returns None"], "src/net.py", "fetch", "trivia")
    assert any("kind" in p for p in problems(bad, IDENTS))
