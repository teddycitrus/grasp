import json
from pathlib import Path


def parse_config(path):
    path = Path(path)
    data = json.loads(path.read_text())
    return {k.lower(): v for k, v in data.items()}


def merge(base, override):
    result = dict(base)
    for key, value in override.items():
        result[key] = value
    return result
