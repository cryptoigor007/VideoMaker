# VideoMaker FIX | 2026.09.09-r43-tail | 2026-09-09
# CHANGED: cut_segment force_reencode + accurate -ss after -i for short tail
# A3 | 2026-10-22: render_proxy 1080p master + native 4K conform (memory-gate/chunks/resume)
# P0-4 | 2026-10-22: memory-gate перед 4K-чанками, manifest resume, -threads 2
# P0-5 | 2026-10-23: memory-pause = RenderMemoryPause (не общий fail), resume
#   с первого неготового чанка; swap больше не повод остановить рендер.
# B4/P2 | 2026-10-23: conform_4k материализует vm-graphic:// до сборки графа
# PREV: 2026.09.03-r25
# REPLACE: video_maker/engines/video.py
"""Движок видео — ffmpeg-операции: склейка, vstack, обрезка, интро/аутро (4K + Apple Silicon VideoToolbox)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone

from ..paths import resolve_existing_path
from . import memory_guard
from .ffmpeg_resilient import (
    atomic_replace,
    ensure_storage,
    inject_hwaccel,
    run_ffmpeg,
    run_vt_encode,
    verify_mp4,
)

log = logging.getLogger(__name__)


class RenderMemoryPause(memory_guard.MemoryPauseError):
    """Мастер остановлен memory-gate: чанки и манифест сохранены для resume.

    Это не общая ошибка рендера: GUI показывает баннер «Продолжить», а
    headless `/api/run` просто перезапускает мастер и продолжает с первого
    неготового чанка из манифеста.
    """

    def __init__(
        self,
        message: str,
        *,
        done_chunks: int = 0,
        total_chunks: int = 0,
        next_chunk: int = 0,
        state: memory_guard.MemoryState | None = None,
    ) -> None:
        super().__init__(message, state=state)
        self.done_chunks = int(done_chunks)
        self.total_chunks = int(total_chunks)
        self.next_chunk = int(next_chunk)


def _inject_fallback_treatments(filter_complex: str, scenes, log_fn=None) -> str:
    """B4: blur+грейд для treated-сцен прямо в готовом filter_complex.

    render_graph не меняем: вставляем фильтры в сегмент нужного входа по
    его выходному label [vi]. Обычные сцены остаются байт-в-байт прежними.
    """
    try:
        from video_maker.cinematic.fallback_render import inject_scene_treatments
        out = inject_scene_treatments(filter_complex, list(scenes or []))
        if out != filter_complex:
            try:
                (log_fn or log.info)("[РЕНДЕР] treated: blur+грейд в графе")
            except Exception:
                pass
        return out
    except Exception as e:
        try:
            (log_fn or log.info)(f"[РЕНДЕР] treated-обработка пропущена: {str(e)[:120]}")
        except Exception:
            pass
        return filter_complex


def _materialize_graphic_plan(plan, output_path: str, width: int, height: int,
                              log_fn=None, fps: int = 30) -> int:
    """B4: маркеры graphic_fallback → реальные карточки/нейтральный фон."""
    try:
        from video_maker.cinematic.fallback_render import materialize_graphic_scenes
        cache_dir = os.path.join(os.path.dirname(output_path) or ".", "graphic_cards")
        n = materialize_graphic_scenes(
            plan, cache_dir=cache_dir, width=int(width), height=int(height),
            fps=int(fps), log_fn=log_fn,
        )
        if n:
            try:
                (log_fn or log.info)(
                    f"[РЕНДЕР] graphic-сцен подготовлено: {n} "
                    f"(кэш: {cache_dir})")
            except Exception:
                pass
        return int(n)
    except Exception as e:
        try:
            (log_fn or log.info)(
                f"[РЕНДЕР] graphic-сцены: подготовка фона пропущена: {str(e)[:140]}")
        except Exception:
            pass
        return 0



def _work_tmp_dir(preferred: str = "") -> str:
    """Каталог для промежуточных файлов: предпочитаем внутренний диск, не /Volumes."""
    candidates = []
    if preferred and not str(preferred).startswith("/Volumes/"):
        candidates.append(preferred)
    home_tmp = os.path.join(os.path.expanduser("~"), "video_maker", "_tmp")
    candidates.append(home_tmp)
    candidates.append("/tmp/video_maker")
    if preferred:
        candidates.append(preferred)
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            test = os.path.join(d, f".w_{os.getpid()}")
            with open(test, "wb") as f:
                f.write(b"1")
            os.remove(test)
            return d
        except OSError:
            continue
    return preferred or "/tmp"


def _ffmpeg_bin() -> str:
    """ffmpeg с поддержкой libass."""
    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and os.path.exists(path):
            return str(path)
    except Exception:
        pass
    return "ffmpeg"


def _ffprobe_video_info(video_path: str) -> tuple[int, int, float]:
    """Получить ширину, высоту и fps видео. Устойчиво к битым файлам."""
    import json
    cmd = [
        "ffprobe", "-v", "quiet",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate",
        "-of", "json",
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        raise ValueError(f"ffprobe failed for {video_path}: {e}") from e
    raw = (result.stdout or "").strip()
    if not raw:
        raise ValueError(f"Пустой ответ ffprobe для {video_path}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Невалидный JSON ffprobe для {video_path}: {e}") from e

    streams = data.get("streams") or []
    if not streams:
        raise ValueError(f"Не удалось найти видеопоток в файле {video_path}")

    stream = streams[0]
    width = stream.get("width") or 3840
    height = stream.get("height") or 2160

    fps_str = stream.get("r_frame_rate") or "30/1"
    if "/" in str(fps_str):
        try:
            num, den = map(int, str(fps_str).split("/"))
            fps = num / den if den != 0 else 30.0
        except Exception:
            fps = 30.0
    else:
        try:
            fps = float(fps_str)
        except (TypeError, ValueError):
            fps = 30.0

    return int(width), int(height), float(fps)


def _probe_av_durations(video_path: str) -> tuple[float, float]:
    """(длительность видеопотока, длительность аудиопотока) через ffprobe.

    Для жёсткого гейта «видео не короче озвучки»: если у видеопотока нет
    своей duration — берём format=duration (для mp4 duration есть всегда).
    """
    import json as _json
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "stream=codec_type,duration:format=duration",
        "-of", "json",
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        data = _json.loads((result.stdout or "").strip() or "{}")
    except Exception:
        return 0.0, 0.0
    vdur = adur = 0.0
    for st in data.get("streams") or []:
        try:
            d = float(st.get("duration") or 0.0)
        except Exception:
            d = 0.0
        if st.get("codec_type") == "video" and d > vdur:
            vdur = d
        elif st.get("codec_type") == "audio" and d > adur:
            adur = d
    if vdur <= 0.0:
        try:
            vdur = float((data.get("format") or {}).get("duration") or 0.0)
        except Exception:
            vdur = 0.0
    return vdur, adur


# --- P0-4: манифест чанков (resume после падения/перезапуска) ---------------

_CHUNK_MANIFEST_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _chunk_manifest_path(output_path: str) -> str:
    return output_path + ".chunks_manifest.json"


def _atomic_write_json(path: str, payload: dict) -> None:
    """Атомарная запись JSON: tmp + fsync + os.replace (рядом с output)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _read_json_file(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _remove_quiet(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _sha256_payload(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _probe_video_duration(path: str) -> float:
    try:
        vdur, _adur = _probe_av_durations(path)
        return float(vdur)
    except Exception:
        return 0.0


def _chunk_record_ready(record: dict) -> bool:
    """Готов ли чанк из манифеста: статус + файл + duration (±5%/1.5с)."""
    if not isinstance(record, dict) or record.get("status") != "done":
        return False
    path = str(record.get("file") or "")
    if not path or not os.path.isfile(path) or os.path.getsize(path) < 1024:
        return False
    if not verify_mp4(path):
        return False
    expected = 0.0
    try:
        expected = float(record.get("duration") or 0.0)
    except (TypeError, ValueError):
        expected = 0.0
    if expected > 0.5:
        actual = _probe_video_duration(path)
        if actual <= 0.0:
            return False
        if abs(actual - expected) > max(1.5, expected * 0.05):
            return False
    return True


def _chunk_resume_index(chunks: list[dict]) -> int:
    """Индекс первого неготового чанка (0-based); len(chunks) — всё готово.

    Чистый предикат resume: используется в логе «продолжаем с чанка N» и
    в тестах. Готовность определяется только статусом из манифеста —
    файлы уже проверены `_chunk_record_ready` при загрузке манифеста.
    """
    for position, record in enumerate(chunks or []):
        if not isinstance(record, dict) or str(record.get("status") or "") != "done":
            return position
    return len(chunks or [])


# --- A3: proxy-мастер 1080p → native 4K conform ------------------------------

# Финальные 4K-геометрии → размер proxy-мастера для быстрого превью.
_PROXY_GEOMETRY = {
    (3840, 2160): (1920, 1080),
    (2160, 3840): (1080, 1920),
}
_PROXY_SUFFIX = ".proxy_1080p.mp4"
_PROXY_BITRATE = "12M"
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif"})


def _env_flag(name: str) -> bool | None:
    """True/False из env; None — переменная не задана."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return None
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "да"}


def _render_proxy_enabled(explicit: bool | None = None) -> bool:
    """A3: включён ли режим proxy-мастера.

    Приоритет: аргумент рендера → env VM_RENDER_PROXY → GUI-настройка
    (~/.video_maker_settings.json: render_proxy) → выключено (совместимость).
    """
    if explicit is not None:
        return bool(explicit)
    flag = _env_flag("VM_RENDER_PROXY")
    if flag is None:
        flag = _env_flag("RENDER_PROXY")
    if flag is not None:
        return flag
    try:
        settings_path = os.path.join(os.path.expanduser("~"), ".video_maker_settings.json")
        with open(settings_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "render_proxy" in data:
            value = data.get("render_proxy")
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on", "да"}
            return bool(value)
    except Exception:
        pass
    return False


def _proxy_size(width: int, height: int) -> tuple[int, int] | None:
    """Размер proxy-мастера для финальной геометрии (None — proxy не нужен)."""
    try:
        return _PROXY_GEOMETRY.get((int(width), int(height)))
    except (TypeError, ValueError):
        return None


def _build_encode_cmd(
    ffmpeg: str,
    filter_complex: str,
    paths: list[str],
    out_file: str,
    *,
    target_duration: float,
    bitrate: str,
    audio_file: str = "",
    has_audio: bool = False,
    with_audio: bool = False,
    threads: int | None = None,
    serialize_graph: bool = False,
    hwaccel: bool = True,
) -> list[str]:
    """Команда кодирования одного графа (чанка/мастера): пути → filter → файл."""
    ins: list[str] = []
    for vf in paths:
        if os.path.splitext(vf)[1].lower() in _IMAGE_SUFFIXES:
            ins.extend(["-loop", "1", "-framerate", "30", "-t", "30", "-i", vf])
        else:
            ins.extend(["-i", vf])
    if serialize_graph or len(paths) >= 8:
        # P0-4: сериализуем декодер — меньше пик памяти на 8 ГБ Mac.
        capped: list[str] = []
        for x in ins:
            if x == "-i":
                capped += ["-threads", "1", "-i"]
            else:
                capped.append(x)
        ins = capped
    if with_audio and has_audio:
        ins += ["-i", audio_file]
    cmd: list[str] = [ffmpeg, "-y"]
    if serialize_graph or len(paths) >= 8:
        cmd += ["-filter_complex_threads", "1"]
    cmd += [
        *ins,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-t", str(target_duration),
        "-c:v", "h264_videotoolbox",
    ]
    if threads is not None:
        cmd += ["-threads", str(int(threads))]
    cmd += [
        "-b:v", bitrate, "-allow_sw", "1",
        "-pix_fmt", "yuv420p",
        "-r", "30",
    ]
    if with_audio and has_audio:
        cmd += [
            "-map", f"{len(paths)}:a",
            "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000",
        ]
    else:
        cmd += ["-an"]
    cmd.append(out_file)
    return inject_hwaccel(cmd) if hwaccel else cmd


def _run_encode_cmd(
    cmd: list[str],
    *,
    target_duration: float,
    log_fn=None,
    label: str = "",
) -> str:
    """VT-энкод с повтором на CPU (libx264). Возвращает 'vt'/'cpu' или ''."""
    _log = log_fn or log.info
    timeout = max(3600.0, float(target_duration) * 14.0)
    res = run_ffmpeg(cmd, log_fn=_log, media_duration=target_duration, timeout=timeout)
    if res.returncode == 0 and verify_mp4(cmd[-1], log_fn=_log):
        return "vt"
    err = ((res.stderr or res.stdout or "")[-600:]) or "verify failed"
    _log(f"[ВИДЕО] VT-энкод не удался{label}, повтор на CPU (libx264): {err[-160:]}")
    cpu_cmd = list(cmd)
    try:
        i_v = cpu_cmd.index("h264_videotoolbox")
        cpu_cmd[i_v] = "libx264"
        if "-allow_sw" in cpu_cmd:
            k = cpu_cmd.index("-allow_sw")
            del cpu_cmd[k:k + 2]
        if "-b:v" in cpu_cmd:
            k = cpu_cmd.index("-b:v")
            del cpu_cmd[k:k + 2]
        if "-threads" in cpu_cmd:
            cpu_cmd[cpu_cmd.index("-threads") + 1] = "2"
            cpu_cmd[i_v + 1:i_v + 1] = ["-preset", "veryfast", "-crf", "18"]
        else:
            cpu_cmd[i_v + 1:i_v + 1] = ["-preset", "veryfast", "-crf", "18", "-threads", "4"]
    except ValueError:
        pass
    res = run_ffmpeg(cpu_cmd, log_fn=_log, media_duration=target_duration, timeout=timeout)
    if res.returncode == 0 and verify_mp4(cmd[-1], log_fn=_log):
        return "cpu"
    err = ((res.stderr or res.stdout or "")[-600:]) or "verify failed"
    _log(f"[ВИДЕО] Ошибка: {err}")
    return ""


def _build_geometry_filter_complex(
    selected: list[str],
    *,
    plan,
    settings,
    width: int,
    height: int,
    target_duration: float,
    fps: int = 30,
) -> str:
    """Граф плана в ЛЮБОЙ геометрии (для proxy 1080p, минуя 4K-гейт CE)."""
    from video_maker.cinematic.config import DEFAULT_CINEMATIC_SETTINGS
    from video_maker.cinematic.render_graph import build_render_graph
    from video_maker.cinematic.vm_bridge import legacy_filter_complex

    settings = settings or DEFAULT_CINEMATIC_SETTINGS
    n = len(selected)
    if plan is None or not getattr(plan, "scenes", None) or n == 0:
        return legacy_filter_complex(max(1, n), int(width), int(height))
    scenes = list(plan.scenes)
    if len(scenes) != n:
        # То же выравнивание, что делает build_ce_filter_complex.
        log.warning("CE plan scenes=%d selected=%d — pad/trim to match", len(scenes), n)
        while len(scenes) < n:
            last = scenes[-1] if scenes else None
            if last is None:
                break
            scenes.append(last)
        scenes = scenes[:n]
        try:
            plan.scenes = scenes
        except Exception:
            pass
    try:
        graph = build_render_graph(
            plan, settings, out_w=int(width), out_h=int(height), fps=fps
        )
        fc = graph.concat_or_xfade
        if "[vout]" in fc:
            fc = fc.replace("[vout]", "[v]")
        # B4: treated-сцены — blur/грейд в любом 4K→1080p/9:16 графе.
        fc = _inject_fallback_treatments(fc, scenes, log_fn=log.info)
        return fc
    except Exception as e:
        log.warning("CE graph at %sx%s failed (%s) — legacy fallback", width, height, e)
        return legacy_filter_complex(max(1, n), int(width), int(height))


def _render_chunked_plan(
    *,
    plan,
    settings,
    target_duration: float,
    width: int,
    height: int,
    bitrate: str,
    audio_file: str,
    has_audio: bool,
    output_path: str,
    tmp_path: str,
    log_fn=None,
    tag: str = "ВИДЕО 4K",
    hwaccel: bool = True,
    chunk_files: list[str] | None = None,
) -> dict:
    """Чанковый рендер готового плана с memory-gate и manifest-resume.

    Возвращает {"started", "ok", "partial"}. MemoryPauseError оборачивается
    в RenderMemoryPause (пауза, не общая ошибка): caller сохраняет готовые
    чанки и не уходит в монолитный энкод (это как раз путь к OOM).
    FFmpegBusyError пробрасывается как есть. Используется и proxy-мастером
    (1080p), и 4K conform — правила памяти одинаковые.
    """
    _log = log_fn or log.info
    sink = chunk_files if chunk_files is not None else []
    partial = False
    started = False
    try:
        from video_maker.cinematic.config import DEFAULT_CINEMATIC_SETTINGS
        from video_maker.cinematic.timeline import apply_rhythm as _apply_rhythm2
        from video_maker.cinematic.render_graph import assemble_render_graph as _assemble
    except Exception as _im_e:
        _log(f"[{tag}] чанки: импорт не удался ({_im_e})")
        return {"started": False, "ok": False, "partial": False}
    settings = settings or DEFAULT_CINEMATIC_SETTINGS

    try:
        if plan is None or not getattr(plan, "scenes", None):
            return {"started": False, "ok": False, "partial": False}
        _tl2 = _apply_rhythm2(
            plan.scenes, settings,
            target_duration=plan.target_duration or plan.total_duration,
        )
        scenes2 = list(_tl2.scenes)
        trans2 = list(_tl2.transitions)
        tl_warnings = list(getattr(_tl2, "warnings", []) or [])
        for w in tl_warnings[:8]:
            _log(f"[CE] timeline: {w}")
        try:
            plan.warnings = list(plan.warnings) + tl_warnings
        except Exception:
            pass

        # [ORD] 8ГБ Mac: даже 13 4K-планов при занятой памяти дают SIGKILL.
        # Режем на чанки начиная с 8 планов (раньше — только 30+).
        if len(scenes2) < 8:
            return {"started": False, "ok": False, "partial": False}
        chunk_size = min(8, max(4, -(-len(scenes2) // 5)))
        groups: list[list[int]] = []
        cur: list[int] = []
        for i in range(len(scenes2)):
            cur.append(i)
            if len(cur) >= chunk_size and i + 1 < len(scenes2):
                tr = trans2[i] if i < len(trans2) else None
                if tr is not None and (tr.spec.kind == "hard" or tr.spec.duration <= 0.01):
                    groups.append(cur)
                    cur = []
        if cur:
            groups.append(cur)
        if len(groups) <= 1:
            return {"started": False, "ok": False, "partial": False}
        started = True

        # --- план чанков + манифест (resume) ---
        specs: list[dict] = []
        for gi, g in enumerate(groups):
            g_scenes = [scenes2[j] for j in g]
            g_tr = [trans2[j] for j in g[:-1]]
            g_graph = _assemble(
                g_scenes, g_tr, settings,
                out_w=width, out_h=height, fps=30,
            )
            g_fc = g_graph.concat_or_xfade
            if "[vout]" in g_fc:
                g_fc = g_fc.replace("[vout]", "[v]")
            # B4: обработка treated-сцен сохраняется и в чанках.
            g_fc = _inject_fallback_treatments(g_fc, g_scenes, log_fn=_log)
            g_paths = [str(getattr(sc, "source_path", "") or "") for sc in g_scenes]
            specs.append({
                "index": gi,
                "graph": g_graph,
                "fc": g_fc,
                "fc_sha": hashlib.sha256(g_fc.encode("utf-8")).hexdigest(),
                "paths": g_paths,
                "file": os.path.abspath(output_path + f".chunk{gi:02d}.mp4"),
                "duration": float(min(g_graph.total_duration, float(target_duration))),
                "scenes": g_scenes,
            })

        fp_payload = {
            "version": _CHUNK_MANIFEST_VERSION,
            "width": int(width),
            "height": int(height),
            "fps": 30,
            "target_duration": round(float(target_duration), 3),
            "bitrate": str(bitrate),
            "chunks": [
                {
                    "index": sp["index"],
                    "duration": round(float(sp["duration"]), 3),
                    "graph_sha": sp["fc_sha"],
                    "scenes": [
                        {
                            "path": os.path.abspath(str(getattr(sc, "source_path", "") or "")),
                            "in": round(float(getattr(sc, "source_in", 0.0) or 0.0), 3),
                            "out": round(float(getattr(sc, "source_out", 0.0) or 0.0), 3),
                        }
                        for sc in sp["scenes"]
                    ],
                }
                for sp in specs
            ],
        }
        fingerprint = _sha256_payload(fp_payload)
        manifest_path = _chunk_manifest_path(output_path)
        manifest: dict = {
            "version": _CHUNK_MANIFEST_VERSION,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "output": os.path.abspath(output_path),
            "fingerprint": fingerprint,
            "width": int(width),
            "height": int(height),
            "fps": 30,
            "target_duration": float(target_duration),
            "chunks": [
                {
                    "index": sp["index"],
                    "file": sp["file"],
                    "duration": float(sp["duration"]),
                    "encoder": "",
                    "status": "pending",
                    "updated_at": _now_iso(),
                }
                for sp in specs
            ],
        }

        def _save_manifest(note: str = "") -> None:
            manifest["updated_at"] = _now_iso()
            if note:
                manifest["last_error"] = note
            try:
                _atomic_write_json(manifest_path, manifest)
            except Exception as _mf_e:
                _log(f"[ВИДЕО] манифест чанков не записан: {_mf_e}")

        old = _read_json_file(manifest_path)
        reused = 0
        if old and old.get("fingerprint") != fingerprint:
            _log("[ВИДЕО] манифест чанков от другого плана — начинаем заново")
            old = None
        if old:
            old_chunks = old.get("chunks")
            old_by_idx = {
                int(r.get("index")): r
                for r in (old_chunks if isinstance(old_chunks, list) else [])
                if isinstance(r, dict) and str(r.get("index", "")).isdigit()
            }
        else:
            old_by_idx = {}

        rec_by_idx = {int(r["index"]): r for r in manifest["chunks"]}
        for sp in specs:
            rec = rec_by_idx[sp["index"]]
            old_rec = old_by_idx.get(sp["index"])
            if not old_rec:
                continue
            if str(old_rec.get("status") or "") != "done":
                continue
            try:
                same_path = os.path.abspath(str(old_rec.get("file") or "")) == sp["file"]
            except Exception:
                same_path = False
            if same_path and _chunk_record_ready(old_rec):
                rec["status"] = "done"
                rec["encoder"] = str(old_rec.get("encoder") or "vt")
                rec["actual_duration"] = old_rec.get("actual_duration", 0.0)
                rec["updated_at"] = str(old_rec.get("updated_at") or _now_iso())
                reused += 1
            else:
                _log(f"[ВИДЕО] чанк {sp['index'] + 1}: файл из манифеста отсутствует/битый — перерендер")
        _save_manifest()

        _log(f"[{tag}] Чанковый рендер: {len(groups)} чанков "
             f"(память {width}x{height}-графа ограничена; манифест: готово {reused})")
        _resume_at = _chunk_resume_index(manifest.get("chunks") or [])
        if _resume_at < len(specs) and (reused or old):
            _log(f"[{tag}] продолжение с чанка {_resume_at + 1}/{len(specs)} "
                 f"(готово {reused} из манифеста)")
        enc_used: set[str] = set()
        for sp in specs:
            gi = int(sp["index"])
            rec = rec_by_idx[gi]
            sink.append(sp["file"])
            if rec.get("status") == "done":
                enc_used.add(str(rec.get("encoder") or "vt"))
                partial = True
                _log(f"[ВИДЕО] чанк {gi + 1}: из манифеста (готов)")
                continue

            _log(f"[{tag}] чанк {gi + 1}/{len(groups)}: "
                 f"{len(sp['paths'])} планов, {sp['duration']:.1f}с → "
                 f"{os.path.basename(sp['file'])}")
            ensure_storage(sp["paths"], sp["file"], log_fn=_log)
            # P0-4: не кодируем параллельно с другим ffmpeg этой сессии.
            memory_guard.wait_for_ffmpeg_idle(_log, label=f"чанк {gi + 1} ({tag})")
            # P0-4: RAM/swap gate — пауза вместо swap/SIGKILL.
            memory_guard.wait_for_memory(_log, label=f"перед чанком {gi + 1} ({tag})")

            t0 = time.monotonic()
            enc = _run_encode_cmd(
                _build_encode_cmd(
                    _ffmpeg_bin(), sp["fc"], sp["paths"], sp["file"],
                    target_duration=target_duration, bitrate=bitrate,
                    with_audio=False, threads=2, serialize_graph=True,
                    hwaccel=hwaccel,
                ),
                target_duration=target_duration,
                log_fn=_log,
                label=f" (чанк {gi + 1})",
            )
            elapsed = time.monotonic() - t0
            if not enc:
                rec.update(status="failed", updated_at=_now_iso(),
                           error="encode failed (VT и CPU)")
                _save_manifest(note=f"чанк {gi + 1}: encode failed")
                return {"started": True, "ok": False, "partial": partial}
            rec.update(status="done", encoder=enc, updated_at=_now_iso(),
                       actual_duration=round(_probe_video_duration(sp["file"]), 3))
            enc_used.add(enc)
            partial = True
            _save_manifest()
            _log(f"[ВИДЕО] чанк {gi + 1}: готов за {elapsed:.1f}с ({enc})")

        # склейка чанков (stream copy) + озвучка
        memory_guard.wait_for_ffmpeg_idle(_log, label="перед сборкой")
        if len(enc_used) > 1:
            memory_guard.wait_for_memory(_log, label="перед сборкой")
        lst = output_path + f".chunks_{uuid.uuid4().hex[:6]}.txt"
        with open(lst, "w", encoding="utf-8") as f:
            for cf in sink:
                f.write(f"file '{os.path.abspath(cf)}'\n")
        ccmd = [_ffmpeg_bin(), "-y", "-f", "concat", "-safe", "0", "-i", lst]
        if has_audio:
            ccmd += ["-i", audio_file]
        ccmd += ["-map", "0:v"]
        if has_audio:
            ccmd += ["-map", "1:a", "-c:a", "aac", "-b:a", "192k",
                     "-ac", "2", "-ar", "48000"]
        else:
            ccmd += ["-an"]
        if len(enc_used) <= 1:
            ccmd += ["-c:v", "copy"]
        else:
            _log(f"[{tag}] чанки разными энкодерами — склейка с перекодированием")
            ccmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-threads", "2"]
        ccmd += ["-t", str(target_duration), tmp_path]
        res = run_ffmpeg(ccmd, log_fn=_log, media_duration=target_duration)
        try:
            os.remove(lst)
        except OSError:
            pass
        if res.returncode != 0 or not verify_mp4(tmp_path, log_fn=_log):
            err = ((res.stderr or res.stdout or "")[-600:]) or "concat failed"
            _log(f"[ВИДЕО] Ошибка сборки чанков: {err}")
            manifest["last_error"] = f"concat: {err[-300:]}"
            _save_manifest()
            return {"started": True, "ok": False, "partial": partial}
        manifest["status"] = "concat_done"
        _save_manifest()
        return {"started": True, "ok": True, "partial": partial}
    except memory_guard.MemoryPressureError as _mp:
        # P0-5: чанки и манифест сохраняем — следующий запуск («Продолжить»)
        # продолжит с первого неготового чанка. Монолитный энкод не запускаем.
        _done = 0
        _total = 0
        _next = 0
        try:
            _chunks = manifest.get("chunks") if "manifest" in locals() else []
            _chunks = _chunks if isinstance(_chunks, list) else []
            _done = sum(1 for _r in _chunks if isinstance(_r, dict)
                        and str(_r.get("status") or "") == "done")
            _total = len(_chunks)
            _next = _chunk_resume_index(_chunks)
        except Exception:
            pass
        try:
            if "manifest" in locals():
                manifest["last_error"] = "memory gate: рендер приостановлен"
                _save_manifest()
            _log(f"[{tag}] {_mp}")
            if _total and _next >= _total:
                _log(f"[{tag}] готово чанков {_done}/{_total}; "
                     "«Продолжить» выполнит только сборку — чанки не переделываются")
            elif _total:
                _log(f"[{tag}] готово чанков {_done}/{_total}; "
                     f"«Продолжить» начнёт с чанка {_next + 1} — чанки не переделываются")
            else:
                _log(f"[{tag}] Чанковый рендер остановлен memory-gate; "
                     "готовые чанки сохранены для resume")
        except Exception:
            pass
        raise RenderMemoryPause(
            str(_mp),
            done_chunks=_done,
            total_chunks=_total,
            next_chunk=_next,
            state=getattr(_mp, "state", None),
        ) from _mp
    except memory_guard.FFmpegBusyError:
        try:
            if "manifest" in locals():
                manifest["last_error"] = "memory gate: ffmpeg ещё работает"
                _save_manifest()
            _log(f"[{tag}] Чанковый рендер остановлен memory-gate; "
                 "готовые чанки сохранены для resume")
        except Exception:
            pass
        raise
    except Exception as _ch_e:
        _log(f"[{tag}] Чанковый рендер не удался ({_ch_e}) — обычный путь")
        return {"started": started, "ok": False, "partial": partial}


def conform_4k(
    *,
    plan=None,
    inputs: list[str] | None = None,
    output_path: str,
    target_duration: float,
    audio_file: str = "",
    cinematic_settings=None,
    width: int = 3840,
    height: int = 2160,
    bitrate: str = "28M",
    fps: int = 30,
    log_fn=None,
    min_chunk_scenes: int = 8,
) -> str:
    """A3: native 4K conform из того же плана/исходников, что и proxy-мастер.

    Никакого апскейла 1080→4K: кадры берутся заново из 4K-исходников по
    source_in/source_out плана. Память — те же правила, что у мастера:
    memory-gate перед чанком, чанки по жёстким склейкам, manifest-resume.
    """
    _log = log_fn or log.info
    target_duration = float(target_duration)
    if target_duration <= 0:
        raise ValueError("conform_4k: target_duration должен быть > 0")
    if not output_path:
        raise ValueError("conform_4k: не задан output_path")
    width, height = int(width), int(height)

    from video_maker.cinematic.config import DEFAULT_CINEMATIC_SETTINGS

    ce = cinematic_settings or DEFAULT_CINEMATIC_SETTINGS
    scenes: list = []
    transitions: list = []
    paths: list[str] = []
    chunkable = False
    filter_complex = ""

    if plan is not None and getattr(plan, "scenes", None):
        # [B4] Прямой вызов conform_4k с планом не должен падать на маркерах
        # vm-graphic://: материализуем их в реальные карточки/фон ДО сборки
        # графа. Вызов идемпотентен (кэш по scene_id/длительности/размеру),
        # поэтому повторный conform того же плана переиспользует файлы.
        _materialize_graphic_plan(
            plan, output_path=output_path, width=width, height=height,
            log_fn=_log, fps=fps,
        )
        try:
            from video_maker.cinematic.timeline import apply_rhythm
            from video_maker.cinematic.render_graph import assemble_render_graph

            _plan_target = (
                getattr(plan, "target_duration", 0.0)
                or getattr(plan, "total_duration", 0.0)
                or target_duration
            )
            tl = apply_rhythm(list(plan.scenes), ce, target_duration=float(_plan_target))
            scenes = list(tl.scenes)
            transitions = list(tl.transitions)
            paths = [str(getattr(sc, "source_path", "") or "") for sc in scenes]
            if not paths or not all(paths):
                raise ValueError("в плане нет source_path")
            graph = assemble_render_graph(
                scenes, transitions, ce, out_w=width, out_h=height, fps=fps
            )
            filter_complex = graph.concat_or_xfade
            if "[vout]" in filter_complex:
                filter_complex = filter_complex.replace("[vout]", "[v]")
            # B4: treated-сцены в 4K conform (тот же план, что у proxy).
            filter_complex = _inject_fallback_treatments(
                filter_complex, scenes, log_fn=_log)
            chunkable = True
            _log(f"[КОНФОРМ 4K] native {width}x{height}: план {len(scenes)} сцен, "
                 f"{len(transitions)} переходов")
            if inputs is not None and len(inputs) != len(paths):
                _log(f"[КОНФОРМ 4K] inputs={len(inputs)} ≠ план {len(paths)} — используем план")
        except Exception as e:
            if inputs:
                _log(f"[КОНФОРМ 4K] план не применён ({e}) — legacy из inputs")
                paths = [str(p) for p in inputs]
                plan = None
                chunkable = False
            else:
                raise

    if not paths:
        paths = [str(p) for p in (inputs or [])]
        if not paths:
            raise ValueError("conform_4k: нужен план или inputs")
        from video_maker.cinematic.vm_bridge import legacy_filter_complex

        filter_complex = legacy_filter_complex(len(paths), width, height)
        _log(f"[КОНФОРМ 4K] legacy {width}x{height}: {len(paths)} входов")

    has_audio = bool(audio_file and os.path.exists(audio_file))
    ffmpeg = _ffmpeg_bin()
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    tmp_out = output_path + f".vm_tmp_{uuid.uuid4().hex[:8]}.mp4"
    manifest_path = _chunk_manifest_path(output_path)
    chunk_files: list[str] = []
    ce_many = bool(getattr(ce, "enabled", False)) and len(paths) > 6
    ok = False
    partial = False
    started = False
    t_start = time.monotonic()

    try:
        ensure_storage(
            paths + ([audio_file] if has_audio else []), tmp_out, log_fn=_log
        )
        if chunkable and len(scenes) >= int(min_chunk_scenes):
            try:
                res = _render_chunked_plan(
                    plan=plan,
                    settings=ce,
                    target_duration=target_duration,
                    width=width,
                    height=height,
                    bitrate=bitrate,
                    audio_file=audio_file,
                    has_audio=has_audio,
                    output_path=output_path,
                    tmp_path=tmp_out,
                    log_fn=_log,
                    tag="КОНФОРМ 4K",
                    hwaccel=not ce_many,
                    chunk_files=chunk_files,
                )
                started = bool(res.get("started"))
                partial = bool(res.get("partial"))
                ok = bool(res.get("ok"))
            except (memory_guard.MemoryPressureError, memory_guard.FFmpegBusyError):
                partial = True
                raise

        if not ok:
            # Монолитный 4K conform: при критической нехватке RAM — пауза,
            # а не попытка энкода «на авось» (SIGKILL). Resume перезапустит
            # conform после того, как пользователь закроет приложения.
            try:
                memory_guard.wait_for_ffmpeg_idle(_log, label="4K conform")
                memory_guard.wait_for_memory(_log, label="перед 4K conform")
            except memory_guard.MemoryPressureError as _mg_e:
                partial = True
                _log(f"[КОНФОРМ 4K] пауза по памяти: {_mg_e}")
                raise RenderMemoryPause(str(_mg_e), state=getattr(_mg_e, "state", None)) from _mg_e
            except memory_guard.FFmpegBusyError as _mg_e:
                _log(f"[КОНФОРМ 4K] memory-gate (монолитный): {_mg_e} — пробуем без ожидания")
            cmd = _build_encode_cmd(
                ffmpeg, filter_complex, paths, tmp_out,
                target_duration=target_duration, bitrate=bitrate,
                audio_file=audio_file, has_audio=has_audio, with_audio=True,
                hwaccel=not ce_many,
            )
            _log(f"[КОНФОРМ 4K] Одна команда: {len(paths)} входов → "
                 f"{os.path.basename(output_path)}")
            if not _run_encode_cmd(
                cmd, target_duration=target_duration, log_fn=_log
            ):
                raise RuntimeError("4K conform encode failed (VT и CPU)")

        # [ГЕЙТ] тот же контракт, что у мастера: видео не короче озвучки.
        vdur, adur = _probe_av_durations(tmp_out)
        if has_audio and adur > 0.5 and vdur < 0.95 * adur:
            _log(f"[ГЕЙТ] ОШИБКА conform: видео {vdur:.1f}с < 0.95×аудио {adur:.1f}с")
            raise RuntimeError(
                f"[ГЕЙТ] conform: видео {vdur:.1f}с короче озвучки {adur:.1f}с")
        atomic_replace(tmp_out, output_path)
        ok = True
        _log(f"[КОНФОРМ 4K] готов за {time.monotonic() - t_start:.1f}с "
             f"({'chunks' if started else 'single'}), видео {vdur:.1f}с / "
             f"аудио {adur:.1f}с → {output_path}")
        return output_path
    finally:
        if os.path.exists(tmp_out):
            try:
                os.remove(tmp_out)
            except OSError:
                pass
        # При memory-pause готовые чанки/манифест НЕ удаляем — resume.
        if ok or (not partial and not started):
            for cf in chunk_files:
                _remove_quiet(cf)
            _remove_quiet(manifest_path)



# Папка использованных B-roll (исключается из ротации)
USED_DIR_NAME = "used"
_SKIP_SUBDIR_NAMES = frozenset({
    "used", "использованная", "использованные", "использовано",
    "_tmp", ".ds_store", "__macosx",
})
_VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif")
_MEDIA_EXTS = _VIDEO_EXTS + _IMAGE_EXTS


def collect_video_files(folder: str, rotate_subfolders: bool = True) -> list[str]:
    """Собрать видео и картинки из папки B-roll (тема/сток).

    Если есть подпапки (кроме used) — ротация по подпапкам:
      1-й клип из подпапки A, 2-й из B, … interleave.
    Папка used / использованная никогда не сканируется.
    Если подпапок нет — плоский список файлов в folder.
    """
    resolved_folder = resolve_existing_path(folder, kind="dir")
    if not resolved_folder:
        return []
    folder = resolved_folder

    def _is_video(name: str) -> bool:
        # Video + still images (topic stock folders may mix both)
        # ._file.mp4 — AppleDouble/resource fork на macOS
        if not name or name.startswith(".") or name.startswith("._"):
            return False
        return name.lower().endswith(_MEDIA_EXTS)

    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return []

    subdirs: list[str] = []
    for name in entries:
        if name.startswith("."):
            continue
        if name.lower() in _SKIP_SUBDIR_NAMES:
            continue
        path = os.path.join(folder, name)
        if os.path.isdir(path):
            subdirs.append(path)

    # Файлы прямо в корне B-roll
    root_files: list[str] = []
    for name in entries:
        if name.startswith(".") or name.lower() in _SKIP_SUBDIR_NAMES:
            continue
        full = os.path.join(folder, name)
        if os.path.isfile(full) and _is_video(name):
            root_files.append(full)

    if rotate_subfolders and subdirs:
        queues: list[list[str]] = []
        for sd in subdirs:
            try:
                files = [
                    os.path.join(sd, f)
                    for f in sorted(os.listdir(sd))
                    if _is_video(f) and os.path.isfile(os.path.join(sd, f))
                ]
            except OSError:
                files = []
            if files:
                queues.append(files)
        # Если подпапки пустые — fallback на корень
        if not queues:
            return root_files
        result: list[str] = []
        max_len = max(len(q) for q in queues)
        for i in range(max_len):
            for q in queues:
                if i < len(q):
                    result.append(q[i])
        # Клипы из корня тоже участвуют (после ротации тем)
        result.extend(root_files)
        return result

    return root_files


def collect_video_files_scene_order(folder: str) -> list[str]:
    """Собрать пул смарт-B-roll в порядке СЦЕН, без round-robin.

    `collect_video_files` перемежает подпапки (1-й клип из A, 1-й из B, …) —
    для тематических папок это красиво, но для пула сцен ломает порядок:
    клип сцены 01 оказывается рядом с клипом сцены 02 и т.д. Здесь подпапки
    идут последовательно (01_, 02_, …, 13_), файлы внутри — по имени.
    Папки used/ и служебные пропускаются.
    """
    resolved_folder = resolve_existing_path(folder, kind="dir")
    if not resolved_folder:
        return []
    folder = resolved_folder

    def _is_media(name: str) -> bool:
        if not name or name.startswith(".") or name.startswith("._"):
            return False
        return name.lower().endswith(_MEDIA_EXTS)

    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return []

    result: list[str] = []
    for name in entries:
        if name.startswith(".") or name.lower() in _SKIP_SUBDIR_NAMES:
            continue
        full = os.path.join(folder, name)
        if os.path.isdir(full):
            try:
                files = [
                    os.path.join(full, f)
                    for f in sorted(os.listdir(full))
                    if _is_media(f) and os.path.isfile(os.path.join(full, f))
                ]
            except OSError:
                files = []
            result.extend(files)
        elif os.path.isfile(full) and _is_media(name):
            result.append(full)
    return result


def move_to_used(root_folder: str, paths: list[str], log_fn=None) -> int:
    """Перенести использованные клипы в {root_folder}/used/."""
    import shutil

    _log = log_fn or log.info
    if not root_folder or not paths:
        return 0
    root_abs = os.path.abspath(root_folder)
    used_dir = os.path.join(root_abs, USED_DIR_NAME)
    try:
        os.makedirs(used_dir, exist_ok=True)
    except OSError as e:
        _log(f"[B-ROLL] не удалось создать {used_dir}: {e}")
        return 0

    moved = 0
    seen: set[str] = set()
    for p in paths:
        if not p:
            continue
        abs_p = os.path.abspath(p)
        if abs_p in seen:
            continue
        seen.add(abs_p)
        if not os.path.isfile(abs_p):
            continue
        try:
            common = os.path.commonpath([root_abs, abs_p])
        except ValueError:
            continue
        if common != root_abs:
            continue
        parts_lower = {x.lower() for x in abs_p.split(os.sep)}
        if USED_DIR_NAME in parts_lower or "использованная" in parts_lower:
            continue
        # B4: сгенерированные карточки/фон — кэш рендера, не B-roll пула.
        if "graphic_cards" in parts_lower:
            continue
        base = os.path.basename(abs_p)
        # P1-7: сохраняем относительный путь (тематические подпапки не вырождаются)
        rel = os.path.relpath(abs_p, root_abs)
        dest = os.path.join(used_dir, rel)
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
        except OSError:
            dest = os.path.join(used_dir, base)
        if os.path.exists(dest):
            stem, ext = os.path.splitext(os.path.basename(dest))
            d_dir = os.path.dirname(dest)
            n = 1
            while os.path.exists(dest):
                dest = os.path.join(d_dir, f"{stem}_{n}{ext}")
                n += 1
        try:
            shutil.move(abs_p, dest)
            moved += 1
            _log(f"[B-ROLL] → used: {rel}")
        except OSError as e:
            _log(f"[B-ROLL] move fail {base}: {e}")
    if moved:
        _log(f"[B-ROLL] в used перенесено: {moved}")
    return moved


def fit_video_to_duration(
    video_files: list[str],
    target_duration: float,
    output_path: str,
    audio_file: str = "",
    log_fn=None,
    broll_root: str = "",
    move_used: bool = True,
    # Cinematic Enhancement (optional; OFF by default via DEFAULT settings)
    cinematic_settings=None,
    edit_plan=None,
    transcript_segments=None,
    output_format: str = "16:9",
    # Geometry (default horizontal 4K; vertical path passes 2160x3840)
    width: int = 3840,
    height: int = 2160,
    bitrate: str = "28M",
    # [ORD] смарт-пул со сценными папками (01_, 02_, …): жёсткая привязка
    # клипов к временным окнам сцен вместо смысловой перетасовки.
    scene_bound: bool = False,
    # Лучшие файлы сцены из манифеста (порядок = приоритет).
    scene_preferred: dict | None = None,
    # A3: None → env VM_RENDER_PROXY / GUI render_proxy; True/False — принудительно.
    render_proxy: bool | None = None,
) -> str:
    """Scale+concat+trim under target duration. Default 3840x2160 @ 28M.

    A3: render_proxy=True → быстрый 1080p proxy-мастер, затем native 4K
    conform из того же плана/исходников (proxy-кадры в 4K не масштабируются).
    """
    _log = log_fn or log.info
    width, height = int(width), int(height)
    _final_output_path = output_path
    _final_bitrate = str(bitrate)
    _proxy_on = _render_proxy_enabled(render_proxy)
    _proxy_wh = _proxy_size(width, height) if _proxy_on else None
    if _proxy_on and _proxy_wh is None:
        _log(f"[РЕНДЕР] render_proxy=on, но геометрия {width}x{height} не 4K — proxy отключён")
        _proxy_on = False
    _render_w, _render_h = _proxy_wh if _proxy_wh else (width, height)
    _encode_bitrate = _PROXY_BITRATE if _proxy_on else _final_bitrate
    _master_output_path = _final_output_path
    if _proxy_on:
        _stem, _sfx_ext = os.path.splitext(_final_output_path)
        _master_output_path = _stem + _PROXY_SUFFIX
        _log(f"[РЕНДЕР] proxy-режим: мастер {_render_w}x{_render_h} @ {_encode_bitrate}, "
             f"финал {width}x{height} @ {_final_bitrate} (conform из 4K-исходников)")
    _render_tag = "ВИДЕО proxy 1080p" if _proxy_on else "ВИДЕО 4K"
    _log(f"[ВИДЕО] Склейка под {target_duration:.1f}с ({width}x{height} @ {bitrate})")

    if not video_files:
        raise FileNotFoundError("Нет видеофайлов для склейки")

    ffmpeg = _ffmpeg_bin()

    # [VM-SB] Смысловой порядок B-roll под сцены («псевдо-нейролинк»).
    # Только при включённом «Кинематографе» и наличии транскрипта.
    # На каждый слот сцены — лучший неиспользованный клип пула; без совпадений
    # порядок не меняется (поведение OFF идентично baseline).
    if (
        video_files
        and transcript_segments
        and not scene_bound
        and getattr(cinematic_settings, "enabled", False)
        and getattr(cinematic_settings, "smart_broll", True)
    ):
        try:
            from video_maker.semantic.matcher import order_pool_for_scenes
            _sb_ordered = order_pool_for_scenes(
                list(video_files), list(transcript_segments), float(target_duration)
            )
            if _sb_ordered:
                video_files = _sb_ordered
                _log(f"[SB] смысловой подбор клипов под сцены (псевдо-нейролинк): пул {len(video_files)}")
        except Exception as _sb_e:
            _log(f"[SB] пропуск смыслового подбора: {_sb_e}")

    # [VM-SB] Потолок вклада клипа = длина плана (9с/темп): чтобы набирались
    # РАЗНЫЕ клипы короткими планами, а не 3 длинных куска.
    _sb_cap = None
    if (
        transcript_segments
        and getattr(cinematic_settings, "enabled", False)
    ):  # потолок вклада клипа — НЕ зависит от перетасовки (иначе 2 клипа на весь ролик)
        try:
            _t = float(getattr(cinematic_settings, "plan_tempo", 1.0) or 1.0)
            _sb_cap = max(4.0, 9.0 / max(0.5, min(2.0, _t)))
            # [VM-SB] пословный монтаж: планы короче → берём больше разных клипов
            if getattr(cinematic_settings, "keyword_cuts", False):
                _ms = float(getattr(cinematic_settings, "min_shot_sec", 2.0) or 2.0)
                _sb_cap = max(1.5, min(6.0, _ms) * 1.2)
        except Exception:
            _sb_cap = None
    # [VM-FIX] с переходами (xfade) видео короче озвучки: берём клипов с запасом
    _sb_stop = float(target_duration) * (1.08 if _sb_cap else 1.0)

    selected: list[str] = []
    total_dur = 0.0
    skipped_bad = 0
    _img_ext = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif"}
    # [ORD] Нельзя останавливать выбор по сумме длительностей, пока не увидели
    # хотя бы один клип из каждой сценной папки: иначе последние сцены
    # остаются без клипов (финал замирает/повторяет чужой кадр).
    _scene_targets: set[int] = set()
    _scene_seen: set[int] = set()
    _scene_idx_fn = None
    if scene_bound:
        try:
            from video_maker.semantic.matcher import scene_index_from_path as _scene_idx_fn
            _scene_targets = {
                i for i in (_scene_idx_fn(p) for p in video_files) if i is not None
            }
            if _scene_targets:
                _log(f"[ORD] сценный пул: папок сцен {len(_scene_targets)} "
                     f"({min(_scene_targets)}–{max(_scene_targets)})")
        except Exception as _ord_e:
            _log(f"[ORD] сценная привязка выключена: {_ord_e}")
    for vf in video_files:
        base = os.path.basename(vf)
        if base.startswith(".") or base.startswith("._"):
            skipped_bad += 1
            continue
        ext = os.path.splitext(base)[1].lower()
        is_img = ext in _img_ext
        if is_img:
            # Still: contributes hold time (slot-sized), looped later in CE graph
            remaining = max(0.5, float(target_duration) - total_dur)
            dur = min(remaining, 6.0)  # one still fills at most 6s per pick
            if not os.path.isfile(vf):
                skipped_bad += 1
                continue
        else:
            try:
                dur = probe_duration(vf)
            except Exception as e:
                skipped_bad += 1
                _log(f"[ВИДЕО] пропуск (ffprobe): {base} — {e}")
                continue
            if dur <= 0.05:
                skipped_bad += 1
                continue
        selected.append(vf)
        total_dur += min(dur, _sb_cap) if _sb_cap else dur
        if scene_bound and _scene_idx_fn is not None:
            _si = _scene_idx_fn(vf)
            if _si is not None:
                _scene_seen.add(_si)
        tag = "IMG" if is_img else "VID"
        _log(f"[ВИДЕО] + [{tag}] {base} ({dur:.1f}s) → сумма {total_dur:.1f}s")
        # [ORD] Сценный пул выбираем ЦЕЛИКОМ: иначе обрыв по сумме может
        # отрезать preferred-клип последней сцены (он ещё не встретился).
        if total_dur >= _sb_stop and not scene_bound:
            break

    if skipped_bad:
        _log(f"[ВИДЕО] Пропущено проблемных клипов: {skipped_bad}")

    if total_dur < target_duration and selected:
        _log(f"[ВИДЕО] Все клипы короче цели ({total_dur:.1f} < {target_duration:.1f}), повторяем")
        safety = 0
        while total_dur < target_duration and safety < 200:
            safety += 1
            for vf in list(selected):
                try:
                    dur = probe_duration(vf)
                except Exception:
                    continue
                if dur <= 0.05:
                    continue
                selected.append(vf)
                total_dur += min(dur, _sb_cap) if _sb_cap else dur
                if total_dur >= _sb_stop:
                    break

    if not selected:
        raise FileNotFoundError("Не удалось набрать ни одного валидного клипа")

    _log(f"[ВИДЕО] Выбрано клипов: {len(selected)} (сумма {total_dur:.1f}s ≥ {target_duration:.1f}s)")

    n = len(selected)
    inputs: list[str] = []
    _img_ext3 = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif"}
    # [VM-FIX] VideoToolbox имеет лимит одновременных сессий: при многих входах
    # (пословный монтаж) энкодер падает с -22. Тогда декод — на CPU.
    _ce_many = bool(getattr(cinematic_settings, "enabled", False)) and n > 6
    for vf in selected:
        if os.path.splitext(vf)[1].lower() in _img_ext3:
            # Still: loop for later trim/hold; duration refined by CE filter -t
            inputs.extend(["-loop", "1", "-framerate", "30", "-t", "30", "-i", vf])
        elif _ce_many:
            inputs.extend(["-i", vf])
        else:
            # decode B-roll на VideoToolbox (Apple Silicon)
            inputs.extend(["-hwaccel", "videotoolbox", "-i", vf])

    has_audio = bool(audio_file and os.path.exists(audio_file))
    if has_audio:
        # аудио — без hwaccel
        inputs.extend(["-i", audio_file])

    # Cinematic Enhancement: rebuild plan on FINAL selected list (P1-01)
    _ce_debug = os.environ.get("CE_DEBUG_PLAN", "").strip().lower() in ("1", "true", "yes")
    _ce_plan = None
    _ce = None
    _ce_4k = False
    try:
        from video_maker.cinematic.vm_bridge import (
            build_ce_filter_complex,
            plan_from_selected_clips,
        )
        from video_maker.cinematic.config import DEFAULT_CINEMATIC_SETTINGS
        _ce = cinematic_settings if cinematic_settings is not None else DEFAULT_CINEMATIC_SETTINGS
        _plan = edit_plan
        try:
            from video_maker.cinematic.feature_flag import is_4k_geometry, is_enabled_for_geometry
            _ce_4k = is_enabled_for_geometry(_ce, width, height)
        except Exception:
            _ce_4k = bool(getattr(_ce, "enabled", False)) and (width, height) in ((3840, 2160), (2160, 3840))
        if not _ce_4k and getattr(_ce, "enabled", False):
            _log(f"[CE] skip non-4K {width}x{height} → legacy")
        if _ce_4k:
            # durations for the exact selected sequence (incl. repeats)
            durs = []
            _img_ext2 = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif"}
            for vf in selected:
                if os.path.splitext(vf)[1].lower() in _img_ext2:
                    # Still can hold any slot length — do not cap plan slice
                    durs.append(max(float(target_duration), 30.0))
                else:
                    try:
                        durs.append(float(probe_duration(vf)))
                    except Exception:
                        durs.append(2.0)
            _plan = plan_from_selected_clips(
                selected,
                target_duration=float(target_duration),
                settings=_ce,
                transcript_segments=list(transcript_segments or []),
                output_format=output_format or "16:9",
                clip_durations=durs,
                scene_bound=scene_bound,
                scene_preferred=scene_preferred,
            )
            _ce_plan = _plan
            _log(f"[CE] plan rebuilt on selected={len(selected)} scenes={len(_plan.scenes)}")
            # [VM-FIX][ГЕЙТ] До рендера: контент плана обязан покрывать озвучку.
            # Если клипов не хватает — повторяем пул циклом и пересобираем план
            # (иначе видео короче аудио и последний кадр замирает).
            try:
                _cover = sum(
                    max(0.0, float(sc.source_out) - float(sc.source_in))
                    for sc in _plan.scenes
                )
                # [B4] hard_only не теряет время на xfade: запас 1% нужен только
                # переходам. Иначе scene-bound план (окна ровно = озвучке)
                # всегда «не покрывал» target и CE падал в legacy без обработки.
                try:
                    _trans_mode = str(getattr(_ce, "allowed_transitions", "hard_only")
                                      or "hard_only")
                except Exception:
                    _trans_mode = "hard_only"
                _need = float(target_duration) * (
                    1.01 if _trans_mode != "hard_only" else 0.999
                )
                if _cover < _need and selected:
                    _log(f"[ГЕЙТ] покрытие плана {_cover:.1f}с < {_need:.1f}с — повторяю клипы и пересобираю план")
                    _extra = list(selected)
                    _safety = 0
                    _extra_cover = _cover
                    while _extra_cover < _need and _safety < 300:
                        _safety += 1
                        for _vf in list(_extra):
                            try:
                                _dd = float(probe_duration(_vf))
                            except Exception:
                                continue
                            _extra.append(_vf)
                            _extra_cover += min(_dd, 9.0)
                            if _extra_cover >= _need:
                                break
                    _durs2: list[float] = []
                    for _vf in _extra:
                        try:
                            _durs2.append(float(probe_duration(_vf)))
                        except Exception:
                            _durs2.append(2.0)
                    _plan = plan_from_selected_clips(
                        _extra,
                        target_duration=float(target_duration),
                        settings=_ce,
                        transcript_segments=list(transcript_segments or []),
                        output_format=output_format or "16:9",
                        clip_durations=_durs2,
                        scene_bound=scene_bound,
                        scene_preferred=scene_preferred,
                    )
                    _ce_plan = _plan
                    _cover = sum(
                        max(0.0, float(sc.source_out) - float(sc.source_in))
                        for sc in _plan.scenes
                    )
                    _log(f"[ГЕЙТ] план пересобран: сцен {len(_plan.scenes)}, покрытие {_cover:.1f}с")
                    if _cover < _need:
                        raise RuntimeError(
                            f"[ГЕЙТ] план не покрывает озвучку: {_cover:.1f}с < {_need:.1f}с")
            except RuntimeError:
                raise
            except Exception as _g_e:
                _log(f"[ГЕЙТ] проверка покрытия плана пропущена: {_g_e}")
            # [B4] graphic_fallback: маркеры плана → реальные карточки/фон.
            # Делаем ДО синхронизации входов: ffmpeg получает готовые файлы,
            # а не пути-маркеры; длительности/порядок сцен не меняются.
            if _plan is not None and getattr(_plan, "scenes", None):
                _materialize_graphic_plan(
                    _plan, output_path=_master_output_path,
                    width=width, height=height, log_fn=_log, fps=30,
                )
            # [VM-FIX] Рендерим ИМЕННО план: входы = пути сцен плана, в его порядке.
            # Иначе фильтр (по плану) и входы (по списку выбора) расходятся по
            # индексам — энкодер получает не те кадры и падает.
            try:
                _plan_paths = [
                    str(getattr(sc, "source_path", "") or "")
                    for sc in _plan.scenes
                ]
                if _plan_paths and all(_plan_paths):
                    selected = _plan_paths
                    n = len(selected)
                    inputs = []
                    for _vf in selected:
                        if os.path.splitext(_vf)[1].lower() in _img_ext3:
                            inputs.extend(["-loop", "1", "-framerate", "30", "-t", "30", "-i", _vf])
                        else:
                            inputs.extend(["-i", _vf])
                    if has_audio:
                        inputs.extend(["-i", audio_file])
                    _log(f"[CE] входы синхронизированы с планом: {len(selected)}")
            except Exception as _syn_e:
                _log(f"[CE] синхронизация входов пропущена: {_syn_e}")
            if _ce_debug and _plan is not None:
                try:
                    from video_maker.cinematic.plan_io import write_plan_json
                    side = output_path + ".edit_plan.json"
                    write_plan_json(_plan, side)
                    _log(f"[CE] debug plan → {side}")
                except Exception as _pe:
                    _log(f"[CE] plan dump skipped: {_pe}")
        filter_complex = build_ce_filter_complex(
            selected,
            width=width,
            height=height,
            target_duration=target_duration,
            settings=_ce,
            plan=_plan,
        )
        # [B4] treated: blur+грейд (обычные сцены не затрагиваются).
        if _plan is not None and getattr(_plan, "scenes", None):
            filter_complex = _inject_fallback_treatments(
                filter_complex, _plan.scenes, log_fn=_log)
        # Optional transition SFX under narration (whoosh on soft dissolves)
        if (
            has_audio
            and _ce_plan is not None
            and getattr(_ce, "transition_sfx", False)
            and getattr(_ce, "allowed_transitions", "hard_only") != "hard_only"
        ):
            try:
                from video_maker.cinematic.timeline import apply_rhythm
                from video_maker.cinematic.pipeline_hooks import maybe_render_transition_sfx
                _tl = apply_rhythm(
                    _ce_plan.scenes, _ce,
                    target_duration=float(target_duration),
                )
                for _w in list(getattr(_tl, "warnings", []) or [])[:8]:
                    _log(f"[CE] timeline: {_w}")
                try:
                    _ce_plan.warnings = list(_ce_plan.warnings) + list(
                        getattr(_tl, "warnings", []) or [])
                except Exception:
                    pass
                _stage = os.path.join(
                    os.path.dirname(output_path) or ".",
                    f"ce_sfx_{uuid.uuid4().hex[:8]}",
                )
                os.makedirs(_stage, exist_ok=True)
                _sfx = maybe_render_transition_sfx(
                    settings=_ce,
                    timeline_transitions=_tl.transitions,
                    total_duration=float(target_duration),
                    staging_dir=_stage,
                    narration_path=audio_file,
                )
                if _sfx.get("mixed_narration_path") and os.path.isfile(_sfx["mixed_narration_path"]):
                    audio_file = _sfx["mixed_narration_path"]
                    # rebuild inputs tail: last -i is audio — replace path in inputs list
                    # inputs built earlier; update has_audio path by rewriting last audio -i
                    for _ii in range(len(inputs) - 1, -1, -1):
                        if inputs[_ii] == "-i" and _ii + 1 < len(inputs):
                            # find audio input (last -i that is not a video from selected)
                            pass
                    # Safer: rebuild audio input only
                    # Remove previous audio -i if present at end
                    if len(inputs) >= 2 and inputs[-2] == "-i":
                        inputs[-1] = audio_file
                    _log(f"[CE] transition SFX mixed under narration ({len(_tl.transitions)} transitions)")
                elif _sfx.get("warnings"):
                    _log(f"[CE] SFX skipped: {_sfx['warnings']}")
            except Exception as _sfx_err:
                _log(f"[CE] SFX path skipped: {_sfx_err}")
    except Exception as _ce_err:
        _log(f"[CE] fallback to legacy filters ({_ce_err})")
        filters = []
        for i in range(n):
            filters.append(
                f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p[v{i}]"
            )
        concat_in = "".join(f"[v{i}]" for i in range(n))
        filters.append(f"{concat_in}concat=n={n}:v=1:a=0[v]")
        filter_complex = ";".join(filters)

    # A3: proxy-мастер — ТОТ ЖЕ план/нарезка, но граф в 1080p.
    # CE-гейт рассчитан только на 4K, поэтому строим render graph напрямую
    # (build_geometry_filter_complex), а не через build_ce_filter_complex.
    if _proxy_on:
        try:
            filter_complex = _build_geometry_filter_complex(
                selected,
                plan=_ce_plan,
                settings=_ce,
                width=_render_w,
                height=_render_h,
                target_duration=target_duration,
            )
            _log(f"[РЕНДЕР] proxy-граф: {width}x{height} → {_render_w}x{_render_h} "
                 "(тот же план/нарезка)")
        except Exception as _px_e:
            _proxy_on = False
            _render_w, _render_h = width, height
            _encode_bitrate = _final_bitrate
            _master_output_path = _final_output_path
            _render_tag = "ВИДЕО 4K"
            _log(f"[РЕНДЕР] proxy отключён ({_px_e}) — рендер сразу 4K")

    # [VM-FIX] 80+ входов × авто-потоки декодера = исчерпание потоков macOS
    # («Error reinitializing filters / Resource temporarily unavailable», -35) —
    # капим потоки декодера на вход (локальный пул тоже падал).
    if n >= 20:
        _capped: list[str] = []
        for _x in inputs:
            if _x == "-i":
                _capped += ["-threads", "1", "-i"]
            else:
                _capped.append(_x)
        inputs = _capped
    os.makedirs(os.path.dirname(_master_output_path) or ".", exist_ok=True)
    # P0-6: пишем в tmp → verify → атомарная подмена (битый мастер не уедет в ветки)
    tmp_out = _master_output_path + f".vm_tmp_{uuid.uuid4().hex[:8]}.mp4"
    check_inputs = list(selected) + ([audio_file] if has_audio else [])
    # [VM-FIX] сериализованный граф медленнее: таймаут 14× реального времени
    _enc_timeout = max(3600.0, float(target_duration) * 14.0)
    _chunk_files: list[str] = []
    _partial_chunks = False
    _pause_keep = False
    _render_ok = False
    _manifest_path = _chunk_manifest_path(_master_output_path)

    def _build_cmd(fc_local: str, paths_local: list[str], out_file: str,
                   with_audio: bool, threads: int | None = None,
                   serialize_graph: bool = False) -> list[str]:
        """Команда кодирования одного графа (чанка): пути → filter → файл.

        threads/serialize_graph — P0-4: для чанков фиксируем -threads 2 и
        последовательный filter_complex (пик памяти на 8 ГБ Mac).
        """
        _ins: list[str] = []
        for _vf in paths_local:
            if os.path.splitext(_vf)[1].lower() in _img_ext3:
                _ins.extend(["-loop", "1", "-framerate", "30", "-t", "30", "-i", _vf])
            else:
                _ins.extend(["-i", _vf])
        if serialize_graph or len(paths_local) >= 8:
            _c1: list[str] = []
            for _x in _ins:
                if _x == "-i":
                    _c1 += ["-threads", "1", "-i"]
                else:
                    _c1.append(_x)
            _ins = _c1
        if with_audio and has_audio:
            # [VM-FIX] аудио — ПОСЛЕ видео-входов (индекс = len(paths)); без него
            # ffmpeg падал с «Invalid input file index: N» при -map N:a
            _ins += ["-i", audio_file]
        _c2 = [ffmpeg, "-y"]
        if serialize_graph or len(paths_local) >= 8:
            # [VM-FIX] сериализация графа — меньше пик памяти (8ГБ Mac)
            _c2 += ["-filter_complex_threads", "1"]
        _c2 += [
            *_ins,
            "-filter_complex", fc_local,
            "-map", "[v]",
            "-t", str(target_duration),
            "-c:v", "h264_videotoolbox",
        ]
        if threads is not None:
            _c2 += ["-threads", str(int(threads))]
        _c2 += [
            "-b:v", _encode_bitrate, "-allow_sw", "1",
            "-pix_fmt", "yuv420p",
            "-r", "30",
        ]
        if with_audio and has_audio:
            _c2 += ["-map", f"{len(paths_local)}:a",
                    "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000"]
        else:
            _c2 += ["-an"]
        _c2.append(out_file)
        return _c2 if _ce_many else inject_hwaccel(_c2)

    def _run_encode(cmd_local: list[str], label: str = "") -> str:
        """VT-энкод с повтором на CPU (libx264). Возвращает 'vt'/'cpu' или ''."""
        res = run_ffmpeg(cmd_local, log_fn=_log, media_duration=target_duration,
                         timeout=_enc_timeout)
        if res.returncode == 0 and verify_mp4(cmd_local[-1], log_fn=_log):
            return "vt"
        err = ((res.stderr or res.stdout or "")[-600:]) or "verify failed"
        _log(f"[ВИДЕО] VT-энкод не удался{label}, повтор на CPU (libx264): {err[-160:]}")
        cpu_cmd = list(cmd_local)
        try:
            i_v = cpu_cmd.index("h264_videotoolbox")
            cpu_cmd[i_v] = "libx264"
            if "-allow_sw" in cpu_cmd:
                k = cpu_cmd.index("-allow_sw")
                del cpu_cmd[k:k + 2]
            if "-b:v" in cpu_cmd:
                k = cpu_cmd.index("-b:v")
                del cpu_cmd[k:k + 2]
            # P0-4: чанк уже несёт -threads 2 — не повышаем до 4 при fallback.
            if "-threads" in cpu_cmd:
                cpu_cmd[cpu_cmd.index("-threads") + 1] = "2"
                cpu_cmd[i_v + 1:i_v + 1] = ["-preset", "veryfast", "-crf", "18"]
            else:
                cpu_cmd[i_v + 1:i_v + 1] = ["-preset", "veryfast", "-crf", "18", "-threads", "4"]
        except ValueError:
            pass
        res = run_ffmpeg(cpu_cmd, log_fn=_log, media_duration=target_duration,
                         timeout=_enc_timeout)
        if res.returncode == 0 and verify_mp4(cmd_local[-1], log_fn=_log):
            return "cpu"
        err = ((res.stderr or res.stdout or "")[-600:]) or "verify failed"
        _log(f"[ВИДЕО] Ошибка: {err}")
        return ""

    def _render_chunked() -> bool:
        """A3/P0-4: чанковый рендер текущего мастера (proxy 1080p или 4K).

        Общая реализация — _render_chunked_plan: memory-gate перед каждым
        чанком, manifest-resume, чанки по жёстким склейкам. False → обычный
        монолитный путь (только для не-памятых ошибок).
        """
        nonlocal _partial_chunks, _pause_keep
        try:
            _res = _render_chunked_plan(
                plan=_ce_plan,
                settings=_ce,
                target_duration=target_duration,
                width=_render_w,
                height=_render_h,
                bitrate=_encode_bitrate,
                audio_file=audio_file,
                has_audio=has_audio,
                output_path=_master_output_path,
                tmp_path=tmp_out,
                log_fn=_log,
                tag=_render_tag,
                hwaccel=not _ce_many,
                chunk_files=_chunk_files,
            )
        except memory_guard.MemoryPressureError as _mp:
            _pause_keep = True
            _partial_chunks = True
            _done = int(getattr(_mp, "done_chunks", 0) or 0)
            _total = int(getattr(_mp, "total_chunks", 0) or 0)
            _next = int(getattr(_mp, "next_chunk", 0) or 0)
            _log(f"[{_render_tag}] {_mp}")
            if _total and _next >= _total:
                _log(f"[{_render_tag}] пауза по памяти: готово чанков {_done}/{_total}; "
                     "после «Продолжить» — только сборка (манифест сохранён)")
            elif _total:
                _log(f"[{_render_tag}] пауза по памяти: готово чанков {_done}/{_total}; "
                     f"после «Продолжить» — с чанка {_next + 1} (манифест сохранён)")
            else:
                _log(f"[{_render_tag}] Чанковый рендер остановлен memory-gate; "
                     "готовые чанки сохранены для resume")
            raise
        except memory_guard.FFmpegBusyError:
            _pause_keep = True
            _partial_chunks = True
            _log(f"[{_render_tag}] Чанковый рендер остановлен memory-gate; "
                 "готовые чанки сохранены для resume")
            raise
        _partial_chunks = _partial_chunks or bool(_res.get("partial"))
        return bool(_res.get("ok"))

    cmd = _build_cmd(filter_complex, selected, tmp_out, with_audio=True)
    _log(f"[{_render_tag}] Одна команда: {n} клипов → {os.path.basename(_master_output_path)}")
    _t_master_start = time.monotonic()
    try:
        ensure_storage(check_inputs, tmp_out, log_fn=_log)
        _done = False
        # [VM-FIX] большие 4K-графы → чанковый рендер (память).
        # A3: в proxy-режиме этот же путь рендерит 1080p-мастер.
        if _ce_4k and _ce_plan is not None and n >= 8:
            _done = _render_chunked()
        if not _done:
            # P0-5: монолитный энкод при критической нехватке RAM — пауза,
            # а не «пробуем без ожидания». Пользователь закроет приложения
            # и нажмёт «Продолжить»; чанки (если были) не удаляем.
            try:
                memory_guard.wait_for_ffmpeg_idle(_log, label="монолитный рендер")
                memory_guard.wait_for_memory(_log, label="перед рендером")
            except memory_guard.MemoryPressureError as _mg_e:
                _pause_keep = True
                _partial_chunks = True
                _log(f"[{_render_tag}] пауза по памяти: {_mg_e}")
                raise RenderMemoryPause(str(_mg_e), state=getattr(_mg_e, "state", None)) from _mg_e
            except memory_guard.FFmpegBusyError as _mg_e:
                _log(f"[{_render_tag}] memory-gate (монолитный): {_mg_e} — пробуем без ожидания")
            if not _run_encode(cmd):
                raise RuntimeError("Быстрая склейка failed (VT и CPU)" )
        # [ГЕЙТ] Жёсткая проверка: видео не короче озвучки (иначе стоп-кадр).
        # Короче 0.95×аудио — ОШИБКА: файл не отдаём, стадия падает.
        try:
            _vdur, _adur = _probe_av_durations(tmp_out)
            if has_audio and _adur > 0.5 and _vdur < 0.95 * _adur:
                _log(f"[ГЕЙТ] ОШИБКА: видео {_vdur:.1f}с < 0.95×аудио {_adur:.1f}с — стоп-кадр, файл не отдаём")
                raise RuntimeError(
                    f"[ГЕЙТ] видео {_vdur:.1f}с короче озвучки {_adur:.1f}с (стоп-кадр)")
            _log(f"[ГЕЙТ] OK: видео {_vdur:.1f}с / аудио {_adur:.1f}с")
        except RuntimeError:
            raise
        except Exception as _ge:
            _log(f"[ГЕЙТ] проверка длительностей пропущена: {_ge}")
        atomic_replace(tmp_out, _master_output_path)
        _render_ok = True
        if _proxy_on:
            # A3: proxy-мастер НЕ является финалом. Финальный 4K — отдельный
            # native conform из того же плана/исходников (без апскейла proxy).
            _log(f"[РЕНДЕР] proxy-мастер 1080p готов → запуск 4K conform "
                 f"(proxy за {time.monotonic() - _t_master_start:.1f}с)")
            _t_conform = time.monotonic()
            conform_4k(
                plan=_ce_plan,
                inputs=selected,
                output_path=_final_output_path,
                target_duration=target_duration,
                audio_file=audio_file if has_audio else "",
                cinematic_settings=_ce,
                width=width,
                height=height,
                bitrate=_final_bitrate,
                log_fn=_log,
            )
            output_path = _final_output_path
            _log(f"[РЕНДЕР] 4K conform готов за {time.monotonic() - _t_conform:.1f}с "
                 f"→ {output_path}")
        if _ce_debug and os.path.isfile(output_path):
            try:
                from video_maker.cinematic.quality_report import probe_media, write_report
                qr = probe_media(output_path)
                write_report(output_path + ".quality_report.json", {
                    "path": output_path,
                    "target_duration": float(target_duration),
                    "probe": qr.to_dict(),
                    "plan_scenes": len(_ce_plan.scenes) if _ce_plan else 0,
                })
                _log(f"[CE] quality_report → {output_path}.quality_report.json")
            except Exception as _qe:
                _log(f"[CE] quality_report skipped: {_qe}")
    finally:
        if os.path.exists(tmp_out):
            try:
                os.remove(tmp_out)
            except OSError:
                pass
        # P0-4: при падении/паузе готовые чанки и манифест НЕ удаляем —
        # следующий запуск продолжит с первого неготового (resume).
        if _render_ok or (not _partial_chunks and not _pause_keep):
            for _cf in _chunk_files:
                _remove_quiet(_cf)
            _remove_quiet(_manifest_path)

    if move_used and broll_root:
        move_to_used(broll_root, selected, log_fn=_log)

    return output_path


def _even(n: int) -> int:
    """Округлить до чётного (требуется для yuv420p / libx264)."""
    n = int(n)
    return n if n % 2 == 0 else n - 1



_VIDEO_META_CACHE: dict[str, dict] = {}
_VIDEO_META_FILE = os.path.join(
    os.path.expanduser("~"), "video_maker", "cache", "probe_video.json"
)
_VIDEO_META_LOADED = False
_VIDEO_META_DIRTY = False


def _video_meta_key(path: str) -> str:
    try:
        st = os.stat(path)
        return f"{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}"
    except OSError:
        return os.path.abspath(path)


def _load_video_meta_cache() -> None:
    """P2-7: постоянный кэш ffprobe (папки B-roll на сотни файлов)."""
    global _VIDEO_META_LOADED
    if _VIDEO_META_LOADED:
        return
    _VIDEO_META_LOADED = True
    try:
        import json as _json

        with open(_VIDEO_META_FILE, "r", encoding="utf-8") as f:
            data = _json.load(f)
        if isinstance(data, dict):
            _VIDEO_META_CACHE.update(
                {k: v for k, v in data.items() if isinstance(v, dict)}
            )
    except Exception:
        pass


def save_video_meta_cache() -> None:
    """Сохранить кэш ffprobe (вызывается по завершении прогона, best-effort)."""
    global _VIDEO_META_DIRTY
    if not _VIDEO_META_DIRTY:
        return
    try:
        import json as _json

        os.makedirs(os.path.dirname(_VIDEO_META_FILE), exist_ok=True)
        tmp_path = f"{_VIDEO_META_FILE}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                _json.dump(_VIDEO_META_CACHE, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, _VIDEO_META_FILE)
            _VIDEO_META_DIRTY = False
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
    except Exception:
        pass


def probe_video_meta(path: str) -> dict:
    """{width,height,fps,duration} с кэшем (память + json). Ошибки → нули."""
    _load_video_meta_cache()
    key = _video_meta_key(path)
    cached = _VIDEO_META_CACHE.get(key)
    if isinstance(cached, dict):
        return cached
    meta = {"width": 0, "height": 0, "fps": 0.0, "duration": 0.0}
    try:
        from .audio import probe_duration

        w, h, fps = _ffprobe_video_info(path)
        meta = {
            "width": int(w),
            "height": int(h),
            "fps": float(fps),
            "duration": float(probe_duration(path) or 0.0),
        }
    except Exception:
        return meta
    global _VIDEO_META_DIRTY
    _VIDEO_META_CACHE[key] = meta
    _VIDEO_META_DIRTY = True
    return meta


def _cached_bg_half(background_path: str, target_w: int, half_h: int, log_fn=None) -> str:
    """Один раз отскейлить фон и кэшировать только полностью записанный PNG."""
    _log = log_fn or log.info
    import hashlib
    try:
        st = os.stat(background_path)
    except OSError:
        return background_path
    key = hashlib.sha256(
        f"{os.path.abspath(background_path)}|{st.st_mtime_ns}|{st.st_size}|"
        f"{getattr(st, 'st_ino', 0)}|{getattr(st, 'st_ctime_ns', 0)}|{target_w}x{half_h}".encode()
    ).hexdigest()[:20]
    cache_dir = os.path.join(os.path.expanduser("~"), "video_maker", "cache", "bg")
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except OSError as e:
        _log(f"[ВИДЕО] bg cache directory unavailable ({e}) — используем исходник")
        return background_path
    out = os.path.join(cache_dir, f"{key}.png")

    def _valid_png(path: str) -> bool:
        try:
            from PIL import Image
            with Image.open(path) as image:
                if image.format != "PNG" or image.size != (target_w, half_h):
                    return False
                image.verify()
            return True
        except Exception:
            return False

    if os.path.isfile(out):
        if _valid_png(out):
            return out
        try:
            os.remove(out)
        except OSError:
            pass

    # Same-directory temporary path ensures os.replace remains atomic. Keep the
    # .png extension because FFmpeg infers its image muxer from the suffix.
    temp_out = os.path.join(cache_dir, f".{key}.{os.getpid()}.{uuid.uuid4().hex}.tmp.png")
    ffmpeg = _ffmpeg_bin()
    cmd = [
        ffmpeg, "-y", "-i", background_path,
        "-vf", f"scale={target_w}:{half_h}:force_original_aspect_ratio=increase,"
               f"crop={target_w}:{half_h},setsar=1,format=rgb24",
        "-frames:v", "1",
        temp_out,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        _log("[ВИДЕО] bg cache timed out after 120s — используем исходник")
        try:
            if os.path.exists(temp_out):
                os.remove(temp_out)
        except OSError:
            pass
        return background_path
    except OSError as e:
        _log(f"[ВИДЕО] bg cache could not start ffmpeg ({e}) — используем исходник")
        try:
            if os.path.exists(temp_out):
                os.remove(temp_out)
        except OSError:
            pass
        return background_path
    try:
        if res.returncode != 0 or not _valid_png(temp_out):
            _log("[ВИДЕО] bg cache fail/invalid PNG — используем исходник")
            return background_path
        os.replace(temp_out, out)
        _log(f"[ВИДЕО] bg cache → {out}")
        return out
    except OSError as e:
        _log(f"[ВИДЕО] bg cache write fail ({e}) — используем исходник")
        return background_path
    finally:
        try:
            if os.path.exists(temp_out):
                os.remove(temp_out)
        except OSError:
            pass


def _publish_work_file(source: str, destination: str, log_fn=None) -> None:
    """Опубликовать локальный временный файл по целевому пути.

    os.replace не работает через границу файловых систем (внутренний диск →
    сетевой том, EXDEV «Cross-device link»), поэтому при такой ошибке
    используется обычный перенос с копированием. Временный файл убирается
    в любом случае.
    """
    _log = log_fn or log.info
    try:
        atomic_replace(source, destination)
    except OSError as exc:
        _log(f"[ВИДЕО] перенос через границу файловых систем: {exc}")
        import shutil
        shutil.move(source, destination)
    finally:
        if os.path.exists(source):
            try:
                os.remove(source)
            except OSError:
                pass


def vstack_video_image(
    video_path: str,
    background_path: str,
    output_path: str,
    log_fn=None,
    top_ratio: float = 0.6,
    ass_path: str = "",
    bitrate: str = "28M",
) -> str:
    """Геометрия 9:16; при переданном ass_path — один encode (geometry + burn ASS).

    Граф: scale+crop video → top | cached bg → bottom | vstack [| ass] → VT.
    """
    import time
    _log = log_fn or log.info
    t0 = time.time()
    has_ass = bool(ass_path and os.path.isfile(ass_path))
    _log(
        f"[ВИДЕО 4K] vertical geometry"
        f"{' + ASS (one encode)' if has_ass else '-only'}"
    )

    ffmpeg = _ffmpeg_bin()
    target_w, target_h = 2160, 3840
    half_h = _even(target_h // 2)

    bg = _cached_bg_half(background_path, target_w, half_h, log_fn=_log)

    # video scale+crop; bg exact size; optional ASS burn in same pass
    if has_ass:
        from .subtitles import _escape_ass_path
        esc = _escape_ass_path(ass_path)
        fc = (
            f"[0:v]scale={target_w}:{half_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{half_h},setsar=1[top];"
            f"[1:v]setsar=1,format=yuv420p[bot];"
            f"[top][bot]vstack=inputs=2[stacked];"
            f"[stacked]ass='{esc}'[outv]"
        )
    else:
        fc = (
            f"[0:v]scale={target_w}:{half_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{half_h},setsar=1[top];"
            f"[1:v]setsar=1,format=yuv420p[bot];"
            f"[top][bot]vstack=inputs=2[outv]"
        )

    dur = probe_duration(video_path)
    if not dur or float(dur) <= 0.05:
        raise RuntimeError(
            f"VSTACK: не удалось определить длительность исходника ({video_path!r}); "
            "рендер отменён, чтобы не опубликовать пустой вертикальный файл"
        )
    local_dir = _work_tmp_dir(os.path.dirname(output_path) or ".")
    local_out = os.path.join(local_dir, f"vstack_{os.getpid()}_{uuid.uuid4().hex[:8]}.mp4")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    def _run(use_hw: bool) -> subprocess.CompletedProcess:
        cmd = [ffmpeg, "-y"]
        if use_hw:
            cmd += ["-hwaccel", "videotoolbox"]
        cmd += [
            "-i", video_path,
            "-loop", "1", "-i", bg,
            "-filter_complex", fc,
            "-map", "[outv]",
            "-map", "0:a?",
            "-c:v", "h264_videotoolbox", "-b:v", bitrate,
            "-allow_sw", "0" if use_hw else "1",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-c:a", "copy",
            "-t", str(dur),
            local_out,
        ]
        _log(f"[ВИДЕО 4K] geometry cmd hw={use_hw} bitrate={bitrate} ass={has_ass}")
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=max(600.0, min(6 * 60 * 60, float(dur or 0.0) * 8.0)),
            )
        except subprocess.TimeoutExpired:
            _log("[ВИДЕО 4K] encode timeout — запускаю fallback/повтор")
            try:
                if os.path.isfile(local_out):
                    os.remove(local_out)
            except OSError:
                pass
            return subprocess.CompletedProcess(cmd, 124, "", "FFmpeg timed out")

    res = _run(True)
    if res.returncode != 0:
        _log("[ВИДЕО] geometry hwaccel fail → retry")
        try:
            if os.path.isfile(local_out):
                os.remove(local_out)
        except OSError:
            pass
        res = _run(False)
    # ASS may fail (fonts/libass); fall back to geometry-only
    if res.returncode != 0 and has_ass:
        _log("[ВИДЕО] geometry+ASS fail → retry geometry-only")
        try:
            if os.path.isfile(local_out):
                os.remove(local_out)
        except OSError:
            pass
        has_ass = False
        fc = (
            f"[0:v]scale={target_w}:{half_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{half_h},setsar=1[top];"
            f"[1:v]setsar=1,format=yuv420p[bot];"
            f"[top][bot]vstack=inputs=2[outv]"
        )
        res = _run(True)
        if res.returncode != 0:
            try:
                if os.path.isfile(local_out):
                    os.remove(local_out)
            except OSError:
                pass
            res = _run(False)
    if res.returncode != 0:
        err = (res.stderr or res.stdout or "")[-900:]
        _log(f"[ВИДЕО] geometry ошибка: {err}")
        raise RuntimeError(f"VSTACK geometry failed: {err}")

    if not os.path.isfile(local_out) or not verify_mp4(local_out, log_fn=_log):
        raise RuntimeError("VSTACK: результат не прошёл проверку; файл не опубликован")
    _publish_work_file(local_out, output_path, log_fn=_log)
    _log(
        f"[ВИДЕО 4K] geometry{'+ASS' if has_ass else ''} OK за "
        f"{time.time()-t0:.1f}s → {os.path.basename(output_path)}"
    )
    return output_path


def cut_segment(
    video_path: str,
    start: float,
    duration: float,
    output_path: str,
    log_fn=None,
    force_reencode: bool = False,
) -> str:
    """Обрезать сегмент. Сначала stream copy (быстро), fallback — VideoToolbox.

    force_reencode=True: сразу точный trim ( -ss после -i ), без copy.
    """
    _log = log_fn or log.info
    _log(f"[ВИДЕО] Обрезка: {start:.1f} — {start + duration:.1f}")

    ffmpeg = _ffmpeg_bin()
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    if not force_reencode:
        # Быстрый путь: copy (без перекодирования, качество = исходник)
        cmd_copy = [
            ffmpeg, "-y",
            "-ss", str(start),
            "-i", video_path,
            "-t", str(duration),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            output_path,
        ]
        try:
            res = subprocess.run(
                cmd_copy, capture_output=True, text=True,
                timeout=max(120.0, min(6 * 60 * 60, float(duration or 0.0) * 8.0)),
            )
        except subprocess.TimeoutExpired:
            _log("[ВИДЕО] stream-copy trim timed out — retry VideoToolbox")
            res = subprocess.CompletedProcess(cmd_copy, 124, "", "FFmpeg timed out")
            try:
                if os.path.isfile(output_path):
                    os.remove(output_path)
            except OSError:
                pass
        if res.returncode == 0 and os.path.isfile(output_path) and os.path.getsize(output_path) > 1000:
            _log("[ВИДЕО] Обрезка: stream copy OK")
            return output_path
        _log("[ВИДЕО] Обрезка: copy не удался → VideoToolbox")
    else:
        _log("[ВИДЕО] Обрезка: force re-encode (точный хвост)")

    # Точный trim: -ss после -i (медленнее, но конец/начало по таймкоду)
    cmd = [
        ffmpeg, "-y",
        "-hwaccel", "videotoolbox",
        "-i", video_path,
        "-ss", str(start),
        "-t", str(duration),
        "-c:v", "h264_videotoolbox", "-b:v", "28M", "-allow_sw", "1",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ]
    run_vt_encode(cmd, [video_path], output_path, log_fn=_log, stage_name="TRIM")
    return output_path


def retime_video_segment(
    video_path: str,
    output_path: str,
    *,
    speed_ratio: float = 1.0,
    remove_intervals: list[tuple[float, float]] | None = None,
    log_fn=None,
    fade_seconds: float = 0.015,
) -> str:
    """Один проход: вырезать интервалы и/или изменить скорость (видео+аудио вместе).

    Для шортов с отдельными настройками: скорость/вырезка поверх готового
    вертикального клипа. Видео и звук масштабируются вместе — прожжённые
    субтитры остаются в синхроне. Если менять нечего — возвращает исходный путь.
    """
    _log = log_fn or log.info
    try:
        speed = float(speed_ratio)
    except (TypeError, ValueError):
        speed = 1.0
    if not (speed > 0):
        speed = 1.0
    speed = min(2.0, max(0.5, speed))
    intervals: list[tuple[float, float]] = []
    for raw_start, raw_end in sorted(remove_intervals or []):
        start = max(0.0, float(raw_start))
        end = max(start, float(raw_end))
        if end - start <= 0.02:
            continue
        if intervals and start <= intervals[-1][1] + 0.005:
            intervals[-1] = (intervals[-1][0], max(intervals[-1][1], end))
        else:
            intervals.append((start, end))
    if not intervals and abs(speed - 1.0) <= 1e-6:
        return video_path

    from .audio import probe_duration

    duration = probe_duration(video_path)
    if duration <= 0:
        raise RuntimeError(f"Не удалось определить длительность для retime: {video_path}")

    # Оставляем куски между вырезанными интервалами.
    segments: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in intervals:
        start = min(start, duration)
        end = min(end, duration)
        if start > cursor + 0.02:
            segments.append((cursor, start))
        cursor = max(cursor, end)
    if duration > cursor + 0.02:
        segments.append((cursor, duration))
    if not segments:
        raise RuntimeError("После вырезаний не осталось видео")

    fade = max(0.005, min(0.05, float(fade_seconds)))
    filters: list[str] = []
    count = len(segments)
    for i, (start, end) in enumerate(segments):
        seg_dur = end - start
        filters.append(
            f"[0:v]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS[v{i}]"
        )
        audio = f"[0:a]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS"
        if i > 0:
            audio += f",afade=t=in:st=0:d={fade:.4f}"
        if i < count - 1:
            audio += f",afade=t=out:st={max(0.0, seg_dur - fade):.6f}:d={fade:.4f}"
        filters.append(f"{audio}[a{i}]")
    concat_in = "".join(f"[v{i}][a{i}]" for i in range(count))
    filters.append(f"{concat_in}concat=n={count}:v=1:a=1[vc][ac]")

    if abs(speed - 1.0) > 1e-6:
        from .speech_rate import atempo_chain

        atempo = ",".join(f"atempo={factor:.6f}" for factor in atempo_chain(speed))
        filters.append(f"[vc]setpts=PTS/{speed:.6f}[v]")
        filters.append(f"[ac]{atempo}[a]")
        v_label, a_label = "[v]", "[a]"
    else:
        v_label, a_label = "[vc]", "[ac]"

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    ffmpeg = _ffmpeg_bin()
    cmd = [
        ffmpeg, "-hide_banner", "-y",
        "-i", video_path,
        "-filter_complex", ";".join(filters),
        "-map", v_label, "-map", a_label,
        "-c:v", "h264_videotoolbox", "-b:v", "28M", "-allow_sw", "1",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-map_metadata", "-1",
        output_path,
    ]
    _log(
        f"[ШОРТ] Обработка: вырезаний={len(intervals)}, скорость=×{speed:.2f}, "
        f"кусков={count}"
    )
    run_vt_encode(cmd, [video_path], output_path, log_fn=_log, stage_name="SHORT ADJUST")
    return output_path


def add_intro_outro_mid(
    video_path: str,
    intro_outro_folder: str,
    enable_intro: bool = False,
    enable_middle: bool = False,
    enable_outro: bool = False,
    output_dir: str = "",
    log_fn=None,
    analysis: dict | None = None,
    explicit_intro: str = "",
    explicit_middle: str = "",
    explicit_outro: str = "",
    intro_duration: float = 3.0,
    middle_duration: float = 1.0,
    outro_duration: float = 3.0,
) -> str:
    """Добавить интро/аутро/мидл к видео (видеофайл или картинка→N сек).

    Приоритет путей (строго):
    1. explicit_* — путь с вкладки Intro/Middle/Outro.
       Если указан — используется ТОЛЬКО он. Поиск по имени в папке НЕ выполняется.
    2. Папка intro_outro_folder — только если explicit_* пустой (не указан).
    """
    _log = log_fn or log.info
    _log("[ВИДЕО 4K M1] Добавление интро/аутро/мидл...")

    if not (enable_intro or enable_middle or enable_outro):
        _log("[IMO] все флаги выключены — пропуск")
        return video_path

    ffmpeg = _ffmpeg_bin()
    if not output_dir:
        output_dir = os.path.dirname(video_path) or "."
    work_dir = _work_tmp_dir(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    main_w, main_h, _ = _ffprobe_video_info(video_path)
    main_dur = probe_duration(video_path)

    if analysis and enable_middle:
        middle_timing = analysis.get("middle", [])
        if middle_timing and isinstance(middle_timing, list) and len(middle_timing) > 0:
            mid_point = float(middle_timing[0].get("start", main_dur / 2))
            _log(f"[ВИДЕО] Middle timing из analysis: {mid_point:.1f}s")
        else:
            mid_point = main_dur / 2
    else:
        mid_point = main_dur / 2

    def _resolve_explicit(raw: str, label: str, enabled: bool) -> tuple[str, bool]:
        if not enabled:
            _log(f"[IMO] {label} выключен чекбоксом — путь/поиск игнорируются")
            return "", False
        raw = (raw or "").strip()
        if not raw:
            return "", False
        if os.path.isfile(raw):
            _log(f"[IMO] {label}: explicit OK → {raw}")
            return raw, True
        _log(f"[IMO] {label}: explicit указан, но файл НЕ найден: {raw}")
        return "", True

    intro_path, intro_explicit = _resolve_explicit(explicit_intro, "intro", enable_intro)
    middle_path, middle_explicit = _resolve_explicit(explicit_middle, "middle", enable_middle)
    outro_path, outro_explicit = _resolve_explicit(explicit_outro, "outro", enable_outro)

    _log(
        f"[IMO] flags intro={enable_intro} middle={enable_middle} outro={enable_outro} | "
        f"folder={intro_outro_folder or '(пусто)'} | "
        f"explicit_set intro={intro_explicit} middle={middle_explicit} outro={outro_explicit}"
    )

    need_folder = (
        (enable_intro and not intro_path and not intro_explicit)
        or (enable_middle and not middle_path and not middle_explicit)
        or (enable_outro and not outro_path and not outro_explicit)
    )

    folder_files: list[str] = []
    if need_folder and intro_outro_folder and os.path.isdir(intro_outro_folder):
        try:
            folder_files = os.listdir(intro_outro_folder)
            _log(f"[IMO] файлов в папке (fallback): {len(folder_files)}")
        except OSError as e:
            _log(f"[ВИДЕО] Не удалось прочитать папку intro/middle/outro: {e}")
    elif need_folder:
        _log(
            f"[IMO] папка IMO отсутствует/пустая, fallback недоступен: "
            f"{intro_outro_folder or '(не задана)'}"
        )

    _media = (".mp4", ".mov", ".jpg", ".jpeg", ".png", ".bmp", ".webp")

    if enable_intro and not intro_path and not intro_explicit and folder_files:
        intro_candidates = [
            os.path.join(intro_outro_folder, f)
            for f in folder_files
            if f.lower().startswith("intro") and f.lower().endswith(_media)
        ]
        if intro_candidates:
            intro_path = intro_candidates[0]
            _log(f"[IMO] intro (fallback папка): {intro_path}")

    if enable_middle and not middle_path and not middle_explicit and folder_files:
        middle_candidates = [
            os.path.join(intro_outro_folder, f)
            for f in folder_files
            if f.lower().startswith("middle") and f.lower().endswith(_media)
        ]
        if middle_candidates:
            middle_path = middle_candidates[0]
            _log(f"[IMO] middle (fallback папка): {middle_path}")

    def _match_media(files: list[str], keywords: tuple[str, ...]) -> list[str]:
        out = []
        for f in files:
            low = f.lower()
            if not low.endswith(_media):
                continue
            stem = os.path.splitext(low)[0]
            if any(k in stem for k in keywords):
                out.append(os.path.join(intro_outro_folder, f))
        return out

    if enable_outro and not outro_path and not outro_explicit and folder_files:
        outro_candidates = _match_media(
            folder_files,
            ("outro", "outtro", "ending", "endcard", "end_card", "финал", "концовка"),
        )
        outro_candidates = [
            c for c in outro_candidates if "intro" not in os.path.basename(c).lower()
        ]
        if not outro_candidates:
            used = set()
            if intro_path:
                used.add(os.path.abspath(intro_path))
            if middle_path:
                used.add(os.path.abspath(middle_path))
            any_media = []
            for f in folder_files:
                low = f.lower()
                if not low.endswith(_media):
                    continue
                stem = os.path.splitext(low)[0]
                if any(k in stem for k in ("intro", "middle", "mid_")) and not intro_path:
                    continue
                full = os.path.abspath(os.path.join(intro_outro_folder, f))
                if full in used:
                    continue
                any_media.append(os.path.join(intro_outro_folder, f))
            outro_candidates = any_media
        if outro_candidates:
            outro_path = sorted(outro_candidates)[-1]
            _log(f"[IMO] outro (fallback папка): {outro_path}")
        else:
            _log(
                f"[IMO] enable_outro=True, explicit пуст, в папке нет media. "
                f"Файлы: {folder_files[:20]}"
            )
    elif enable_outro and not outro_path and outro_explicit:
        _log("[IMO] outro: explicit указан, файл отсутствует — поиск в папке ОТКЛЮЧЁН")

    def _ensure_video(path: str, label: str, duration: float = 1.0) -> str:
        if not path:
            return ""
        if not os.path.isfile(path):
            _log(f"[IMO] {label}: файл не существует: {path}")
            return ""
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif"):
            _log(f"[IMO] {label}: видеофайл → {path}")
            return path
        dur = max(0.5, float(duration or 1.0))
        out = os.path.join(work_dir, f"{label}_still_{uuid.uuid4().hex[:8]}.mp4")
        w, h = _even(main_w), _even(main_h)
        try:
            img_cmd = [
                ffmpeg, "-y", "-loop", "1", "-i", path, "-t", str(dur),
                "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1",
                "-c:v", "h264_videotoolbox", "-b:v", "28M", "-allow_sw", "0",
                "-pix_fmt", "yuv420p", "-r", "30", out,
            ]
            run_vt_encode(img_cmd, [path], out, log_fn=_log, stage_name="IMO")
        except Exception as e:
            _log(f"[IMO] картинка→{dur}с fail {path}: {e}")
            return ""
        _log(f"[IMO] {label}: картинка → {dur}с видео: {out}")
        return out

    intro_path = _ensure_video(intro_path, "intro", intro_duration) if intro_path else ""
    middle_path = _ensure_video(middle_path, "middle", middle_duration) if middle_path else ""
    outro_path = _ensure_video(outro_path, "outro", outro_duration) if outro_path else ""

    _log(
        f"[IMO] итог путей: intro={intro_path or '—'} | "
        f"middle={middle_path or '—'} | outro={outro_path or '—'}"
    )

    if not (intro_path or middle_path or outro_path):
        _log("[ВИДЕО] Файлы интро/мидл/аутро не найдены, пропускаем")
        return video_path


    # ============================================================
    # r15 FAST IMO: intro/outro (and middle) as short VT segs matching master
    # profile; main body via stream-copy; concat demuxer (-c copy);
    # then master audio. On any failure → full REPLACE fallback + reason=.
    # Duration target = main_dur; |drift| ≤ 0.15 preferred.
    # ============================================================

    intro_real_dur = 0.0
    if intro_path:
        try:
            intro_real_dur = float(probe_duration(intro_path))
        except Exception as e:
            _log(f"[IMO] intro probe fail: {e}, fallback={intro_duration}")
            intro_real_dur = float(intro_duration or 3.0)
        if intro_real_dur <= 0.05:
            _log("[IMO] intro duration invalid — disable")
            intro_path, intro_real_dur = "", 0.0
        else:
            _log(f"[IMO] intro REPLACE first {intro_real_dur:.2f}s of main")

    outro_real_dur = 0.0
    if outro_path:
        try:
            outro_real_dur = float(probe_duration(outro_path))
        except Exception as e:
            _log(f"[IMO] outro probe fail: {e}, fallback={outro_duration}")
            outro_real_dur = float(outro_duration or 3.0)
        if outro_real_dur <= 0.05:
            _log("[IMO] outro duration invalid — disable")
            outro_path, outro_real_dur = "", 0.0
        else:
            _log(f"[IMO] outro REPLACE last {outro_real_dur:.2f}s of main")

    middle_real_dur = 0.0
    if middle_path:
        try:
            middle_real_dur = float(probe_duration(middle_path))
        except Exception as e:
            _log(f"[IMO] middle probe fail: {e}, fallback={middle_duration}")
            middle_real_dur = float(middle_duration or 1.0)
        if middle_real_dur <= 0.05:
            _log("[IMO] middle duration invalid — disable")
            middle_path, middle_real_dur = "", 0.0
        else:
            lo = intro_real_dur
            hi = main_dur - outro_real_dur - middle_real_dur
            if hi < lo:
                _log("[IMO] middle не влезает между intro/outro — disable")
                middle_path, middle_real_dur = "", 0.0
            else:
                if mid_point < lo:
                    mid_point = lo
                if mid_point > hi:
                    mid_point = hi
                _log(
                    f"[IMO] middle REPLACE at {mid_point:.2f}s "
                    f"dur={middle_real_dur:.2f}s (Gemini/clamp)"
                )

    used = intro_real_dur + outro_real_dur + middle_real_dur
    if used >= main_dur - 0.3:
        _log(f"[IMO] WARN: overlays {used:.1f}s >= main {main_dur:.1f}s — clamp outro")
        outro_real_dur = max(0.5, main_dur - intro_real_dur - middle_real_dur - 0.5)
        if outro_real_dur <= 0.05:
            outro_path, outro_real_dur = "", 0.0

    if not (intro_path or middle_path or outro_path):
        _log("[IMO] нечего накладывать — пропуск")
        return video_path

    import time as _time
    _t0 = _time.monotonic()
    output_path = os.path.join(output_dir, f"with_intro_outro_{uuid.uuid4().hex[:8]}.mp4")
    scale_filter = (
        f"scale={main_w}:{main_h}:force_original_aspect_ratio=decrease,"
        f"pad={main_w}:{main_h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )

    def _probe_profile(path: str) -> dict:
        """width, height, fps, fps_str, pix_fmt for matching segments."""
        import json
        meta = {"width": main_w, "height": main_h, "fps": 30.0, "fps_str": "30", "pix_fmt": "yuv420p"}
        try:
            r = subprocess.run(
                [
                    "ffprobe", "-v", "quiet", "-select_streams", "v:0",
                    "-show_entries", "stream=width,height,r_frame_rate,pix_fmt",
                    "-of", "json", path,
                ],
                capture_output=True, text=True, timeout=30,
            )
            data = json.loads(r.stdout or "{}")
            st = (data.get("streams") or [{}])[0]
            meta["width"] = int(st.get("width") or main_w)
            meta["height"] = int(st.get("height") or main_h)
            meta["pix_fmt"] = (st.get("pix_fmt") or "yuv420p").strip() or "yuv420p"
            fps_str = st.get("r_frame_rate") or "30/1"
            meta["fps_str"] = str(fps_str)
            if "/" in str(fps_str):
                num, den = map(int, str(fps_str).split("/"))
                meta["fps"] = num / den if den else 30.0
            else:
                meta["fps"] = float(fps_str)
        except Exception:
            pass
        return meta

    master_prof = _probe_profile(video_path)
    mw, mh = _even(master_prof["width"]), _even(master_prof["height"])
    target_fps = master_prof["fps"] if master_prof["fps"] > 1 else 30.0
    target_pix = master_prof.get("pix_fmt") or "yuv420p"
    if target_pix not in ("yuv420p", "yuv422p", "yuv444p", "nv12"):
        target_pix = "yuv420p"

    def _imo_cache_key(src: str, label: str, dur: float) -> str:
        """Stable key: path + mtime + size + dur + profile + codec/bitrate."""
        import hashlib
        try:
            st = os.stat(src)
            mtime_ns = st.st_mtime_ns
            size = st.st_size
        except OSError:
            mtime_ns, size = 0, 0
        raw = (
            f"{os.path.abspath(src)}|{mtime_ns}|{size}|"
            f"{label}|{dur:.4f}|{mw}x{mh}|fps={target_fps:.6f}|"
            f"pix={target_pix}|codec=h264_videotoolbox|br=16M|an=1"
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def _make_seg(src: str, label: str, dur: float) -> str:
        """Encode short overlay to exact master profile, video-only (-an).

        intro/outro: persistent cache under ~/video_maker/cache/imo/
        middle: no cache (depends on placement in specific video).
        """
        use_cache = label in ("intro", "outro")
        cache_path = ""
        if use_cache:
            cache_dir = os.path.join(
                os.path.expanduser("~"), "video_maker", "cache", "imo"
            )
            try:
                os.makedirs(cache_dir, exist_ok=True)
            except OSError:
                pass
            cache_path = os.path.join(
                cache_dir, f"{label}_{_imo_cache_key(src, label, dur)}.mp4"
            )
            if cache_path and os.path.isfile(cache_path):
                # Size alone is not proof of a valid MP4: a previous crash may have
                # left a large but truncated cache entry. Validate once before use.
                if os.path.getsize(cache_path) > 1000 and verify_mp4(cache_path, log_fn=_log):
                    out = os.path.join(
                        work_dir, f"imo_{label}_{uuid.uuid4().hex[:8]}.mp4"
                    )
                    try:
                        import shutil
                        shutil.copy2(cache_path, out)
                        if os.path.isfile(out) and os.path.getsize(out) > 1000:
                            _log(f"[IMO_{label.upper()}] cache HIT → {cache_path}")
                            return out
                    except OSError as e:
                        _log(f"[IMO_{label.upper()}] cache HIT copy fail: {e}")
                else:
                    _log(f"[IMO_{label.upper()}] invalid cache entry; regenerate")
                    try:
                        os.remove(cache_path)
                    except OSError:
                        pass

        out = os.path.join(work_dir, f"imo_{label}_{uuid.uuid4().hex[:8]}.mp4")
        vf = (
            f"scale={mw}:{mh}:force_original_aspect_ratio=decrease,"
            f"pad={mw}:{mh}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={target_fps:.6f}"
        )
        if use_cache:
            _log(f"[IMO_{label.upper()}] cache MISS → encode")
        cmd = [
            ffmpeg, "-y", "-i", src, "-t", f"{dur:.4f}",
            "-vf", vf,
            "-an",
            "-c:v", "h264_videotoolbox", "-b:v", "16M", "-allow_sw", "0",
            "-pix_fmt", target_pix,
            "-r", f"{target_fps:.6f}",
            out,
        ]
        run_vt_encode(cmd, [src], out, log_fn=_log, stage_name=f"IMO_{label.upper()}")
        if (
            use_cache
            and cache_path
            and os.path.isfile(out)
            and os.path.getsize(out) > 1000
            and verify_mp4(out, log_fn=_log)
        ):
            cache_tmp = os.path.join(
                os.path.dirname(cache_path),
                f".{os.path.basename(cache_path)}.{os.getpid()}.{uuid.uuid4().hex}.tmp.mp4",
            )
            try:
                import shutil
                shutil.copy2(out, cache_tmp)
                if verify_mp4(cache_tmp, log_fn=_log):
                    os.replace(cache_tmp, cache_path)
                    _log(f"[IMO_{label.upper()}] cache STORE → {cache_path}")
                else:
                    _log(f"[IMO_{label.upper()}] cache temp verification failed; not stored")
            except OSError as e:
                _log(f"[IMO_{label.upper()}] cache STORE fail: {e}")
            finally:
                try:
                    if os.path.exists(cache_tmp):
                        os.remove(cache_tmp)
                except OSError:
                    pass
        return out

    def _copy_trim(src: str, t0: float, t1: float, label: str) -> str:
        """Prefer stream-copy trim of master body (video only)."""
        if t1 - t0 <= 0.04:
            return ""
        out = os.path.join(work_dir, f"imo_{label}_{uuid.uuid4().hex[:8]}.mp4")
        dur = t1 - t0
        # map only video — critical for concat demuxer with -an segs
        cmd = [
            ffmpeg, "-y",
            "-ss", f"{t0:.4f}", "-i", src,
            "-t", f"{dur:.4f}",
            "-map", "0:v:0",
            "-c:v", "copy",
            "-avoid_negative_ts", "make_zero",
            out,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if res.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 1000:
            _log(f"[IMO] mid_copy {label} ok t0={t0:.3f} dur={dur:.3f}")
            return out
        # fallback re-encode body (rare)
        _log(f"[IMO] mid_copy {label} failed → VT body")
        cmd = [
            ffmpeg, "-y",
            "-ss", f"{t0:.4f}", "-i", src,
            "-t", f"{dur:.4f}",
            "-vf", f"scale={mw}:{mh},setsar=1,fps={target_fps:.6f}",
            "-an",
            "-c:v", "h264_videotoolbox", "-b:v", "16M", "-allow_sw", "0",
            "-pix_fmt", target_pix,
            out,
        ]
        run_vt_encode(cmd, [src], out, log_fn=_log, stage_name=f"IMO_BODY_{label}")
        return out

    def _concat_demuxer(seg_paths: list[str], out_path: str) -> None:
        list_file = os.path.join(work_dir, f"imo_concat_{uuid.uuid4().hex[:8]}.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for p in seg_paths:
                # absolute + escape single quotes for concat demuxer
                ap = os.path.abspath(p).replace("'", "'\\''")
                f.write(f"file '{ap}'\n")
        cmd = [
            ffmpeg, "-y", "-f", "concat", "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            out_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        try:
            os.remove(list_file)
        except OSError:
            pass
        if res.returncode != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) < 1000:
            raise RuntimeError(
                f"concat demuxer failed rc={res.returncode} "
                f"stderr={(res.stderr or '')[:400]}"
            )

    def _try_fast() -> str:
        """Build FAST path. Raises on any problem → caller falls back."""
        segs: list[str] = []
        # intro
        if intro_path and intro_real_dur > 0.05:
            segs.append(_make_seg(intro_path, "intro", intro_real_dur))
        # body / middle
        t = intro_real_dur if intro_path else 0.0
        if middle_path and middle_real_dur > 0.05:
            # main before middle
            if mid_point - t > 0.04:
                s = _copy_trim(video_path, t, mid_point, "pre_mid")
                if s:
                    segs.append(s)
            segs.append(_make_seg(middle_path, "middle", middle_real_dur))
            t = mid_point + middle_real_dur
        end_main = main_dur - (outro_real_dur if outro_path else 0.0)
        if end_main - t > 0.04:
            s = _copy_trim(video_path, t, end_main, "body")
            if s:
                segs.append(s)
        # outro
        if outro_path and outro_real_dur > 0.05:
            segs.append(_make_seg(outro_path, "outro", outro_real_dur))

        if not segs:
            raise RuntimeError("no segments for FAST")

        tmp_v = os.path.join(work_dir, f"imo_fast_v_{uuid.uuid4().hex[:8]}.mp4")
        _concat_demuxer(segs, tmp_v)

        # duration check / light align
        try:
            actual = float(probe_duration(tmp_v))
        except Exception:
            actual = main_dur
        drift = actual - main_dur
        _log(f"[IMO] mid_copy target={main_dur:.3f} actual={actual:.3f} drift={drift:.3f}")
        if abs(drift) > 0.15:
            # minimal re-trim to target if slightly long
            if drift > 0.15 and actual > 0.5:
                trimmed = tmp_v + ".trim.mp4"
                cmd = [
                    ffmpeg, "-y", "-i", tmp_v, "-t", f"{main_dur:.4f}",
                    "-c", "copy", trimmed,
                ]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                if res.returncode == 0 and os.path.isfile(trimmed):
                    try:
                        os.remove(tmp_v)
                    except OSError:
                        pass
                    tmp_v = trimmed
                    actual = float(probe_duration(tmp_v))
                    drift = actual - main_dur
                    _log(f"[IMO] mid_copy after trim actual={actual:.3f} drift={drift:.3f}")

        # attach master audio only + explicit -t main_dur (no pad/silence)
        cmd_a = [
            ffmpeg, "-y",
            "-i", tmp_v,
            "-i", video_path,
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-t", f"{main_dur:.4f}",
            output_path,
        ]
        res_a = subprocess.run(cmd_a, capture_output=True, text=True, timeout=120)
        if res_a.returncode != 0 or not os.path.isfile(output_path) or os.path.getsize(output_path) < 1000:
            # soft fallback to existing helper
            from .audio import replace_audio
            try:
                replace_audio(tmp_v, video_path, output_path, log_fn=_log)
            except Exception as ae:
                raise RuntimeError(f"audio attach failed: {ae}") from ae
        if not os.path.isfile(output_path) or os.path.getsize(output_path) < 1000:
            raise RuntimeError("FAST output missing or too small after audio attach")
        try:
            os.remove(tmp_v)
        except OSError:
            pass
        for s in segs:
            try:
                os.remove(s)
            except OSError:
                pass
        return output_path

    def _fallback_full(reason: str) -> str:
        _log(f"[IMO] path=FALLBACK reason={reason}")
        inputs = [video_path]
        input_idx = {"main": 0}
        if intro_path:
            inputs.append(intro_path)
            input_idx["intro"] = len(inputs) - 1
        if middle_path:
            inputs.append(middle_path)
            input_idx["middle"] = len(inputs) - 1
        if outro_path:
            inputs.append(outro_path)
            input_idx["outro"] = len(inputs) - 1

        filter_parts = []
        concat_labels = []
        vcount = 0

        def add_overlay(key: str):
            nonlocal vcount
            i = input_idx[key]
            lab = f"v{vcount}"
            vcount += 1
            filter_parts.append(f"[{i}:v]{scale_filter}[{lab}];")
            concat_labels.append(f"[{lab}]")

        def add_main_trim(t0: float, t1: float):
            nonlocal vcount
            if t1 - t0 <= 0.04:
                return
            lab = f"v{vcount}"
            vcount += 1
            filter_parts.append(
                f"[0:v]trim={t0:.4f}:{t1:.4f},setpts=PTS-STARTPTS[{lab}];"
            )
            concat_labels.append(f"[{lab}]")

        t = 0.0
        outro_d = outro_real_dur if outro_path else 0.0
        if intro_path:
            add_overlay("intro")
            t = intro_real_dur
        if middle_path:
            add_main_trim(t, mid_point)
            add_overlay("middle")
            t = mid_point + middle_real_dur
            end_main = main_dur - outro_d
            add_main_trim(t, end_main)
        else:
            end_main = main_dur - outro_d
            add_main_trim(t, end_main)

        if outro_path:
            add_overlay("outro")

        if not concat_labels:
            _log("[IMO] пустой concat — пропуск")
            return video_path

        filter_parts.append(
            f"{''.join(concat_labels)}concat=n={len(concat_labels)}:v=1:a=0[outv]"
        )
        filter_complex = "".join(filter_parts)

        cmd = [
            ffmpeg, "-y",
            *[arg for inp in inputs for arg in ("-i", inp)],
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-c:v", "h264_videotoolbox", "-b:v", "28M", "-allow_sw", "0",
            "-pix_fmt", target_pix if target_pix else "yuv420p",
            "-r", f"{target_fps:.6f}",
            output_path,
        ]
        run_vt_encode(cmd, inputs, output_path, log_fn=_log, stage_name="INTRO")

        from .audio import replace_audio
        tmp_out = output_path + ".tmp.mp4"
        os.rename(output_path, tmp_out)
        replace_audio(tmp_out, video_path, output_path, log_fn=_log)
        try:
            os.remove(tmp_out)
        except OSError:
            pass
        return output_path

    # Prefer FAST; on any error → FALLBACK with reason
    try:
        result = _try_fast()
        wall = _time.monotonic() - _t0
        try:
            final_dur = float(probe_duration(result))
        except Exception:
            final_dur = main_dur
        _log("[IMO] path=FAST")
        _log(
            f"[IMO] done | audio=master | dur={final_dur:.3f} | wall={wall:.1f}s"
        )
        return result
    except Exception as e:
        reason = str(e)[:200]
        _log(f"[IMO] FAST failed: {reason}")
        result = _fallback_full(reason)
        wall = _time.monotonic() - _t0
        try:
            final_dur = float(probe_duration(result))
        except Exception:
            final_dur = main_dur
        _log(
            f"[IMO] done | audio=master | dur={final_dur:.3f} | wall={wall:.1f}s"
        )
        return result


from .audio import probe_duration
