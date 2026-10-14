# VideoMaker FIX | 2026.09.01-r5 | 2026-09-01
# CHANGED: inject_hwaccel() — авто -hwaccel videotoolbox перед каждым видео -i
#   вызывается из run_vt_encode → покрывает intro/outro/cut/и др.
# REPLACE: video_maker/engines/ffmpeg_resilient.py

"""Устойчивый запуск ffmpeg/VideoToolbox: classify, SSD wait, retry, без libx264."""
from __future__ import annotations

import os
import subprocess
import time
import uuid
from typing import Callable, Optional, Sequence

LogFn = Optional[Callable[[str], None]]


class VideoEncodeFailed(RuntimeError):
    """Аппаратный encode (VT) не удался — без software fallback."""


class SubtitleStageFailed(VideoEncodeFailed):
    """Стадия субтитров не завершилась."""


def classify_ffmpeg_error(stderr: str | None, returncode: int) -> str:
    """ok | io | filter | encoder | unknown."""
    if returncode == 0:
        return "ok"
    err = (stderr or "").lower()
    io_markers = (
        "no such file",
        "error opening input",
        "error opening output",
        "input/output error",
        "i/o error",
        "device not configured",
        "permission denied",
        "disk quota",
        "no space left",
        "host is down",
        "broken pipe",
        "resource temporarily unavailable",
        "errno 5",
        "errno=5",
    )
    if any(m in err for m in io_markers):
        return "io"
    encoder_markers = (
        "error while opening encoder",
        "cannot create videotoolbox",
        "session not found",
        "videotoolbox_encoder",
    )
    if any(m in err for m in encoder_markers):
        return "encoder"
    if "videotoolbox" in err and "conversion failed" in err:
        return "encoder"
    filter_markers = (
        "error initializing filter",
        "no such filter",
        "fontselect",
        "libass",
        "ass filter",
    )
    if any(m in err for m in filter_markers):
        return "filter"
    if "subtitles" in err and ("error" in err or "fail" in err):
        return "filter"
    if "conversion failed" in err:
        return "unknown"
    return "unknown"


def _volume_root(path: str) -> str | None:
    p = os.path.abspath(path)
    if p.startswith("/Volumes/"):
        parts = p.split(os.sep)
        if len(parts) >= 3:
            return os.sep.join(parts[:3])
    return None


def path_is_readable(path: str, min_bytes: int = 32) -> bool:
    try:
        if not path or not os.path.isfile(path):
            return False
        if os.stat(path).st_size < min_bytes:
            return False
        with open(path, "rb") as f:
            f.read(1)
        return True
    except OSError:
        return False


def path_is_writable_dir(dir_path: str) -> bool:
    try:
        d = dir_path if os.path.isdir(dir_path) else os.path.dirname(dir_path) or "."
        os.makedirs(d, exist_ok=True)
        test = os.path.join(d, f".vm_write_test_{os.getpid()}")
        with open(test, "wb") as f:
            f.write(b"ok")
        os.remove(test)
        return True
    except OSError:
        return False


def ensure_storage(
    input_paths: str | Sequence[str],
    output_path: str,
    log_fn: LogFn = None,
    wait_schedule: tuple[float, ...] = (2.0, 4.0, 8.0, 15.0),
) -> None:
    """Дождаться читаемости input(ов) и записи output. Без auto-remount."""
    _log = log_fn or (lambda m: None)
    if isinstance(input_paths, str):
        inputs = [input_paths]
    else:
        inputs = list(input_paths)

    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    vols = set()
    for p in list(inputs) + [output_path]:
        v = _volume_root(str(p))
        if v:
            vols.add(v)

    media_ext = (
        ".mp4", ".mov", ".mkv", ".m4a", ".wav", ".mp3",
        ".png", ".jpg", ".jpeg", ".webm", ".aac", ".m4v",
    )

    for attempt, delay in enumerate((0.0,) + wait_schedule):
        if delay:
            _log(f"[STORAGE] Ожидание диска {delay:.0f}с ({attempt}/{len(wait_schedule)})...")
            time.sleep(delay)
        bad_vol = False
        for vol in vols:
            if not os.path.isdir(vol):
                _log(f"[STORAGE] Том не смонтирован: {vol}")
                bad_vol = True
        if bad_vol:
            continue
        ok_in = True
        for ip in inputs:
            ip = str(ip)
            if any(ip.lower().endswith(e) for e in media_ext) or os.path.isfile(ip):
                if not path_is_readable(ip):
                    _log(f"[STORAGE] Input недоступен: {ip}")
                    ok_in = False
        if not ok_in:
            continue
        if not path_is_writable_dir(out_dir):
            _log(f"[STORAGE] Output недоступен: {out_dir}")
            continue
        if attempt:
            _log("[STORAGE] Диск снова доступен")
        return

    raise VideoEncodeFailed(
        f"SSD/том недоступен. Подключите диск и повторите.\n"
        f"  inputs={inputs[:5]}\n  output_dir={out_dir}"
    )


def probe_clip_meta(video_path: str) -> dict:
    meta = {
        "path": video_path, "width": 0, "height": 0,
        "duration": 0.0, "bitrate_mbps": None, "codec": "",
    }
    try:
        import json
        r = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,codec_name,bit_rate,duration",
                "-show_entries", "format=duration,bit_rate",
                "-of", "json", video_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(r.stdout or "{}")
        st = (data.get("streams") or [{}])[0]
        fmt = data.get("format") or {}
        meta["width"] = int(st.get("width") or 0)
        meta["height"] = int(st.get("height") or 0)
        meta["codec"] = (st.get("codec_name") or "").lower()
        meta["duration"] = float(st.get("duration") or fmt.get("duration") or 0)
        br = st.get("bit_rate") or fmt.get("bit_rate")
        if br:
            meta["bitrate_mbps"] = int(br) / 1_000_000.0
    except Exception:
        pass
    return meta


def calculate_adaptive_bitrate(
    clips: list | None = None,
    video_path: str | None = None,
    target_w: int = 3840,
    target_h: int = 2160,
    min_mbps: float = 18.0,
    max_mbps: float = 40.0,
    safety: float = 1.12,
    log_fn: LogFn = None,
) -> str:
    _log = log_fn or (lambda m: None)
    paths = list(clips or [])
    if video_path and video_path not in paths:
        paths.append(video_path)
    if not paths:
        return "28M"

    target_pixels = max(1, target_w * target_h)
    total_w = 0.0
    total_d = 0.0
    for p in paths:
        if not path_is_readable(p):
            continue
        m = probe_clip_meta(p)
        br = m.get("bitrate_mbps")
        dur = float(m.get("duration") or 0) or 1.0
        if not br or br <= 0:
            continue
        codec = m.get("codec") or ""
        if codec in ("hevc", "h265", "hev1", "hvc1"):
            br *= 1.4
        w, h = int(m.get("width") or 0), int(m.get("height") or 0)
        pixels = max(1, w * h)
        if pixels < target_pixels * 0.5:
            br = br * (target_pixels / pixels) ** 0.5
        total_w += br * dur
        total_d += dur

    if total_d <= 0 or total_w <= 0:
        target = 28.0
        _log("[BITRATE] Нет probe-данных → default 28M")
    else:
        weighted = total_w / total_d
        target = weighted * safety
        _log(f"[BITRATE] weighted={weighted:.1f}M × {safety} → {target:.1f}M")

    target = max(min_mbps, min(max_mbps, target))
    out = f"{int(round(target))}M"
    _log(f"[BITRATE] final target={out}")
    return out



_AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma", ".aiff")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif", ".tif", ".tiff")


def inject_hwaccel(cmd: list[str]) -> list[str]:
    """Вставить -hwaccel videotoolbox перед каждым видео-входом (-i).

    Не трогаем:
      - уже есть -hwaccel перед этим -i
      - -loop 1 (картинка→видео)
      - чисто аудио / картинки как вход (hwaccel для них бесполезен или падает)
    """
    if not cmd:
        return cmd
    out: list[str] = []
    i = 0
    while i < len(cmd):
        if cmd[i] == "-hwaccel":
            out.append(cmd[i])
            if i + 1 < len(cmd):
                out.append(cmd[i + 1])
                i += 2
            else:
                i += 1
            continue
        if cmd[i] == "-i" and i + 1 < len(cmd):
            inp = str(cmd[i + 1])
            prev = out[-1] if out else ""
            prev2 = out[-2] if len(out) >= 2 else ""
            is_loop = prev == "1" and prev2 == "-loop"
            low = inp.lower()
            is_audio = low.endswith(_AUDIO_EXTS)
            is_image = low.endswith(_IMAGE_EXTS)
            already = len(out) >= 2 and out[-2] == "-hwaccel"
            if already or is_loop or is_audio or is_image:
                out.append("-i")
                out.append(inp)
            else:
                out.extend(["-hwaccel", "videotoolbox", "-i", inp])
            i += 2
            continue
        out.append(cmd[i])
        i += 1
    return out


def vt_encode_args(bitrate: str = "28M", allow_sw: bool = False) -> list[str]:
    """Только hardware VideoToolbox.

    allow_sw=True разрешает внутренний software-путь самого VideoToolbox
    (не libx264) — используется там, где раньше стоял -allow_sw 1.
    """
    br = bitrate if str(bitrate).upper().endswith("M") else f"{bitrate}M"
    return [
        "-c:v", "h264_videotoolbox", "-b:v", br,
        "-allow_sw", "1" if allow_sw else "0", "-pix_fmt", "yuv420p",
    ]


def sw_encode_args(crf: int = 18, preset: str = "medium") -> list[str]:
    """Программный fallback (libx264) — только при отказе VideoToolbox."""
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]


def sw_fallback_enabled() -> bool:
    """Разрешён ли libx264-fallback (VIDEOMAKER_SW_FALLBACK=0 — выключить)."""
    return os.environ.get("VIDEOMAKER_SW_FALLBACK", "1").strip().lower() not in ("0", "false", "no")


def to_software_cmd(cmd: Sequence[str], crf: int = 18) -> list[str]:
    """Заменить видеокодер в готовой команде на libx264 (CRF вместо -b:v)."""
    out: list[str] = []
    i = 0
    n = len(cmd)
    while i < n:
        c = cmd[i]
        if c == "-c:v" and i + 1 < n:
            out += sw_encode_args(crf=crf)
            i += 2
            continue
        if c in ("-b:v", "-allow_sw", "-maxrate", "-bufsize", "-pix_fmt") and i + 1 < n:
            i += 2
            continue
        out.append(c)
        i += 1
    return out


def default_encode_timeout() -> float:
    """Таймаут энкода: env VIDEOMAKER_FFMPEG_TIMEOUT или 4 часа."""
    try:
        return max(60.0, float(os.environ.get("VIDEOMAKER_FFMPEG_TIMEOUT", "14400")))
    except ValueError:
        return 14400.0


def verify_mp4(path: str, log_fn: LogFn = None) -> bool:
    _log = log_fn or (lambda m: None)
    if not path_is_readable(path, min_bytes=1024):
        _log(f"[VERIFY] Файл нечитаем: {path}")
        return False
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=codec_type", "-of", "csv=p=0", path,
            ],
            capture_output=True, text=True, timeout=60,
        )
        ok = r.returncode == 0 and "video" in (r.stdout or "").lower()
        if not ok:
            _log(f"[VERIFY] ffprobe fail: {path}")
        return ok
    except Exception as e:
        _log(f"[VERIFY] {e}")
        return False


def run_ffmpeg(
    cmd: list[str],
    log_fn: LogFn = None,
    timeout: float | None = None,
    media_duration: float | None = None,
):
    """Запустить ffmpeg с логом и гарантированным таймаутом.

    media_duration (если известно) даёт таймаут 8× реального времени —
    зависший энкод больше не блокирует пайплайн навсегда.
    """
    _log = log_fn or (lambda m: None)
    if timeout is None:
        if media_duration and float(media_duration) > 0:
            timeout = max(600.0, float(media_duration) * 8.0)
        else:
            timeout = default_encode_timeout()
    cmd_s = " ".join(str(c) for c in cmd)
    if len(cmd_s) > 500:
        _log(f"[FFMPEG] CMD ({len(cmd)} args): {cmd_s[:500]}...")
    else:
        _log(f"[FFMPEG] CMD: {cmd_s}")
    t0 = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        _log(f"[FFMPEG] TIMEOUT {timeout:.0f}s — процесс убит")
        raise VideoEncodeFailed(
            f"ffmpeg timeout {timeout:.0f}s: {' '.join(str(c) for c in cmd[:8])}…"
        ) from e
    elapsed = time.time() - t0
    err = (result.stderr or "").strip()
    out = (result.stdout or "").strip()
    _log(
        f"[FFMPEG] done rc={result.returncode} elapsed={elapsed:.1f}s "
        f"stderr_len={len(err)} stdout_len={len(out)}"
    )
    if result.returncode != 0:
        tail = err[-800:] if err else out[-400:]
        _log(f"[FFMPEG] FAIL tail: {tail}")
    elif elapsed > 30:
        # прогресс/скорость из stderr ffmpeg
        for line in reversed((err or "").splitlines()):
            if "speed=" in line or "time=" in line:
                _log(f"[FFMPEG] last progress: {line.strip()[:200]}")
                break
    return result


def atomic_replace(tmp_path: str, final_path: str) -> None:
    """Атомарно заменить final_path содержимым tmp_path.

    os.replace не работает через границу файловых систем (внутренний диск →
    сетевой/external том, «Cross-device link»), поэтому при такой ошибке файл
    переносится копированием. Это защищает все вызывающие места от сбоя.
    """
    os.makedirs(os.path.dirname(os.path.abspath(final_path)) or ".", exist_ok=True)
    try:
        os.replace(tmp_path, final_path)
    except OSError:
        import shutil
        shutil.move(tmp_path, final_path)


def run_vt_encode(
    cmd: list[str],
    input_files: Sequence[str],
    output_path: str,
    log_fn: LogFn = None,
    max_io_retries: int = 4,
    stage_name: str = "VT",
    timeout: float | None = None,
) -> str:
    """
    Команда с h264_videotoolbox:
    storage check → atomic tmp → verify → rename
    I/O → wait + retry; encoder → одна попытка libx264 (если разрешено), иначе fail.
    """
    _log = log_fn or (lambda m: None)
    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    tmp_out = os.path.join(out_dir, f".vm_vt_{uuid.uuid4().hex[:10]}.mp4")

    cmd = list(cmd)
    cmd = inject_hwaccel(cmd)
    if cmd and cmd[-1] == output_path:
        cmd[-1] = tmp_out
    elif output_path in cmd:
        cmd[cmd.index(output_path)] = tmp_out
    else:
        cmd.append(tmp_out)

    last_err = ""
    sw_tried = False
    for attempt in range(max_io_retries):
        try:
            ensure_storage(list(input_files), tmp_out, log_fn=_log)
        except VideoEncodeFailed:
            if attempt >= max_io_retries - 1:
                raise
            continue

        _log(f"[{stage_name}] VT attempt {attempt + 1}/{max_io_retries} out={output_path}")
        _log(f"[{stage_name}] inputs={list(input_files)[:6]}")
        result = None
        try:
            result = run_ffmpeg(cmd, log_fn=_log, timeout=timeout)
        except VideoEncodeFailed as e:
            last_err = str(e)
            reason = "encoder"
        except Exception as e:
            last_err = str(e)
            reason = "io"
        else:
            last_err = (result.stderr or "")[-500:]
            if result.returncode == 0 and verify_mp4(tmp_out, log_fn=_log):
                atomic_replace(tmp_out, output_path)
                _log(f"[{stage_name}] OK → {output_path}")
                return output_path
            reason = classify_ffmpeg_error(result.stderr, result.returncode)

        try:
            if os.path.exists(tmp_out):
                os.remove(tmp_out)
        except OSError:
            pass

        _log(f"[{stage_name}] fail reason={reason}: {last_err[-200:]}")

        if reason == "io":
            continue
        if reason == "encoder":
            # P0-4: одна контролируемая попытка программным кодеком
            if sw_fallback_enabled() and not sw_tried:
                sw_tried = True
                cmd = to_software_cmd(cmd)
                _log(
                    f"[{stage_name}] VideoToolbox недоступен → libx264 fallback "
                    f"(crf=18, preset=medium)"
                )
                continue
            raise VideoEncodeFailed(
                f"{stage_name}: VideoToolbox encoder error "
                f"(software fallback {'уже пробовали' if sw_tried else 'отключён'}).\n{last_err}"
            )
        if attempt < max_io_retries - 1:
            continue
        raise VideoEncodeFailed(
            f"{stage_name}: encode failed after retries (reason={reason}).\n{last_err}"
        )

    raise VideoEncodeFailed(f"{stage_name}: SSD недоступен.\n{last_err}")
