# notebook_runner.py
# Executes another .ipynb in the current global namespace without requiring nbformat.
import json
from pathlib import Path

def run_ipynb(path, globs=None):
    if globs is None:
        globs = globals()
    path = Path(path)
    nb = json.loads(path.read_text(encoding="utf-8"))
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        code = "".join(cell.get("source", []))
        # Skip pure install cells and old %run loader cells.
        stripped = code.strip()
        if not stripped:
            continue
        if stripped.startswith("!pip"):
            continue
        if stripped.startswith("%run"):
            target = stripped.split(maxsplit=1)[1].strip()
            run_ipynb(path.parent / target, globs)
            continue
        # Jupyter-only helper lines are not valid Python in exec.
        exec(compile(code, str(path), "exec"), globs)
