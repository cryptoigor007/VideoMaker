# VideoMaker | r55-cover-hook-parity | 2026-09-16
"""Обложка Shorts: фон (картинка) + hook в том же визуале, что on-screen хук.

Не трогает burn субтитров ролика и long cover_* — только отдельный JPG.
Визуал хука = subtitles.build_short_hook_cover_ass (тот же, что _build_hook_events).
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

# Вертикальный thumbnail YouTube Shorts
COVER_W = 1080
COVER_H = 1920


def _ffmpeg_bin() -> str:
    try:
        import imageio_ffmpeg

        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and os.path.isfile(path):
            return path
    except Exception:
        pass
    return "ffmpeg"


def render_short_cover(
    background_path: str,
    hook_text: str,
    output_path: str,
    log_fn=None,
    clip_start: float | None = None,
    color_index: int | None = None,
) -> str | None:
    """Собрать short cover JPG. None при skip/ошибке (не бросает наружу).

    Цвет = как on-screen хук: MARKER_COLORS[(0 + int(start_t * 10)) % 4],
    где start_t — абсолютное начало клипа (clip.start) на полном ролике.
    """
    _log = log_fn or (lambda m: log.info(m))
    bg = (background_path or "").strip()
    hook = (hook_text or "").strip()
    out = (output_path or "").strip()

    if not bg or not os.path.isfile(bg):
        _log("[COVER] skip: нет файла фона")
        return None
    if not hook:
        _log("[COVER] skip: пустой hook")
        return None
    if not out:
        _log("[COVER] skip: нет output_path")
        return None

    try:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    except OSError as e:
        _log(f"[COVER] mkdir fail: {e}")
        return None

    # Цвет 1:1 с _build_hook_events: (idx + int(start_t * 10)) % 4, idx=0
    if color_index is None:
        try:
            st = float(clip_start) if clip_start is not None else 0.0
        except (TypeError, ValueError):
            st = 0.0
        color_index = (0 + int(st * 10)) % 4
    else:
        color_index = int(color_index) % 4

    # Тот же визуал, что хук на первых кадрах short (цвет, UPPER, pos 12%, fs/bord)
    try:
        from .subtitles import build_short_hook_cover_ass

        ass_text = build_short_hook_cover_ass(
            hook, COVER_W, COVER_H, color_index=color_index
        )
    except Exception as e:
        _log(f"[COVER] ASS build fail: {e}")
        return None
    if not (ass_text or "").strip():
        _log("[COVER] skip: пустой ASS после нормализации hook")
        return None

    ffmpeg = _ffmpeg_bin()
    tmp_ass = None
    try:
        fd, tmp_ass = tempfile.mkstemp(prefix="vm_cover_", suffix=".ass")
        os.close(fd)
        # utf-8-sig как в burn_subtitles — libass на части платформ дружелюбнее
        Path(tmp_ass).write_text(ass_text, encoding="utf-8-sig")

        ass_esc = tmp_ass.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        vf = (
            f"scale={COVER_W}:{COVER_H}:force_original_aspect_ratio=increase,"
            f"crop={COVER_W}:{COVER_H},"
            f"subtitles='{ass_esc}'"
        )
        cmd = [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-i",
            bg,
            "-t",
            "0.5",
            "-ss",
            "0.15",
            "-vf",
            vf,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            out,
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0 or not os.path.isfile(out) or os.path.getsize(out) < 100:
            err = (proc.stderr or proc.stdout or "")[-400:]
            _log(f"[COVER] ffmpeg fail rc={proc.returncode}: {err}")
            try:
                if os.path.isfile(out):
                    os.remove(out)
            except OSError:
                pass
            return None
        _log(f"[COVER] OK (hook parity) → {out}")
        return out
    except Exception as e:
        _log(f"[COVER] error: {e}")
        try:
            if out and os.path.isfile(out):
                os.remove(out)
        except OSError:
            pass
        return None
    finally:
        if tmp_ass:
            try:
                os.remove(tmp_ass)
            except OSError:
                pass
