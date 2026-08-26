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

def clean_words(words):
    unique = {}
    for item in words:
        key = (round(item["start"], 6), round(item["end"], 6), item["word"])
        unique[key] = item
    words = list(unique.values())
    words.sort(key=lambda x: x["start"])
    return words

def find_phrase(words, phrase):
    target = tokenize_phrase(phrase)
    if not target: return None
    norm = [normalize_word(item["word"]) for item in words]
    n = len(target)
    for i in range(len(norm) - n + 1):
        if norm[i:i+n] == target:
            matched = words[i:i+n]
            return {
                "start": matched[0]["start"],
                "end": matched[-1]["end"],
                "matched_words": matched,
                "target_words": target,
                "match_type": "EXACT"
            }
    return None

def format_time(seconds):
    seconds = float(seconds)
    h = int(seconds // 3600); m = int((seconds % 3600) // 60); s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}" if h else f"{m:02d}:{s:06.3f}"
