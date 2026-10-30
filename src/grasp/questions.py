"""The Question type and the programmatic quality gate every question must pass."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

KINDS = ("branch", "removed", "default", "param", "error", "loop", "constant", "impact", "why", "other")


@dataclass
class Question:
    text: str
    rubric: list[str]
    file: str
    anchor: str
    kind: str = "other"
    snippet: str = ""
    source: str = "static"
    concepts: list[str] = field(default_factory=list)

    def to_log(self) -> dict:
        d = asdict(self)
        d.pop("concepts")
        return d


BANNED = [re.compile(p, re.I) for p in (
    r"\bwhat (does|did|is) (this|the|your) (change|diff|code|commit|patch|pr|pull request)\b",
    r"\bwhy did you (make|write|do) (this|the|these)\b",
    r"\b(explain|describe|summari[sz]e) (this|the|your|these) (diff|change|code|commit|patch)",
    r"\bwhat (is|was) the (purpose|point|goal) of (this|the|your) (change|diff|commit|patch)",
    r"\bwhat changed in (this|the) (diff|commit|file|code)\b",
    r"\bwalk (me )?through\b",
    r"\bline (number )?\d+",
    r"\bwhat is the name of\b",
    r"\bwhat (language|syntax)\b",
    r"\bbig[- ]?o\b|\btime complexity\b|\bspace complexity\b",
    r"\b(other|another|different) (file|module)s?\b",
    r"\belsewhere in the (code|repo|project|codebase)\b",
    r"\bhow is .* formatted\b",
    r"\bwhat does `?[\w.]+(\(\))?`? do\??$",
    r"\bwhat is the (new |old |updated |current )?(default|value|name|type)\b",
    r"\bwhy (did|was|were|is|are|has|have) .{0,60}\b(changed?|added|set|introduced|necessary|needed|important|useful|chosen)\b",
    r"\band why is (it|this|that) (useful|important|necessary|needed|good|better)\b",
    r"\bwhat (change|changes) (was|were|is|are|allows?|lets|enables?)\b",
    r"^\s*what (error|exception|value|status)( code)? (is|gets|does it) (raised|returned|thrown|raise|return)\b",
)]
SCENARIO = re.compile(r"\b(when|if|unless|happens?|without|instead|before|previously|would|could|break|fail\w*|empty|"
                      r"none|null|nil|zero|negative|missing|still|edge|crash\w*|error|timeout|times out)\b", re.I)
STOP = set("""a an and are as at be been but by can do does for from has have how if in into is it its not of on or
so than that the then there these this those to was what when where which while who why will with would you your
self this cls true false none null nil undefined return def class function func fn const let var new else elif for
while try except catch finally raise throw import from pass break continue public private static void int str bool
string list dict set get value values data result item items code change changes diff""".split())
LEAK_STOP = STOP | set("""before after now what happens happen case cases caller callers answer mentions explains
names says describes would without state result results work bad rest loop body done them they their input inputs
behavior function change check lost condition branch true false items old new instead does should could makes make
leads specifically concrete""".split())
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def anchors(text: str, diff_idents: set[str]) -> list[str]:
    """Identifiers named in the question that really appear in the diff."""
    cands = set()
    for span in re.findall(r"`([^`]+)`", text):
        cands |= set(WORD_RE.findall(span))
    cands |= set(re.findall(r"\b[A-Za-z_]\w*(?=\()", text))
    cands |= {w for w in WORD_RE.findall(text) if "_" in w.strip("_") or re.search(r"[a-z][A-Z]", w)}
    return sorted(c for c in cands if c in diff_idents and c.lower() not in STOP)


def _content(s: str) -> set[str]:
    return {w.lower() for w in WORD_RE.findall(s) if len(w) > 2 and w.lower() not in LEAK_STOP}


def problems(q: Question, diff_idents: set[str]) -> list[str]:
    """Empty list means the question is acceptable."""
    out = []
    if not 25 <= len(q.text) <= 400:
        out.append(f"question length {len(q.text)} outside 25-400")
    if not anchors(q.text, diff_idents):
        out.append("names no identifier from the diff")
    for rx in BANNED:
        if rx.search(q.text):
            out.append(f"banned phrase: {rx.search(q.text).group(0)!r}")
    prose = re.sub(r"`[^`]*`", " ", q.text)  # an `if` inside quoted code is not a scenario
    if re.match(r"\s*(what|how|which) (does|do|is|are|was)\b", q.text, re.I) and not SCENARIO.search(prose):
        out.append("restates the code: asks what something is or does, with no scenario or consequence")
    if not 1 <= len(q.rubric) <= 3:
        out.append(f"rubric has {len(q.rubric)} points, need 1-3")
    if any(not 3 <= len(p) <= 200 for p in q.rubric):
        out.append("rubric point length outside 3-200")
    qwords = _content(q.text)
    for p in q.rubric:
        words = _content(re.sub(r"`[^`]*`", " ", p))  # identifiers quoted from the question are not the answer
        if len(words) >= 2 and len(words & qwords) / len(words) >= 0.8:
            out.append(f"leaks rubric point: {p!r}")
    if q.kind not in KINDS:
        out.append(f"unknown kind {q.kind!r}")
    return out
