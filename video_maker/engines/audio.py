"""Движок аудио — 48kHz, BGM, loudnorm."""
from __future__ import annotations

import json
import logging
import os
import random
import re
import subprocess

log = logging.getLogger(__name__)

_PROBE_CACHE: dict[str, float] = {}


def probe_duration(path: str) -> float:
    """Получить длительность медиафайла в секундах.

    Устойчиво к битым/неполным файлам: при ошибке ffprobe возвращает 0.0
    (вызывающий код обычно пропускает клипы с dur <= 0.05).
    """
    # session cache (abspath → duration)
    if path:
        key = os.path.abspath(path)
        if key in _PROBE_CACHE:
            return _PROBE_CACHE[key]
    else:
        key = ""
    if not path or not os.path.exists(path):
        log.warning("[АУДИО] probe_duration: путь не существует: %s", path)
        return 0.0
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        log.warning("[АУДИО] probe_duration ffprobe failed for %s: %s", path, e)
        return 0.0

    raw = (result.stdout or "").strip()
    if not raw:
        log.warning(
            "[АУДИО] probe_duration: пустой ответ ffprobe для %s (rc=%s stderr=%s)",
            os.path.basename(path),
            result.returncode,
            (result.stderr or "")[:200],
        )
        return 0.0

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning("[АУДИО] probe_duration: невалидный JSON для %s: %s", path, e)
        return 0.0

    # 1) format.duration
    fmt = data.get("format") or {}
    dur = fmt.get("duration")
    if dur is not None:
        try:
            val = float(dur)
            if val > 0:
                if key:
                    _PROBE_CACHE[key] = val
                return val
        except (TypeError, ValueError):
            pass

    # 2) fallback: max stream duration
    best = 0.0
    for stream in data.get("streams") or []:
        sd = stream.get("duration")
        if sd is None:
            continue
        try:
            val = float(sd)
            if val > best:
                best = val
        except (TypeError, ValueError):
            continue
    if best > 0:
        if key:
            _PROBE_CACHE[key] = best
        return best

    log.warning(
        "[АУДИО] probe_duration: нет duration в format/streams для %s keys=%s",
        os.path.basename(path),
        list(data.keys()),
    )
    return 0.0


def probe_sample_rate(path: str) -> int:
    """Получить частоту дискретизации аудио."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate",
        "-of", "json",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    if streams:
        return int(streams[0].get("sample_rate", 0))
    return 0


def replace_audio(
    video_path: str,
    audio_path: str,
    output_path: str,
    log_fn=None,
) -> str:
    """Заменить аудиодорожку в видео, ресемпл в 48kHz."""
    _log = log_fn or log.info
    _log(f"[АУДИО] Замена аудио в {os.path.basename(video_path)}")

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-ar", "48000",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def voice_enhance_filter(audio_path: str, output_path: str, log_fn=None) -> str:
    """Улучшение голоса: highpass, lowpass, компрессия, лимитер."""
    _log = log_fn or log.info
    _log("[АУДИО] Улучшение голоса (voice_enhance)...")

    # Фильтр: highpass 80Hz, lowpass 14kHz, компрессор, де-эссер, лимитер
    af = (
        "highpass=f=80,"
        "lowpass=f=14000,"
        "acompressor=threshold=-18dB:ratio=3:attack=5:release=50:makeup=2,"
        "deesser=i=0.4:m=0.4:f=0.5,"
        "alimiter=limit=0.95"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", audio_path,
        "-af", af,
        "-c:a", "aac", "-b:a", "192k",
        "-ar", "48000",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def mix_bgm(
    video_path: str,
    bgm_folder: str,
    output_path: str,
    log_fn=None,
    loudnorm: bool = False,
    target_lufs: float = -14.0,
) -> str:
    """Смешать голос с BGM (sidechain compression) — луп до конца видео."""
    _log = log_fn or log.info
    _log("[АУДИО] Смешивание с BGM...")

    bgm_files = [
        os.path.join(bgm_folder, f)
        for f in [f for f in os.listdir(bgm_folder) if not f.startswith(".") and f.lower().endswith((".mp3", ".wav", ".m4a", ".flac", ".aac"))]
        if f.lower().endswith((".mp3", ".wav", ".flac", ".m4a"))
    ]
    if not bgm_files:
        _log("[АУДИО] BGM файлы не найдены, пропускаем")
        return video_path

    bgm_file = random.choice(bgm_files)

    # Получаем длительность видео для обрезки BGM
    video_dur = probe_duration(video_path)

    # BGM: бесконечный луп -> громкость -18 LUFS -> обрезка по длине видео
    if loudnorm:
        filter_complex = (
            f"[1:a]aloop=loop=-1:size=2e9,"
            f"loudnorm=I=-18:TP=-3:LRA=11,"
            f"atrim=0:{video_dur},"
            f"apad=whole_dur={video_dur}[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first:weights=1 0.3:dropout_transition=3,"
            f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11[out]"
        )
    else:
        filter_complex = (
            f"[1:a]aloop=loop=-1:size=2e9,"
            f"loudnorm=I=-18:TP=-3:LRA=11,"
            f"atrim=0:{video_dur},"
            f"apad=whole_dur={video_dur}[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first:weights=1 0.3:dropout_transition=3[out]"
        )

    # Всегда пишем во временный файл (ffmpeg не умеет in-place на том же пути)
    import tempfile
    out_dir = os.path.dirname(output_path) or "."
    fd, tmp_output_path = tempfile.mkstemp(suffix=".mp4", prefix="bgm_", dir=out_dir)
    os.close(fd)
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", bgm_file,
            "-filter_complex", filter_complex,
            "-map", "0:v", "-map", "[out]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000",
            tmp_output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "")[-600:]
            _log(f"[АУДИО] mix_bgm ffmpeg error: {err}")
            raise RuntimeError(f"mix_bgm failed: {err}")
        os.replace(tmp_output_path, output_path)
    finally:
        if os.path.exists(tmp_output_path):
            try:
                os.remove(tmp_output_path)
            except OSError:
                pass

    return output_path


def apply_loudnorm(
    video_path: str,
    output_path: str,
    target_lufs: float = -14.0,
    log_fn=None,
) -> str:
    """Применить loudnorm к итоговому видео (two-pass для точности)."""
    _log = log_fn or log.info
    _log(f"[АУДИО] Loudnorm: target={target_lufs} LUFS")

    # First pass: measure
    cmd1 = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd1, capture_output=True, text=True)
    
    # Parse JSON from stderr
    json_match = re.search(r"\{.*\}", result.stderr, re.DOTALL)
    if json_match:
        try:
            loudnorm_data = json.loads(json_match.group())
            measured_i = loudnorm_data.get("input_i", -14.0)
            measured_tp = loudnorm_data.get("input_tp", -1.5)
            measured_lra = loudnorm_data.get("input_lra", 11.0)
            measured_thresh = loudnorm_data.get("input_thresh", -20.0)
            offset = loudnorm_data.get("target_offset", 0.0)
            
            _log(f"[АУДИО] Measured: I={float(measured_i):.1f} LUFS, TP={float(measured_tp):.1f}, LRA={float(measured_lra):.1f}")

            # Уже у цели → только copy, без повторного encode аудио
            try:
                mi = float(measured_i)
                mtp = float(measured_tp)
            except (TypeError, ValueError):
                mi, mtp = -99.0, 0.0
            if abs(mi - float(target_lufs)) <= 0.6 and mtp <= -1.0:
                _log(f"[АУДИО] Уже ≈{target_lufs} LUFS → copy (skip loudnorm encode)")
                import shutil
                shutil.copy2(video_path, output_path)
                return output_path

            # Second pass: apply with measured values
            filter_str = (
                f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:"
                f"measured_I={measured_i}:measured_TP={measured_tp}:"
                f"measured_LRA={measured_lra}:measured_thresh={measured_thresh}:"
                f"offset={offset}:linear=true:print_format=summary"
            )
        except (json.JSONDecodeError, KeyError) as e:
            _log(f"[АУДИО] Loudnorm parse error: {e}, fallback to single pass")
            filter_str = f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"
    else:
        filter_str = f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-af", filter_str,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-ar", "48000",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def measure_loudness(path: str) -> dict | None:
    """Замерить громкость (LUFS и true peak) через ebur128."""
    cmd = [
        "ffmpeg", "-i", path,
        "-af", "ebur128=peak=true",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    stderr = result.stderr

    i_lufs = None
    peak_dbtp = None

    lines = stderr.split("\n")
    for i, line in enumerate(lines):
        # Ищем "I:         -26.9 LUFS" после "Integrated loudness:"
        if "I:" in line and "LUFS" in line:
            parts = line.split("I:")
            if len(parts) >= 2:
                try:
                    val = parts[1].strip().split()[0]
                    i_lufs = float(val)
                except (ValueError, IndexError):
                    pass
        # Ищем "Peak:      -22.9 dBFS" или "TPK: -22.9 dBFS" после "True peak:"
        if "Peak:" in line and "dBFS" in line:
            parts = line.split("Peak:")
            if len(parts) >= 2:
                try:
                    val = parts[1].strip().split()[0]
                    peak_dbtp = float(val)
                except (ValueError, IndexError):
                    pass
        if "TPK:" in line and "dBFS" in line:
            parts = line.split("TPK:")
            if len(parts) >= 2:
                try:
                    val = parts[1].strip().split()[0]
                    peak_dbtp = float(val)
                except (ValueError, IndexError):
                    pass

    if i_lufs is None:
        return None

    return {"i_lufs": i_lufs, "peak_dbtp": peak_dbtp or 0.0}


def judge_loudness(i_lufs: float | None) -> str:
    """Оценить громкость по порогам."""
    if i_lufs is None:
        return "?"
    if -20 <= i_lufs <= -13:
        return "ok"
    if i_lufs < -20:
        return "тихо"
    return "громко"