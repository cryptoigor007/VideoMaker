# AISIE Phase 4.4 — Safe Zone Visual Check
# Проверка безопасных зон: зоны-гиды, препятствия, конфликты,
# математическая проверка пересечений текста и рендеринг видео-примеров.

from __future__ import annotations

from pathlib import Path

from moviepy import ColorClip, CompositeVideoClip
from PIL import Image, ImageDraw, ImageFont

from advanced_text_features import AdvancedTextEngine
from .placement import PROFILES, Obstacles, PlacementEngine
from .timing import TimingEngine
from text_style_utils import HOOK_TYPES, VISUAL_WEIGHTS

OUT = Path(__file__).resolve().parent.parent / "asie_safezone_tests"
OUT.mkdir(exist_ok=True)

VW, VH = 1080, 1920
DUR = 4.5
FPS = 24

FONT = None  # инициализируем шрифт при первом вызове


def _resolve_font(size: int = 75) -> ImageFont.FreeTypeFont:
    """Загружает шрифт Montserrat ExtraBold один раз."""
    global FONT
    if FONT is None:
        from text_overlay_engine import _resolve_font_path
        FONT = _resolve_font_path(None, size, "montserrat extrabold")
    return FONT


# ------------------------------------------------------------
ZONE_COLORS = {
    "primary": (0, 200, 0, 30),
    "secondary": (0, 150, 200, 30),
    "bottom": (200, 100, 0, 30),
    "top": (200, 200, 200, 20),
    "text_box": (0, 255, 0, 80),
    "obstacle": (255, 0, 0, 80),
}

PROFILE = PROFILES["youtube_shorts"]


def _zone_frame(vw: int, vh: int) -> Image.Image:
    """Прозрачный слой с зонами-гидами (PRIMARY/SECONDARY/bottom_safe)."""
    img = Image.new("RGBA", (vw, vh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    y_top = int(vh * PROFILE.top_safe)
    draw.line([(0, y_top), (vw, y_top)], fill=ZONE_COLORS["top"][:3], width=2)

    y1, y2 = int(vh * 0.10), int(vh * 0.25)
    draw.rectangle([0, y1, vw, y2], fill=ZONE_COLORS["primary"], outline=(0, 255, 0, 200), width=2)

    y1, y2 = int(vh * 0.30), int(vh * 0.45)
    draw.rectangle([0, y1, vw, y2], fill=ZONE_COLORS["secondary"], outline=(0, 150, 255, 200), width=2)

    y1 = int(vh * (1 - PROFILE.bottom_safe))
    draw.rectangle([0, y1, vw, VH], fill=ZONE_COLORS["bottom"], outline=(255, 150, 0, 200), width=2)

    font_small = ImageFont.load_default()
    draw.text((10, int(vh * 0.10) + 2), "PRIMARY (10-25%)", fill=(0, 255, 0, 255), font=font_small)
    draw.text((10, int(vh * 0.30) + 2), "SECONDARY (30-45%)", fill=(0, 150, 255, 255), font=font_small)
    draw.text((10, VH - 60), f"bottom_safe ({int((1 - PROFILE.bottom_safe) * 100)}%)", fill=(255, 150, 0, 255), font=font_small)

    return img


def _text_box_rect(vw: int, vh: int, y_frac: float, font_frac: float, width_frac: float = 0.86) -> tuple:
    """Прямоугольник текста (x0, y0, x1, y1) в пикселях."""
    h = vh * 0.085 * 1.35
    w = min(vw * 0.86, vw - 40)
    x = (vw - w) / 2
    y = y_frac * vh
    return (x, y, x + w, y + h)


def _check_overlap(box1: tuple, box2: tuple) -> bool:
    b1 = (float(box1[0]), float(box1[1]), float(box1[2]), float(box1[3]))
    b2 = (float(box2[0]), float(box2[1]), float(box2[2]), float(box2[3]))
    return not (b1[2] <= b2[0] or b2[2] <= b1[0] or b1[3] <= b2[1] or b2[3] <= b1[1])


def _check_conflicts(text_box: tuple, obstacles: list[tuple]) -> list[int]:
    return [i for i, obst in enumerate(obstacles) if _check_overlap(text_box, obst)]


# ------------------------------------------------------------
def _build_scenario(
    name: str,
    obstacles: list[tuple],
    hook_type: str = "CONTRADICTION",
    text: str = "СВОБОДЫ СТАЛО БОЛЬШЕ. НО МЫ НЕ СТАЛИ СЧАСТЛИВЕЕ.",
) -> dict:
    """Строит сценарий: Placement → проверка конфликтов → отчёт + видео.
    VISUAL_WEIGHTS и HOOK_TYPES импортированы на уровне модуля (строка 21),
    локальные import внутри функции запрещены — иначе Python считает их
    локальными переменными для всей функции (UnboundLocalError)."""
    engine = AdvancedTextEngine()
    placer = PlacementEngine(PROFILES["youtube_shorts"])

    # Placement decision
    dec = placer.choose(
        (VW, VH),
        Obstacles(obstacles),
        current_visual_weight="L4",
        font_frac_by_weight={"L0": .050, "L1": .060, "L2": .075,
                             "L3": .085, "L4": .100},
    )

    # Таймлайн хука — проверяем Keyword Delay
    groups = TimingEngine().build_timeline(text, 0.30, hook_type=hook_type)

    # Получаем текстовое поле и проверяем пересечения
    # font_frac берем из VISUAL_WEIGHTS (уже импортировано на уровне модуля, строка 21)
    override_w = dec.visual_weight_override or "L4"
    font_frac = VISUAL_WEIGHTS[override_w]["font_scale"]
    text_box = _text_box_rect(VW, VH, dec.y_percent / 100.0, font_frac)
    conflicts = _check_conflicts(text_box, obstacles) if obstacles else []

    report = {
        "scenario": name,
        "hook_type": hook_type,
        "decision_zone": dec.zone,
        "y_percent": dec.y_percent,
        "text_box_y_frac": (text_box[1] / VH, text_box[3] / VH),
        "text_box_x_frac": (text_box[0] / VW, text_box[2] / VW),
        "conflicts_with_obstacles": conflicts,
        "placement_reason": dec.reason,
        "weight_downgraded": bool(dec.visual_weight_override),
        "keyword_delay_ms": sum(g.delay_ms for g in groups if g.is_keyword_group),
        "keyword_groups": sum(1 for g in groups if g.is_keyword_group),
    }

    # Рендерим видео-пример (если нет жесткого конфликта)
    if not conflicts or (obstacles and len(conflicts) < len(obstacles)):
        # VISUAL_WEIGHTS и HOOK_TYPES уже доступны из модуля, локальный import удален
        _FONT = _resolve_font(int(75 * VISUAL_WEIGHTS["L4"]["font_scale"]))

        style = {
            "font_size": int(75 * VISUAL_WEIGHTS[override_w]["font_scale"]),
            "effect": HOOK_TYPES["CONTRADICTION"]["preset_effect"],
            "visual_weight": override_w,
            "position": "top",
            "text_color": "#FF7A12",
            "stroke_color": "#000000",
            "stroke_width": 1,
            "uppercase": True,
        }

        bg_clip = ColorClip(size=(VW, VH), color=(15, 15, 18), duration=DUR)
        text_clips = []

        for g in groups:
            g_style = dict(style)
            g_style["top_offset_percent"] = dec.y_percent / 100.0
            clip = engine.create_styled_text_clip(
                g.text.upper(), g.start, min(g.end + 0.4, DUR),
                (VW, VH), g_style, _FONT)
            text_clips.append(clip)

        final = CompositeVideoClip([bg_clip] + text_clips, size=(VW, VH)).with_duration(DUR)
        out_mp4 = OUT / f"safezone_{name}.mp4"
        final.write_videofile(str(out_mp4), codec="libx264", audio_codec="aac", fps=FPS, logger=None)
        final.close()
        report["video_mp4"] = str(out_mp4)
    else:
        report["video_mp4"] = None

    return report


def _scenario_clean() -> tuple[str, list[tuple]]:
    return "clean", []


def _scenario_face_top() -> tuple[str, list[tuple]]:
    return "face_top", [(200, 150, 900, 700)]


def _scenario_busy() -> tuple[str, list[tuple]]:
    return "busy", [
        (0, 0, 1080, 800),
        (100, 600, 980, 950),
        (0, 1350, 1080, 1700),
    ]


def main() -> int:
    OUT.mkdir(exist_ok=True)

    scenarios = [
        ("🟢 Чистый кадр", _scenario_clean()),
        ("🟡 Лицо сверху", _scenario_face_top()),
        ("🔴 Загруженный кадр", _scenario_busy()),
    ]

    all_ok = True
    for label, (name, obstacles) in scenarios:
        print(f"\n{label}")
        report = _build_scenario(name, obstacles)

        conflicts = report["conflicts_with_obstacles"]
        ok = len(conflicts) == 0
        all_ok = all_ok and ok

        print(f"  зона: {report['decision_zone']}  y={report['y_percent']}%")
        print(f"  текст в Y: {report['text_box_y_frac'][0]*100:.1f}% - {report['text_box_y_frac'][1]*100:.1f}%")
        print(f"  кол-во конфликтов: {len(conflicts)}  {'✓' if ok else '✗ КОНФЛИКТ'}")
        print(f"  вес снижен: {report['weight_downgraded']}")
        print(f"  keyword delay: {report['keyword_delay_ms']}ms (ключевых групп: {report['keyword_groups']})")

    print(f"\n{'ВСЕ ЗОНЫ ЧИСТЫ ✓' if all_ok else 'ЕСТЬ КОНФЛИКТЫ ✗'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())