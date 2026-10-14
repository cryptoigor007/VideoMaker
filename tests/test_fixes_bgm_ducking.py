"""Дакинг музыки: измеримая проверка — музыка приглушается под голос.

Голос — тон 440 Гц с паузами, музыка — тон 3000 Гц. Реальный фильтр
``build_bgm_filter(duck=True)`` обязан дать заметно большую разницу уровней
музыки «при голосе / в паузе», чем ``duck=False`` (замер r57-api.31: эффект ~10 dB).
Тест закрепляет эффект: если дакинг сломают или отключат — он упадёт.
"""
from __future__ import annotations

import re
import shutil
import subprocess

import pytest


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
    if result.returncode:
        raise RuntimeError("FFmpeg failed: " + " ".join(cmd) + "\n" + (result.stderr or "")[-1200:])
    return result


def _mean_volume_db(ffmpeg: str, path, start: float, dur: float) -> float:
    """Уровень музыки (полоса >1500 Гц) в окне — голос 440 Гц отфильтрован."""
    result = _run([
        ffmpeg, "-hide_banner", "-ss", str(start), "-t", str(dur), "-i", str(path),
        "-map", "0:a:0", "-af", "highpass=f=1500,volumedetect", "-f", "null", "-",
    ])
    match = re.search(r"mean_volume:\s*(-?[\d.]+) dB", result.stderr)
    if not match:
        raise RuntimeError("volumedetect не вернул mean_volume:\n" + (result.stderr or "")[-800:])
    return float(match.group(1))


def test_build_bgm_filter_contains_sidechain():
    """Граф микса обязан содержать дакинг-компрессор (sidechaincompress)."""
    from video_maker.engines.audio import build_bgm_filter

    graph = build_bgm_filter(12.0)
    assert "sidechaincompress" in graph
    assert "threshold=0.03" in graph
    assert "amix=inputs=2" in graph


def test_bgm_ducking_measured(tmp_path):
    """Измеряем эффект: при голосе музыка тише, чем в паузе (и без duck — нет)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg недоступен")

    from video_maker.engines.audio import build_bgm_filter

    voice = tmp_path / "voice.mp4"
    music = tmp_path / "music.wav"

    # Голос: 12с, тон 440 Гц; звучит 0–3 и 6–9, паузы 3–6 и 9–12.
    _run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=320x180:r=25:d=12",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=12",
        "-map", "0:v:0", "-map", "1:a:0",
        "-af", "volume=enable='between(t,3,6)+between(t,9,12)':volume=0",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(voice),
    ])
    _run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "sine=frequency=3000:sample_rate=48000:duration=12",
        "-c:a", "pcm_s16le", str(music),
    ])

    on_window = (6.5, 2.0)   # голос звучит (без фейдов)
    off_window = (3.5, 2.0)  # пауза (без фейдов)

    deltas = {}
    for duck in (True, False):
        out = tmp_path / f"mixed_{'duck' if duck else 'noduck'}.mp4"
        graph = build_bgm_filter(12.0, target_lufs=-14.0, measured=None, duck=duck, out_label="out")
        _run([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(voice), "-i", str(music),
            "-filter_complex", graph, "-map", "0:v:0", "-map", "[out]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out),
        ])
        on_db = _mean_volume_db(ffmpeg, out, *on_window)
        off_db = _mean_volume_db(ffmpeg, out, *off_window)
        deltas[duck] = off_db - on_db

    effect = deltas[True] - deltas[False]
    assert effect >= 6.0, (
        f"дакинг не работает: эффект {effect:.1f} dB "
        f"(duck: {deltas[True]:.1f} dB, без duck: {deltas[False]:.1f} dB)"
    )
