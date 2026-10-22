"""B4: реальная обработка fallback-сцен в production-рендере.

Две задачи:

1. ``treated``-сцены (associative / proxy) получают лёгкую обработку:
   blur + нейтральный грейд. Параметры выбираются по ``fallback`` из
   reason/предупреждений сцены: ``treated`` — sigma 5, ``proxy_forced`` /
   ``literal_proxy`` — sigma 9.
2. ``graphic_fallback``-сцены не должны занимать клип чужой сцены. Planner
   ставит в план маркер ``vm-graphic://scene/N``, а этот модуль материализует
   его в реальный файл: графическая карточка (``tools/graphic_cards.py``)
   либо процедурный нейтральный фон через lavfi (без текста).

Модуль не импортирует planner/render_graph, чтобы не создавать циклов.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

GRAPHIC_PATH_PREFIX = "vm-graphic://"

# ``_apply_fallback_flags`` пишет флаги в reason (он переживает apply_rhythm,
# который клонирует Scene и теряет warnings). Дублируем разбор по warnings —
# так модуль работает и с планами, собранными до/без planner-а.
_RE_TREATED = re.compile(r"\btreated(?:=1)?\b")
_RE_GRAPHIC = re.compile(r"\bgraphic_fallback(?:=1)?\b")
_RE_FALLBACK = re.compile(r"(?:^|[;\s])fallback=([a-zA-Z_]+)")

# fallback-значения, для которых политика разрешает «грязный» клип только
# с сильной обработкой (proxy): blur 8–12 px.
_PROXY_FALLBACKS = frozenset({
    "proxy_forced",
    "literal_proxy",
    "repeat_forced",
    "proxy",
})

_BLUR_LIGHT = 5.0
_BLUR_PROXY = 9.0
_GRADE = "eq=contrast=1.05:saturation=0.95"


@dataclass(frozen=True)
class SceneFlags:
    treated: bool = False
    graphic_fallback: bool = False
    fallback: str = "none"


@dataclass(frozen=True)
class Treatment:
    kind: str          # "light" | "proxy"
    sigma: float
    eq: str = _GRADE


def scene_text(scene: Any) -> str:
    parts = [
        str(getattr(scene, "reason", "") or ""),
        " ".join(str(w) for w in (getattr(scene, "warnings", None) or [])),
    ]
    return " ".join(parts)


def scene_flags(scene: Any) -> SceneFlags:
    """Флаги сцены из reason/warnings + маркерного source_path."""
    text = scene_text(scene)
    m = _RE_FALLBACK.search(text)
    fallback = (m.group(1) if m else "none") or "none"
    graphic = bool(_RE_GRAPHIC.search(text)) or is_graphic_path(
        str(getattr(scene, "source_path", "") or "")
    )
    return SceneFlags(
        treated=bool(_RE_TREATED.search(text)),
        graphic_fallback=graphic,
        fallback=fallback,
    )


def treatment_for_scene(scene: Any) -> Treatment | None:
    """None — обычная сцена; иначе параметры blur/грейда."""
    flags = scene_flags(scene)
    if not flags.treated or flags.graphic_fallback:
        return None
    if flags.fallback in _PROXY_FALLBACKS:
        return Treatment(kind="proxy", sigma=_BLUR_PROXY)
    return Treatment(kind="light", sigma=_BLUR_LIGHT)


def treatment_filter(scene: Any) -> str:
    """Фрагмент ffmpeg-фильтра (без ведущей запятой) или пусто."""
    t = treatment_for_scene(scene)
    if t is None:
        return ""
    # gblur — Gaussian, стабильнее boxblur по краям кадра; steps=2 убирает
    # полосы на 4K при больших sigma.
    steps = 2 if t.kind == "proxy" else 1
    return f"gblur=sigma={t.sigma:.2f}:steps={steps},{t.eq}"


def inject_scene_treatments(filter_complex: str, scenes: Sequence[Any]) -> str:
    """Вставить обработку в готовый filter_complex по индексам входов.

    render_graph собирает каждый вход как ``[i:v]...trim...[vi]``. Находим
    сегмент нужного входа и дописываем blur/грейд перед его выходным [vi].
    Идемпотентно: повторный вызов не дублирует gblur.
    """
    if not filter_complex or not scenes:
        return filter_complex
    segments = filter_complex.split(";")
    for i, scene in enumerate(scenes):
        frag = treatment_filter(scene)
        if not frag:
            continue
        src_tag = f"[{i}:v]"
        out_tag = f"[v{i}]"
        for j, seg in enumerate(segments):
            stripped = seg.strip()
            if src_tag not in stripped or not stripped.endswith(out_tag):
                continue
            if "gblur=sigma" in stripped:
                break
            body = stripped[: -len(out_tag)]
            segments[j] = f"{body},{frag}{out_tag}"
            break
    return ";".join(segments)


def is_graphic_path(path: str | os.PathLike[str] | None) -> bool:
    return str(path or "").startswith(GRAPHIC_PATH_PREFIX)


def graphic_marker_path(scene_index: int) -> str:
    return f"{GRAPHIC_PATH_PREFIX}scene/{max(1, int(scene_index))}"


# ---------------------------------------------------------------------------
# Материализация графического фона
# ---------------------------------------------------------------------------

def _ffprobe() -> str:
    return shutil.which("ffprobe") or "ffprobe"


def _ffmpeg() -> str:
    # Тот же бинарник, что у production-энкодера (imageio-ffmpeg), чтобы
    # поведение lavfi-фильтров не зависело от Homebrew-сборки.
    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and os.path.exists(path):
            return str(path)
    except Exception:
        pass
    return shutil.which("ffmpeg") or "ffmpeg"


def probe_media(path: str | os.PathLike[str]) -> tuple[int, int, float]:
    """(width, height, duration); (0, 0, 0.0) при ошибке."""
    try:
        proc = subprocess.run(
            [
                _ffprobe(), "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,duration",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            return 0, 0, 0.0
        vals = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
        # ffprobe с двумя -show_entries печатает width, height, duration потока,
        # затем duration формата.
        width = int(float(vals[0])) if len(vals) > 0 else 0
        height = int(float(vals[1])) if len(vals) > 1 else 0
        dur_stream = float(vals[2]) if len(vals) > 2 else 0.0
        dur_fmt = float(vals[3]) if len(vals) > 3 else 0.0
        return width, height, max(dur_stream, dur_fmt)
    except Exception:
        return 0, 0, 0.0


def _valid_media(path: Path, need: float, width: int, height: int) -> bool:
    if not path.is_file():
        return False
    w, h, d = probe_media(path)
    return w == int(width) and h == int(height) and d + 1e-6 >= float(need)


def _card_text(raw: Any) -> str:
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    text = re.sub(r"^[\-–—•·\s]+", "", text)
    if len(text) > 90:
        cut = text[:90].rsplit(" ", 1)[0].rstrip(" ,.;:—-")
        text = (cut or text[:90]).rstrip() + "…"
    return text


def _cache_key(*parts: Any) -> str:
    h = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return h[:20]


def _log(log_fn, msg: str) -> None:
    try:
        if log_fn is not None:
            log_fn(msg)
        else:
            import logging
            logging.getLogger(__name__).info("%s", msg)
    except Exception:
        pass


def _render_procedural(
    out_path: Path,
    duration: float,
    width: int,
    height: int,
    fps: int,
) -> str:
    """Тёмный градиент + шум + виньетка. Без текста. 4K/9:16 по size."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{out_path.stem}.", suffix=".mp4", dir=str(out_path.parent)
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.unlink(missing_ok=True)
        src = (
            f"gradients=s={width}x{height}:c0=0x141A22:c1=0x05070A:"
            f"x0=0:y0=0:x1={width}:y1={height}:r={fps}:d={duration:.3f}"
        )
        vf = "noise=alls=5:allf=t+u,vignette=PI/5,format=yuv420p"
        cmd = [
            _ffmpeg(), "-hide_banner", "-y", "-loglevel", "error", "-nostdin",
            "-f", "lavfi", "-i", src,
            "-vf", vf,
            "-t", f"{duration:.3f}",
            "-r", str(int(fps)),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an",
            str(tmp),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if proc.returncode != 0 or not tmp.is_file():
            raise RuntimeError((proc.stderr or "ffmpeg failed").strip()[-400:])
        os.replace(tmp, out_path)
        return str(out_path)
    finally:
        tmp.unlink(missing_ok=True)


def _render_plain_color(
    out_path: Path,
    duration: float,
    width: int,
    height: int,
    fps: int,
) -> str:
    """Последний резерв: ровный тёмный кадр, если gradients/noise недоступны."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{out_path.stem}.", suffix=".mp4", dir=str(out_path.parent)
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.unlink(missing_ok=True)
        src = f"color=c=0x0B0E13:s={width}x{height}:r={fps}:d={duration:.3f}"
        cmd = [
            _ffmpeg(), "-hide_banner", "-y", "-loglevel", "error", "-nostdin",
            "-f", "lavfi", "-i", src,
            "-vf", "vignette=PI/6,format=yuv420p",
            "-t", f"{duration:.3f}", "-r", str(int(fps)),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an",
            str(tmp),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0 or not tmp.is_file():
            raise RuntimeError((proc.stderr or "ffmpeg failed").strip()[-400:])
        os.replace(tmp, out_path)
        return str(out_path)
    finally:
        tmp.unlink(missing_ok=True)


def _load_make_card():
    """Импорт модуля карточек; None, если его ещё нет/он сломан."""
    try:
        from tools.graphic_cards import make_card
        return make_card
    except Exception:
        root = Path(__file__).resolve().parents[2]
        if (root / "tools" / "graphic_cards.py").is_file():
            try:
                if str(root) not in sys.path:
                    sys.path.insert(0, str(root))
                from tools.graphic_cards import make_card as _mk
                return _mk
            except Exception:
                return None
        return None


def _ensure_graphic_media(
    scene: Any,
    *,
    scene_index: int,
    cache_dir: str | os.PathLike[str],
    width: int,
    height: int,
    fps: int,
    log_fn=None,
) -> str:
    planned = max(0.05, float(getattr(scene, "source_out", 0.0))
                  - float(getattr(scene, "source_in", 0.0)))
    width, height = int(width), int(height)
    # Небольшой запас: финальная кадровая обрезка не должна упереться в EOF.
    need = planned + 0.10
    text = _card_text(getattr(scene, "transcript_ref", ""))
    key = _cache_key(
        getattr(scene, "scene_id", ""), round(planned, 3), width, height,
        text[:90], "v1",
    )
    base = Path(cache_dir).expanduser()
    base.mkdir(parents=True, exist_ok=True)

    # 1) Кэш: карточка или фон, уже собранный для этой сцены/длительности.
    for name in (f"card_{key}.mp4", f"bg_{key}.mp4"):
        cached = base / name
        if _valid_media(cached, planned, width, height):
            return str(cached)

    # 2) Графическая карточка (модуль другого агента; может отсутствовать).
    if text and need <= 12.0:
        try:
            make_card = _load_make_card()
            if make_card is None:
                raise ImportError("tools.graphic_cards.missing")
            card = base / f"card_{key}.mp4"
            make_card(
                text,
                min(12.0, max(0.5, need + 0.10)),
                card,
                width=width,
                height=height,
                fps=int(fps),
            )
            if _valid_media(card, planned, width, height):
                _log(log_fn, f"[РЕНДЕР] graphic-сцена {scene_index}: "
                            f"карточка make_card")
                return str(card)
            _log(log_fn, f"[РЕНДЕР] graphic-сцена {scene_index}: карточка "
                        f"короче сцены — процедурный фон")
        except Exception as e:
            _log(log_fn, f"[РЕНДЕР] graphic-сцена {scene_index}: make_card "
                        f"недоступен ({str(e)[:100]}) — процедурный фон")

    # 3) Процедурный нейтральный фон без текста (4K/9:16, длительность сцены).
    duration = need + 0.30
    bg = base / f"bg_{key}.mp4"
    try:
        _render_procedural(bg, duration, width, height, fps)
    except Exception as e:
        _log(log_fn, f"[РЕНДЕР] graphic-сцена {scene_index}: gradients "
                    f"не удались ({str(e)[:100]}) — ровный фон")
        _render_plain_color(bg, duration, width, height, fps)
    if not _valid_media(bg, planned, width, height):
        raise RuntimeError("процедурный фон не прошёл ffprobe")
    _log(log_fn, f"[РЕНДЕР] graphic-сцена {scene_index}: нейтральный фон")
    return str(bg)


def materialize_graphic_scenes(
    plan: Any,
    *,
    cache_dir: str | os.PathLike[str],
    width: int,
    height: int,
    fps: int = 30,
    log_fn=None,
) -> int:
    """Заменить маркеры graphic-сцен реальными файлами. Возвращает число сцен."""
    scenes = list(getattr(plan, "scenes", None) or [])
    if not scenes:
        return 0
    done = 0
    for i, scene in enumerate(scenes):
        try:
            flags = scene_flags(scene)
            if not flags.graphic_fallback:
                continue
            # graphic-сцена всегда должна получить свой фон/карточку: если в
            # старом плане ей достался чужой клип, он будет заменён.
            planned = max(
                0.05,
                float(getattr(scene, "source_out", 0.0))
                - float(getattr(scene, "source_in", 0.0)),
            )
            path = _ensure_graphic_media(
                scene,
                scene_index=i + 1,
                cache_dir=cache_dir,
                width=width,
                height=height,
                fps=fps,
                log_fn=log_fn,
            )
            scene.source_path = path
            scene.source_in = 0.0
            # Длительность плана не меняем: сгенерировано с запасом.
            scene.source_out = planned
            try:
                from .edit_plan import CropRect
                scene.crop = CropRect()
            except Exception:
                pass
            if "graphic_fallback" not in (getattr(scene, "warnings", None) or []):
                try:
                    scene.warnings.append("graphic_fallback")
                except Exception:
                    pass
            done += 1
        except Exception as e:
            _log(log_fn, f"[РЕНДЕР] graphic-сцена {i + 1}: фон не создан "
                        f"({str(e)[:140]})")
    return done


__all__ = [
    "GRAPHIC_PATH_PREFIX",
    "SceneFlags",
    "Treatment",
    "graphic_marker_path",
    "inject_scene_treatments",
    "is_graphic_path",
    "materialize_graphic_scenes",
    "probe_media",
    "scene_flags",
    "treatment_filter",
    "treatment_for_scene",
]
