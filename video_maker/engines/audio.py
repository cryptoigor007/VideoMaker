"""Движок аудио — 48kHz, BGM, loudnorm."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from collections import OrderedDict

from ..paths import resolve_existing_path
from .media_process import media_timeout_for_duration, run_ffmpeg_atomic

log = logging.getLogger(__name__)

# r57: кэш с инвалидацией (path|mtime_ns|size) — перезаписанный файл
# по тому же пути больше не возвращает старую длительность.
_PROBE_CACHE: "OrderedDict[str, float]" = OrderedDict()
_PROBE_CACHE_MAX = 4096


def _probe_key(path: str) -> str:
    """Ключ кэша probe: путь + mtime + размер (дёшево, без ffprobe)."""
    if not path:
        return ""
    try:
        st = os.stat(path)
        return f"{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}"
    except OSError:
        return os.path.abspath(path)


def _probe_cache_put(key: str, value: float) -> None:
    if not key:
        return
    _PROBE_CACHE[key] = value
    _PROBE_CACHE.move_to_end(key)
    while len(_PROBE_CACHE) > _PROBE_CACHE_MAX:
        _PROBE_CACHE.popitem(last=False)


def probe_duration(path: str) -> float:
    """Получить длительность медиафайла в секундах.

    Устойчиво к битым/неполным файлам: при ошибке ffprobe возвращает 0.0
    (вызывающий код обычно пропускает клипы с dur <= 0.05).
    """
    # session cache (path|mtime|size → duration)
    resolved_path = resolve_existing_path(path, kind="file")
    if not resolved_path:
        log.warning("[АУДИО] probe_duration: путь не существует: %s", path)
        return 0.0
    path = resolved_path
    key = _probe_key(path)
    if key and key in _PROBE_CACHE:
        _PROBE_CACHE.move_to_end(key)
        return _PROBE_CACHE[key]
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
                _probe_cache_put(key, val)
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
        _probe_cache_put(key, best)
        return best

    log.warning(
        "[АУДИО] probe_duration: нет duration в format/streams для %s keys=%s",
        os.path.basename(path),
        list(data.keys()),
    )
    return 0.0


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
    duration = probe_duration(video_path)
    return run_ffmpeg_atomic(
        cmd, output_path,
        timeout=media_timeout_for_duration(duration, minimum_seconds=300),
        log_fn=_log,
    )


def voice_enhance_filter(audio_path: str, output_path: str, log_fn=None) -> str:
    """Улучшение голоса: highpass, lowpass, компрессия, лимитер."""
    _log = log_fn or log.info
    _log("[АУДИО] Улучшение голоса (voice_enhance)...")
    audio_path = resolve_existing_path(audio_path, kind="file") or audio_path

    # Фильтр: highpass 80Hz, lowpass 14kHz, компрессор, де-эссер, лимитер
    af = (
        "highpass=f=80,"
        "lowpass=f=14000,"
        "acompressor=threshold=-18dB:ratio=3:attack=5:release=50:makeup=2,"
        "deesser=i=0.4:m=0.4:f=0.5,"
        "alimiter=limit=0.95"
    )

    # Shared pre-master narration uses a WAV container so enhancement stays
    # lossless until the normal master encode creates its AAC delivery stream.
    # Preserve legacy AAC behavior for existing direct calls targeting .aac/.m4a.
    if os.path.splitext(output_path)[1].lower() == ".wav":
        codec_args = ["-c:a", "pcm_s16le"]
    else:
        codec_args = ["-c:a", "aac", "-b:a", "192k"]
    cmd = [
        "ffmpeg", "-y",
        "-i", audio_path,
        "-af", af,
        *codec_args,
        "-ar", "48000",
        output_path,
    ]
    duration = probe_duration(audio_path)
    return run_ffmpeg_atomic(
        cmd, output_path,
        timeout=media_timeout_for_duration(duration, minimum_seconds=300),
        log_fn=_log,
    )


# ── BGM: подбор трека, loudnorm-хелперы, сборка аудио-цепочки ──────────────
_BGM_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus")


from .bgm_policy import list_bgm_tracks as _policy_list_bgm_tracks
from .bgm_policy import pick_bgm_track as _policy_pick_bgm_track


def list_bgm_tracks(bgm_folder: str) -> list[str]:
    """Поддерживает как отдельный файл музыки, так и папку с треками."""
    return _policy_list_bgm_tracks(bgm_folder)


def pick_bgm_track(bgm_folder: str, seed_key: str = "") -> str:
    """Выбрать трек с устойчивой историей повторов для конкретного выходного файла."""
    return _policy_pick_bgm_track(bgm_folder, seed_key=seed_key)


def measure_loudnorm_params(
    media_path: str,
    target_lufs: float = -14.0,
    log_fn=None,
) -> dict | None:
    """Pass-1 loudnorm по АУДИО (быстро: -vn). Возвращает measured_* или None.

    Измеряем один раз на мастер-аудио и переиспользуем во всех энкодах —
    точность two-pass без дополнительных проходов по 4K-видео.
    """
    _log = log_fn or log.info
    if not media_path:
        return None
    media_path = resolve_existing_path(media_path, kind="file") or media_path
    if not os.path.isfile(media_path):
        return None
    cmd = [
        "ffmpeg", "-y", "-i", media_path, "-vn",
        "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception as e:
        _log(f"[АУДИО] loudnorm measure failed: {e}")
        return None
    m = re.search(r"\{.*\}", res.stderr or "", re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group())
        out = {
            "i": float(d.get("input_i", -14.0)),
            "tp": float(d.get("input_tp", -1.5)),
            "lra": float(d.get("input_lra", 11.0)),
            "thresh": float(d.get("input_thresh", -20.0)),
            "offset": float(d.get("target_offset", 0.0)),
        }
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        _log(f"[АУДИО] loudnorm measure parse: {e}")
        return None
    _log(
        f"[АУДИО] loudnorm measured: I={out['i']:.1f} LUFS, "
        f"TP={out['tp']:.1f}, LRA={out['lra']:.1f}"
    )
    return out


def loudnorm_tail(target_lufs: float, measured: dict | None) -> str:
    """Хвост цепочки loudnorm: two-pass при наличии измерений, иначе one-pass."""
    if measured:
        return (
            f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:"
            f"measured_I={measured['i']}:measured_TP={measured['tp']}:"
            f"measured_LRA={measured['lra']}:measured_thresh={measured['thresh']}:"
            f"offset={measured['offset']}:linear=true"
        )
    return f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"


def build_bgm_filter(
    video_dur: float,
    target_lufs: float = -14.0,
    measured: dict | None = None,
    duck: bool = True,
    bgm_label: str = "1:a",
    voice_label: str = "0:a",
    out_label: str = "outa",
) -> str:
    """Аудио-цепочка «голос + BGM» для filter_complex.

    Используется и в mix_bgm (аудио-ремукс), и в inline-энкодах
    (burn субтитров / vstack) — один источник правды.
    """
    dur = max(0.5, float(video_dur or 0.0))
    bgm = (
        f"[{bgm_label}]aloop=loop=-1:size=2e9,"
        f"loudnorm=I=-18:TP=-3:LRA=11,"
        f"afade=t=in:st=0:d=1.5,"
        f"atrim=0:{dur:.3f},asetpts=N/SR/TB[bg]"
    )
    if duck:
        ducking = (
            f"[bg][{voice_label}]sidechaincompress="
            f"threshold=0.03:ratio=8:attack=5:release=250[bgd]"
        )
        mix_in = f"[{voice_label}][bgd]"
    else:
        ducking = ""
        mix_in = f"[{voice_label}][bg]"
    fade_out = f"afade=t=out:st={max(0.0, dur - 2.0):.3f}:d=2," if dur > 3.0 else ""
    mix = (
        f"{mix_in}amix=inputs=2:duration=first:weights=1 0.9:"
        f"dropout_transition=3,{fade_out}{loudnorm_tail(target_lufs, measured)}[{out_label}]"
    )
    return bgm + ";" + (ducking + ";" if ducking else "") + mix


def mix_bgm(
    video_path: str,
    bgm_folder: str,
    output_path: str,
    log_fn=None,
    loudnorm: bool = False,
    target_lufs: float = -14.0,
    seed_key: str = "",
) -> str:
    """Смешать голос с BGM и проверить готовый MP4 через ffprobe."""
    _log = log_fn or log.info
    _log("[АУДИО] Смешивание с BGM...")

    bgm_source = (bgm_folder or "").strip()
    resolved_bgm = resolve_existing_path(bgm_source, kind="any")
    if not resolved_bgm:
        raise RuntimeError(f"BGM requested but file/folder is unavailable: {bgm_source!r}")
    bgm_source = resolved_bgm

    asset_key = seed_key or os.path.abspath(video_path)
    bgm_file = pick_bgm_track(bgm_source, seed_key=asset_key)
    if not bgm_file:
        raise RuntimeError(f"BGM requested but no supported audio tracks were found in {bgm_source!r}")
    _log(f"[АУДИО] BGM трек: {os.path.basename(bgm_file)}")

    video_dur = probe_duration(video_path)
    filter_complex = build_bgm_filter(
        video_dur,
        target_lufs=target_lufs,
        measured=None,
        duck=True,
        out_label="out",
    )
    from .bgm_validation import validate_bgm_filter, verify_bgm_output
    validate_bgm_filter(filter_complex)

    # Всегда пишем во временный файл (ffmpeg не умеет in-place на том же пути)
    import tempfile
    out_dir = os.path.dirname(output_path) or "."
    os.makedirs(out_dir, exist_ok=True)
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
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("mix_bgm timeout 3600s") from e
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "")[-600:]
            _log(f"[АУДИО] mix_bgm ffmpeg error: {err}")
            raise RuntimeError(f"mix_bgm failed: {err}")
        os.replace(tmp_output_path, output_path)
        if not os.path.isfile(output_path) or os.path.getsize(output_path) < 1000:
            raise RuntimeError(f"mix_bgm did not produce a valid destination: {output_path}")
        try:
            verification = verify_bgm_output(video_path, output_path)
            _log(
                "[АУДИО] BGM output verified: "
                f"audio={verification.get('audio_codec', 'unknown')} "
                f"duration={float(verification.get('output_duration_seconds', 0.0)):.2f}s "
                f"size={int(verification.get('output_size_bytes', 0))} bytes"
            )
        except Exception:
            try:
                os.remove(output_path)
            except OSError:
                pass
            raise
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
    duration = probe_duration(video_path)
    try:
        result = subprocess.run(
            cmd1, capture_output=True, text=True,
            timeout=media_timeout_for_duration(duration, minimum_seconds=300),
        )
    except subprocess.TimeoutExpired as exc:
        _log(f"[АУДИО] Loudnorm analysis timeout after {exc.timeout}s")
        raise RuntimeError(f"Loudnorm analysis exceeded timeout ({exc.timeout}s)") from exc
    
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
                copy_tmp = f"{output_path}.vm_tmp_{os.getpid()}"
                try:
                    shutil.copy2(video_path, copy_tmp)
                    os.replace(copy_tmp, output_path)
                finally:
                    try:
                        if os.path.exists(copy_tmp):
                            os.remove(copy_tmp)
                    except OSError:
                        pass
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
    return run_ffmpeg_atomic(
        cmd, output_path,
        timeout=media_timeout_for_duration(duration, minimum_seconds=300),
        log_fn=_log,
    )


def measure_loudness(path: str) -> dict | None:
    """Замерить громкость (LUFS и true peak) через ebur128."""
    cmd = [
        "ffmpeg", "-i", path,
        "-af", "ebur128=peak=true",
        "-f", "null", "-",
    ]
    duration = probe_duration(path)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=media_timeout_for_duration(duration, minimum_seconds=300),
        )
    except subprocess.TimeoutExpired as exc:
        log.warning("[АУДИО] measure_loudness timeout after %ss for %s", exc.timeout, path)
        return None
    if result.returncode != 0:
        log.warning("[АУДИО] measure_loudness ffmpeg failed (rc=%s): %s", result.returncode, (result.stderr or "")[-400:])
        return None
    stderr = result.stderr or ""

    i_lufs = None
    peak_dbtp = None

    lines = stderr.split("\n")
    for line in lines:
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