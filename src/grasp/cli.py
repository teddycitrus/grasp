"""Command line entry point."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from grasp import __version__, config, hook, llm, run
from grasp.diff import git

KEY_ENVS = {
    "ANTHROPIC_API_KEY": {"provider": "anthropic", "base_url": "https://api.anthropic.com", "model": "claude-haiku-4-5"},
    "OPENAI_API_KEY": {"provider": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
}
KIND_LABELS = {"branch": "branches and edge cases", "removed": "removed code", "default": "changed defaults",
               "param": "new parameters", "error": "error handling", "loop": "loops", "constant": "changed constants",
               "impact": "impact on other code", "why": "design choices", "other": "general behavior"}


def _ask(prompt: str, default: str, yes: bool) -> str:
    if yes:
        return default
    try:
        return input(f"{prompt} [{default}]: ").strip() or default
    except EOFError:
        return default


def _choice(prompt: str, options, default: str, yes: bool) -> str:
    by_lower = {o.lower(): o for o in options}
    for _ in range(5):
        v = _ask(f"{prompt} ({'/'.join(options)})", default, yes).lower()
        if v in by_lower:
            return by_lower[v]
        print(f"  choose one of: {', '.join(options)}")
    print(f"  using {default}")
    return default


def _test_key(b: llm.LLMBackend) -> str:
    t0 = time.monotonic()
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    try:
        b.chat("Reply with JSON only.", 'Reply with {"ok": true}', schema, max_tokens=20)
    except llm.BackendError as e:
        return f"key test failed: {e}"
    return f"key works ({time.monotonic() - t0:.1f}s)"


def _configure(cfg: dict, yes: bool) -> dict:
    local = llm.from_config("local", cfg, {})
    status = local.health()
    keys = [k for k in KEY_ENVS if os.environ.get(k)]
    print("Detected:")
    print(f"  Ollama at {local.base_url}: " + {
        "ok": f"running, {local.model} available",
        "no-model": f"running, but {local.model} is not pulled. Run: ollama pull {local.model}",
        "down": "not reachable (static questions will be used unless you start it)"}[status])
    print(f"  API keys in environment: {', '.join(keys) or 'none'}")
    print("Backends: auto = local, then cloud if enabled, then static. static = no model, honor-system grading.")
    cfg["backend"] = _choice("Backend", config.BACKENDS, "auto", yes)
    cfg["mode"] = _choice("Mode", config.MODES, "normal", yes)
    cfg["threshold"] = int(_ask("Ask only when at least this many meaningful lines changed", str(cfg["threshold"]), yes))
    if cfg["backend"] in ("local", "auto") and status == "no-model":
        print(f"  Pull the model first: ollama pull {local.model}  (low RAM: ollama pull qwen2.5-coder:3b and set "
              f"local.model)")
    if cfg["backend"] == "cloud" or (cfg["backend"] == "auto" and keys):
        if not keys:
            print("  No API key found. Export ANTHROPIC_API_KEY or OPENAI_API_KEY, then rerun `grasp init`.")
            return cfg
        env = keys[0] if len(keys) == 1 else _choice("Which key", keys, keys[0], yes)
        preset = KEY_ENVS[env]
        print(f"\nPrivacy: the cloud backend sends the staged diff (after filtering and redaction) and your answers\n"
              f"to {preset['base_url']}. Lockfiles, binaries, vendored and secret-looking files, and .graspignore\n"
              f"matches are never sent; secret-like strings are replaced with [REDACTED]. Nothing else leaves your\n"
              f"machine. The key is read from ${env} at run time and is never written to disk.")
        enable = "y" if cfg["backend"] == "cloud" else "n"
        if _ask("Enable the cloud backend? (y/n)", enable, yes and cfg["backend"] != "cloud").lower().startswith("y"):
            cfg["cloud"] |= preset | {"api_key_env": env}
            print("  " + _test_key(llm.from_config("cloud", cfg, os.environ)))
        else:
            cfg["cloud"]["api_key_env"] = ""
    return cfg


def cmd_init(yes: bool) -> int:
    try:
        root = git("rev-parse", "--show-toplevel").strip()
    except subprocess.CalledProcessError:
        print("grasp: run `grasp init` inside a git repository.")
        return 1
    path = config.config_path()
    cfg = config.load()
    if path.exists() and _ask(f"Config found at {path}. Keep it? (y/n)", "y", yes).lower().startswith("y"):
        print(f"Using config at {path}")
    else:
        cfg = _configure(cfg, yes)
        print(f"Wrote {config.save(cfg)}")
    try:
        for line in hook.install(root):
            print(line)
    except hook.HookError as e:
        print(f"grasp: {e}")
        return 1
    print(f"Done. grasp ({cfg['backend']} backend, {cfg['mode']} mode) runs on your next `git commit` with "
          f"{cfg['threshold']}+ meaningful lines.\nSkip once with GRASP_SKIP=1 or git commit --no-verify; both are logged.")
    return 0


def cmd_uninstall() -> int:
    try:
        lines = hook.uninstall(git("rev-parse", "--show-toplevel").strip())
    except subprocess.CalledProcessError:
        print("grasp: run this inside a git repository.")
        return 1
    print("\n".join(lines) or "No grasp hooks found in this repository.")
    return 0


def cmd_log(n: int) -> int:
    entries = config.read_log()
    if not entries:
        print(f"No attempts logged yet ({config.log_path()}).")
        return 0
    for e in entries[-n:]:
        grades = " ".join(q.get("grade", "?") for q in e.get("questions", []))
        why = e.get("reason") or grades
        print(f"{e.get('ts', '')[:16].replace('T', ' ')}  {Path(e.get('repo', '')).name:<16} {e.get('result', ''):<14}"
              f"{e.get('backend', '-'):<14} {why}")
    recent = entries[-50:]
    asked = [q for e in recent for q in e.get("questions", [])]
    missed = Counter(q.get("kind", "other") for q in asked if q.get("grade") != "pass")
    total = Counter(q.get("kind", "other") for q in asked)
    skips = Counter(e.get("reason", "") for e in recent if e.get("result") == "skip")
    print(f"\nLast {len(recent)} attempts:")
    if missed:
        spots = sorted(missed, key=lambda k: (-missed[k] / total[k], -total[k]))[:3]
        print("  Weak spots: " + ", ".join(f"{KIND_LABELS.get(k, k)} ({missed[k]} of {total[k]} not fully passed)"
                                            for k in spots))
    elif asked:
        print("  Weak spots: none, every question passed.")
    if skips:
        print(f"  Skips: {sum(skips.values())} (" + ", ".join(f"{r}: {c}" for r, c in skips.items()) + ")")
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    p = argparse.ArgumentParser(prog="grasp", description="Explain your staged diff before it lands.")
    p.add_argument("--version", action="version", version=f"grasp {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")
    sub.add_parser("init", help="install the hooks in this repo and set up config").add_argument(
        "-y", "--yes", action="store_true", help="accept defaults without prompting (never enables cloud)")
    sub.add_parser("uninstall", help="remove grasp's blocks from this repo's hooks")
    sub.add_parser("log", help="show recent attempts and weak spots").add_argument(
        "-n", type=int, default=10, help="number of attempts to show (default 10)")
    e = sub.add_parser("eval", help="print generated questions for fixture diffs, with automatic checks")
    e.add_argument("path", nargs="?", help="directory of cases (default: bundled fixtures)")
    e.add_argument("--backend", choices=["static", "local", "cloud"], default="static")
    e.add_argument("--snippets", action="store_true", help="also print the code shown with each question")
    sub.add_parser("hook", help="entry point used by the git hooks").add_argument(
        "name", choices=["pre-commit", "post-commit"])
    a = p.parse_args(argv)
    if a.cmd == "init":
        return cmd_init(a.yes)
    if a.cmd == "uninstall":
        return cmd_uninstall()
    if a.cmd == "log":
        return cmd_log(a.n)
    if a.cmd == "eval":
        from grasp.evaluate import run_eval
        return run_eval(a.path, a.backend, config.load(), os.environ, a.snippets)
    return run.pre_commit() if a.name == "pre-commit" else run.post_commit()
