# AISIE Phase 2.3 — Validation: Forbidden Animations & Color/Typography rules
# (разделы 16-19 ТЗ)
#
# ЗАПРЕЩЕНО (раздел 17):
#   Bounce / Elastic / Spin / Glitch / Rainbow / Constant zoom /
#   Karaoke highlighting / Typewriter everywhere / Большие прыжки текста /
#   Постоянный glow / Случайные transitions
#
# ЦВЕТ (раздел 18): только фирменная палитра «оранжевого луча».
#   Иерархия через Размер→Время→Позицию→Scale→Анимацию, НЕ сменой цвета.
#
# ТИПОГРАФИКА (раздел 19): hook/subtitle — UPPERCASE.

from __future__ import annotations

from dataclasses import dataclass

ALLOWED_EFFECTS = {"fade_move", "phrase_reveal", "soft_scale", "quick_cut",
                   "hold_accent", "none"}

LEGACY_MAP = {                      # мягкая миграция старых имён эффектов
    "fade": "fade_move",
    "pop": "soft_scale",
    "slide_up": "fade_move",
    "slide_down": "fade_move",
    "slide_left": "fade_move",
}

FORBIDDEN_EFFECTS = {"bounce", "elastic", "spin", "glitch", "rainbow"}

# Фирменная палитра «луча» (тёплые оранжево-золотые тона из пресета проекта)
BRAND_PALETTE = {
    "#FF7A12", "#FFE53B", "#FF5E00", "#FFAE00", "#FF5100",
    "#D82600", "#FFF566", "#FFFFFF", "#000000",
}

# Насыщенные чистые тона вне палитры = «rainbow-риск» (раздел 18)
FORBIDDEN_HUES = {"#FF0000": "red", "#00FF00": "green", "#0000FF": "blue",
                  "#00FFFF": "cyan", "#FF00FF": "magenta", "#800080": "purple"}

SCALE_JUMP_LIMIT = 0.07            # «большие прыжки»: |Δscale| > 7%
GLOW_LIMIT = 6                     # «постоянный glow»


@dataclass
class Issue:
    level: str        # "error" | "warn"
    code: str         # машинный код правила
    message: str

    def __str__(self) -> str:
        icon = "✗" if self.level == "error" else "⚠"
        return f"{icon} [{self.code}] {self.message}"


class StyleValidator:
    """Проверяет стиль/эффект клипа на соответствие визуальному языку ТЗ."""

    def validate_clip(self, style: dict, effect: str | None = None,
                      visual_weight: str = "L0") -> list[Issue]:
        issues: list[Issue] = []
        eff = (effect or style.get("effect") or "").lower()
        vw = (style.get("visual_weight") or visual_weight).upper()

        # --- Эффекты ---
        if eff in FORBIDDEN_EFFECTS:
            issues.append(Issue("error", "FORBIDDEN_EFFECT",
                                f"эффект «{eff}» запрещён разделом 17"))
        elif eff and eff not in ALLOWED_EFFECTS \
                and eff not in ("zoom_in", "typewriter"):   # у них свои правила ниже
            fix = LEGACY_MAP.get(eff)
            msg = f"неизвестный эффект «{eff}» (случайный transition)"
            if fix:
                msg += f"; заменить на «{fix}»"
            issues.append(Issue("error" if not fix else "warn",
                                "UNKNOWN_EFFECT", msg))

        if eff == "typewriter":
            lvl = "warn" if vw in ("L3", "L4") else "error"
            issues.append(Issue(lvl, "TYPEWRITER_EVERYWHERE",
                                "typewriter допустим лишь как редкое исключение"))

        if eff == "zoom_in":
            issues.append(Issue("warn", "CONSTANT_ZOOM",
                                "перманентный zoom_in = «constant zoom» (запрещён); "
                                "использовать soft_scale 96→100%"))

        # --- Прыжки масштаба / glow ---
        scale_from, scale_to = style.get("scale_from"), style.get("scale_to")
        if scale_from is not None and scale_to is not None \
                and abs(scale_to - scale_from - 1.0) > SCALE_JUMP_LIMIT:
            issues.append(Issue("error", "SCALE_JUMP",
                                f"прыжок масштаба {scale_from}→{scale_to} "
                                f"превышает ±{int(SCALE_JUMP_LIMIT*100)}%"))
        glow = int(style.get("glow_radius") or 0)
        if glow > GLOW_LIMIT:
            issues.append(Issue("warn", "CONSTANT_GLOW",
                                f"glow_radius={glow} > {GLOW_LIMIT} — постоянное свечение"))

        # --- Карaoke ---
        if style.get("karaoke"):
            issues.append(Issue("error", "KARAOKE",
                                "разноцветные karaoke-субтитры запрещены"))

        # --- Цвета ---
        for key in ("text_color", "highlight_color", "stroke_color"):
            c = style.get(key)
            if isinstance(c, str) and c.upper() in FORBIDDEN_HUES:
                issues.append(Issue(
                    "error", "FOREIGN_COLOR",
                    f"{key}={c} ({FORBIDDEN_HUES[c.upper()]}) вне фирменной палитры"))
            elif isinstance(c, str) and c.startswith("#") \
                    and c.upper() not in BRAND_PALETTE \
                    and c.upper() not in FORBIDDEN_HUES:
                issues.append(Issue("warn", "OFF_PALETTE",
                                    f"{key}={c} не входит в палитру луча "
                                    "(проверить намеренность)"))

        grad = style.get("gradient_colors")
        if grad:
            uniq_hues = {g[:7].upper() for g in grad if isinstance(g, str)}
            foreign = uniq_hues & set(FORBIDDEN_HUES)
            if foreign:
                issues.append(Issue("error", "RAINBOW_GRADIENT",
                                    f"градиент содержит чужие тона: {sorted(foreign)}"))

        # --- Типографика ---
        if vw in ("L2", "L3", "L4"):
            txt = str(style.get("text") or style.get("_sample_text") or "")
            if txt and not txt.isupper():
                issues.append(Issue("warn", "NOT_UPPERCASE",
                                    "hook/subtitle должны быть UPPERCASE (раздел 19)"))
            if not style.get("uppercase", False) and not txt:
                pass          # нет данных — не штрафуем

        return issues

    def validate_sequence(self, clip_specs: list[dict]) -> list[Issue]:
        """Пакетная проверка очередности клипов: ловит 'typewriter everywhere'
        и хаотичную смену переходов."""
        issues: list[Issue] = []
        tw = sum(1 for c in clip_specs
                 if (c.get("effect") or "").lower() == "typewriter")
        if tw > 1:
            issues.append(Issue("error", "TYPEWRITER_EVERYWHERE",
                                f"typewriter использован {tw}× — разрешён максимум 1"))
        unknown = [c.get("effect") for c in clip_specs
                   if (c.get("effect") or "") .lower()
                   not in ALLOWED_EFFECTS | FORBIDDEN_EFFECTS | {"none", ""}
                   and (c.get("effect") or "").lower() != "typewriter"]
        if len(set(map(str, unknown))) > 2:
            issues.append(Issue("warn", "RANDOM_TRANSITIONS",
                                f"{len(set(map(str, unknown)))} разных нестандартных "
                                "переходов — визуальный язык становится случайным"))
        return issues


if __name__ == "__main__":
    v = StyleValidator()
    cases = [
        ("Правильный хук (эталон)", {
            "effect": "phrase_reveal", "visual_weight": "L3", "uppercase": True,
            "text_color": "#FF7A12", "glow_radius": 4,
            "gradient_colors": ("#FFF566", "#FFAE00")}, []),
        ("Запрещённый bounce", {"effect": "bounce", "visual_weight": "L3"}, []),
        ("Karaoke + чужой цвет", {
            "effect": "fade_move", "karaoke": True, "highlight_color": "#00FF00"}, []),
        ("Constant zoom + сильный glow", {
            "effect": "zoom_in", "glow_radius": 9, "visual_weight": "L2"}, []),
        ("Старый pop (миграция)", {"effect": "pop", "visual_weight": "L0"}, []),
        ("Прыжок масштаба", {
            "effect": "hold_accent", "scale_from": 1.0, "scale_to": 1.15}, []),
    ]
    total_bad = 0
    for name, stl, _ in cases:
        found = v.validate_clip(stl)
        bad = [i for i in found if i.level == "error"]
        total_bad += len(bad)
        print(f"— {name}: {'ЧИСТО' if not found else ''}")
        for i in found:
            print(f"    {i}")
    seq = v.validate_sequence([
        {"effect": "typewriter"}, {"effect": "typewriter"},
        {"effect": "fade_move"}, {"effect": "mystery_fx"},
        {"effect": "spin"}, {"effect": "elastic"},
    ])
    print("— Последовательность из 6 клипов:")
    for i in seq:
        print(f"    {i}")
    print(f"\nОшибок уровня error: {total_bad} (ожидаем ≥4)")
