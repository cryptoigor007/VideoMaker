"""Семантическая таксономия «псевдо-нейролинк» для подбора B-roll по смыслу.

Без нейросетей: темы + словарь RU↔EN + расширение запросов.
Порт и расширение из VideoMaker Next (library/taxonomy.py), адаптировано
под лексический матчер основного приложения.

Ключевые правила:
- RU-стемы (>=3 символов) матчатся по префиксу токена («телефоне» ≈ «телефон»);
- EN-слова — точное совпадение токена или простая форма (friend/friends);
- тема сработала → в расширение попадают ВСЕ её ru-стемы и en-слова;
- en-фразы — КОНКРЕТНЫЕ запросы для стока (визуальные, человеческие),
  первый — основной; избегаем общих слов («friends» → хаски и т.п.).
"""

from __future__ import annotations

# Тема → ru-стемы (по префиксу) и en-запросы для стока (фразы).
# Порядок фраз важен: первый — основной запрос.
THEMES: dict[str, dict[str, list[str]]] = {
    "solitude": {
        "ru": ["одиноч", "один", "изоляц", "уединен", "пуст", "покинут", "никому", "самот"],
        "en": ["lonely person window", "man alone city", "empty room"],
    },
    "friendship": {
        "ru": ["друг", "друж", "друз", "приятел", "общен", "встреч", "разговор", "поддержк", "довери", "бесед"],
        "en": ["friends talking", "people conversation", "friends walking", "friends laughing"],
    },
    "love_family": {
        "ru": ["любов", "любл", "люби", "семь", "мам", "пап", "родител", "дом", "дет", "ребен", "родн", "близк"],
        "en": ["family together home", "mother child", "couple hug"],
    },
    "phone_digital": {
        "ru": ["телефон", "смартфон", "контакт", "смс", "сообщен", "уведомл", "экран", "приложен",
               "соцсет", "интернет", "лайк", "подписчик", "чат", "скролл", "онлайн", "сеть",
               "перепис", "звон", "позвон", "звонок"],
        "en": ["smartphone hands", "phone notification", "typing phone", "messaging chat screen"],
    },
    "work_money": {
        "ru": ["работ", "офис", "деньг", "зарплат", "карьер", "бизнес", "коллег", "начальник",
               "труд", "проект", "совещан", "дедлайн"],
        "en": ["office people working", "desk laptop", "business meeting"],
    },
    "city_street": {
        "ru": ["город", "улиц", "толп", "транспорт", "метро", "прохож", "район", "этаж", "квартал", "центр"],
        "en": ["city street traffic", "urban street", "city buildings"],
    },
    "nature": {
        "ru": ["природ", "мор", "океан", "волн", "лес", "дерев", "неб", "облак", "гор", "рек",
               "солнц", "дожд", "снег", "ветер", "озер", "поле"],
        "en": ["nature landscape", "sea waves", "forest trees"],
    },
    "night_light": {
        "ru": ["ноч", "темн", "свет", "ламп", "огон", "свеч", "фонар", "рассвет", "закат", "сумер"],
        "en": ["night city lights", "street lamp night", "candle flame"],
    },
    "emotions_face": {
        "ru": ["эмоц", "чувств", "лиц", "глаз", "улыбк", "слез", "груст", "радост", "страх",
               "тревог", "стресс", "депресс", "устал", "боль", "печал", "злост",
               "шут", "смех", "смешн", "юмор", "улыб"],
        "en": ["face emotion", "sad portrait", "thinking person"],
    },
    "time_memory": {
        "ru": ["врем", "час", "минут", "возраст", "прошл", "будущ", "памят", "воспоминан", "год", "старе"],
        "en": ["old photos", "clock ticking", "vintage memories"],
    },
    "life_meaning": {
        "ru": ["жизн", "смерт", "смысл", "судьб", "душ", "сознан", "существ", "быти", "философ"],
        "en": ["person thinking sunset", "silhouette sunrise", "sky clouds"],
    },
    "party_social": {
        "ru": ["выпива", "выпит", "бар", "алкогол", "вечеринк", "праздник", "вино", "тост", "застол"],
        "en": ["friends party celebration", "people toasting glasses", "bar drinks"],
    },
    "health_body": {
        "ru": ["здоров", "болезн", "врач", "лекарств", "спорт", "тел", "сердц", "мозг", "сон", "бессонн"],
        "en": ["doctor hospital", "fitness workout", "sleep bed"],
    },
    "food_coffee": {
        "ru": ["кофе", "чай", "кухн", "готов", "обед", "ужин", "ресторан", "кафе", "завтрак",
               "еда", "куша"],
        "en": ["coffee cup close up", "cooking kitchen", "food table", "breakfast eating"],
    },
    "school_learn": {
        "ru": ["школ", "учеб", "книг", "чита", "знани", "образова", "студент", "урок", "лекц", "курс"],
        "en": ["books study", "library books", "student writing"],
    },
    "tech_ai": {
        "ru": ["технолог", "робот", "искусственн", "алгоритм", "компьютер", "ноутбук", "код", "программ",
               "нейросет", "данн", "цифров"],
        "en": ["computer code screen", "technology abstract", "robot"],
    },
    "mirror_shadow": {
        "ru": ["зеркал", "тень", "отражен", "силуэт", "двойник"],
        "en": ["mirror reflection", "shadow wall", "silhouette person"],
    },
    "hands_detail": {
        "ru": ["рук", "пальц", "пиш", "рису", "держ", "каса", "ладон"],
        "en": ["hands close up", "writing hands", "holding hands"],
    },
    "society_crowd": {
        "ru": ["обществ", "люд", "народ", "масс", "социум", "культур", "поколен", "граждан"],
        "en": ["crowd street", "people walking", "busy street"],
    },
    "window_view": {
        "ru": ["окн", "стекл", "вид из", "балкон", "подоконник"],
        "en": ["window rain view", "person looking window", "window light"],
    },
    "walk_journey": {
        "ru": ["шаг", "шага", "шаги", "бег", "движен", "дорог", "путь", "странств", "путешеств",
               "поездк", "переезд", "идет", "идеш", "идем", "идти", "пошл", "ходить", "ходит"],
        "en": ["person walking road", "walking feet", "travel journey"],
    },
    "water_calm": {
        "ru": ["вод", "капл", "течен", "спокойн", "тишин", "медитац", "дыхан"],
        "en": ["water drops", "calm water surface", "meditation nature"],
    },
    "abstract_bg": {
        "ru": ["абстракт", "фон", "текстур", "градиент", "частиц", "минимал", "паттерн"],
        "en": ["abstract background", "particles motion", "gradient background"],
    },
    "aerial_sky": {
        "ru": ["дрон", "высот", "полет", "панорам", "птиц", "сверху"],
        "en": ["aerial city", "drone landscape", "aerial nature"],
    },
    "childhood": {
        "ru": ["детств", "игр", "игрушк", "юност", "молод", "школьн", "подрост"],
        "en": ["children playing", "kids playground", "child running"],
    },
    "aging": {
        "ru": ["старост", "пожил", "бабушк", "дедушк", "пенси", "седин"],
        "en": ["elderly person hands", "grandparents", "old man portrait"],
    },
    "greeting_meeting": {
        "ru": ["рукопожат", "знаком", "приветств", "здорова", "представ"],
        "en": ["handshake business", "people greeting", "meeting people"],
    },
    "waiting_sitting": {
        "ru": ["сидит", "стоит", "ждет", "ожидан", "задума", "размышл", "очеред", "лифт"],
        "en": ["man sitting bench", "person waiting", "thinking person",
               "queue waiting line", "elevator waiting person"],
    },
    "music_art": {
        "ru": ["музык", "гитар", "пианино", "рисован", "художник", "творчеств", "искусств", "танец"],
        "en": ["guitar playing", "artist painting", "dancing"],
    },
    "sport_activity": {
        "ru": ["спорт", "бегун", "трениров", "йог", "плаван", "велосипед", "фитнес"],
        "en": ["running sport", "yoga fitness", "cycling"],
    },
}

# Простые EN-формы: слово → его словоформы (для точного расширения)
_EN_FORMS: dict[str, list[str]] = {
    "friend": ["friends", "friendship"],
    "friends": ["friend", "friendship"],
    "family": ["families"],
    "child": ["children", "kids"],
    "person": ["people"],
    "people": ["person"],
    "man": ["men"],
    "woman": ["women"],
    "city": ["cities"],
    "phone": ["phones", "smartphone"],
    "hand": ["hands"],
    "eye": ["eyes"],
    "tear": ["tears", "crying"],
    "cry": ["crying", "tears"],
    "walk": ["walking"],
    "work": ["working"],
    "talk": ["talking", "conversation"],
    "meet": ["meeting"],
    "think": ["thinking"],
    "run": ["running"],
    "rain": ["raining"],
}


def _ru_stems_of(theme: str) -> list[str]:
    return list(THEMES.get(theme, {}).get("ru", []))


def _en_phrases_of(theme: str) -> list[str]:
    return list(THEMES.get(theme, {}).get("en", []))


def _en_words_of(theme: str) -> list[str]:
    words: list[str] = []
    for phrase in _en_phrases_of(theme):
        for w in phrase.split():
            if w not in words:
                words.append(w)
    return words


def _build_index() -> tuple[dict[str, str], dict[str, str]]:
    """Возвращает (ru_stem → theme, en_word → theme) в порядке приоритета тем."""
    ru: dict[str, str] = {}
    en: dict[str, str] = {}
    for theme in _ordered_themes():
        for stem in _ru_stems_of(theme):
            ru.setdefault(stem, theme)
        for w in _en_words_of(theme):
            en.setdefault(w, theme)
    return ru, en


# Приоритет тем: порядок матчинга стемов и ранжирования запросов
_THEME_PRIORITY: list[str] = [
    "friendship", "solitude", "phone_digital", "society_crowd", "childhood",
    "love_family", "emotions_face", "city_street", "work_money", "time_memory",
    "night_light", "nature", "life_meaning", "party_social", "greeting_meeting",
    "window_view", "walk_journey", "waiting_sitting", "hands_detail", "mirror_shadow",
    "water_calm", "abstract_bg", "aerial_sky", "aging", "health_body",
    "food_coffee", "school_learn", "tech_ai", "music_art", "sport_activity",
]


def _ordered_themes() -> list[str]:
    known = set(THEMES.keys())
    out = [t for t in _THEME_PRIORITY if t in known]
    out += [t for t in THEMES.keys() if t not in out]
    return out


_RU_STEM_TO_THEME, _EN_WORD_TO_THEME = _build_index()

# Слова-исключения: начинаются как стемы, но к темам не относятся
_RU_EXCEPT: tuple[str, ...] = (
    "реклам", "одинаков", "часто", "часть", "деталь", "детектив",
)


def theme_for_token(token: str) -> str | None:
    """Тема для токена: RU по префиксу (>=3 симв.), EN по точному слову/форме."""
    if not token:
        return None
    t = token.lower()
    for bad in _RU_EXCEPT:
        if t.startswith(bad):
            return None
    # EN-слово или его простая форма
    if t in _EN_WORD_TO_THEME:
        return _EN_WORD_TO_THEME[t]
    for base, forms in _EN_FORMS.items():
        if t == base or t in forms:
            th = _EN_WORD_TO_THEME.get(base)
            if th:
                return th
            for f in forms:
                th = _EN_WORD_TO_THEME.get(f)
                if th:
                    return th
    # RU-стема по префиксу
    for stem, theme in _RU_STEM_TO_THEME.items():
        if len(stem) >= 3 and t.startswith(stem):
            return theme
    return None


def themes_for_tokens(tokens: set[str]) -> dict[str, int]:
    """Сколько попаданий у каждой темы (токен может дать одну тему)."""
    hits: dict[str, int] = {}
    for tok in tokens:
        th = theme_for_token(tok)
        if th:
            hits[th] = hits.get(th, 0) + 1
    return hits


def primary_theme(tokens: set[str]) -> str | None:
    """Главная тема текста/клипа: максимум попаданий, тай-брейк по приоритету тем.

    Нужна для точного матчинга: сцена «про друзей» (со словом «годами»)
    должна предпочитать клип о друзьях, а не о часах.
    """
    hits = themes_for_tokens(tokens)
    if not hits:
        return None
    order = {t: i for i, t in enumerate(_ordered_themes())}
    return min(hits.items(), key=lambda kv: (-kv[1], order.get(kv[0], 999)))[0]


def expand_tokens(tokens: set[str]) -> set[str]:
    """Расширение токенов: сработавшая тема добавляет все свои ru-стемы и en-слова."""
    out = set(tokens)
    for th in themes_for_tokens(tokens):
        out.update(_ru_stems_of(th))
        out.update(_en_words_of(th))
    return out


def queries_for_tokens(tokens: set[str], max_queries: int = 2, phrase_index: int = 0) -> list[str]:
    """EN-запросы для стока: по одному от лучших тем (разнообразие).

    Ранжирование: больше попаданий → приоритетнее тема (порядок _THEME_PRIORITY).
    phrase_index — какой по счёту запрос темы брать (0 — основной, 1.. — альтернативы).
    """
    hits = themes_for_tokens(tokens)
    if not hits:
        return []
    order = {t: i for i, t in enumerate(_ordered_themes())}
    ranked = sorted(hits.items(), key=lambda kv: (-kv[1], order.get(kv[0], 999)))
    out: list[str] = []
    for theme, _n in ranked:
        phrases = _en_phrases_of(theme)
        if not phrases:
            continue
        phrase = phrases[min(max(0, int(phrase_index)), len(phrases) - 1)]
        if phrase not in out:
            out.append(phrase)
        if len(out) >= max(1, max_queries):
            break
    return out


def all_theme_names() -> list[str]:
    return list(THEMES.keys())
