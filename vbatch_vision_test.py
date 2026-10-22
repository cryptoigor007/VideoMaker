#!/usr/bin/env python3
"""Контрольный тест Vision-батча (шаги 4-5): привязка кадров, замер, предфильтр.

Запуск: .venv/bin/python vbatch_vision_test.py
Только чтение библиотеки; оценки в кэш не пишутся (use_cache=False).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import video_maker.semantic.verify as V  # noqa: E402
from video_maker.engines.video import _ffmpeg_bin  # noqa: E402
from video_maker.semantic.smart_broll import _load_gemini_keys  # noqa: E402

MODEL = "gemini-3.1-flash-lite"
SCENE = "Человек держит смартфон и печатает сообщение"
CLIPS = [
    ("phone", "смартфон в руках", "/Volumes/video/broll_library/phone_digital/smartphone_hands_47743_pixabay.mp4"),
    ("lake", "природа/озеро", "/Volumes/video/broll_library/horizontal/love_family/mother_child_39972_pixabay.mp4"),
    ("office", "офис/ноутбук", "/Volumes/video/broll_library/work_money/office_people_working_87592_pixabay.mp4"),
    ("food", "еда/готовка", "/Volumes/video/broll_library/horizontal/food_coffee/cooking_kitchen_153727_pixabay.mp4"),
]


def _working_key() -> str:
    key, keys = _load_gemini_keys()
    allk: list[str] = []
    for k in [key] + list(keys or []):
        if k and k not in allk:
            allk.append(k)
    if not allk:
        raise SystemExit("нет ключей Gemini")
    import urllib.request
    for k in allk:
        body = json.dumps({"contents": [{"parts": [{"text": "ping"}]}],
                           "generationConfig": {"maxOutputTokens": 1}}).encode()
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
            data=body, headers={"Content-Type": "application/json", "x-goog-api-key": k},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                json.loads(r.read())
            return k
        except Exception:
            continue
    raise SystemExit("ни один ключ Gemini не ответил")


def _old_way(key: str, td: Path) -> tuple[list[dict], float, float]:
    out: list[dict] = []
    api_times: list[float] = []
    orig = V._gemini_vision

    def timed(*a, **kw):
        t = time.time()
        r = orig(*a, **kw)
        api_times.append(time.time() - t)
        return r

    V._gemini_vision = timed
    t0 = time.time()
    try:
        for label, _expect, f in CLIPS:
            r = V.verify_clip(f, SCENE, [SCENE], key=key, model=MODEL, tmp_dir=td / label)
            out.append({"label": label, **r})
    finally:
        V._gemini_vision = orig
    return out, time.time() - t0, sum(api_times)


def _batch_way(key: str, td: Path) -> tuple[list[dict], float, list[str]]:
    logs: list[str] = []
    entries = [{"file": f, "scene": SCENE, "query": "", "duration": None, "label": label}
               for label, _e, f in CLIPS]
    t0 = time.time()
    V.verify_candidates_gemini(entries, key=key, keys=[], model=MODEL, batch_size=4,
                               min_score=5, use_cache=False, frames_count=2,
                               tmp_dir=td, log=lambda s: (logs.append(str(s)), print(s)))
    dt = time.time() - t0
    return [{"label": e["label"], "score": e.get("score"), "what": e.get("what")}
            for e in entries], dt, logs


def _prefilter_test() -> dict:
    logs: list[str] = []
    td = Path(tempfile.mkdtemp(prefix="vbatch_pf_"))
    short = td / "short.mp4"
    subprocess.run([_ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=30:duration=1", "-pix_fmt", "yuv420p",
                    str(short)], capture_output=True)
    broken = td / "broken.mp4"
    broken.write_text("not a video" * 1000)
    phone, lake, office, food = (f for _l, _e, f in CLIPS)
    entries = [
        {"file": phone, "duration": 7, "tags": "smartphone, hands, typing"},
        {"file": lake, "duration": 15, "tags": "swans, lake, nature"},
        {"file": office, "duration": 21, "tags": "office, laptop"},
        {"file": phone, "duration": 7, "tags": "smartphone"},              # дубль пути
        {"file": food, "duration": 11, "tags": "food, smiling",           # hard
         "description": "chef smiling while cooking", "hard": ["smiling"]},
        {"file": str(short), "duration": 1.0},                             # короткий
        {"file": str(broken), "duration": None},                           # битый
    ]
    dups = V.prefilter_candidates(entries, log=lambda s: (logs.append(str(s)), print(s)))
    return {
        "log": logs,
        "dups": len(dups),
        "entries": [{"file": Path(e["file"]).name, "score": e.get("score"),
                     "verified": bool(e.get("verified")), "what": e.get("what")}
                    for e in entries],
    }


def _split_fallback_test() -> dict:
    td = Path(tempfile.mkdtemp(prefix="vbatch_split_"))
    files = []
    for i in range(5):
        p = td / f"c{i}.mp4"
        subprocess.run([_ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=640x360:rate=30:duration=2", "-vf", f"hue=h={i * 60}",
                        "-pix_fmt", "yuv420p", str(p)], capture_output=True)
        files.append(str(p))

    def fake(parts, keys, model, *, timeout=180, log=print, tries=3, expect="dict", counter=None):
        if counter is not None:
            counter[0] += 1
        n = sum(1 for p in parts if str(p.get("text", "")).startswith("Кандидат "))
        if n > 3:
            return [{"n": 1, "score": 9, "what": "invalid batch"}]
        return [{"n": i + 1, "score": 8 if i == 0 else 1, "what": f"cat {i + 1}"}
                for i in range(n)]

    logs: list[str] = []
    orig = V._gemini_json
    V._gemini_json = fake
    try:
        entries = [{"file": f, "scene": "split test", "duration": 2} for f in files]
        V.verify_candidates_gemini(entries, key="x", model="fake", batch_size=5,
                                   use_cache=False, min_score=5,
                                   log=lambda s: (logs.append(str(s)), print(s)))
        got = [e.get("score") for e in entries]
    finally:
        V._gemini_json = orig
    return {"scores": got, "log": logs,
            "pass": got == [8, 1, 8, 1, 1]}


def main() -> int:
    key = _working_key()
    print("ключ Gemini: рабочий найден")
    td = Path(tempfile.mkdtemp(prefix="vbatch_control_"))

    print("\n=== СТАРЫЙ СПОСОБ: по одному клипу ===")
    old, old_dt, old_api_dt = _old_way(key, td / "old")
    print("\n=== БАТЧ: 4 кандидата за 1 вызов ===")
    batch, batch_dt, batch_logs = _batch_way(key, td / "batch")

    print("\n=== ПРЕДФИЛЬТР ===")
    pf = _prefilter_test()
    print("\n=== FALLBACK: дробление 5 -> 2+3 (без API) ===")
    split = _split_fallback_test()

    print("\n\n| Кандидат | Ожидание | Старый способ | Батч | Что в кадре (батч) |")
    print("|---|---|---|---|---|")
    for (label, expect, _f), o, b in zip(CLIPS, old, batch):
        print(f"| {label} | {expect} | {o.get('score')}/10 | {b.get('score')}/10 | "
              f"{str(b.get('what'))[:70]} |")

    calls = sum(1 for ln in batch_logs if "батч " in ln and "вызов" in ln)
    batch_api_dt = 0.0
    for ln in batch_logs:
        if "батч " in ln and "вызов" in ln:
            try:
                batch_api_dt += float(str(ln).split(", ")[-1].split("с")[0])
            except Exception:
                pass
    print(f"\nВремя: старый {old_dt:.1f}с (4 вызова; Vision {old_api_dt:.1f}с) | "
          f"батч {batch_dt:.1f}с (1 вызов; Vision {batch_api_dt:.1f}с + 4 извлечения кадров)")
    print(f"Батч-лог: {next((ln for ln in batch_logs if 'батч ' in ln), '')}")
    print(f"Предфильтр: {pf['log'][0] if pf['log'] else 'нет отсева'}")
    print(f"Fallback 5->2+3: {'PASS' if split['pass'] else 'FAIL'} {split['scores']}")

    out = {
        "scene": SCENE,
        "old": old, "old_sec": round(old_dt, 2), "old_api_sec": round(old_api_dt, 2),
        "batch": batch, "batch_sec": round(batch_dt, 2),
        "batch_api_sec": round(batch_api_dt, 2),
        "batch_log": batch_logs,
        "prefilter": pf,
        "split_fallback": split,
        "batch_calls": calls,
    }
    Path("vbatch_results.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    print("результаты: vbatch_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
