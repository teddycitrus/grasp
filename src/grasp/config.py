"""Config file and attempt log. Paths follow the XDG defaults on every OS."""
from __future__ import annotations

import json
import os
import tomllib
from datetime import datetime, timezone
from pathlib import Path

BACKENDS = ("auto", "static", "local", "cloud")
MODES = ("strict", "normal", "advisory")
DEFAULTS = {
    "backend": "auto",
    "mode": "normal",
    "threshold": 15,
    "local": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5-coder:7b",
        "max_diff_tokens": 3000,
        "timeout": 120,
    },
    "cloud": {
        "provider": "anthropic",
        "base_url": "https://api.anthropic.com",
        "model": "claude-haiku-4-5",
        "api_key_env": "",
        "max_diff_tokens": 12000,
        "timeout": 30,
    },
}


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "grasp" / "config.toml"


def log_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "grasp" / "log.jsonl"


def load() -> dict:
    cfg = {k: dict(v) if isinstance(v, dict) else v for k, v in DEFAULTS.items()}
    path = config_path()
    if path.exists():
        for k, v in tomllib.loads(path.read_text(encoding="utf-8")).items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    if cfg["backend"] not in BACKENDS:
        raise ValueError(f"backend must be one of {BACKENDS}, got {cfg['backend']!r}")
    if cfg["mode"] not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {cfg['mode']!r}")
    return cfg


def _toml(v) -> str:
    if isinstance(v, bool):
        return str(v).lower()
    return str(v) if isinstance(v, (int, float)) else json.dumps(v)


def save(cfg: dict) -> Path:
    lines = ["# grasp config. The API key itself is never stored here, only the env var name."]
    lines += [f"{k} = {_toml(v)}" for k, v in cfg.items() if not isinstance(v, dict)]
    for k, v in cfg.items():
        if isinstance(v, dict):
            lines += ["", f"[{k}]"] + [f"{kk} = {_toml(vv)}" for kk, vv in v.items()]
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def log(entry: dict) -> None:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def read_log() -> list[dict]:
    path = log_path()
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
