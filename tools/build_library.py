#!/usr/bin/env python3
"""Сборка «большой базы» клипов на сервере: скачивание → резкость → Vision → БД.

Примеры:
    # 2 клипа на запрос, 20 запросов из словаря тем
    python3 tools/build_library.py --per-query 2 --max-queries 20

    # все темы словаря (~100 запросов) — большая база
    python3 tools/build_library.py --per-query 2 --max-queries 0

    # свои запросы
    python3 tools/build_library.py --queries "lonely man window; friends talking; phone night"

База: /Volumes/video/broll_library (files + library.db). Размытые клипы
отбрасываются (кроме запросов, где размытие — часть замысла: blur/bokeh).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

# Состояние бесплатной VL-модели OpenCode (консоль может отвалиться — тогда Gemini)
_VL_STATE: dict = {"opencode_ok": True}


def _all_queries() -> list[tuple[str, str, str]]:
    """(запрос, категория, фраза) — словарь тем + универсальные вариации монтажа."""
    from video_maker.semantic.taxonomy import THEMES, _en_phrases_of
    out: list[tuple[str, str, str]] = []
    for theme in THEMES:
        for ph in _en_phrases_of(theme):
            out.append((ph, theme, ph))
    # универсальные вариации (покрытие монтажных нужд: таймлапсы, атмосфера, детали)
    universal = [
        ("city timelapse traffic", "universal_city", "city timelapse traffic"),
        ("night city aerial", "universal_city", "night city aerial"),
        ("crowd timelapse street", "universal_city", "crowd timelapse street"),
        ("subway train people", "universal_city", "subway train people"),
        ("rain window drops", "universal_atmosphere", "rain window drops"),
        ("clouds sky timelapse", "universal_atmosphere", "clouds sky timelapse"),
        ("sunset golden hour", "universal_atmosphere", "sunset golden hour"),
        ("foggy forest walk", "universal_atmosphere", "foggy forest walk"),
        ("ocean waves slow", "universal_nature", "ocean waves slow"),
        ("mountain drone view", "universal_nature", "mountain drone view"),
        ("river water flow", "universal_nature", "river water flow"),
        ("field grass wind", "universal_nature", "field grass wind"),
        ("office timelapse people", "universal_work", "office timelapse people"),
        ("hands typing keyboard", "universal_detail", "hands typing keyboard"),
        ("eyes close up blink", "universal_detail", "eyes close up blink"),
        ("face portrait thinking", "universal_detail", "face portrait thinking"),
        ("coffee cup steam", "universal_detail", "coffee cup steam"),
        ("phone screen glow night", "universal_detail", "phone screen glow night"),
        ("walking feet pavement", "universal_detail", "walking feet pavement"),
        ("train window view", "universal_travel", "train window view"),
        ("airport people waiting", "universal_travel", "airport people waiting"),
        ("car driving night lights", "universal_travel", "car driving night lights"),
        ("light leak transition", "universal_transition", "light leak transition"),
        ("abstract bokeh lights", "universal_transition", "abstract bokeh lights"),
        ("smoke slow motion dark", "universal_transition", "smoke slow motion dark"),
        ("water drops slow motion", "universal_transition", "water drops slow motion"),
        ("paper burning close up", "universal_transition", "paper burning close up"),
        ("dust particles light beam", "universal_transition", "dust particles light beam"),
        ("old film grain texture", "universal_transition", "old film grain texture"),
        ("clock hands time lapse", "universal_detail", "clock hands time lapse"),
    ]
    out.extend(universal)
    return out


def _blur_variance(frame_path: str) -> float:
    """Резкость кадра (дисперсия Лапласа): меньше ~40 — размыто/непонятно."""
    try:
        import cv2
        img = cv2.imread(frame_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return -1.0
        return float(cv2.Laplacian(img, cv2.CV_64F).var())
    except Exception:
        return -1.0


def _bitrate_of(path) -> float:
    """Битрейт клипа (бит/с) — качество 4K."""
    try:
        import subprocess as _sp
        r = _sp.run(["ffprobe", "-v", "error", "-show_entries", "format=bit_rate",
                     "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=25)
        return float((r.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def _describe_batch(batch: list[dict], *, log=print) -> bool:
    """Vision-описание партии клипов бесплатной VL-моделью OpenCode."""
    from video_maker.semantic.verify import extract_frames, _find_opencode
    bin_path = _find_opencode()
    if not bin_path:
        return False
    tmp = Path(str(batch[0]["file"])).parent / f".describe_frames_{os.getpid()}"
    try:  # чистая папка кадров этой партии
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)
    except Exception:
        pass
    lines: list[str] = []
    frame_files: list[str] = []
    included: list[dict] = []
    n = 0
    for e in batch:
        frames = extract_frames(str(e["file"]), tmp, count=0)
        if not frames:
            continue
        included.append(e)
        lines.append(f"{len(included)}. Кадры {n + 1}-{n + len(frames)}.")
        frame_files.extend(frames)
        n += len(frames)
    if not lines:
        return False
    prompt = (
        "Опиши каждый видеоклип по его кадрам (кадры идут по порядку).\n"
        + "\n".join(lines) + "\n"
        "Для КАЖДОГО клипа верни ТОЛЬКО валидный JSON без пояснений: "
        '{"clips":[{"clip":N,"description":"1-2 фразы: кто/что, где, действие, настроение",'
        '"tags":["4-8 тегов на английском: предмет, действие, место, настроение, тип плана (close-up/wide/aerial/medium)"],'
        '"negative":["3-6 слов на английском: чего в клипе НЕТ или что вводит в заблуждение (например, имя файла про телефон, а в кадре шоссе)"],'
        '"quality":0-10,"blurry":true/false},...]} — по элементу на клип, N — номер клипа.'
    )
    args = [bin_path, "run", "-m", "opencode/nemotron-3.5-super-vl-free",
            "--format", "json", prompt]
    for f in frame_files:
        args += ["-f", f]

    data: dict | None = None
    rate_limited = False
    # 1) бесплатный VL OpenCode (3 попытки; при лимите частоты — пауза и повтор)
    if _VL_STATE.get("opencode_ok", True) and bin_path and time.time() >= float(_VL_STATE.get("opencode_skip_until", 0)):
        import subprocess as _sp
        import fcntl as _fl
        for attempt in (1, 2):
            r = None
            try:
                with open("/tmp/vl_call.lock", "w") as _lf:
                    _fl.flock(_lf, _fl.LOCK_EX)
                    try:
                        r = _sp.run(args, capture_output=True, text=True, timeout=150)
                    finally:
                        _fl.flock(_lf, _fl.LOCK_UN)
            except Exception as e:
                log(f"[LIB] opencode VL недоступен: {str(e)[:100]}")
                break
            text = ""
            for ln in (r.stdout or "").splitlines():
                try:
                    ev = json.loads(ln)
                except Exception:
                    continue
                if isinstance(ev, dict) and ev.get("type") == "text":
                    text += str((ev.get("part") or {}).get("text") or "")
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(0))
                except Exception:
                    data = None
            if data is not None:
                break
            if "Rate limit" in (r.stdout or ""):
                rate_limited = True
                _VL_STATE["opencode_skip_until"] = time.time() + 1800  # 30 мин — сразу к Gemini
                if attempt < 2:
                    log(f"[LIB] лимит частоты opencode — пауза 45с (попытка {attempt}/2)")
                    time.sleep(45)
                    continue
                break
            log("[LIB] VL вернул ошибку авторизации — переключаюсь на Gemini")
            _VL_STATE["opencode_ok"] = False
            break
    # 2) фолбэк: Gemini (бесплатные ключи по очереди; работает и при лимите opencode)
    if data is None:
        try:
            from video_maker.semantic.verify import _gemini_vision
            from video_maker.semantic.smart_broll import _load_gemini_keys
            _key, _keys = _load_gemini_keys()
            allk = ([_key] if _key else []) + list(_keys or [])
            for k in allk:
                for mdl in ("gemini-3.5-flash-lite", "gemini-flash-latest", "gemini-3.8-flash"):
                    try:
                        d = _gemini_vision(prompt, frame_files, key=k, model=mdl, timeout=90)
                    except Exception:
                        d = None
                    if isinstance(d, dict):
                        data = d
                        break
                if data is not None:
                    break
            if data is not None and allk:
                log(f"[LIB] Gemini сработал (ключ …{str(k)[-6:]})")
        except Exception as e:
            log(f"[LIB] Gemini-фолбэк не удался: {str(e)[:100]}")
    if data is None:
        return False
    try:
        clips = data.get("clips") or []
    except Exception:
        return False
    # сопоставление по номеру клипа (N); позиционное — только как запасной путь
    by_n: dict[int, dict] = {}
    for c in clips:
        if isinstance(c, dict):
            try:
                by_n[int(str(c.get("clip")))] = c
            except Exception:
                pass
    ok = False
    for idx, e in enumerate(included, 1):
        c = by_n.get(idx)
        if c is None and len(clips) == len(included) and idx - 1 < len(clips):
            c = clips[idx - 1]
        if not isinstance(c, dict):
            continue
        e["description"] = str(c.get("description") or "")[:200]
        tags = c.get("tags") or []
        e["tags"] = [str(t) for t in tags][:8]
        neg = c.get("negative") or []
        e["neg_tags"] = [str(t) for t in neg][:8]
        try:
            e["score"] = max(0, min(10, int(round(float(c.get("quality", -1))))))
        except Exception:
            e["score"] = -1
        e["blurry_vision"] = bool(c.get("blurry"))
        ok = True
    if ok:
        time.sleep(4)  # мягкий темп: не долбим бесплатный лимит
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="Сборка базы клипов с Vision-описаниями")
    ap.add_argument("--root", default="/Volumes/video/broll_library")
    ap.add_argument("--per-query", type=int, default=2, help="клипов на запрос")
    ap.add_argument("--max-queries", type=int, default=20,
                    help="сколько запросов обработать (0 = все из словаря)")
    ap.add_argument("--queries", default="", help="свои запросы через ;")
    ap.add_argument("--min-blur", type=float, default=40.0,
                    help="минимальная резкость (дисперсия Лапласа); меньше — отбросить")
    ap.add_argument("--orientation", choices=["wide", "vertical"], default="wide")
    ap.add_argument("--only-4k", action="store_true", default=True,
                    help="только 4K (стандарт базы; по умолчанию включено)")
    ap.add_argument("--allow-hd", action="store_true", help="разрешить HD (не для базы)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--redescribe", action="store_true",
                    help="заново описать ВСЕ клипы базы (Vision) и обновить БД")
    ap.add_argument("--min-bitrate", type=float, default=12.0,
                    help="минимальный битрейт, Мбит/с (качественный 4K; 0 — не проверять)")
    ap.add_argument("--add-dir", default="",
                    help="добавить в базу свои видео из папки (без скачивания)")
    ap.add_argument("--redescribe-shard", default="",
                    help="шард переописания N/M (для параллельных потоков)")
    args = ap.parse_args()

    from video_maker.semantic.library import (
        DEFAULT_LIBRARY_ROOT, PIXABAY_LICENSE, PIXABAY_LICENSE_URL,
        PEXELS_LICENSE, PEXELS_LICENSE_URL, init_db, url_known, upsert_clip,
    )
    from video_maker.semantic.stock import download_file, safe_slug, search_stock
    from video_maker.semantic.verify import extract_frames

    root = Path(args.root) if args.root else Path(DEFAULT_LIBRARY_ROOT)
    queries = [q.strip() for q in args.queries.split(";") if q.strip()]
    if queries:
        packs = [(q, "custom", q) for q in queries]
    else:
        packs = _all_queries()
    if args.max_queries:
        packs = packs[: args.max_queries]

    conn = init_db(root)

    # ── РЕЖИМ ПЕРЕОПИСАНИЯ: заново описать все клипы (исправление смещённых описаний) ──
    # Возобновляемый: прогресс в файле состояния — обрыв не теряет работу.
    if args.redescribe:
        shard_n, shard_m = 0, 1
        if args.redescribe_shard:
            try:
                shard_n, shard_m = (int(x) for x in args.redescribe_shard.split("/"))
            except Exception:
                shard_n, shard_m = 0, 1
        state = Path("/tmp/library_redescribe_done.txt" if shard_m <= 1 else f"/tmp/library_redescribe_done_{shard_n}.txt")
        done_set: set[str] = set()
        if state.exists():
            try:
                done_set = {ln.strip() for ln in state.read_text(encoding="utf-8").splitlines() if ln.strip()}
            except Exception:
                done_set = set()
        rows = conn.execute(
            "SELECT id, file, query, category, provider, source_url, author, license, license_url, "
            "blur, width, height, duration, orientation FROM clips ORDER BY id"
        ).fetchall()
        todo: list[dict] = []
        for r in rows:
            f = str(r[1] or "")
            if not f or not Path(f).exists() or f in done_set:
                continue
            if shard_m > 1 and (int(r[0]) % shard_m) != shard_n:
                continue
            todo.append({
                "file": f, "query": r[2], "category": r[3], "provider": r[4],
                "source_url": r[5], "author": r[6], "license": r[7], "license_url": r[8],
                "blur": r[9], "width": r[10], "height": r[11], "duration": r[12],
                "orientation": r[13],
            })
        print(f"[LIB] переописание: {len(todo)} клипов (партии по 3, 3 кадра; "
              f"уже сделано ранее: {len(done_set)})", flush=True)
        done = 0
        _vl_fail = 0
        _sf = open(state, "a", encoding="utf-8")
        for i in range(0, len(todo), 3):
            b = todo[i:i + 3]
            if _describe_batch(b, log=print):
                _vl_fail = 0
                for e in b:
                    try:
                        upsert_clip(conn, e, root)
                    except Exception as _ue:
                        print(f"[LIB] ⚠ запись в БД не удалась (пропуск): {str(_ue)[:80]}", flush=True)
                        continue
                    done += 1
                    print(f"[LIB] ✎(re) {Path(e['file']).name[:40]} — {str(e.get('description'))[:70]} | {e.get('tags')}", flush=True)
                    _sf.write(str(e["file"]) + "\n")
                _sf.flush()
            else:
                print(f"[LIB] ✎(re) партия пропущена: {Path(b[0]['file']).name[:40]}…", flush=True)
                _vl_fail += 1
                if _vl_fail >= 3:  # Vision недоступен — ждём восстановления, потом сами продолжим
                    print(f"[LIB] ⏸ Vision недоступен ({_vl_fail} партий подряд) — пауза 10 мин, затем пробуем снова", flush=True)
                    time.sleep(600)
                    _VL_STATE["opencode_ok"] = True  # перепроверим консоль после паузы
                    _vl_fail = 0
        _sf.close()
        print(f"\n[LIB] ПЕРЕОПИСАНО: {done}/{len(todo)}", flush=True)
        return 0

    # ── СВОИ ВИДЕО В БАЗУ (без скачивания): папка → 4K/битрейт → Vision → БД ──
    if args.add_dir:
        import glob as _glob
        import shutil as _sh
        src_dir = Path(args.add_dir)
        files = [f for f in sorted(_glob.glob(str(src_dir / "**/*.mp4"), recursive=True))
                 if not any(x in f for x in ("готовые шортсы", "Использованные", "_tmp"))]
        print(f"[LIB] добавление своих видео: {len(files)} файлов из {src_dir}", flush=True)
        pending_add: list[dict] = []
        added_local = skipped_local = 0
        for f in files:
            f = str(f)
            try:
                import subprocess as _sp
                r = _sp.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                             "-show_entries", "stream=width,height", "-of", "csv=p=0", f],
                            capture_output=True, text=True, timeout=25)
                parts = (r.stdout or "0,0").strip().split(",")
                w, h = int(parts[0]), int(parts[1])
            except Exception:
                continue
            if not args.allow_hd and not (w >= 3840 or h >= 2160):
                skipped_local += 1
                print(f"[LIB] ✗ не 4K — пропущен: {Path(f).name[:50]}")
                continue
            br = _bitrate_of(f)
            if args.min_bitrate and 0 < br < args.min_bitrate * 1_000_000:
                skipped_local += 1
                print(f"[LIB] ✗ низкий битрейт ({br / 1e6:.1f} Мбит/с) — пропущен: {Path(f).name[:50]}")
                continue
            orient = "vertical" if h > w else "wide"
            folder = root / ("vertical" if orient == "vertical" else "horizontal") / "local"
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / Path(f).name
            if dest.exists() or conn.execute("SELECT 1 FROM clips WHERE file = ? LIMIT 1", (str(dest),)).fetchone():
                continue
            try:
                _sh.copy2(f, dest)
            except Exception as e:
                print(f"[LIB] ✗ копирование не удалось: {Path(f).name[:50]} — {e}")
                continue
            rec = {
                "file": str(dest), "orientation": orient, "provider": "local",
                "source_url": "", "author": "", "query": Path(f).stem[:60], "category": "local",
                "license": "сток (скачано ранее; Pixabay/Pexels — свободная лицензия)",
                "license_url": "https://pixabay.com/service/license-summary/",
                "blur": -1, "width": w, "height": h, "duration": 0, "bitrate": round(br, 0),
                "tags": [],
            }
            upsert_clip(conn, rec, root)
            added_local += 1
            pending_add.append(rec)
            print(f"[LIB] ✓ своё видео: {dest.name[:50]} ({w}x{h}, {br / 1e6:.0f} Мбит/с)")
        for i in range(0, len(pending_add), 3):
            b = pending_add[i:i + 3]
            if _describe_batch(b, log=print):
                for e in b:
                    upsert_clip(conn, e, root)
                    print(f"[LIB] ✎ {Path(e['file']).name[:40]} — {str(e.get('description'))[:70]}")
        print(f"[LIB] добавлено своих: {added_local} | пропущено: {skipped_local}", flush=True)
        return 0

    print(f"[LIB] база: {root} | запросов: {len(packs)} | на запрос: {args.per_query}")

    added = skipped_blur = skipped_low = failed = 0
    pending: list[dict] = []  # на Vision-описание
    for q, cat, _ph in packs:
        cands = search_stock(q, orientation=args.orientation)
        taken = 0
        for c in cands:
            if taken >= args.per_query:
                break
            url = str(c.get("url") or "")
            if not url or url_known(conn, url):
                continue
            if not args.allow_hd and not c.get("is4k"):
                continue  # стандарт: только 4K
            slug = safe_slug(q)
            folder = root / ("vertical" if args.orientation == "vertical" else "horizontal") / cat
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / f"{slug}_{c.get('id')}_{c.get('provider')}.mp4"
            if args.dry_run:
                print(f"[LIB] (dry) {q} → {dest.name}")
                taken += 1
                continue
            if not download_file(url, dest):
                failed += 1
                continue
            # резкость: 2 кадра; размытые отбрасываем (если запрос не про blur/bokeh)
            wants_blur = bool(re.search(r"blur|bokeh|размыт", q, re.IGNORECASE))
            frames = extract_frames(str(dest), root / ".frames", count=2)
            blur = min((_blur_variance(f) for f in frames), default=-1.0)
            if not wants_blur and 0 <= blur < args.min_blur:
                skipped_blur += 1
                print(f"[LIB] ✗ размыто ({blur:.0f}) — отброшен: {dest.name}")
                try:
                    dest.unlink()
                except Exception:
                    pass
                continue
            br = _bitrate_of(dest)
            if args.min_bitrate and 0 < br < args.min_bitrate * 1_000_000:
                skipped_low += 1
                print(f"[LIB] ✗ низкий битрейт ({br / 1e6:.1f} Мбит/с) — отброшен: {dest.name}")
                try:
                    dest.unlink()
                except Exception:
                    pass
                continue
            prov = str(c.get("provider") or "pixabay")
            rec = {
                "file": str(dest), "orientation": args.orientation,
                "provider": prov, "source_url": url,
                "author": str(c.get("author") or ""), "query": q, "category": cat,
                "license": PIXABAY_LICENSE if prov == "pixabay" else PEXELS_LICENSE,
                "license_url": PIXABAY_LICENSE_URL if prov == "pixabay" else PEXELS_LICENSE_URL,
                "blur": round(blur, 1), "bitrate": round(br, 0),
                "width": c.get("width"), "height": c.get("height"),
                "duration": c.get("duration"), "tags": [t.strip() for t in str(c.get("tags") or "").split(",") if t.strip()],
            }
            upsert_clip(conn, rec, root)
            added += 1
            taken += 1
            pending.append(rec)
            print(f"[LIB] ✓ {dest.name} ({c.get('duration'):.0f}с, {c.get('width')}x{c.get('height')}, резкость {blur:.0f})")
        # описание партиями по 3
        while len(pending) >= 3:
            b, pending = pending[:3], pending[3:]
            if _describe_batch(b):
                for e in b:
                    upsert_clip(conn, e, root)
                    print(f"[LIB] ✎ {Path(e['file']).name[:40]} — {str(e.get('description'))[:60]} | {e.get('tags')}")
    for e in pending:
        if _describe_batch([e]):
            upsert_clip(conn, e, root)
            print(f"[LIB] ✎ {Path(e['file']).name[:40]} — {str(e.get('description'))[:60]}")

    # ── РЕТРАЙ: описать всё, что осталось без описания (сбои Vision) ──
    try:
        rows = conn.execute(
            "SELECT file, query, category, provider, source_url, author, license, license_url, "
            "blur, width, height, duration, orientation FROM clips "
            "WHERE description IS NULL OR description = ''"
        ).fetchall()
        todo = [{
            "file": r[0], "query": r[1], "category": r[2], "provider": r[3],
            "source_url": r[4], "author": r[5], "license": r[6], "license_url": r[7],
            "blur": r[8], "width": r[9], "height": r[10], "duration": r[11],
            "orientation": r[12],
        } for r in rows]
        if todo:
            print(f"[LIB] ретрай описаний: {len(todo)} клипов", flush=True)
            for i in range(0, len(todo), 3):
                b = todo[i:i + 3]
                if _describe_batch(b, log=print):
                    for e in b:
                        upsert_clip(conn, e, root)
                        print(f"[LIB] ✎(retry) {Path(e['file']).name[:40]} — {str(e.get('description'))[:60]}", flush=True)
    except Exception as e:
        print(f"[LIB] ретрай пропущен: {e}", flush=True)

    # ── покрытие ──
    try:
        total = conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
        described = conn.execute(
            "SELECT COUNT(*) FROM clips WHERE description IS NOT NULL AND description != ''"
        ).fetchone()[0]
        print(f"\n[LIB] ПОКРЫТИЕ: {described}/{total} клипов с описанием", flush=True)
    except Exception:
        pass
    print(f"[LIB] добавлено: {added} | отброшено размытых: {skipped_blur} | низкий битрейт: {skipped_low} | ошибок: {failed}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
