"""Face / subject ROI detection for framing.

Priority:
  1) Explicit clip ROI from host metadata
  2) OpenCV Haar frontal face (bundled cascade XML — no internet)
  3) Skin-tone + center-weighted saliency (numpy)
  4) Mild center crop

Always returns a valid CropRect. Never raises into the pipeline.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .edit_plan import CropRect

log = logging.getLogger(__name__)

_CASCADE_NAME = "haarcascade_frontalface_default.xml"
_CASCADE_CACHE: Any = None


def _cascade_path() -> Path:
    return Path(__file__).resolve().parent / "data" / _CASCADE_NAME


def _load_cascade():
    """Load CascadeClassifier once. Returns None if cv2 or XML missing."""
    global _CASCADE_CACHE
    if _CASCADE_CACHE is not None:
        return _CASCADE_CACHE if _CASCADE_CACHE is not False else None
    try:
        import cv2  # type: ignore
    except ImportError:
        log.debug("cv2 not available — face detect disabled")
        _CASCADE_CACHE = False
        return None
    path = _cascade_path()
    if not path.is_file():
        # try system paths
        candidates = [
            "/usr/share/opencv4/haarcascades/" + _CASCADE_NAME,
            "/usr/share/opencv/haarcascades/" + _CASCADE_NAME,
        ]
        path = next((Path(p) for p in candidates if Path(p).is_file()), path)
    if not path.is_file():
        log.warning("Haar cascade XML not found at %s", path)
        _CASCADE_CACHE = False
        return None
    clf = cv2.CascadeClassifier(str(path))
    if clf.empty():
        log.warning("Haar cascade failed to load: %s", path)
        _CASCADE_CACHE = False
        return None
    _CASCADE_CACHE = clf
    return clf


def face_detector_available() -> bool:
    return _load_cascade() is not None


def _clip_roi(clip: dict[str, Any] | None) -> CropRect | None:
    if not clip:
        return None
    roi = clip.get("roi") or clip.get("crop")
    if isinstance(roi, CropRect):
        return roi if not roi.validate() else None
    if isinstance(roi, dict):
        try:
            c = CropRect(
                x=float(roi.get("x", 0)),
                y=float(roi.get("y", 0)),
                w=float(roi.get("w", 1)),
                h=float(roi.get("h", 1)),
            )
            return c if not c.validate() else None
        except (TypeError, ValueError):
            return None
    return None


def _load_profile_cascade():
    try:
        import cv2  # type: ignore
    except ImportError:
        return None
    path = Path(__file__).resolve().parent / "data" / "haarcascade_profileface.xml"
    if not path.is_file():
        return None
    clf = cv2.CascadeClassifier(str(path))
    return None if clf.empty() else clf


def detect_faces_bgr(img) -> list[tuple[int, int, int, int]]:
    """
    Detect faces (frontal + profile) in a BGR numpy image.
    Returns list of (x, y, w, h) in pixels, largest first.
    """
    clf = _load_cascade()
    if clf is None:
        return []
    try:
        import cv2  # type: ignore
        if img is None or getattr(img, "size", 0) == 0:
            return []
        h, w = img.shape[:2]
        if w < 24 or h < 24:
            return []
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        min_sz = (max(24, w // 20), max(24, h // 20))
        faces = clf.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=4, flags=0, minSize=min_sz,
        )
        boxes: list[tuple[int, int, int, int]] = []
        if faces is not None and len(faces) > 0:
            boxes.extend((int(x), int(y), int(fw), int(fh)) for (x, y, fw, fh) in faces)
        # Profile (left)
        pclf = _load_profile_cascade()
        if pclf is not None:
            pf = pclf.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=4, flags=0, minSize=min_sz,
            )
            if pf is not None and len(pf) > 0:
                boxes.extend((int(x), int(y), int(fw), int(fh)) for (x, y, fw, fh) in pf)
            # mirrored for right profile
            flipped = cv2.flip(gray, 1)
            pf2 = pclf.detectMultiScale(
                flipped, scaleFactor=1.1, minNeighbors=4, flags=0, minSize=min_sz,
            )
            if pf2 is not None and len(pf2) > 0:
                for (x, y, fw, fh) in pf2:
                    boxes.append((int(w - x - fw), int(y), int(fw), int(fh)))
        # NMS-ish: drop heavy overlaps, keep larger
        boxes = _nms(boxes, iou_thresh=0.35)
        boxes.sort(key=lambda b: b[2] * b[3], reverse=True)
        return boxes
    except Exception as e:
        log.debug("detect_faces_bgr failed: %s", e)
        return []


def _nms(boxes: list[tuple[int, int, int, int]], iou_thresh: float = 0.35) -> list[tuple[int, int, int, int]]:
    if not boxes:
        return []
    boxes = sorted(boxes, key=lambda b: b[2] * b[3], reverse=True)
    keep: list[tuple[int, int, int, int]] = []
    for b in boxes:
        x, y, w, h = b
        ok = True
        for kx, ky, kw, kh in keep:
            ix1, iy1 = max(x, kx), max(y, ky)
            ix2, iy2 = min(x + w, kx + kw), min(y + h, ky + kh)
            inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            union = w * h + kw * kh - inter
            if union > 0 and inter / union > iou_thresh:
                ok = False
                break
        if ok:
            keep.append(b)
    return keep


def faces_to_crop(
    faces: list[tuple[int, int, int, int]],
    frame_w: int,
    frame_h: int,
    *,
    expand: float = 2.2,
    max_box: float = 0.60,
    min_box: float = 0.28,
) -> CropRect | None:
    """Convert face box(es) to normalized CropRect.

    1 face → expanded around it.
    2+ faces → group bounding box expanded mildly (keeps people in frame).
    """
    if not faces or frame_w <= 0 or frame_h <= 0:
        return None
    if len(faces) == 1:
        x, y, fw, fh = faces[0]
        cx = (x + fw / 2) / frame_w
        cy = (y + fh / 2) / frame_h
        box_w = min(max_box, max(min_box, (fw / frame_w) * expand))
        box_h = min(max_box, max(min_box, (fh / frame_h) * expand))
        side = max(box_w, box_h)
        box_w = box_h = min(max_box, side)
    else:
        # Group box
        xs = [f[0] for f in faces]
        ys = [f[1] for f in faces]
        x2s = [f[0] + f[2] for f in faces]
        y2s = [f[1] + f[3] for f in faces]
        gx, gy = min(xs), min(ys)
        gw, gh = max(x2s) - gx, max(y2s) - gy
        pad = 0.35
        cx = (gx + gw / 2) / frame_w
        cy = (gy + gh / 2) / frame_h
        box_w = min(0.85, max(0.40, (gw / frame_w) * (1 + pad)))
        box_h = min(0.85, max(0.40, (gh / frame_h) * (1 + pad)))
    rx = max(0.0, min(1.0 - box_w, cx - box_w / 2))
    ry = max(0.0, min(1.0 - box_h, cy - box_h / 2))
    c = CropRect(x=rx, y=ry, w=box_w, h=box_h)
    return c if not c.validate() else None


def detect_face_roi_from_image(img) -> CropRect | None:
    """BGR image → face CropRect or None."""
    if img is None:
        return None
    h, w = img.shape[:2]
    faces = detect_faces_bgr(img)
    return faces_to_crop(faces, w, h)


def detect_face_roi_from_path(image_path: str | Path) -> CropRect | None:
    try:
        import cv2  # type: ignore
    except ImportError:
        return None
    img = cv2.imread(str(image_path))
    if img is None:
        return None
    return detect_face_roi_from_image(img)


def _skin_saliency_roi(img) -> CropRect | None:
    """Fallback subject estimate without face cascade."""
    try:
        import numpy as np
    except ImportError:
        return None
    try:
        if img is None or img.size == 0:
            return None
        # downscale for speed
        import cv2  # type: ignore
        small = cv2.resize(img, (160, 90), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB).astype("float32")
        r_c, g_c, b_c = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
        skin = (
            (r_c > 95) & (g_c > 40) & (b_c > 20)
            & (r_c > g_c) & (r_c > b_c)
            & ((r_c - g_c) > 15)
        )
        yy, xx = np.mgrid[0:90, 0:160]
        weight = np.exp(-(((xx - 80) / 80.0) ** 2 + ((yy - 45) / 45.0) ** 2) * 1.5)
        sal = skin.astype("float32") * 0.75 + weight * 0.25
        total = float(sal.sum())
        if total < 1e-3:
            return None
        mx = float((sal * xx).sum() / total) / 160.0
        my = float((sal * yy).sum() / total) / 90.0
        box = 0.42
        rx = max(0.0, min(1.0 - box, mx - box / 2))
        ry = max(0.0, min(1.0 - box, my - box / 2))
        c = CropRect(x=rx, y=ry, w=box, h=box)
        return c if not c.validate() else None
    except Exception as e:
        log.debug("skin ROI failed: %s", e)
        return None


def extract_frame(path: str | Path, t_sec: float = 0.5) -> Path | None:
    """Extract one JPEG frame for analysis."""
    path = Path(path)
    if not path.is_file():
        return None
    fd, raw = tempfile.mkstemp(suffix=".jpg", prefix="ce_face_")
    os.close(fd)
    out = Path(raw)
    try:
        r = subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{max(0.0, t_sec):.3f}",
                "-i", str(path),
                "-vframes", "1",
                "-q:v", "5",
                str(out),
            ],
            capture_output=True, timeout=25,
        )
        if r.returncode == 0 and out.is_file() and out.stat().st_size > 100:
            return out
        out.unlink(missing_ok=True)
    except Exception:
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass
    return None


def estimate_roi(
    source_path: str | Path,
    *,
    clip: dict[str, Any] | None = None,
    t_sec: float = 0.5,
    allow_probe: bool = True,
) -> CropRect:
    """
    Best-effort subject crop for framing.
    Always returns a valid CropRect.
    """
    explicit = _clip_roi(clip)
    if explicit is not None:
        return explicit

    if not allow_probe:
        return CropRect(x=0.15, y=0.15, w=0.70, h=0.70)

    # Image file directly
    src = Path(source_path)
    img = None
    frame_path = None
    try:
        import cv2  # type: ignore
        if src.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
            img = cv2.imread(str(src))
        else:
            frame_path = extract_frame(src, t_sec=t_sec)
            if frame_path is not None:
                img = cv2.imread(str(frame_path))
    except ImportError:
        img = None

    try:
        if img is not None:
            face = detect_face_roi_from_image(img)
            if face is not None:
                log.debug("face ROI from %s → %s", src.name, face)
                return face
            skin = _skin_saliency_roi(img)
            if skin is not None:
                log.debug("skin ROI from %s → %s", src.name, skin)
                return skin
    finally:
        if frame_path is not None:
            try:
                frame_path.unlink(missing_ok=True)
            except OSError:
                pass

    return CropRect()  # full frame → center behavior downstream
