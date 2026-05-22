"""Fetch prose documentation from the pandas repo (offline corpus build).

Real mode: shallow + sparse-checkout of pandas-dev/pandas at the
configured ref, caching the clone under ``~/.cache/maintainers-copilot/
pandas-repo/`` so re-runs reuse it. Sparse patterns cover ``README.md``,
``CONTRIBUTING.md``, and ``doc/source/**/*.rst`` (note ``doc/source``,
not ``docs/`` — the pandas repo's documentation tree).

Fixture mode (``fixture_dir`` provided): walks the fixture directly,
bypassing the cache and the network. Used by the corpus-build smoke
against ``tests/fixtures/rag_smoke``.

Each emitted ``DocSource`` carries the file's relative path under the
pandas repo (the ``source_id`` in ``rag_chunks``), its commit timestamp
(``source_timestamp``), and the raw text. Code-only RST files (mostly
auto-generated API reference) are skipped on a per-file heuristic and
the count is reported back so the corpus_report can log it.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("rag.fetch_docs")

DEFAULT_REPO = "https://github.com/pandas-dev/pandas.git"
DEFAULT_CACHE = Path.home() / ".cache" / "maintainers-copilot" / "pandas-repo"

# Files to keep regardless of heuristic.
ROOT_FILES = ("README.md", "CONTRIBUTING.md")

# Files under doc/source/**/*.rst pass through the code-density filter.
DOC_GLOB = "doc/source/**/*.rst"

# Lines we treat as RST/Sphinx directives or roles. If >30% of a file's
# non-blank lines look like this, the file is mostly code/auto-generated,
# not prose, and we skip it.
DIRECTIVE_LINE = re.compile(r"^\s*\.\.\s+\S")
DIRECTIVE_THRESHOLD = 0.30


@dataclass(frozen=True)
class DocSource:
    source_id: str          # path inside the pandas repo (e.g. doc/source/user_guide/groupby.rst)
    source_timestamp: datetime
    raw_text: str


@dataclass
class FetchResult:
    sources: list[DocSource]
    skipped_files: int


def _is_code_heavy(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return True
    directive_lines = sum(1 for ln in lines if DIRECTIVE_LINE.match(ln))
    return directive_lines / len(lines) > DIRECTIVE_THRESHOLD


def _file_timestamp(repo_root: Path, rel_path: str) -> datetime:
    """Last-commit time for `repo_root/rel_path`, falling back to mtime."""
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--format=%cI", "--", rel_path],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if out:
            return datetime.fromisoformat(out)
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        pass
    mtime = (repo_root / rel_path).stat().st_mtime
    return datetime.fromtimestamp(mtime, tz=UTC)


def _ensure_clone(cache_dir: Path, repo: str, ref: str) -> Path:
    """Shallow + sparse-checkout the pandas repo at `ref` into `cache_dir`."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    git_dir = cache_dir / ".git"
    if not git_dir.exists():
        logger.info("cold cache — shallow-cloning %s@%s -> %s", repo, ref, cache_dir)
        subprocess.check_call(
            [
                "git",
                "clone",
                "--depth=1",
                "--filter=blob:none",
                "--sparse",
                "--branch",
                ref,
                repo,
                str(cache_dir),
            ]
        )
        subprocess.check_call(
            [
                "git",
                "-C",
                str(cache_dir),
                "sparse-checkout",
                "set",
                "--no-cone",
                "README.md",
                "CONTRIBUTING.md",
                "doc/source/",
            ]
        )
    else:
        logger.info("warm cache at %s — refreshing", cache_dir)
        subprocess.check_call(
            ["git", "-C", str(cache_dir), "fetch", "--depth=1", "origin", ref]
        )
        subprocess.check_call(
            ["git", "-C", str(cache_dir), "checkout", "FETCH_HEAD"]
        )
    return cache_dir


def _walk_repo(repo_root: Path) -> tuple[list[Path], list[Path]]:
    """Collect (root_files, doc_files) under `repo_root` matching the patterns."""
    root = [repo_root / name for name in ROOT_FILES if (repo_root / name).is_file()]
    doc = sorted(repo_root.glob(DOC_GLOB))
    return root, doc


def fetch(
    *,
    repo: str = DEFAULT_REPO,
    ref: str = "main",
    cache_dir: Path = DEFAULT_CACHE,
    fixture_dir: Path | None = None,
) -> FetchResult:
    """Return prose docs as `DocSource`s, plus the count of skipped files."""
    if fixture_dir is not None:
        return _fetch_from_dir(fixture_dir)
    repo_root = _ensure_clone(cache_dir, repo, ref)
    return _fetch_from_dir(repo_root, _walk_repo)


def _fetch_from_dir(
    repo_root: Path,
    walker: object = None,
) -> FetchResult:
    """Pure walk + filter step. Reused for fixture mode and real mode."""
    if walker is None:
        # Fixture mode: only the docs/ subdirectory; the fixture's own
        # README.md / CONTRIBUTING.md are documentation about the fixture
        # itself, not pandas prose, and must not be included.
        root_files: list[Path] = []
        doc_files: list[Path] = sorted(repo_root.glob("docs/*.rst"))
    else:
        root_files, doc_files = walker(repo_root)  # type: ignore[operator]

    sources: list[DocSource] = []
    skipped = 0
    for path in root_files + doc_files:
        rel = path.relative_to(repo_root).as_posix()
        raw = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix == ".rst" and _is_code_heavy(raw):
            logger.info("skip code-heavy file: %s", rel)
            skipped += 1
            continue
        timestamp = _file_timestamp(repo_root, rel)
        sources.append(
            DocSource(source_id=rel, source_timestamp=timestamp, raw_text=raw)
        )
    return FetchResult(sources=sources, skipped_files=skipped)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    fixture = os.environ.get("FIXTURE_DIR")
    result = fetch(fixture_dir=Path(fixture) if fixture else None)
    print(f"sources={len(result.sources)} skipped={result.skipped_files}")
    for src in result.sources:
        print(f"  {src.source_id}  {len(src.raw_text)} chars  {src.source_timestamp.isoformat()}")
