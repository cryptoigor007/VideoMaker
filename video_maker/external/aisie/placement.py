# AISIE Phase 1.4 — Placement Engine + Platform Safe Zones (разделы 6-11, 23)
#
# Порядок решения места (раздел 25):
#   КОМПОЗИЦИЯ → SAFE ZONE → МЕСТО. Не наоборот.
#
# Цепочка выбора (раздел 11):
#   PRIMARY свободна → PRIMARY
#   иначе SECONDARY
#   иначе скан свободных полос
#   иначе уменьшить visual_weight (L4→L2)
#   иначе минимальный subtitle-режим
#
# PRIMARY HOOK ZONE: Y = 10–25% высоты кадра (первый кандидат, не догма).
# SECONDARY HOOK ZONE: Y = 30–45%.

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlatformProfile:
    name: str
    aspect: str                      # "9:16" | "16:9"
    top_safe: float                  # доля высоты, закрытая UI сверху
    bottom_safe: float               # description / CTA / прогресс-бар снизу
    left_safe: float                 # правый/левый UI (для Reels/TikTok)
    right_safe: float


PROFILES = {
    "youtube_shorts":   PlatformProfile("youtube_shorts", "9:16", 0.08, 0.17, 0.02, 0.10),
    "instagram_reels":  PlatformProfile("instagram_reels", "9:16", 0.09, 0.20, 0.06, 0.14),
    "tiktok":           PlatformProfile("tiktok",           "9:16", 0.07, 0.19, 0.04, 0.16),
    "youtube_16_9":     PlatformProfile("youtube_16_9",    "16:9", 0.05, 0.05, 0.01, 0.01),
}


PRIMARY_ZONE = (0.10, 0.25)      # доли высоты
SECONDARY_ZONE = (0.30, 0.45)


def _rect_overlap(a: tuple[float, float, float, float],
                  b: tuple[float, float, float, float]) -> bool:
    """a,b = (x0,y0,x1,y1) в пикселях."""
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


@dataclass
class Obstacles:
    """Боксы препятствий в пикселях кадра. Детекция лиц/объектов подключается
    позже (MediaPipe); сейчас движок принимает готовые боксы."""
    faces: list = field(default_factory=list)       # [(x0,y0,x1,y1)]
    subjects: list = field(default_factory=list)
    graphics: list = field(default_factory=list)    # существующие надписи/логотипы

    def all(self) -> list:
        return self.faces + self.subjects + self.graphics

    def any_face(self) -> bool:
        return bool(self.faces)


@dataclass
class PlacementDecision:
    zone: str                        # primary|secondary|scanned|minimal_subtitle
    y_percent: float                 # верх текста, % высоты кадра
    x_percent: float = 50.0          # центр по X (позже left/right layouts для 16:9)
    visual_weight_override: str | None = None   # 'L2' при перегрузке кадра
    reason: str = ""


class PlacementEngine:
    def __init__(self, profile: PlatformProfile):
        self.p = profile

    # ---------- служебное ----------

    def _text_box(self, video_w, video_h, y_frac, font_frac=0.085,
                  width_frac=0.86) -> tuple:
        """Прямоугольник текста (по умолчанию L3 ≈8.5% высоты)."""
        h = video_h * font_frac * 1.35          # запас на межстрочный
        w = min(video_w * width_frac, video_w - 40)
        x = (video_w - w) / 2
        return (x, y_frac * video_h, x + w, y_frac * video_h + h)

    def _zone_free(self, zone: tuple[float, float], ob: Obstacles,
                   vw: int, vh: int, font_frac=0.085) -> tuple[bool, float]:
        """Проверяет зону: целиком внутри safe и без пересечений.
        Возвращает (свободна, y_percent кандидата)."""
        y_top = max(zone[0], self.p.top_safe + 0.005)
        y_cand = min(y_top, zone[1])
        box = self._text_box(vw, vh, y_cand, font_frac)
        if box[3] > vh * (1 - self.p.bottom_safe):
            return False, y_cand * 100
        for obstacle in ob.all():
            if _rect_overlap(box, obstacle):
                return False, y_cand * 100
        return True, round(y_cand * 100, 1)

    # ---------- публичный API ----------

    def choose(
        self,
        video_size: tuple[int, int],
        obstacles: Obstacles | None = None,
        current_visual_weight: str = "L3",
        font_frac_by_weight: dict | None = None,
    ) -> PlacementDecision:
        ob = obstacles or Obstacles()
        vw, vh = video_size
        ff = (font_frac_by_weight or {"L0": .050, "L1": .060, "L2": .075,
                                      "L3": .085, "L4": .100}) \
            .get(current_visual_weight, 0.085)

        # 1) PRIMARY HOOK ZONE
        free, y = self._zone_free(PRIMARY_ZONE, ob, vw, vh, ff)
        if free:
            return PlacementDecision("primary", y, reason="PRIMARY 10-25% свободна")

        # 2) SECONDARY HOOK ZONE
        free, y = self._zone_free(SECONDARY_ZONE, ob, vw, vh, ff)
        if free:
            return PlacementDecision("secondary", y,
                                     reason="верх занят → SECONDARY 30-45%")

        # 3) Скан свободных полос сверху вниз (шаг 5%)
        scan_y = self.p.top_safe + 0.03
        while scan_y < (1 - self.p.bottom_safe):
            box = self._text_box(vw, vh, scan_y, ff)
            if box[3] <= vh * (1 - self.p.bottom_safe) and \
                    not any(_rect_overlap(box, o) for o in ob.all()):
                zone = "scanned"
                return PlacementDecision(zone, round(scan_y * 100, 1),
                                         reason=f"свободная полоса Y={scan_y:.0%}")
            scan_y += 0.05

        # 4) Всё занято → понижаем визуальный вес (меньше шрифт → меньше бокс)
        lighter = {"L4": "L2", "L3": "L2", "L2": "L1"}.get(current_visual_weight)
        if lighter:
            down_ff = (font_frac_by_weight or {}).get(lighter, 0.075)
            scan_y = self.p.top_safe + 0.03
            while scan_y < (1 - self.p.bottom_safe):
                box = self._text_box(vw, vh, scan_y, down_ff)
                if box[3] <= vh * (1 - self.p.bottom_safe) and \
                        not any(_rect_overlap(box, o) for o in ob.all()):
                    return PlacementDecision(
                        "scanned", round(scan_y * 100, 1),
                        visual_weight_override=lighter,
                        reason=f"перегрузка кадра → вес {lighter}, Y={scan_y:.0%}")
                scan_y += 0.05

        # 5) Крайний случай: минимальный subtitle вместо огромного хука
        return PlacementDecision(
            "minimal_subtitle",
            round((1 - self.p.bottom_safe - 0.12) * 100, 1),
            visual_weight_override="L0",
            reason="свободного места нет → минимальный subtitle (не пихаем огромный текст)",
        )


if __name__ == "__main__":
    VW, VH = 1080, 1920                     # 9:16

    def show(title, dec):
        print(f"{title:<46} zone={dec.zone:<16} y={dec.y_percent:>5}% "
              f"w={dec.visual_weight_override or '-':<3} {dec.reason}")

    print("=== youtube_shorts ===")
    eng = PlacementEngine(PROFILES["youtube_shorts"])

    show("1. Чистый кадр:", eng.choose((VW, VH)))
    show("2. Лицо в верхней трети:",
         eng.choose((VW, VH), Obstacles(faces=[(200, 200, 900, 700)])))
    show("3. Лицо + графика в secondary:",
         eng.choose((VW, VH), Obstacles(
             faces=[(200, 150, 900, 650)],
             graphics=[(100, 600, 980, 950)])))
    show("4. Кадр полностью забит:",
         eng.choose((VW, VH), Obstacles(
             faces=[(0, 0, 1080, 800)],
             subjects=[(0, 750, 1080, 1400)],
             graphics=[(0, 1350, 1080, 1700)])))

    print("\n=== Профили платформ (чистый кадр, низ отличается) ===")
    for name, prof in PROFILES.items():
        d = PlacementEngine(prof).choose((VW, VH))
        print(f"{name:<18} zone={d.zone:<9} y={d.y_percent}%")
