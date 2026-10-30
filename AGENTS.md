# Agent Notes

## Commands
- Setup: `python -m venv .venv` then `.venv/Scripts/python -m pip install -e ".[dev]"` (Windows paths; `bin/` elsewhere).
- Tests: `.venv/Scripts/python -m pytest -q` (git hook tests need the venv's `grasp` script installed).
- Question quality: `.venv/Scripts/grasp eval` (static), `--backend local` (Ollama, slow on CPU), `--backend cloud`. Exit code 1 means a check failed.
- Offline proof: put a `sitecustomize.py` that breaks `socket` on `PYTHONPATH` and run `pytest tests/test_e2e.py tests/test_static.py`.

## Rules
- Never run `git commit` or `git push` in this repo; the user does that. Temp repos made by tests are fine.
- Runtime dependencies: httpx only. CLI is argparse.
- README follows the Best-README-Template layout with an Architecture section before Getting Started.
