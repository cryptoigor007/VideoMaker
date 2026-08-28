# AISIE visual style constants (HOOK_TYPES, VISUAL_WEIGHTS)
# Moved here from legacy text_style_utils.py

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
    "L0": {
        "font_scale": 0.85,
        "glow_radius": 0,
        "font_name": "TikTok Sans Bold",
        "text_color": "#FFFFFF",
        "description": "Normal subtitle",
    },
    "L1": {
        "font_scale": 0.92,
        "glow_radius": 0,
        "font_name": "TikTok Sans Bold",
        "text_color": "#FFFFFF",
        "description": "Emphasis",
    },
    "L2": {
        "font_scale": 1.00,
        "glow_radius": 0,
        "font_name": "TikTok Sans ExtraBold",
        "text_color": "#FFFF00",
        "description": "Keyword",
    },
    "L3": {
        "font_scale": 1.08,
        "glow_radius": 0,
        "font_name": "TikTok Sans Black",
        "text_color": "#FF3B30",
        "description": "Hook",
    },
    "L4": {
        "font_scale": 1.15,
        "glow_radius": 0,
        "font_name": "TikTok Sans Black",
        "text_color": "#FF3B30",
        "description": "Climax / Punchline",
    },
}
