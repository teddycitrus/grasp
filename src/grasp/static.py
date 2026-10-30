"""Static backend: questions built from concrete facts in the diff. No model, no network.

Python files are compared with the ast module (old vs new version of each touched function).
Other languages, and Python that does not parse, use line regexes. To add a better extractor for a
language later, register a function with the same signature as python_facts in EXTRACTORS.
"""
from __future__ import annotations

import ast
import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from typing import Callable

from grasp.diff import FileDiff, redact, snippet
from grasp.questions import Question, problems

SourceFn = Callable[[str, str], "str | None"]  # (path, "old" | "new") -> file text or None


@dataclass
class Fact:
    kind: str
    file: str
    fn: str
    line: int
    side: str = "new"
    data: dict = field(default_factory=dict)
    extra: str = ""


PRIORITY = ["changed_condition", "removed_branch", "removed_call", "new_branch", "new_except", "new_raise",
            "changed_default", "new_param", "removed_param", "new_return", "changed_constant", "new_while",
            "new_loop", "hunk"]
SKIP_CALLS = re.compile(
    r"^(print|len|str|int|float|bool|list|dict|set|tuple|isinstance|super|range|enumerate|zip|repr|type|sorted|"
    r"min|max|getattr|hasattr|puts|println!?|console\.\w+|(self\.)?(log|logger|logging|_log|_logger)\.\w+|"
    r"fmt\.Print\w*|System\.out\.\w+)$")
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _changed_lines(f: FileDiff) -> tuple[set[int], set[int]]:
    added, removed = set(), set()
    for h in f.hunks:
        for p, _, o, n in h.numbered():
            if p == "+":
                added.add(n)
            elif p == "-":
                removed.add(o)
    return added, removed


# ---------------------------------------------------------------- Python (ast)

DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _own(fn):
    """Nodes inside a function, not descending into nested defs."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, DEFS):
            stack.extend(ast.iter_child_nodes(node))


def _functions(tree) -> dict[str, ast.AST]:
    out = {}

    def visit(node, prefix):
        for c in ast.iter_child_nodes(node):
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[prefix + c.name] = c
                visit(c, f"{prefix}{c.name}.")
            elif isinstance(c, ast.ClassDef):
                visit(c, f"{prefix}{c.name}.")
            elif isinstance(c, ast.stmt):
                visit(c, prefix)
    visit(tree, "")
    return out


def _params(fn) -> dict[str, str | None]:
    a = fn.args
    pos = a.posonlyargs + a.args
    defaults = [None] * (len(pos) - len(a.defaults)) + [ast.unparse(d) for d in a.defaults]
    out = {p.arg: d for p, d in zip(pos, defaults)}
    out |= {p.arg: ast.unparse(d) if d is not None else None for p, d in zip(a.kwonlyargs, a.kw_defaults)}
    out.pop("self", None)
    out.pop("cls", None)
    return out


def _exc_name(exc) -> str:
    if exc is None:
        return "the caught exception"
    return ast.unparse(exc.func if isinstance(exc, ast.Call) else exc)


def _exit(body) -> tuple[str, str] | None:
    """(verb, code) when a block ends by leaving: return, raise, continue or break."""
    last = body[-1]
    if isinstance(last, ast.Return):
        return "return", ast.unparse(last.value) if last.value else "None"
    if isinstance(last, ast.Raise):
        return "raise", _exc_name(last.exc)
    return {ast.Continue: ("continue", ""), ast.Break: ("break", "")}.get(type(last))


DOTTED_RE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")


def _pair(new: list[str], old: list[str]) -> list[tuple[str, str]]:
    """Greedily pair added and removed conditions that look like edits of each other."""
    def score(n, o):
        shared_path = bool(set(DOTTED_RE.findall(n)) & set(DOTTED_RE.findall(o)))
        return SequenceMatcher(None, n, o).ratio() + 0.3 * shared_path
    scored = sorted(((score(n, o), n, o) for n in new for o in old), reverse=True)
    pairs, used = [], set()
    for s, n, o in scored:
        if s >= 0.6 and n not in used and o not in used:
            pairs.append((n, o))
            used |= {n, o}
    return pairs


def _compare_function(qual, file, o, n, added, removed, osrc="", nsrc="") -> list[Fact]:
    facts: list[Fact] = []
    had_old = o is not None

    def add(kind, line, side="new", **data):
        facts.append(Fact(kind, file, qual, line, side, data))

    if had_old:
        po, pn = _params(o), _params(n)
        for name in sorted(pn.keys() - po.keys()):
            add("new_param", n.lineno, param=name, default=pn[name])
        for name in sorted(po.keys() - pn.keys()):
            add("removed_param", o.lineno, "old", param=name)
        for name in sorted(po.keys() & pn.keys()):
            if po[name] != pn[name] and None not in (po[name], pn[name]):
                add("changed_default", n.lineno, param=name, old=po[name], new=pn[name])

    parent = {c: p for p in _own(n) for c in ast.iter_child_nodes(p)}
    parent |= {c: n for c in ast.iter_child_nodes(n)}

    def text(node, src):
        return " ".join((ast.get_source_segment(src, node) or ast.unparse(node)).split())

    def when(node) -> str:
        p = parent.get(node)
        return text(p.test, nsrc) if isinstance(p, ast.If) and node in p.body else ""

    def conds(fn, src):  # canonical text -> (node, text as written)
        if fn is None:
            return {}
        return {ast.unparse(x.test): (x, text(x.test, src)) for x in _own(fn) if isinstance(x, (ast.If, ast.While))}
    nc, oc = conds(n, nsrc), conds(o, osrc)
    new_c = [t for t, (x, _) in nc.items() if t not in oc and x.lineno in added]
    old_c = [t for t, (x, _) in oc.items() if t not in nc and x.lineno in removed]
    consumed: set[int] = set()
    for t, t2 in _pair(new_c, old_c):
        new_c.remove(t)
        old_c.remove(t2)
        add("changed_condition", nc[t][0].lineno, old=oc[t2][1], new=nc[t][1])
    for t in new_c:
        x, shown = nc[t]
        if isinstance(x, ast.While):
            add("new_while", x.lineno, cond=shown)
            continue
        ex = _exit(x.body)
        add("new_branch", x.lineno, cond=shown, exit=ex, had_old=had_old)
        if ex:
            consumed.add(x.body[-1].lineno)
    for t in old_c:
        x, shown = oc[t]
        if isinstance(x, ast.If):
            add("removed_branch", x.lineno, "old", cond=shown)

    if had_old:
        new_calls = Counter(ast.unparse(c.func) for c in _own(n) if isinstance(c, ast.Call))
        old_calls = [(ast.unparse(c.func), c.lineno) for c in _own(o) if isinstance(c, ast.Call)]
        old_count, seen = Counter(name for name, _ in old_calls), set()
        for name, line in sorted(old_calls, key=lambda x: x[1]):
            if (line in removed and old_count[name] > new_calls[name] and name not in seen
                    and not SKIP_CALLS.match(name) and "(" not in name and len(name) <= 40):
                seen.add(name)
                add("removed_call", line, "old", call=name)

    def names(fn, typ, key):
        return Counter() if fn is None else Counter(key(x) for x in _own(fn) if isinstance(x, typ))

    old_raises = names(o, ast.Raise, lambda r: _exc_name(r.exc))
    for r in sorted((x for x in _own(n) if isinstance(x, ast.Raise)), key=lambda x: x.lineno):
        exc = _exc_name(r.exc)
        if r.exc is not None and r.lineno in added and r.lineno not in consumed and exc not in old_raises:
            add("new_raise", r.lineno, exc=exc, when=when(r), had_old=had_old)

    def handler_name(h):
        return ast.unparse(h.type) if h.type else "Exception"
    old_handlers = names(o, ast.ExceptHandler, handler_name)
    for h in sorted((x for x in _own(n) if isinstance(x, ast.ExceptHandler)), key=lambda x: x.lineno):
        if h.lineno in added and handler_name(h) not in old_handlers:
            last = h.body[-1]
            add("new_except", h.lineno, exc=handler_name(h), reraise=isinstance(last, ast.Raise) and last.exc is None)

    if had_old:
        old_returns = names(o, ast.Return, ast.unparse)
        for r in sorted((x for x in _own(n) if isinstance(x, ast.Return)), key=lambda x: x.lineno):
            if (r.lineno in added and r.lineno not in consumed and r is not n.body[-1] and when(r)
                    and ast.unparse(r) not in old_returns):
                add("new_return", r.lineno, val=ast.unparse(r.value) if r.value else "None", when=when(r))

    old_loops = names(o, ast.For, lambda x: ast.unparse(x.iter))
    for x in sorted((x for x in _own(n) if isinstance(x, (ast.For, ast.AsyncFor))), key=lambda x: x.lineno):
        literal = isinstance(x.iter, (ast.List, ast.Tuple, ast.Set, ast.Constant))  # never empty, nothing to ask
        if x.lineno in added and ast.unparse(x.iter) not in old_loops and not literal:
            add("new_loop", x.lineno, target=ast.unparse(x.target), iter=text(x.iter, nsrc))
    return facts


CONST_NAME = re.compile(r"^_?[A-Z][A-Z0-9_]+$")


def _constants(tree) -> dict[str, tuple[str, int]]:
    out = {}
    body = tree.body + [b for c in tree.body if isinstance(c, ast.ClassDef) for b in c.body]
    for node in body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and node.value is not None and isinstance(node.target, ast.Name):
            target = node.target.id
        else:
            continue
        if CONST_NAME.match(target):
            out[target] = (ast.unparse(node.value), node.lineno)
    return out


def _usage(name: str, src: str, skip_line: int, fns: dict) -> tuple[str, str] | None:
    """(enclosing function, numbered lines around the first other use of name in src)."""
    lines = src.splitlines()
    for i, text in enumerate(lines, 1):
        if i != skip_line and re.search(rf"\b{re.escape(name)}\b", text):
            fn = next((q for q, node in fns.items() if node.lineno <= i <= node.end_lineno), "")
            ctx = "\n".join(f"{j:>5}   {lines[j - 1].rstrip()}" for j in range(max(1, i - 2), min(len(lines), i + 2) + 1))
            return fn, ctx
    return None


def python_facts(f: FileDiff, old_src: str | None, new_src: str | None) -> list[Fact] | None:
    """Facts for one Python file, or None if either version does not parse."""
    if new_src is None:
        return []
    try:
        nt = ast.parse(new_src)
        ot = ast.parse(old_src) if old_src else ast.Module(body=[], type_ignores=[])
    except SyntaxError:
        return None
    added, removed = _changed_lines(f)
    nf, of = _functions(nt), _functions(ot)
    facts = []
    for qual, n in nf.items():
        o = of.get(qual)
        touched = any(n.lineno <= ln <= n.end_lineno for ln in added) or (
            o is not None and any(o.lineno <= ln <= o.end_lineno for ln in removed))
        if touched:
            facts += _compare_function(qual, f.path, o, n, added, removed, old_src or "", new_src)
    oc = _constants(ot)
    for name, (val, line) in _constants(nt).items():
        if name in oc and oc[name][0] != val and line in added and max(len(val), len(oc[name][0])) <= 40:
            use = _usage(name, new_src, line, nf)
            if use:
                facts.append(Fact("changed_constant", f.path, "", line, "new",
                                  {"name": name, "old": oc[name][0], "new": val, "use_fn": use[0]}, use[1]))
    return facts


# ---------------------------------------------------------------- other languages (regex)

KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "new", "else", "function", "elif", "unless", "until",
            "do", "try", "with", "match", "case", "sizeof", "typeof", "await", "throw", "defer", "go", "select"}
FUNC_RES = [re.compile(p) for p in (
    r"\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)\s*(?:<[^()]*?>)?\s*\(",
    r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*(?::\s*[^=]+)?=>|[A-Za-z_$][\w$]*\s*=>)",
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_]\w*)",
    r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(",
    r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*[?!]?)",
    r"^\s*(?:(?:public|private|protected|internal|static|final|abstract|async|override|virtual|export|default|"
    r"synchronized)\s+)*[\w<>\[\],.?]+\s+([A-Za-z_]\w*)\s*\([^;]*$",
    r"^\s*(?:(?:public|private|protected|static|async|get|set)\s+)*([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*(?::\s*[\w<>\[\]|]+\s*)?\{\s*$",
)]
COND_RE = re.compile(r"^\s*(?:\}\s*)?(?:else\s+if|elif|elsif|if|unless)\b\s*(.+?)\s*(?:\{|:|\bthen)?\s*$")
WHILE_RE = re.compile(r"^\s*(?:\}\s*)?while\b\s*(.+?)\s*(?:\{|:|\bdo)?\s*$")
FOR_RE = re.compile(r"^\s*for\b\s*(.+?)\s*(?:\{|:|\bdo)?\s*$")
RAISE_RE = re.compile(r"^\s*(?:throw|raise)\s+(?:new\s+)?([A-Za-z_][\w.:]*)|\b(panic)!?\s*\(|"
                      r"\breturn\b.*?\b(errors\.New|fmt\.Errorf|Err)\s*\(")
CATCH_RE = re.compile(r"\b(?:catch|except|rescue)\b\s*\(?\s*([A-Za-z_][\w.:]*)?")
EXIT_RE = re.compile(r"^\s*(return\b.*|throw\b.*|raise\b.*|continue\b|break\b)")
CALL_RE = re.compile(r"^\s*(?:await\s+|defer\s+|go\s+)?([A-Za-z_$][\w$]*(?:(?:\.|->|::)[A-Za-z_$][\w$]*)*)\s*\(.*\)\s*;?\s*$")
CONST_RE = re.compile(r"^\s*(?:export\s+)?(?:(?:pub|const|let|var|final|static|public|private|protected|readonly)\s+)*"
                      r"(?:[\w<>\[\]]+\s+)?([A-Z][A-Z0-9_]{2,})\s*(?::\s*[^=]+?)?\s*:?=\s*(.+?);?\s*$")
DEFINE_RE = re.compile(r"^\s*#define\s+([A-Z][A-Z0-9_]{2,})\s+(.+?)\s*$")


def _func_name(line: str) -> str | None:
    for rx in FUNC_RES:
        m = rx.search(line)
        if m and m.group(1) not in KEYWORDS:
            return m.group(1)
    return None


def _strip_cond(s: str) -> str:
    s = s.strip().rstrip("{:").strip()
    if s.startswith("(") and s.endswith(")"):
        depth = 0
        for i, ch in enumerate(s):
            depth += ch == "("
            depth -= ch == ")"
            if depth == 0 and i < len(s) - 1:
                return s
        s = s[1:-1].strip()
    return s


def _paren_body(line: str, name: str) -> str | None:
    m = re.search(rf"\b{re.escape(name)}\s*(?:<[^()]*?>)?\s*\(", line)
    if not m:
        return None
    depth, start = 0, m.end() - 1
    for i in range(start, len(line)):
        depth += line[i] in "([{"
        depth -= line[i] in ")]}"
        if depth == 0:
            return line[start + 1:i]
    return line[start + 1:]


def _sig_params(line: str, name: str, ext: str) -> dict[str, str | None]:
    body = _paren_body(line, name)
    if body is None:
        return {}
    parts, depth, cur = [], 0, ""
    for i, ch in enumerate(body):
        depth += ch in "([{<"
        depth -= ch in ")]}" or (ch == ">" and body[i - 1:i] != "=")
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    out = {}
    for part in filter(None, (p.strip() for p in parts)):
        head, _, default = part.partition("=")
        tokens = WORD_RE.findall(head.split(":")[0] if ":" in head else head)
        if tokens:
            pname = tokens[0] if ext == ".go" or ":" in head else tokens[-1]
            if pname not in ("self", "this", "cls"):
                out[pname] = default.strip() or None
    return out


def _next_exit(rows, i) -> tuple[str, str, int] | None:
    """(verb, code, line) if the first added statement after rows[i] leaves the block."""
    nxt = [r for r in rows[i + 1:i + 4] if r[0] == "+" and r[1].strip() not in ("{", "")]
    if nxt and (m := EXIT_RE.match(nxt[0][1])):
        verb, _, code = m.group(1).strip().rstrip(";").partition(" ")
        return verb, code.strip(), nxt[0][3]
    return None


def _loop_iter(cond: str) -> str:
    m = re.search(r"(?:\bof\b|\bin\b|\brange\b)\s+(.+)$", _strip_cond(cond))
    return m.group(1).strip() if m else _strip_cond(cond)


def regex_facts(f: FileDiff, new_src: str | None) -> list[Fact]:
    facts: list[Fact] = []
    ext = PurePosixPath(f.path).suffix.lower()
    for h in f.hunks:
        rows = list(h.numbered())
        fn = _func_name(h.header) or ""
        plus = [t for p, t, _, _ in rows if p == "+"]
        plus_text = "\n".join(plus)
        minus_conds = [_strip_cond(m.group(1)) for p, t, _, _ in rows if p == "-" and (m := COND_RE.match(t))]
        plus_conds = [_strip_cond(m.group(1)) for t in plus if (m := COND_RE.match(t))]
        minus_defs = {_func_name(t): t for p, t, _, _ in rows if p == "-" and _func_name(t)}
        minus_consts = {m.group(1): m.group(2) for p, t, _, _ in rows if p == "-" and (m := CONST_RE.match(t) or DEFINE_RE.match(t))}
        pairs = dict(_pair([c for c in plus_conds if c not in minus_conds], [c for c in minus_conds if c not in plus_conds]))
        paired, consumed = set(pairs.values()), set()

        def add(kind, line, side="new", **data):
            facts.append(Fact(kind, f.path, fn, line, side, data))

        for i, (p, t, o, n) in enumerate(rows):
            name = _func_name(t)
            if name and p != "-":
                fn = name
            if p == "+":
                if m := COND_RE.match(t):
                    cond = _strip_cond(m.group(1))
                    if cond in minus_conds:
                        continue
                    if cond in pairs:
                        add("changed_condition", n, old=pairs[cond], new=cond)
                        continue
                    ex = _next_exit(rows, i)
                    if ex:
                        consumed.add(ex[2])
                    add("new_branch", n, cond=cond, exit=ex and ex[:2], had_old=not f.new_file)
                elif m := WHILE_RE.match(t):
                    add("new_while", n, cond=_strip_cond(m.group(1)))
                elif (m := FOR_RE.match(t)) and not _loop_iter(m.group(1)).startswith(("[", "(", "{")):
                    add("new_loop", n, target="", iter=_loop_iter(m.group(1)))
                elif (m := RAISE_RE.search(t)) and n not in consumed:
                    add("new_raise", n, exc=next(g for g in m.groups() if g), when="", had_old=not f.new_file,
                        returns_error=bool(m.group(3)), code=t.strip().rstrip(";"))
                elif m := CATCH_RE.search(t):
                    exc, ex = m.group(1) or "", _next_exit(rows, i)
                    add("new_except", n, exc=exc if exc[:1].isupper() else "",
                        reraise=bool(ex and ex[0] in ("throw", "raise")))
                elif name and name in minus_defs:
                    po, pn = _sig_params(minus_defs[name], name, ext), _sig_params(t, name, ext)
                    for pname in pn.keys() - po.keys():
                        add("new_param", n, param=pname, default=pn[pname])
                    for pname in po.keys() - pn.keys():
                        add("removed_param", n, param=pname)
                    for pname in po.keys() & pn.keys():
                        if po[pname] != pn[pname] and None not in (po[pname], pn[pname]):
                            add("changed_default", n, param=pname, old=po[pname], new=pn[pname])
                elif (m := CONST_RE.match(t) or DEFINE_RE.match(t)) and m.group(1) in minus_consts:
                    cname, val, old = m.group(1), m.group(2), minus_consts[m.group(1)]
                    if old != val and max(len(old), len(val)) <= 40 and new_src:
                        use = _usage(cname, new_src, n, {})
                        if use:
                            facts.append(Fact("changed_constant", f.path, "", n, "new",
                                              {"name": cname, "old": old, "new": val, "use_fn": ""}, use[1]))
            elif p == "-":
                if m := COND_RE.match(t):
                    cond = _strip_cond(m.group(1))
                    if cond not in plus_conds and cond not in paired:
                        add("removed_branch", o, "old", cond=cond, exit=None)
                elif (m := CALL_RE.match(t)) and not name:
                    call = m.group(1)
                    if call not in plus_text and not SKIP_CALLS.match(call) and call.split(".")[0] not in KEYWORDS:
                        add("removed_call", o, "old", call=call)
    return facts


EXTRACTORS: dict[str, Callable] = {".py": python_facts}


# ---------------------------------------------------------------- templates

def _code(s: str, limit: int = 60) -> str:
    s = redact(" ".join(s.replace("`", "'").split()))
    return s if len(s) <= limit else s[: limit - 3] + "..."


EDGE = {"True": "`False`", "False": "`True`", "None": "a non-None value", "0": "a negative number", "''": "an empty string",
        '""': "an empty string"}

KIND_WORDS = {
    "branch": ["true", "false", "skip", "return", "none", "null", "empty", "missing", "invalid", "error", "crash"],
    "removed": ["before", "used", "longer", "anymore", "stale", "safe", "already", "duplicate", "handled"],
    "error": ["error", "exception", "raise", "throw", "crash", "fail", "propagate", "catch", "caller"],
    "default": ["default", "caller", "pass", "omit", "value", "zero", "none", "longer", "shorter", "more", "fewer"],
    "param": ["caller", "pass", "default", "zero", "none", "disable", "skip", "value"],
    "loop": ["empty", "zero", "nothing", "iteration", "each", "never", "forever", "infinite", "stop"],
    "constant": ["more", "less", "fewer", "longer", "shorter", "limit", "larger", "smaller", "higher", "lower"],
    "other": ["before", "after", "now", "input"],
}


def _render(fact: Fact) -> tuple[str, list[str], str]:
    d = {k: _code(v) if isinstance(v, str) else v for k, v in fact.data.items()}
    fn = f"`{fact.fn}`" if fact.fn else f"`{PurePosixPath(fact.file).name}`"
    where = f"In {fn}, "
    k = fact.kind
    if k == "changed_condition":
        return (f"{where}the condition changed from `{d['old']}` to `{d['new']}`. Give one input that is handled "
                f"differently now and what happens to it, or explain why every input still behaves the same.",
                ["An input that the old and new conditions treat differently (an edge value), or why none exists",
                 "What happens to that input now compared with before"], "branch")
    if k == "new_branch" and d["exit"]:
        verb, code = d["exit"][0], _code(d["exit"][1], 50)
        cond = d["cond"]
        if verb == "break":
            return (f"{where}the new check `{cond}` stops the loop early. When does it trigger, and what would "
                    f"{fn} produce without it?",
                    [f"The situation in which `{cond}` becomes true",
                     "What the result would be if the loop kept going"], "branch")
        if verb == "continue":
            return (f"{where}the new check `{cond}` skips the rest of the loop body. Which items get skipped, and "
                    f"what would happen to them without the check?",
                    [f"Which items make `{cond}` true",
                     "What the rest of the loop body would have done with them"], "branch")
        return (f"{where}the new check `{cond}` makes it {verb} `{code}` early. What situation is this guarding "
                f"against, and what would go wrong further down without it?",
                [f"The input or state that makes `{cond}` true",
                 "What the later code would do wrong or crash on if the check were missing"], "branch")
    if k == "new_branch" and d.get("had_old"):
        return (f"{where}you added a branch on `{d['cond']}`. What happened before this change in the case it now "
                f"handles, and what is different now?",
                [f"The case or input where `{d['cond']}` is true",
                 "How the old code behaved in that case compared with the new code"], "branch")
    if k == "new_branch":
        return (f"{where}the branch on `{d['cond']}`: what case is it there to handle, and what would go wrong in "
                f"that case without it?",
                [f"The concrete case or input where `{d['cond']}` is true",
                 "The wrong result or failure that would happen without the branch"], "branch")
    if k == "removed_branch":
        return (f"This diff removes the check `{d['cond']}` from {fn}. What used to happen when it was true, and "
                f"what happens to that same case now?",
                [f"What the removed branch did when `{d['cond']}` held",
                 "How that case is handled after the change, and why that is acceptable"], "removed")
    if k == "removed_call":
        return (f"This diff removes the call to `{d['call']}()` in {fn}. What was it doing there, and why is it "
                f"safe to drop?",
                [f"The effect `{d['call']}()` had at that point (state, output or side effect)",
                 "Why the code no longer needs it, or what now covers that job"], "removed")
    if k == "new_except":
        exc = f"`{d['exc']}`" if d["exc"] else "an exception"
        if d.get("reraise"):
            return (f"{fn} now catches {exc}, does some work, and re-raises it. What problem does the work in the "
                    f"handler solve, and what would be left in a bad state without it?",
                    ["What the handler cleans up, restores or records before re-raising",
                     "The bad state or lost work that would result without it"], "error")
        return (f"{fn} now catches {exc}. What did a caller see in that situation before this change, and what do "
                f"they get now that they have to handle?",
                ["Before: the error propagated out of the function to the caller",
                 "Now: the value or behavior the caller receives instead, and how it must handle it"], "error")
    if k == "new_raise":
        cond = f" when `{d['when']}`" if d.get("when") else ""
        if d.get("returns_error"):
            return (f"{fn} now has `{_code(fact.data['code'], 70)}`. Which failure produces that error, and what did "
                    f"the old code return to the caller in that situation?",
                    ["The failure or input that leads to this error return",
                     "What the old code returned instead (the bad value or silent success it prevents)"], "error")
        if d.get("had_old"):
            return (f"{fn} now raises `{d['exc']}`{cond}. What did the old code do with that same input, and what "
                    f"problem does raising here prevent?",
                    [f"The input or state that now triggers `{d['exc']}`",
                     "What the old code did with it (the silent or wrong behavior this prevents)"], "error")
        return (f"{fn} raises `{d['exc']}`{cond}. Which input triggers it, and why raise there instead of "
                f"returning a default value?",
                [f"The input or state that triggers `{d['exc']}`",
                 "Why a silent default would hide a problem from the caller"], "error")
    if k == "changed_default":
        return (f"The default for `{d['param']}` in {fn} changed from `{d['old']}` to `{d['new']}`. What does a "
                f"caller that omits it now experience differently, and what could go wrong for a caller that relied "
                f"on the old default?",
                [f"What `{d['param']}` controls inside {fn}",
                 f"The concrete difference between `{d['old']}` and `{d['new']}` for callers relying on the default"],
                "default")
    if k == "new_param":
        if d["default"] is None:
            return (f"{fn} now requires `{d['param']}`. What does {fn} use it for, and what happens if a caller "
                    f"passes `None` or an empty value?",
                    [f"What `{d['param']}` controls inside {fn}", "The behavior for a None or empty value"], "param")
        edge = EDGE.get(fact.data["default"]) or ("`0`" if re.fullmatch(r"-?\d+(\.\d+)?", fact.data["default"])
                                                    else "`None`")
        return (f"{fn} now takes `{d['param']}={d['default']}`. What changes in behavior when a caller passes "
                f"{edge}?",
                [f"What `{d['param']}` controls inside {fn}", f"What specifically happens with {edge}"], "param")
    if k == "removed_param":
        return (f"{fn} no longer takes `{d['param']}`. What did `{d['param']}` control before, and how is that "
                f"decided now?",
                [f"What `{d['param']}` used to control", "Where that decision comes from after the change"], "removed")
    if k == "new_return":
        return (f"{fn} has a new early `return {d['val']}` when `{d['when']}`. When does that happen in practice, "
                f"and what did the function do in that case before?",
                [f"A concrete situation where `{d['when']}` holds",
                 "What the old code did in that situation (and why returning early is better)"], "branch")
    if k == "changed_constant":
        use = f" Looking at how `{d['use_fn']}` uses it," if d.get("use_fn") else " Looking at where it is used,"
        return (f"`{d['name']}` changed from `{d['old']}` to `{d['new']}`.{use} what will someone running the code "
                f"notice differently?",
                [f"What `{d['name']}` limits or controls where it is used",
                 f"The visible effect of going from `{d['old']}` to `{d['new']}`"], "constant")
    if k == "new_while":
        return (f"{where}the new loop `while {d['cond']}`: what changes on each pass that eventually makes it false, "
                f"and what else can end the loop earlier?",
                [f"What changes each pass so that `{d['cond']}` turns false",
                 "Other exits (break, return, exception) and when they happen, or that there are none"], "loop")
    if k == "new_loop":
        it = d["iter"]
        fixed = re.fullmatch(r"range\((\d+)\)", it) or re.search(r";\s*\w+\s*<=?\s*(\d+)\s*;", it)
        if fixed:
            return (f"{where}the new loop runs at most {fixed.group(1)} times. What ends it early, and what does {fn} "
                    f"do if no pass succeeds?",
                    ["What the loop body does on each pass and what makes it stop early",
                     "What happens after the final pass (fallback, default value or error)"], "loop")
        bound = re.search(r";\s*\w+\s*<=?\s*([A-Za-z_][\w.]*)\s*;", it)
        it = bound.group(1) if bound else it
        empty = f"`{it}` is 0" if bound else f"`{it}` is empty"
        return (f"{where}you added a loop over `{it}`. What happens when {empty}, and what state is left behind "
                f"once the loop finishes?",
                [f"The behavior when {empty}", "What the loop builds up or changes by the end"], "loop")
    return (f"{where}pick one input or call that behaves differently after this change than before. What happens to "
            f"it now, and what happened before?",
            ["A concrete input or situation the change affects", "Its behavior before versus after"], "other")


def _concepts(fact: Fact, kind: str) -> list[str]:
    words = {fact.fn.split(".")[-1]} if fact.fn else set()
    for v in fact.data.values():
        if isinstance(v, str):
            words |= {w for w in WORD_RE.findall(v) if len(w) >= 3}
    return sorted(w for w in words if w) + KIND_WORDS.get(kind, KIND_WORDS["other"])


def to_question(fact: Fact, f: FileDiff) -> Question:
    text, rubric, kind = _render(fact)
    snip = snippet(f, fact.line, fact.side, needle=fact.data.get("call") or fact.data.get("cond"))
    if fact.extra:
        snip += f"\n  ... where it is used:\n{redact(fact.extra)}"
    anchor = fact.fn or next(iter(fact.data.values()), "") or f.path
    return Question(text, rubric, f.path, str(anchor), kind, snip, "static", _concepts(fact, kind))


def _hunk_facts(f: FileDiff) -> list[Fact]:
    out = []
    for h in f.hunks:
        rows = list(h.numbered())
        first = next((r for r in rows if r[0] != " "), None)
        if not first:
            continue
        fn = _func_name(h.header) or next((_func_name(r[1]) for r in rows if _func_name(r[1])), None)
        if fn:  # only code inside a known function; prose and data files get no fallback question
            out.append(Fact("hunk", f.path, fn, first[3] or first[2], "new" if first[0] == "+" else "old"))
    return out


def facts_for(files: list[FileDiff], source: SourceFn) -> list[Fact]:
    out = []
    for f in files:
        new = None if f.deleted else source(f.path, "new")
        extractor = EXTRACTORS.get(PurePosixPath(f.path).suffix.lower())
        facts = None
        if extractor:
            old = None if f.new_file else source(f.old_path or f.path, "old")
            facts = extractor(f, old, new)
        out += facts if facts is not None else regex_facts(f, new)
    seen, unique = set(), []
    for fact in sorted(out, key=lambda x: (PRIORITY.index(x.kind), not x.data.get("exit"))):
        key = (fact.file, fact.kind, repr(sorted(fact.data.items())))
        if key not in seen:
            seen.add(key)
            unique.append(fact)
    return unique


def generate(files: list[FileDiff], source: SourceFn, n: int, avoid=(), diff_idents: set[str] | None = None) -> list[Question]:
    """Up to n valid questions, spread across functions and fact kinds, skipping any text in avoid."""
    from grasp.diff import identifiers
    idents = diff_idents if diff_idents is not None else identifiers(files)
    by_path = {f.path: f for f in files}
    facts = facts_for(files, source) + [h for f in files for h in _hunk_facts(f)]
    candidates = []
    for fact in facts:
        q = to_question(fact, by_path[fact.file])
        if q.text not in avoid and not problems(q, idents) and q.text not in (c[1].text for c in candidates):
            candidates.append((fact, q))
    def template(fact: Fact) -> str:
        ex = fact.data.get("exit")
        return fact.kind + ("" if not ex else "-" + ex[0] if ex[0] in ("break", "continue") else "-exit")

    def overlaps(fact: Fact) -> bool:
        """True if an already picked question names this fact's parameter or constant (it would give hints)."""
        key = fact.data.get("param") or fact.data.get("name")
        return bool(key) and any(re.search(rf"\b{re.escape(key)}\b", q.text) for _, q in picked)

    real = [c for c in candidates if c[0].kind != "hunk"]
    picked: list[tuple[Fact, Question]] = []
    passes = [lambda f: all(f.fn != p.fn and template(f) != template(p) for p, _ in picked),  # spread out
              lambda f: all(template(f) != template(p) for p, _ in picked),  # at least vary the question shape
              lambda f: True]
    for i, ok in enumerate(passes):
        for c in real:
            if len(picked) < n and c not in picked and ok(c[0]) and (i == 2 or not overlaps(c[0])):
                picked.append(c)
    if not picked:  # nothing concrete found: fall back to one anchored question per hunk
        picked = [c for c in candidates if c[0].kind == "hunk"][:n]
    return [q for _, q in picked]


def weak_check(q: Question, answer: str) -> str | None:
    """None if the answer passes the minimal check, else a short reason it did not."""
    if len(answer.split()) < 4 or len(answer.strip()) < 15:
        return "Answer in at least one full sentence."
    low = answer.lower()
    if not any(c.lower() in low for c in q.concepts):
        return f"Your answer should refer to the code involved, for example `{q.anchor}`."
    return None
