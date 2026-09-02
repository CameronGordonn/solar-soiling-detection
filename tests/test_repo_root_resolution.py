"""Guard against the repo-root path bug, which has now bitten this repo twice.

`scripts/predict/compare_runs.py` once used `Path(__file__).parent.parent` to find
the repo root. From `scripts/predict/` that is `scripts/`, so it silently found
zero runs. It was fixed to `parents[2]`.

The identical bug was still live in `scripts/predict/ingest_nrel_soiling_map.py`
as of 2026-08-27: every default path resolved under `scripts/data/external/`, so
re-running the NREL label ingestion would have written the Stage-2 training labels
to a directory nothing reads, while leaving the real `data/external/` copy stale.
Nothing would have errored.

This test checks the whole class rather than the two instances: any module-level
or main() repo-root expression must resolve to a directory that actually looks
like the repo root.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: A directory is the repo root if it holds these.
ROOT_MARKERS = ("pyproject.toml", "src", "scripts", "configs")


def _is_repo_root(p: Path) -> bool:
    return all((p / m).exists() for m in ROOT_MARKERS)


def test_markers_identify_the_real_root():
    assert _is_repo_root(REPO), f"{REPO} should be the repo root"
    assert not _is_repo_root(REPO / "scripts"), "scripts/ must NOT look like the root"


def _root_expressions(path: Path) -> list[tuple[str, int, int]]:
    """Find `Path(__file__).resolve().parent(s)...` chains assigned to a root-ish name.

    Returns (variable_name, lineno, n_levels_up).
    """
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not node.targets:
            continue
        tgt = node.targets[0]
        name = getattr(tgt, "id", None)
        if not name or "root" not in name.lower() and name not in ("REPO", "_REPO"):
            continue
        src = ast.unparse(node.value)
        if "__file__" not in src:
            continue
        # Normalise both spellings to "how many times .parent is applied to the
        # FILE path". They are NOT the same number:
        #   Path(f).parents[0]  ==  Path(f).parent            -> 1 application
        #   Path(f).parents[2]  ==  f.parent.parent.parent    -> 3 applications
        # Conflating them is an off-by-one, and this test had exactly that bug on
        # its first run: it flagged 66 correct files as broken.
        if ".parents[" in src:
            try:
                n = int(src.split(".parents[")[1].split("]")[0]) + 1
            except (IndexError, ValueError):
                continue
        else:
            n = src.count(".parent")
        if n:
            out.append((name, node.lineno, n))
    return out


PY_FILES = sorted(
    p for p in list((REPO / "scripts").rglob("*.py")) + list((REPO / "src").rglob("*.py"))
    if "__pycache__" not in p.parts
)


@pytest.mark.parametrize("path", PY_FILES, ids=lambda p: str(p.relative_to(REPO)))
def test_repo_root_expressions_resolve_to_the_repo_root(path: Path):
    """Every repo-root expression must actually land on the repo root."""
    for name, lineno, levels in _root_expressions(path):
        resolved = path.resolve()
        for _ in range(levels):
            resolved = resolved.parent
        assert _is_repo_root(resolved), (
            f"{path.relative_to(REPO)}:{lineno}: `{name}` goes {levels} level(s) up "
            f"from {path.relative_to(REPO)} and lands on {resolved}, which is not the "
            f"repo root. It should be parents[{len(path.relative_to(REPO).parts) - 2}]. "
            f"This is the compare_runs.py / ingest_nrel_soiling_map.py bug: nothing "
            f"errors, the paths just silently point somewhere nothing reads."
        )
