"""Утилиты для создания красивых субтитров, хуков и объемного градиентного текста."""

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter


def hex_to_rgb(color):
    """Преобразует HEX (#RRGGBB или #RRGGBBAA) или кортеж в RGB/RGBA кортеж."""
    if isinstance(color, str):
        c = color.strip()
        if c.startswith("#"):
            if len(c) == 7:
                try:
                    return (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))
                except ValueError:
                    pass
            elif len(c) == 9:
                try:
                    return (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16), int(c[7:9], 16))
                except ValueError:
                    pass
    if isinstance(color, (tuple, list)):
        return tuple(color)
    return (255, 255, 255)


def draw_gradient_text_3d(
    img,
    text,
    position,
    font,
    gradient_colors=("#FFE53B", "#FFAE00", "#FF5100", "#D82600"),
    gradient_direction="vertical",
    glow_color=None,
    glow_radius=4,
    stroke_width=1,
    stroke_color=None,
    shadow_color=(0, 0, 0),
    shadow_offset=(3, 4),
    shadow_blur=4,
    inner_bevel=True,
    underline=False,
    underline_words=None,
):
    """
    Рисует сочный, необъёмный/объёмный (3D) градиентный текст:
    - Плавный многоцветный градиент (например, от ярко-жёлтого к огненно-оранжевому)
    - Внутренний световой блик / фаска (создаёт эффект объёма/глянца, убирает плоскость)
    - Мягкое тёплое свечение (halo / маркерный ореол)
    - Чёткий контур/обводка для читаемости
    - Мягкая тень для контраста на видео
    - Поддержка каллиграфического подчеркивания
    """
    if not text:
        return

    x, y = position
    dummy = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    d = ImageDraw.Draw(dummy)
    bbox = d.textbbox((0, 0), text, font=font)

    font_sz = getattr(font, "size", 60)
    pad = int(font_sz * 0.45)
    tw = max(1, bbox[2] - bbox[0])
    th = max(1, bbox[3] - bbox[1])
    w = tw + pad * 2
    h = th + pad * 2 + (int(font_sz * 0.35) if underline else 0)

    tx = pad - bbox[0]
    ty = pad - bbox[1]

    text_canvas = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))

    # 1. Базовая маска текста
    mask = Image.new("L", (int(w), int(h)), 0)
    m_draw = ImageDraw.Draw(mask)
    m_draw.text((tx, ty), text, font=font, fill=255)

    # Линии подчеркивания
    underlines = []
    if underline:
        words = text.split()
        if underline_words:
            target_words = [str(uw).strip(".,!?…:;»«\"'()") for uw in underline_words]
        else:
            target_words = [words[-1].strip(".,!?…:;»«\"'()")] if words else []

        curr_x = tx
        for word in words:
            clean_w = word.strip(".,!?…:;»«\"'()")
            w_len = d.textlength(word, font=font)
            if not underline_words or clean_w in target_words or word in target_words:
                ux1 = curr_x
                ux2 = curr_x + w_len
                uy = ty + th + int(font_sz * 0.08)
                uh = max(3, int(font_sz * 0.05))
                underlines.append((ux1, ux2, uy, uh))
            curr_x += w_len + d.textlength(" ", font=font)

        for ux1, ux2, uy, uh in underlines:
            pts = [
                (ux1, uy + uh / 2),
                (ux1 + (ux2 - ux1) * 0.15, uy),
                (ux1 + (ux2 - ux1) * 0.85, uy),
                (ux2 + int(font_sz * 0.08), uy + uh / 2),
                (ux1 + (ux2 - ux1) * 0.85, uy + uh),
                (ux1 + (ux2 - ux1) * 0.15, uy + uh),
            ]
            m_draw.polygon(pts, fill=255)
            m_draw.line(
                [(ux1, uy + uh / 2), (ux2 + int(font_sz * 0.06), uy + uh / 2)],
                fill=255,
                width=uh,
            )

    # 2. Мягкая тень (для отделения от фона видео)
    if shadow_color and (shadow_offset[0] != 0 or shadow_offset[1] != 0 or shadow_blur > 0):
        sh_img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
        sh_draw = ImageDraw.Draw(sh_img)
        sh_rgba = hex_to_rgb(shadow_color)
        if len(sh_rgba) == 3:
            sh_rgba = (*sh_rgba, 170)
        sh_draw.text(
            (tx + shadow_offset[0], ty + shadow_offset[1]),
            text,
            font=font,
            fill=sh_rgba,
        )
        for ux1, ux2, uy, uh in underlines:
            sh_draw.line(
                [
                    (ux1 + shadow_offset[0], uy + shadow_offset[1] + uh / 2),
                    (ux2 + shadow_offset[0], uy + shadow_offset[1] + uh / 2),
                ],
                fill=sh_rgba,
                width=uh,
            )
        if shadow_blur > 0:
            sh_img = sh_img.filter(ImageFilter.GaussianBlur(shadow_blur))
        text_canvas.alpha_composite(sh_img)

    # 3. Мягкое тёплое свечение (Halo / Neon / Fire glow)
    if glow_color and glow_radius > 0:
        glow_rgba = hex_to_rgb(glow_color)
        if len(glow_rgba) == 3:
            glow_rgba = (*glow_rgba, 255)
        glow_base = mask.filter(ImageFilter.GaussianBlur(glow_radius))
        g_arr = np.array(glow_base, dtype=float)
        g_arr = np.clip(g_arr * 2.2, 0, 255).astype(np.uint8)
        glow_mask_final = Image.fromarray(g_arr, "L")
        glow_layer = Image.new("RGBA", (int(w), int(h)), glow_rgba)
        text_canvas.paste(glow_layer, (0, 0), glow_mask_final)

    # 4. Контурная обводка (Contour edge)
    if stroke_color and stroke_width > 0:
        st_rgba = hex_to_rgb(stroke_color)
        if len(st_rgba) == 3:
            st_rgba = (*st_rgba, 255)
        st_mask = Image.new("L", (int(w), int(h)), 0)
        st_draw = ImageDraw.Draw(st_mask)
        st_draw.text(
            (tx, ty),
            text,
            font=font,
            fill=255,
            stroke_width=stroke_width,
            stroke_fill=255,
        )
        for ux1, ux2, uy, uh in underlines:
            st_draw.line(
                [(ux1, uy + uh / 2), (ux2 + int(font_sz * 0.06), uy + uh / 2)],
                fill=255,
                width=uh + stroke_width * 2,
            )
        st_layer = Image.new("RGBA", (int(w), int(h)), st_rgba)
        text_canvas.paste(st_layer, (0, 0), st_mask)

    # 5. Тело градиента (Volumetric gradient)
    rgb_stops = [hex_to_rgb(c) for c in (gradient_colors or [glow_color or "#FFAE00"])]
    n_stops = len(rgb_stops)
    if n_stops == 1:
        c_rgb = rgb_stops[0][:3]
        grad_layer = Image.new("RGBA", (int(w), int(h)), (*c_rgb, 255))
    else:
        iw, ih = int(w), int(h)
        if gradient_direction == "horizontal":
            pos = np.linspace(0, n_stops - 1, iw)[None, :]
            pos = np.repeat(pos, ih, axis=0)
        elif gradient_direction == "diagonal":
            pos = (
                np.linspace(0, 1, iw)[None, :] + np.linspace(0, 1, ih)[:, None]
            ) / 2.0 * (n_stops - 1)
        else:  # vertical
            y_norm = np.linspace(0, 1, ih)[:, None]
            y_norm = np.repeat(y_norm, iw, axis=1)
            y_top = ty / max(1, ih)
            y_bot = min(1.0, (ty + th) / max(1, ih))
            pos = np.clip(
                (y_norm - y_top) / max(1e-4, y_bot - y_top), 0.0, 1.0
            ) * (n_stops - 1)

        grad_arr = np.zeros((ih, iw, 4), dtype=np.float32)
        for i in range(n_stops - 1):
            mask_seg = (pos >= i) & (pos <= (i + 1))
            t = np.clip(pos - i, 0.0, 1.0)[:, :, None]
            c1 = np.array(rgb_stops[i], dtype=np.float32)[:3]
            c2 = np.array(rgb_stops[i + 1], dtype=np.float32)[:3]
            interp = (1.0 - t) * c1 + t * c2
            grad_arr = np.where(
                mask_seg[:, :, None],
                np.concatenate([interp, np.full((ih, iw, 1), 255.0)], axis=2),
                grad_arr,
            )
        grad_layer = Image.fromarray(
            np.clip(grad_arr, 0, 255).astype(np.uint8), "RGBA"
        )

    text_canvas.paste(grad_layer, (0, 0), mask)

    # 6. Внутренний световой блик (3D фаска / specular highlight)
    if inner_bevel:
        shift_px = max(1, font_sz // 45)
        shifted = ImageChops.offset(mask, 0, shift_px)
        sh_d = ImageDraw.Draw(shifted)
        sh_d.rectangle([0, 0, w, shift_px], fill=0)
        ridge = ImageChops.subtract(mask, shifted).filter(ImageFilter.GaussianBlur(0.7))
        # Тёплый светло-золотистый блик
        hl_layer = Image.new("RGBA", (int(w), int(h)), (255, 253, 220, 210))
        text_canvas.paste(hl_layer, (0, 0), ridge)

    # Накладываем на целевое изображение
    dest_x = round(x - pad + bbox[0])
    dest_y = round(y - pad + bbox[1])
    img.alpha_composite(text_canvas, (dest_x, dest_y))


def draw_text_with_shadow(
    img,
    text,
    position,
    font,
    fill,
    stroke_width=0,
    stroke_fill=None,
    shadow_color=(0, 0, 0),
    shadow_offset=(3, 3),
    shadow_blur=0,
    gradient_colors=None,
    gradient_direction="vertical",
    glow_color=None,
    glow_radius=0,
    inner_bevel=False,
    underline=False,
    underline_words=None,
):
    """Рисует текст с тенью, обводкой, свечением или 3D-градиентом."""
    if gradient_colors or glow_color or inner_bevel or underline:
        draw_gradient_text_3d(
            img,
            text,
            position,
            font,
            gradient_colors=gradient_colors or (fill, fill),
            gradient_direction=gradient_direction,
            glow_color=glow_color,
            glow_radius=glow_radius,
            stroke_width=stroke_width,
            stroke_color=stroke_fill,
            shadow_color=shadow_color,
            shadow_offset=shadow_offset,
            shadow_blur=shadow_blur,
            inner_bevel=inner_bevel,
            underline=underline,
            underline_words=underline_words,
        )
        return

    draw = ImageDraw.Draw(img)
    x, y = position
    # Тень
    if shadow_color is not None and (shadow_offset[0] or shadow_offset[1]):
        shadow_img = Image.new("RGBA", img.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow_img)
        shadow_draw.text(
            (x + shadow_offset[0], y + shadow_offset[1]),
            text,
            font=font,
            fill=shadow_color,
            stroke_width=stroke_width,
            stroke_fill=shadow_color,
        )
        if shadow_blur > 0:
            shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(shadow_blur))
        img.paste(shadow_img, (0, 0), shadow_img)
    # Основной текст
    draw.text(
        (x, y),
        text,
        font=font,
        fill=fill,
        stroke_width=stroke_width,
        stroke_fill=stroke_fill,
    )


def draw_gradient_background(
    size, color1, color2, direction="vertical", radius=0, alpha=140
):
    """Создаёт прямоугольник с градиентом и скруглением."""
    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    if color1 is None or color2 is None:
        bg = Image.new("RGBA", size, color1 if color1 else (0, 0, 0, alpha))
    else:
        if direction == "vertical":
            grad = np.linspace(0, 1, h)
            grad = np.repeat(grad[:, None], w, axis=1)
        else:  # horizontal
            grad = np.linspace(0, 1, w)
            grad = np.repeat(grad[None, :], h, axis=0)
        c1 = np.array(color1) / 255.0
        c2 = np.array(color2) / 255.0
        mixed = (1 - grad[:, :, None]) * c1 + grad[:, :, None] * c2
        rgba = np.concatenate(
            [mixed * 255, np.full((h, w, 1), alpha)], axis=2
        ).astype(np.uint8)
        bg = Image.fromarray(rgba, "RGBA")
    if radius > 0:
        mask = Image.new("L", size, 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
        img.paste(bg, (0, 0), mask)
    else:
        img.paste(bg, (0, 0))
    return img


class PlatformProfile:
    """Профиль платформы с безопасными зонами и ограничениями UI."""
    PROFILES = {  # noqa: RUF012
        "youtube_shorts": {
            "name": "YouTube Shorts",
            "aspect_ratio": "9:16",
            "top_safe": 0.10,        # 10% сверху (поиск/кнопки)
            "bottom_safe": 0.20,     # 20% снизу (описание, канал, звук, прогресс-бар)
            "left_safe": 0.06,       # 6% слева
            "right_safe": 0.14,      # 14% справа (лайк, комменты, репост)
            "primary_hook_y": (0.10, 0.25),   # 10–25% высоты кадра
            "secondary_hook_y": (0.30, 0.45), # 30–45% высоты кадра
        },
        "instagram_reels": {
            "name": "Instagram Reels",
            "aspect_ratio": "9:16",
            "top_safe": 0.12,
            "bottom_safe": 0.22,
            "left_safe": 0.06,
            "right_safe": 0.14,
            "primary_hook_y": (0.12, 0.25),
            "secondary_hook_y": (0.30, 0.45),
        },
        "tiktok": {
            "name": "TikTok",
            "aspect_ratio": "9:16",
            "top_safe": 0.10,
            "bottom_safe": 0.24,
            "left_safe": 0.06,
            "right_safe": 0.16,
            "primary_hook_y": (0.10, 0.25),
            "secondary_hook_y": (0.30, 0.45),
        },
        "youtube_16_9": {
            "name": "YouTube 16:9",
            "aspect_ratio": "16:9",
            "top_safe": 0.08,
            "bottom_safe": 0.12,
            "left_safe": 0.08,
            "right_safe": 0.08,
            "primary_hook_y": (0.10, 0.30),
            "secondary_hook_y": (0.35, 0.55),
        },
    }

    @classmethod
    def get_profile(cls, platform="youtube_shorts"):
        key = str(platform).lower().replace(" ", "_").replace("-", "_")
        return cls.PROFILES.get(key, cls.PROFILES["youtube_shorts"])


HOOK_TYPES = {
    "QUESTION": {
        "visual_weight": "L3",
        "preset_effect": "fade_move",
        "description": "Вопрос зрителю",
    },
    "CONTRADICTION": {
        "visual_weight": "L4",
        "preset_effect": "quick_cut",
        "description": "Парадокс или противоречие",
    },
    "STATEMENT": {
        "visual_weight": "L3",
        "preset_effect": "fade_move",
        "description": "Прямое сильное утверждение",
    },
    "CURIOSITY": {
        "visual_weight": "L3",
        "preset_effect": "phrase_reveal",
        "description": "Создание интриги и ожидания",
    },
    "IDENTITY": {
        "visual_weight": "L3",
        "preset_effect": "phrase_reveal",
        "description": "Узнавание себя / адресация",
    },
    "LOSS": {
        "visual_weight": "L3",
        "preset_effect": "fade_move",
        "description": "Упущенная выгода / потеря",
    },
    "REVELATION": {
        "visual_weight": "L4",
        "preset_effect": "hold_accent",
        "description": "Откровение / разворот мысли",
    },
    "VISUAL_HOOK": {
        "visual_weight": "L0",
        "preset_effect": "none",
        "description": "Визуальный хук (минимум текста)",
    },
}

VISUAL_WEIGHTS = {
    "L0": {"font_scale": 0.85, "glow_radius": 0, "font_name": "TikTok Sans Bold", "text_color": "#FFFFFF", "description": "Normal subtitle"},
    "L1": {"font_scale": 0.92, "glow_radius": 0, "font_name": "TikTok Sans Bold", "text_color": "#FFFFFF", "description": "Emphasis"},
    "L2": {"font_scale": 1.00, "glow_radius": 0, "font_name": "TikTok Sans ExtraBold", "text_color": "#FFFF00", "description": "Keyword"},
    "L3": {"font_scale": 1.08, "glow_radius": 0, "font_name": "TikTok Sans Black", "text_color": "#FF3B30", "description": "Hook"},
    "L4": {"font_scale": 1.15, "glow_radius": 0, "font_name": "TikTok Sans Black", "text_color": "#FF3B30", "description": "Climax / Punchline"},
}

# 7-color system for word emphasis (TRUE NEON palette)
WORD_COLORS = {
    "base": "#FFFFFF",       # Основной текст
    "highlight": "#FFFF00",  # Хайлайт (чистый жёлтый)
    "negative": "#FF0000",   # Негатив/запрет (чистый красный)
    "positive": "#00FF00",   # Позитив (чистый зелёный)
    "keyword": "#FFFFFF",    # Ключевое слово — оранжевый убран, красится в белый (нейтраль)
    "question": "#00FFFF",   # Вопрос (чистый циан)
    "emotion": "#FF00FF",    # Эмоция/драма (чистый пинк)
}

# Word lists for automatic color detection
NEGATIVE_WORDS = {
    # абсолютное отрицание
    "никогда", "нигде", "никого", "никому", "никем", "никак",
    "никакого", "никакой", "никакие", "никакими", "ничто", "ничего",
    "ничему", "ничем", "нет", "ни", "нельзя",
    # ошибка / проблема
    "ошибка", "ошибки", "ошибку", "ошибок", "ошибкам", "ошибках",
    "ошибками", "ошибся", "ошиблась", "ошиблись", "ошибаешься",
    "ошибаетесь", "ошибочный", "ошибочная", "ошибочные", "ошибочно",
    "проблема", "проблемы", "проблему", "проблем", "проблеме",
    "проблемам", "проблемами", "проблемах", "проблемный", "проблемная",
    "проблемные", "проблематично", "неприятность", "неприятности",
    "неприятно", "неприятный", "неприятная",
    # качество
    "плохо", "плохой", "плохая", "плохое", "плохие", "плохого",
    "плохому", "хуже", "худший", "худшая", "худшие", "худшего",
    "ужас", "ужаса", "ужасы", "ужасно", "ужасный", "ужасная",
    "ужасное", "ужасные", "кошмар", "кошмара", "кошмарно",
    "кошмарный", "кошмарная", "отвратительно", "отвратительный",
    "отвратительные", "мерзко", "мерзость", "мерзкий", "гадость",
    "гадко", "гадкий", "противно", "противный", "паршиво", "паршивый",
    # опасность / риск
    "опасно", "опасный", "опасная", "опасные", "опасность",
    "опасности", "опасностей", "угроза", "угрозы", "угрозу", "угроз",
    "угрожает", "угрожают", "риск", "риски", "рисков", "риску",
    "рискуешь", "рискуете", "рискованный", "рискованно", "угрожающе",
    # запрет / стоп
    "хватит", "прекрати", "прекратите", "остановись", "остановитесь",
    "забудь", "забудьте", "выброси", "убери", "уберите", "отстань",
    "замолчи", "замолчите", "заткнись", "запрещено", "запрещён",
    "запрещена", "запрещены", "запрет", "запрета", "запретили",
    "воспрещено", "табу", "-stop",
    # провал / банкротство
    "провал", "провала", "провалу", "провалом", "провалы", "провалов",
    "провалился", "провалилась", "провалились", "провальный",
    "неудача", "неудачи", "неудачу", "неудач", "неудачник",
    "неудачница", "фиаско", "крах", "краха", "крахом", "рухнул",
    "рухнула", "рухнули", "банкрот", "банкротом", "банкротство",
    "обанкротился", "обанкротилась", "проигрыш", "проиграл",
    "проиграла", "проиграли", "поражение", "поражений", "слабее",
    "бессильный", "бессилен",
    # обман / предательство
    "обман", "обмана", "обманом", "обманул", "обманула", "обманули",
    "обманутый", "обманщик", "обманщица", "жулик", "жульё",
    "мошенник", "мошенники", "мошенничество", "афера", "аферы",
    "надувательство", "ложь", "лжи", "ложью", "лживый", "врёт",
    "врут", "наврал", "наврали", "солгал", "предатель",
    "предательство", "предал", "предала", "предали", "измена", "подлый", "подлость", "подлец",
    # лень / слабость
    "лень", "лени", "ленью", "ленивый", "ленивая", "ленивые",
    "лентяй", "лентяйка", "бездельник", "слабый", "слабая", "слабо",
    "слабость", "слабак", "беспомощный", "беспомощность",
    "бесполезный", "бесполезно", "бесполезность", "напрасно",
    "впустую", "зря",
    # интеллект / характер
    "тупой", "тупая", "тупо", "тупость", "тупица", "глупый", "глупая",
    "глупо", "глупость", "глупец", "идиот", "идиотка", "идиотский",
    "дурак", "дура", "дурацкий", "безмозглый", "жадный", "жадность",
    "жадина", "жадничают", "зависть", "завистник", "завистливый",
    # вражда
    "враг", "врага", "врагу", "врагом", "враги", "врагов", "врагами",
    "вражда", "враждебный", "недруг", "противник", "злой", "злая",
    "зло", "злобный", "злоба", "агрессия", "агрессии", "агрессивный",
    "жестокий", "жестоко", "жестокость", "тиран", "деспот", "насилие",
    # стыд / позор
    "стыд", "стыда", "стыдно", "позор", "позора", "позорно",
    "позорный", "срам", "срамота", "унижение", "унизили",
    "унизительный", "оскорбление", "оскорбили", "издевательство",
    "издеваются", "насмешка", "насмехаются", "высмеяли", "обозвали",
    # война / конфликт
    "война", "войны", "войне", "войной", "драка", "драки", "драку",
    "драк", "ссора", "ссоры", "ссориться", "конфликт", "конфликты",
    "конфликтов", "скандал", "скандалы", "скандала", "междуусобица",
    # смерть / здоровье
    "смерть", "смерти", "смертью", "смертельный", "смертельная",
    "смертельно", "погиб", "погибла", "погибли", "умирает", "умер",
    "умерла", "труп", "могила", "могилы", "боль", "боли", "больно",
    "болезненно", "болезнь", "болезни", "болезней", "больной",
    "больная", "болею", "болеешь", "страдание", "страдаю",
    "страдаешь", "страдает", "мучение", "мучаюсь", "мучительно",
    "рак", "опухоль", "инсульт", "инфаркт", "диабет", "вирус",
    "вирусы", "инфекция", "зараза", "заражение", "эпидемия",
    "пандемия", "яд", "ядовитый", "токсичный", "токсично",
    "токсичность", "наркоман", "наркомания", "пьяный", "пьяная",
    "пьянство", "алкоголик",
    # криминал
    "тюрьма", "тюрьмы", "тюрьме", "арест", "арестовали", "арестован",
    "судимость", "преступник", "преступники", "преступление",
    "преступника", "криминал", "криминальный", "кража", "кражи",
    "вор", "воры", "вора", "воров", "воровство", "украл", "украла",
    "украли", "террор", "террорист",
    # катастрофы
    "авария", "аварии", "катастрофа", "катастрофы", "катастрофически",
    "трагедия", "трагедии", "трагический", "трагично", "цунами",
    "землетрясение", "пожар", "пожары",
    # деньги вниз
    "долги", "долгов", "долгам", "задолжал", "задолженность",
    "нищий", "нищета", "нищим", "бедность", "бедняк", "бомж",
    "копейки", "копеек",
    # потеря / поломка
    "потерял", "потеряла", "потеряли", "потеря", "потери", "потерять",
    "теряешь", "теряют", "пропало", "пропали", "исчезло", "исчезли",
    "сломался", "сломалась", "сломалось", "сломан", "поломка",
    "поломки", "дефект", "дефекты", "неисправность", "грязный",
    "грязная", "грязно", "грязь", "воняет", "вонь", "смрад",
    # увольнение / безработица
    "увольнение", "уволили", "уволен", "уволена", "безработный",
    "безработица", "выгоняют", "выгнали", "сократили",
    # раздражение
    "бесит", "раздражает", "раздражают", "достал", "достала",
    "надоели", "надоел", "надоела", "задолбал",
    "задолбали", "невыносимо", "невыносимый",
}

POSITIVE_WORDS = {
    # базовые оценки
    "хорошо", "хороший", "хорошая", "хорошее", "хорошие", "лучше",
    "лучший", "лучшая", "лучшее", "лучшие", "отлично", "отличный",
    "отличная", "отличное", "отличные", "класс", "классный",
    "классная", "круто", "крутой", "крутая", "крутые", "супер",
    "шикарно", "шикарный", "шикарная", "шик", "блеск", "блестяще",
    "блестящий", "великолепно", "великолепный", "великолепная",
    "изумительно", "изумительный", "потрясающе", "потрясающий",
    "офигенно", "огонь", "имба", "топ", "топовый", "топовая",
    "топчик", "зачёт", "зачет", "красава", "молодец", "молодцы",
    "умница", "умник", "гениально", "гениальный", "гений",
    "восхитительно", "восхитительный",
    # идеал / чудо
    "идеально", "идеальный", "идеальная", "идеальное", "идеальные",
    "совершенство", "совершенный", "совершенная", "прекрасно",
    "прекрасный", "прекрасная", "прекрасное", "замечательно",
    "замечательный", "замечательная", "чудесно", "чудесный", "чудо",
    "чудеса", "волшебно", "волшебный", "сказочно",
    # красота
    "красиво", "красивый", "красивая", "красивое", "красивейший",
    "красота", "эстетично", "стильный", "стильная", "модно",
    "модный", "трендово",
    # любовь / симпатия (страсть и влюблённость — в EMOTION)
    "люблю", "любимые", "любимый", "любимая", "обожаю", "нравится",
    "нравишься", "понравилось", "понравился", "понравилась",
    "приятно", "приятный", "приятная",
    # успех / победа
    "успех", "успеха", "успеху", "успехом", "успехи", "успехов",
    "успешный", "успешная", "успешные", "успешно", "победа",
    "победы", "победу", "победить", "победил", "победила",
    "победили", "выиграл", "выиграла", "выиграли", "выигрыш",
    "чемпион", "чемпионка", "чемпионы", "лидер", "лидеры",
    "лидером", "первый", "первая",
    # результат / достижение
    "результат", "результаты", "результатов", "результативный",
    "добился", "добилась", "добились", "достиг", "достигла",
    "достигли", "достижение", "достижения", "получилось", "вышло",
    "сработало", "заработало", "помог", "помогла", "помогли",
    "помогло", "решено", "решается",
    # польза / деньги вверх
    "польза", "пользы", "полезный", "полезная", "полезно", "выгодно",
    "выгодный", "выгода", "выгоды", "прибыль", "прибыли",
    "прибыльный", "доход", "доходы", "доходов", "заработок",
    "заработал", "заработала", "заработали", "разбогател",
    "богатство", "богатый", "богатая", "богатеют", "бонус",
    "бонусы", "премия", "приз", "призы", "джекпот", "повезло",
    "повезёт", "удача", "удачи", "удачный", "удачно", "везение",
    "бесплатно", "даром",
    # сила / здоровье
    "сила", "силы", "силу", "сильный", "сильная", "сильные", "мощь",
    "мощно", "мощный", "энергия", "энергии", "здоровье", "здоровья",
    "здоровый", "здоровая", "вылечился", "вылечилась", "выздоровел",
    "исцелился", "бодро", "бодрый", "свежий",
    # свобода / спокойствие
    "свобода", "свободы", "свободен", "свободная", "независимость",
    "независимый", "безопасно", "безопасный", "спокойный",
    "спокойно", "надёжный", "надежный", "надёжно", "надёжность",
    "гарантия", "гарантии", "комфорт", "комфортно", "уют", "уютно",
    "уютный", "удобно",
    # честь / ум
    "честно", "честный", "честная", "честность", "искренне",
    "искренний", "верность", "верный", "доверие", "доверяю",
    "доверяют", "умный", "умная", "умно", "мудрый", "мудрость",
    "смышлёный", "сообразительный", "талант", "таланта", "таланты",
    "способности", "одарённый",
    # храбрость
    "смелый", "смелость", "храбрость", "храбрый", "отважный",
    "героизм", "герой", "героиня", "подвиг",
    # драйв
    "кайф", "кайфовый", "драйв", "заряжает", "заряжают",
    # благодарность
    "спасибо", "благодарю", "благодарность", "благодарен",
    "заслужил", "заслуженно", "достоин", "достойный", "достойно",
    # развитие
    "прогресс", "прогресса", "развитие", "развития", "развивается",
    "рост", "роста", "растёт", "растут", "вырос", "выросли",
    "повышение", "повысил", "повысилась", "улучшил", "улучшилась",
    "улучшение", "обновил", "обновление", "перспектива",
    "перспективы",
    # отношения
    "друг", "друга", "друзья", "друзей", "дружба", "дружбе",
    "подруга", "брат", "сестра", "семья", "семьи", "семейный",
    "горжусь", "гордимся", "поддержка", "поддерживают", "помогают",
    # юмор
    "улыбка", "улыбается", "смех", "смеётся", "смеялся", "шутка",
    "шутку", "юмор", "весело", "весёлый", "весёлая", "праздник",
    "подарок", "подарки", "подарил", "сюрприз",
}

QUESTION_WORDS = {
    # вопросительные слова и формы
    "почему", "отчего", "зачем", "как", "каким", "каком", "какова",
    "каков", "каково", "каковы", "что", "чего", "чему", "чем",
    "кто", "кого", "кому", "кем", "который", "которая", "которое",
    "которые", "какой", "какая", "какое", "какие", "какого",
    "какому", "каких", "чей", "чья", "чьё", "чьи",
    "когда", "куда", "откуда", "где", "сколько", "скольких",
    "насколько", "ли", "ужели", "неужели", "разве",
    # разговорные маркеры вопроса
    "вообще", "реально", "серьёзно", "серьезно", "точно", "уверен",
    "уверена", "правда",
    # слова о вопросе и ответе
    "вопрос", "вопросы", "вопросов", "вопросом", "спросил",
    "спросила", "спрашивают", "спрашивал", "ответ", "ответы",
    "ответом", "отвечу", "отвечаю", "отвечал", "ответил",
    "ответила", "узнал", "узнала", "узнать", "проверь", "проверил",
    "проверила", "догадался", "догадалась", "угадаешь", "угадаете",
}

EMOTION_WORDS = {
    # страх / тревога
    "страх", "страха", "страхом", "страхи", "страшно", "страшный",
    "страшная", "страшные", "испугался", "испугалась", "боюсь",
    "боишься", "боится", "боятся", "боялся", "боялась", "опасения",
    "тревога", "тревоги", "тревожно", "тревожный", "паника",
    "панике", "паникую", "паниковать", "истерика", "нервничаю",
    "нервничает", "нервный", "жуть", "жутко", "жуткий",
    # любовь / нежность
    "любовь", "любви", "любовью", "любить", "любил", "любила",
    "влюблён", "влюблена", "влюбился", "влюбилась", "романтика",
    "романтично", "романтичный", "нежность", "нежно", "нежный",
    "нежная", "страсть", "страстно", "сердце", "сердца", "душа",
    "души",
    # гнев (злой/злоба/агрессия — в NEGATIVE)
    "ненависть", "ненавижу", "ненавидел", "злюсь", "злится",
    "злятся", "гнев", "гнева", "ярость", "ярости", "яростный",
    "бешенство", "бесится",
    # радость / счастье
    "радость", "радости", "радостный", "радостно", "радуюсь",
    "рад", "рада", "счастье", "счастья", "счастливый", "счастливая",
    "счастлив", "счастлива", "счастливы", "ликует", "торжество",
    "эйфория", "экстаз", "наслаждение", "наслаждаюсь",
    "удовольствие", "удовольствия", "удовлетворение", "доволен",
    "довольна", "кайфанул",
    # грусть / подавленность
    "грусть", "грусти", "грустно", "грустный", "грустить",
    "печаль", "печали", "печальный", "печально", "тоска", "тоски",
    "тоскует", "уныние", "уныло", "депрессия", "депрессии",
    "депрессивный", "апатия", "апатично", "одиночество", "одинокий",
    "одинокая", "одиноко", "горе", "горя", "горюю", "слёзы",
    "слезы", "плачу", "плакал", "плакала", "рыдает", "скорбит",
    "отчаяние", "отчаялся", "отчаялась", "безысходность",
    "разочарование", "разочарован", "разочарована", "обида",
    "обиды", "обидно", "обиженный", "скучаю", "соскучился",
    # удивление / шок
    "удивление", "удивлён", "удивлена", "удивительно",
    "удивительный", "неожиданность", "неожиданно", "внезапно",
    "шок", "шока", "шокирован", "шокирована", "ошеломлён",
    "потрясён", "невероятно", "немыслимо",
    # вдохновение / интерес
    "восторг", "восторга", "восторженный", "восхищение",
    "восхищён", "вдохновение", "вдохновляет", "вдохновил",
    "энтузиазм", "интересно", "интересный", "увлечение",
    "увлечён", "захватывающе", "захватывающий", "интригующе",
    "загадка", "загадки",
    # надежда / мечта
    "надежда", "надежды", "надеюсь", "верю", "верит", "мечта",
    "мечты", "мечту", "мечтаю", "мечтал", "желание", "желания",
    "желаю", "стремление", "цель", "цели", "целей",
    # прочие чувства
    "смущение", "смущён", "застенчивый", "стесняется", "гордость",
    "гордый", "ностальгия", "сочувствие", "жалею", "жалость",
    "спокойствие", "умиротворение", "блаженство", "тепло",
}

KEYWORD_WORDS = {
    # важность / выделение
    "важно", "важность", "важный", "важная", "важные", "главное",
    "главный", "главная", "главные", "самый", "сама", "сам", "саме",
    "сами", "только", "лишь", "исключительно", "именно", "обязательно",
    "непременно", "однозначно", "определённо", "определенно",
    "конкретно", "буквально", "особенно",
    # секрет / правда / факт
    "секрет", "секрета", "секреты", "секретов", "тайна", "тайну",
    "скрытый", "скрывает", "скрываю", "скрывали",
    "правду", "правды", "истина", "истины", "факт", "факты",
    "фактов", "доказано", "доказательства", "доказательство",
    # универсальность
    "все", "всё", "всех", "всем", "каждый", "каждая", "каждое",
    "любой", "любая", "любое", "любые", "никто", "всегда",
    "постоянно", "вечно",
    # причина / вывод
    "причина", "причины", "причину", "причин", "потому", "поэтому",
    "следствие", "следствия", "итог", "итоги", "итоге", "вывод",
    "выводы", "выводом", "значит", "следовательно", "результате",
    # влияние / изменение
    "влияет", "влияют", "влияние", "зависит", "зависят", "изменение",
    "изменения", "меняется", "меняются", "изменяется", "превращается",
    "становится", "превратил", "изменил", "изменила",
    # деньги
    "деньги", "денег", "деньгам", "деньгами", "деньгах", "миллион",
    "миллиона", "миллионов", "миллионер", "миллиард", "миллиардов",
    "миллиардер", "рублей", "рубля", "рубль", "доллар", "долларов",
    "баксов", "зарплата", "зарплаты", "зарплату", "цена", "цены",
    "ценой", "дорого", "дешево", "дешёвый", "стоимость",
    "капитал", "бюджет", "финансы",
    # время
    "время", "времени", "временем", "минута", "минуты", "минут",
    "секунды", "секунд", "час", "часа", "часы", "день", "дня",
    "дни", "год", "года", "лет", "месяц", "месяцы", "сегодня",
    "вчера", "завтра", "сейчас", "потом", "сразу", "мгновенно",
    "момент", "момента", "быстро", "медленно", "давно", "скоро",
    # власть / статус
    "власть", "власти", "властью", "король", "королева", "царь",
    "император", "президент", "начальник", "босс", "директор",
    "звезда", "звезды",
    # мир / люди
    "народ", "народа", "народы", "страна", "страны", "стране",
    "планета", "планеты", "человечество", "человек", "человека",
    "люди", "людей", "личность", "история", "истории", "будущее",
    "прошлое", "настоящее",
    # жизнь / разум
    "жизнь", "жизни", "жизнью", "жить", "судьба", "судьбы",
    "случайность", "закономерность", "мозг", "мозга", "мозгу",
    "мозги", "разум", "сознание", "подсознание", "мышление",
    "мысль", "мысли", "память", "памяти", "логика", "логически",
    # знание / наука
    "наука", "науки", "учёные", "ученые", "исследование",
    "исследования", "изучение", "эксперимент", "эксперименты",
    "статистика", "данные", "цифры", "число", "процент", "процента",
    "процентов", "теория", "теории", "гипотеза",
    # система / метод
    "закон", "законы", "закона", "правило", "правила", "правил",
    "система", "системы", "систем", "механизм", "принцип",
    "принципы", "формула", "формулы", "метод", "методы", "способ",
    "способы", "техника", "навык", "навыки", "опыт", "опыта",
    "знания", "знание", "учиться", "учатся", "обучение", "уроки",
    "урок", "схема", "схемы", "алгоритм", "алгоритмы", "лайфхак",
    "лайфхаки", "инструкция", "пошагово",
}


def auto_detect_word_color(word: str) -> str:
    """Detect color for a word based on its meaning.
    Returns HEX color string from WORD_COLORS palette.
    """
    w = word.strip().lower().rstrip(".,!?…:;»«\"'()")

    if w in NEGATIVE_WORDS:
        return WORD_COLORS["negative"]
    if w in POSITIVE_WORDS:
        return WORD_COLORS["positive"]
    if w in QUESTION_WORDS:
        return WORD_COLORS["question"]
    if w in EMOTION_WORDS:
        return WORD_COLORS["emotion"]
    if w in KEYWORD_WORDS:
        return WORD_COLORS["keyword"]
    return WORD_COLORS["highlight"]  # default yellow


def build_accent_colors_from_text(text: str) -> dict:
    """Build accent_colors dict from text, auto-detecting colors for each word."""
    import re
    words = re.findall(r"[а-яА-ЯёЁa-zA-Z]+", text)
    result = {}
    for w in words:
        color = auto_detect_word_color(w)
        if color != WORD_COLORS["highlight"]:  # only non-default colors
            result[w.lower()] = color
    return result


# Hook color auto-detect engine
HOOK_COLORS = {
    "question": "#00FFFF",   # cyan - questions
    "negative": "#FF0000",   # red - problems, warnings
    "positive": "#00FF00",   # green - success, results
    "emotion": "#FF00FF",    # pink - emotions, drama
    "general": "#FFFFFF",    # white - default (standard)
}

# Strong signals for each type (weighted by confidence)
QUESTION_SIGNALS = {
    "почему": 1.0, "зачем": 1.0, "как": 0.8, "что": 0.7,
    "когда": 0.9, "где": 0.9, "кто": 0.9, "какой": 0.8,
    "какая": 0.8, "какое": 0.8, "сколько": 0.8, "правда ли": 1.0,
    "неужели": 1.0, "разве": 0.9, "?": 1.0,
}

NEGATIVE_SIGNALS = {
    "никогда": 1.0, "нельзя": 1.0, "ошибка": 1.0, "проблема": 1.0,
    "плохо": 0.9, "опасно": 1.0, "хватит": 0.9, "забудь": 0.8,
    "запрещено": 1.0, "ужасно": 1.0, "кошмар": 1.0, "провал": 1.0,
    "крах": 1.0, "обман": 1.0, "смерть": 1.0, "враг": 0.9,
    "stop": 1.0, "нет": 0.7, "не": 0.5,
}

POSITIVE_SIGNALS = {
    "хорошо": 0.9, "круто": 1.0, "отлично": 1.0, "результат": 1.0,
    "успех": 1.0, "победа": 1.0, "люблю": 0.9, "лучше": 0.8,
    "красиво": 0.8, "идеально": 1.0, "супер": 1.0, "класс": 0.9,
    "прекрасно": 1.0, "замечательно": 1.0, "восхитительно": 1.0,
    "yes": 1.0, "да": 0.7,
}

EMOTION_SIGNALS = {
    "любовь": 1.0, "ненависть": 1.0, "страх": 1.0, "радость": 1.0,
    "грусть": 1.0, "злость": 1.0, "удивление": 0.9, "шок": 1.0,
    "восторг": 1.0, "отчаяние": 1.0, "надежда": 0.9, "мечта": 0.9,
    "счастье": 1.0, "горе": 1.0, "боль": 0.9, "слёзы": 0.9,
    "обида": 0.9, "ревность": 1.0, "зависть": 0.9,
}


def detect_hook_type(text: str) -> tuple[str, float]:
    """Detect hook type and confidence score.
    Returns (type_name, confidence) where confidence is 0.0 to 1.0.
    
    Algorithm:
    1. Normalize text (lowercase, strip punctuation)
    2. Check for strong signals (question marks, specific words)
    3. Calculate weighted scores for each type
    4. Return type with highest score if above threshold
    5. Default to 'general' if no strong signal
    """
    import re
    
    text_lower = text.lower().strip()
    words = re.findall(r"[а-яА-ЯёЁa-zA-Z]+", text_lower)
    
    # Check for question mark (strongest signal for questions)
    has_question = "?" in text or "?" in text
    
    # Calculate scores for each type
    scores = {"question": 0.0, "negative": 0.0, "positive": 0.0, "emotion": 0.0}
    
    # Question signals
    for word in words:
        if word in QUESTION_SIGNALS:
            scores["question"] += QUESTION_SIGNALS[word]
    if has_question:
        scores["question"] += 1.5  # Strong boost for question mark
    
    # Negative signals
    for word in words:
        if word in NEGATIVE_SIGNALS:
            scores["negative"] += NEGATIVE_SIGNALS[word]
    
    # Positive signals
    for word in words:
        if word in POSITIVE_SIGNALS:
            scores["positive"] += POSITIVE_SIGNALS[word]
    
    # Emotion signals
    for word in words:
        if word in EMOTION_SIGNALS:
            scores["emotion"] += EMOTION_SIGNALS[word]
    
    # Find the highest scoring type
    max_type = max(scores, key=scores.get)
    max_score = scores[max_type]
    
    # Normalize confidence (0.0 to 1.0)
    # Threshold: need at least 1.0 to be confident
    confidence = min(1.0, max_score / 2.0)
    
    # If no strong signal, default to general
    if max_score < 0.8:
        return "general", 0.5
    
    return max_type, confidence


def detect_hook_words(text: str) -> dict:
    """Detect which words in hook should have which color.
    Returns dict of {word: color_hex}.
    
    Logic:
    1. Find all signal words
    2. Assign colors based on their type
    3. Non-signal words get the general accent color
    """
    import re
    
    words = re.findall(r"[а-яА-ЯёЁa-zA-Z]+", text)
    
    result = {}
    general_color = HOOK_COLORS["general"]
    
    for word in words:
        word_lower = word.lower()
        
        # Check each signal type
        if word_lower in QUESTION_SIGNALS:
            result[word_lower] = HOOK_COLORS["question"]
        elif word_lower in NEGATIVE_SIGNALS:
            result[word_lower] = HOOK_COLORS["negative"]
        elif word_lower in POSITIVE_SIGNALS:
            result[word_lower] = HOOK_COLORS["positive"]
        elif word_lower in EMOTION_SIGNALS:
            result[word_lower] = HOOK_COLORS["emotion"]
        else:
            # Non-signal word gets general color
            result[word_lower] = general_color
    
    return result


def get_hook_accent_color(text: str) -> str:
    """Get the primary accent color for a hook based on its content.
    Returns hex color string.
    
    This is the main function to use for hook rendering.
    """
    hook_type, confidence = detect_hook_type(text)
    
    # If confidence is low, use general color
    if confidence < 0.5:
        return HOOK_COLORS["general"]
    
    return HOOK_COLORS[hook_type]

ANIMATION_PRESETS = {
    "fade_move": "FADE_MOVE: Fade-in + slide up 12px, ease-out cubic, 150ms",
    "phrase_reveal": "PHRASE_REVEAL: Sequential word groups with 120ms delay",
    "soft_scale": "SOFT_SCALE: Scale 92%→100% + fade-in, ease-out, 140ms",
    "quick_cut": "QUICK_CUT: Instant appearance for contradictions",
    "hold_accent": "HOLD_ACCENT: Subtle pulse 97%→102%→100% for climax",
    "bounce": "BOUNCE: Spring undershoot 108%→100%, 220ms",
    "pop": "POP: Scale 80%→105%→100% with overshoot, 140ms",
}


def apply_preset_style(preset_name):
    """Возвращает готовый стиль по имени."""
    presets = {
        "AISIE Оранжевый луч (Editorial)": {
            "font_name": "TikTok Sans Black",
            "font_size": 75,
            "text_color": "#FFE53B",
            "stroke_color": "#8F1500",
            "stroke_width": 1,
            "background_color": False,
            "rounded_background": False,
            "shadow_color": (0, 0, 0),
            "shadow_offset": (3, 4),
            "shadow_blur": 4,
            "gradient_colors": None,  # градиенты убраны
            "glow_color": "#FF5E00",
            "glow_radius": 4,
            "inner_bevel": True,
            "text_3d": True,
            "uppercase": True,
            "underline": True,
            "effect": "phrase_reveal",
        },
        "Огненный 3D": {
            "font_name": "TikTok Sans Black",
            "font_size": 75,
            "text_color": "#FFE53B",
            "stroke_color": "#8F1500",
            "stroke_width": 1,
            "background_color": False,
            "rounded_background": False,
            "shadow_color": (0, 0, 0),
            "shadow_offset": (3, 4),
            "shadow_blur": 4,
            "gradient_colors": None,  # градиенты убраны
            "glow_color": "#FF5E00",
            "glow_radius": 4,
            "inner_bevel": True,
            "text_3d": True,
            "uppercase": True,
            "underline": True,
            "effect": "phrase_reveal",
        },
        "Золотой 3D": {
            "font_name": "TikTok Sans Black",
            "font_size": 75,
            "text_color": "#FFF8DC",
            "stroke_color": "#7A5200",
            "stroke_width": 1,
            "background_color": False,
            "rounded_background": False,
            "shadow_color": (0, 0, 0),
            "shadow_offset": (3, 3),
            "shadow_blur": 4,
            "gradient_colors": None,  # градиенты убраны
            "glow_color": "#FFFF00",
            "glow_radius": 3,
            "inner_bevel": True,
            "text_3d": True,
            "uppercase": True,
            "underline": True,
            "effect": "fade_move",
        },
        "Минимализм": {
            "font_name": "Inter SemiBold",
            "font_size": 0,             # 0 = auto (5% canvas_height через visual_weight L0)
            "visual_weight": "L0",
            "text_color": "#FFFFFF",
            "stroke_color": "#000000",
            "stroke_width": 0,
            "background_color": False,
            "rounded_background": False,
            "shadow_color": (0, 0, 0),
            "shadow_offset": (1, 1),
            "shadow_blur": 2,
            "gradient_colors": None,
            "glow_color": None,
            "inner_bevel": False,
            "text_3d": False,
            "uppercase": True,
            "effect": "fade_move",
        },
        "Жирный": {
            "font_size": 80,
            "text_color": "#FFFF00",
            "stroke_color": "#000000",
            "stroke_width": 3,
            "background_color": "#000000",
            "rounded_background": True,
            "shadow_color": (0, 0, 0),
            "shadow_offset": (3, 3),
            "shadow_blur": 5,
            "gradient_colors": None,  # градиенты убраны
            "uppercase": True,
        },
        "Бумажный": {
            "font_size": 55,
            "text_color": "#333333",
            "stroke_color": "#FFFFFF",
            "stroke_width": 2,
            "background_color": "#FFFFE0",
            "rounded_background": False,
            "shadow_color": (0, 0, 0),
            "shadow_offset": (2, 2),
            "shadow_blur": 2,
            "gradient_colors": None,
            "uppercase": True,
        },
        "Кино": {
            "font_name": "Inter SemiBold",
            "font_size": 0,
            "visual_weight": "L1",
            "text_color": "#FFFFFF",
            "stroke_color": "#000000",
            "stroke_width": 1,
            "background_color": False,
            "rounded_background": False,
            "shadow_color": (0, 0, 0),
            "shadow_offset": (4, 5),
            "shadow_blur": 4,
            "gradient_colors": None,
            "uppercase": True,
            "effect": "fade_move",
        },
    }
    return presets.get(preset_name, presets.get("AISIE Оранжевый луч (Editorial)", {}))


