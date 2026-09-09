import re


def normalize_word(word):
    word = str(word or "").lower().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", "", word)

def tokenize_phrase(text):
    raw = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", text)
    return [normalize_word(x) for x in raw if normalize_word(x)]

def extract_words(obj, result):
    if isinstance(obj, dict):
        if "word" in obj and isinstance(obj.get("start"), (int, float)) and isinstance(obj.get("end"), (int, float)):
            result.append({
                "word": str(obj.get("word", "")),
                "start": float(obj["start"]),
                "end": float(obj["end"]),
                "score": float(obj["score"]) if isinstance(obj.get("score"), (int, float)) else None
            })
        for value in obj.values(): extract_words(value, result)
    elif isinstance(obj, list):
        for item in obj: extract_words(item, result)

