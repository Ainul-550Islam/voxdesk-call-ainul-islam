"""Measure pytest node count once per exact test/config/environment snapshot.

Repository statistics and due-diligence generation both need the *collected*
node count, not an estimate. A full pytest collection imports the whole test
suite and is expensive, so this helper shares a fingerprinted cache between
those commands. The cache is valid only while all Python tests, pytest config,
Python, pytest, and pytest plugin versions are unchanged.
"""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterator

CACHE_VERSION = 1
CACHE_RELATIVE_PATH = Path(".cache") / "pytest-collection-stats.json"
PYTEST_CONFIGS = (
    "pytest.ini",
    ".pytest.ini",
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
    "conftest.py",
)


def _environment_fingerprint() -> bytes:
    """Return environment details that can affect pytest collection."""
    pieces = [
        sys.implementation.name,
        sys.version,
        sys.executable,
    ]
    distributions = []
    for distribution in importlib.metadata.distributions():
        name = (distribution.metadata.get("Name") or "").strip().lower().replace("_", "-")
        if name == "pytest" or name.startswith("pytest-"):
            distributions.append((name, distribution.version or ""))
    pieces.extend(f"{name}=={version}" for name, version in sorted(set(distributions)))
    return "\n".join(pieces).encode("utf-8")


def _input_files(root: Path) -> list[Path]:
    files: set[Path] = set()
    tests = root / "tests"
    if tests.is_dir():
        files.update(path for path in tests.rglob("*.py") if path.is_file())
    files.update(path for name in PYTEST_CONFIGS if (path := root / name).is_file())
    helper_path = Path(__file__).resolve()
    if helper_path.is_relative_to(root):
        files.add(helper_path)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def _fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    digest.update(f"cache-version={CACHE_VERSION}\0".encode("ascii"))
    digest.update(str(root.resolve()).encode("utf-8"))
    digest.update(b"\0")
    digest.update(_environment_fingerprint())
    digest.update(b"\0")
    for path in _input_files(root):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        digest.update(b"\0")
    return digest.hexdigest()


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _cached_count(cache_path: Path, fingerprint: str) -> int | None:
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    count = data.get("collected_pytest_nodes")
    if (
        data.get("schema_version") == CACHE_VERSION
        and data.get("fingerprint") == fingerprint
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count > 0
    ):
        return count
    return None


def measure_collected_pytest_nodes(
    root: Path,
    *,
    timeout: int = 900,
    use_cache: bool = True,
) -> int:
    """Return the exact unique pytest collection count for ``root``.

    Collection is still performed by pytest itself. The cached value is reused
    only when the test sources, pytest settings, interpreter, and pytest plugin
    versions match the snapshot that produced it.
    """
    root = root.resolve()
    if not (root / "tests").is_dir():
        raise FileNotFoundError(f"pytest test directory does not exist: {root / 'tests'}")
    cache_path = root / CACHE_RELATIVE_PATH
    lock_path = cache_path.with_suffix(".lock")

    with _exclusive_lock(lock_path):
        fingerprint = _fingerprint(root)
        if use_cache:
            cached = _cached_count(cache_path, fingerprint)
            if cached is not None:
                return cached

        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "--collect-only",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"pytest collection exceeded {timeout} seconds") from exc

        if result.returncode != 0:
            raise RuntimeError(
                "pytest --collect-only failed; refusing to publish an unmeasured count\n"
                + (result.stdout or "")[-2000:]
                + (result.stderr or "")[-2000:]
            )
        nodes = [
            line
            for line in result.stdout.splitlines()
            if line.startswith("tests/") and "::" in line
        ]
        if not nodes or len(nodes) != len(set(nodes)):
            raise RuntimeError("pytest collection produced no unique node list")
        count = len(nodes)

        if use_cache:
            payload = {
                "schema_version": CACHE_VERSION,
                "fingerprint": fingerprint,
                "collected_pytest_nodes": count,
            }
            temporary = cache_path.with_name(f"{cache_path.name}.{os.getpid()}.tmp")
            temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            temporary.replace(cache_path)
        return count
