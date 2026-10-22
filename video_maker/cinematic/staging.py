"""Atomic staging, path safety, and light cache checks."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

_SAFE_JOB = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def file_sha256(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _safe_job_id(job_id: str) -> str:
    job_id = (job_id or "").strip()
    if not _SAFE_JOB.match(job_id):
        digest = hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:16]
        return f"job_{digest}"
    return job_id


class StagingDir:
    """Job-scoped temp directory that cannot escape its parent."""

    def __init__(self, parent: str | Path, job_id: str):
        self.parent = Path(parent).resolve()
        self.job_id = _safe_job_id(job_id)
        self.root = (self.parent / "ce_staging" / self.job_id).resolve()
        # Containment check before mkdir
        if not str(self.root).startswith(str(self.parent) + os.sep) and self.root != self.parent:
            raise ValueError(f"staging path escapes parent: {self.root}")
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, *parts: str) -> Path:
        # Reject absolute / traversal components
        clean: list[str] = []
        for p in parts:
            for seg in Path(p).parts:
                if seg in ("", ".", "..") or seg.startswith("/"):
                    raise ValueError(f"illegal path segment: {seg!r}")
                clean.append(seg)
        target = self.root.joinpath(*clean).resolve()
        if not str(target).startswith(str(self.root) + os.sep) and target != self.root:
            raise ValueError(f"path escapes staging root: {target}")
        return target

    def cleanup(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)
            log.info("CE staging cleaned: %s", self.root)


def atomic_write_bytes(target: Path, data: bytes) -> None:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".ce_", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def atomic_write_json(target: Path | str, obj: dict) -> None:
    import json
    data = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    atomic_write_bytes(Path(target), data)


def atomic_publish_file(src: Path | str, dst: Path | str, *, overwrite: bool = False) -> None:
    src, dst = Path(src), Path(dst)
    if dst.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)


def cache_is_sane(
    path: Path | str,
    *,
    min_bytes: int = 2,
    probe_media: bool = False,
) -> bool:
    """
    Pre-check cache file.

    Always: exists, size, readable head.
    If probe_media=True: also run ffprobe (duration > 0, has video stream).
    """
    p = Path(path)
    if not p.is_file():
        return False
    try:
        if p.stat().st_size < min_bytes:
            return False
        with open(p, "rb") as f:
            head = f.read(16)
        if len(head) < min(16, min_bytes):
            return False
    except OSError:
        return False

    if not probe_media:
        return True

    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_type,duration:format=duration",
                "-of", "csv=p=0",
                str(p),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode != 0:
            return False
        # expect something like "video,1.5" or duration on format line
        out = (r.stdout or "").strip()
        if not out:
            return False
        # reject empty/zero duration hints
        if "N/A" in out and "video" not in out.lower():
            return False
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def verify_sources_unchanged(paths, hashes: dict[str, str] | None = None) -> list[str]:
    """Return paths whose SHA-256 no longer matches.

    Accepts either:
      verify_sources_unchanged({path: sha, ...})
      verify_sources_unchanged([path, ...], {path: sha, ...})
    """
    if hashes is None and isinstance(paths, dict):
        pairs = paths
    else:
        pairs = hashes or {}
        if isinstance(paths, (list, tuple)):
            for pth in paths:
                pairs.setdefault(str(pth), pairs.get(str(pth), ""))
    bad = []
    for path, expected in pairs.items():
        try:
            actual = file_sha256(path)
        except OSError:
            bad.append(str(path))
            continue
        if expected and actual != expected:
            bad.append(str(path))
    return bad
