import json
from pathlib import Path


def parse_config(path, strict=False):
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if strict and not isinstance(data, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return {k.lower(): v for k, v in data.items()}


def merge(base, override):
    result = dict(base)
    for key, value in override.items():
        if value is None:
            result.pop(key, None)
            continue
        result[key] = value
    return result
