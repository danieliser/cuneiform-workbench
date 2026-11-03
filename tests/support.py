"""Hermetic test roots: every test builds its own research root and UI home under a scratch directory.

Nothing here reads or writes the canonical research root's snapshots. The three small retained
evidence samples are copied (read-only source) into the scratch root when present.
"""
import os
import shutil
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REAL_HOME = Path(os.environ.get('CUNEIFORM_UI_REAL_HOME', str(Path.home() / '.local/share/cuneiform-ui'))).expanduser()
REAL_RESEARCH = Path.home() / '.local/share/cuneiform-research'
REPO = Path(os.environ.get('CUNEIFORM_SOURCE_REPO', str(Path.home() / 'Projects/Fun/cuneiform-research')))
SAMPLES = ['ebl-frozen-example.json', 'ebl-one', 'cdli-three.csv', 'source-receipts.json']


def make_roots(tag: str) -> tuple[Path, Path, Path]:
    """Return (scratch, research_root, ui_home). The caller removes ``scratch`` (use ``rm``)."""
    scratch = REAL_HOME / 'test-roots' / f'{tag}-{os.getpid()}-{uuid.uuid4().hex[:8]}'
    research, home = scratch / 'research', scratch / 'home'
    (research / 'evidence').mkdir(parents=True)
    home.mkdir(parents=True)
    for name in SAMPLES:
        src = REAL_RESEARCH / 'evidence' / name
        if src.is_file():
            shutil.copyfile(src, research / 'evidence' / name)
    return scratch, research, home


def rm(path: Path) -> None:
    """Remove a scratch tree, including backend-published read-only (0444/0555) snapshot files."""
    if not path.exists():
        return
    for p in path.rglob('*'):
        try:
            p.chmod(0o755 if p.is_dir() else 0o644)
        except OSError:
            pass
    path.chmod(0o755)
    shutil.rmtree(path, ignore_errors=True)


def writable(p: Path) -> None:
    p.chmod(0o755 if p.is_dir() else 0o644)
