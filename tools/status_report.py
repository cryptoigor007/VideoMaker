#!/usr/bin/env python3
"""Страница прогресса базы: /tmp/vm_status/status.html (+status.json).

Обновляется каждые 30с (внешним циклом). Открывается с любого устройства:
    http://<ip-мака>:8899/status.html
Показывает: что идёт сейчас, сколько сделано/осталось (по количеству и времени),
очереди, статистику базы и последние строки журнала.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

OUT = Path("/tmp/vm_status")
DB = Path.home() / "video_maker" / "broll_library.db"
LIC_DIR = Path("/Volumes/video/broll_library/licenses")
LOG = Path("/tmp/library_build.log")
ROOT = Path("/Volumes/video/broll_library")


def sh(cmd: str) -> str:
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


def alive(pat: str) -> bool:
    return bool(sh(f'pgrep -f "{pat}" | head -1'))


def db_stats() -> dict:
    out = {"total": 0, "4k": 0, "desc": 0, "neg": 0, "br": 0, "low_br": 0, "wide": 0, "vert": 0}
    try:
        conn = sqlite3.connect(str(DB))
        q = conn.execute
        out["total"] = q("SELECT COUNT(*) FROM clips").fetchone()[0]
        out["4k"] = q("SELECT COUNT(*) FROM clips WHERE width >= 3840 OR height >= 2160").fetchone()[0]
        out["desc"] = q("SELECT COUNT(*) FROM clips WHERE description IS NOT NULL AND description != ''").fetchone()[0]
        try:
            out["neg"] = q("SELECT COUNT(*) FROM clips WHERE neg_tags IS NOT NULL AND neg_tags != '' AND neg_tags != '[]'").fetchone()[0]
        except Exception:
            pass
        try:
            out["br"] = q("SELECT COUNT(*) FROM clips WHERE bitrate > 0").fetchone()[0]
            out["low_br"] = q("SELECT COUNT(*) FROM clips WHERE bitrate > 0 AND bitrate < 12000000").fetchone()[0]
        except Exception:
            pass
        out["wide"] = q("SELECT COUNT(*) FROM clips WHERE orientation = 'wide'").fetchone()[0]
        out["vert"] = q("SELECT COUNT(*) FROM clips WHERE orientation = 'vertical'").fetchone()[0]
        conn.close()
    except Exception:
        pass
    return out


def cert_count() -> tuple[int, int]:
    try:
        j = len(list(LIC_DIR.glob("*.json")))
        h = len(list(LIC_DIR.glob("*.html")))
        return j, h
    except Exception:
        return 0, 0


def redescribe_progress() -> tuple[int, int, float]:
    """(сделано промежуточного прохода, сделано полного прохода, часов в работе)."""
    main_done = shard_done = 0
    for f in Path("/tmp").glob("library_redescribe_done*.txt"):
        try:
            n = len([ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()])
        except Exception:
            n = 0
        if f.name == "library_redescribe_done.txt":
            main_done += n
        else:
            shard_done += n
    hours = 0.0
    pids = sh('pgrep -f "redescribe" | head -3').split()
    for p in pids:
        et = sh(f"ps -o etime= -p {p}").strip()
        try:
            parts = et.replace("-", ":").split(":")
            sec = 0
            for x in parts:
                sec = sec * 60 + int(x)
            hours = max(hours, sec / 3600.0)
        except Exception:
            pass
    return main_done, shard_done, hours


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    st = db_stats()
    certs_json, certs_html = cert_count()
    main_done, shard_done, hours = redescribe_progress()

    dl_wide = alive("build_library.py --orientation wide")
    dl_vert = alive("build_library.py --orientation vertical")
    sh_pids = len(sh('pgrep -f "redescribe-shard"').splitlines())
    # ── серия: сцены/оценки/добор (по последнему манифесту) ──
    series_lines: list[str] = []
    try:
        import glob as _g
        _mans = sorted(_g.glob("/Volumes/SSD/темп/**/smart_broll_manifest.json", recursive=True),
                       key=lambda f: -Path(f).stat().st_mtime)
        if _mans:
            _man = json.loads(Path(_mans[0]).read_text(encoding="utf-8"))
            _scs = _man.get("scenes") or []
            _dls = _man.get("downloaded") or []
            _from_lib = sum(1 for d in _dls if d.get("from_library"))
            _dl = sum(1 for d in _dls if not d.get("from_library"))
            _scores = [d.get("score") for d in _dls if isinstance(d.get("score"), int)]
            _avg = (sum(_scores) / len(_scores)) if _scores else 0
            _rep = Path(_mans[0]).parent / "проверка_сцен.txt"
            _ok = _bad = _q = 0
            if _rep.exists():
                _t = _rep.read_text(encoding="utf-8")
                _ok = _t.count("\n✓") + (1 if _t.startswith("✓") else 0)
                _bad = _t.count("\n✗") + (1 if _t.startswith("✗") else 0)
                _q = _t.count("\n?") + (1 if _t.startswith("?") else 0)
            _need = max(0, len(_scs) - _ok)
            series_lines.append(f"Серия «{_man.get('series')}»: сцен {len(_scs)}")
            series_lines.append(f"✓ подходящих: {_ok} | ✗ не хватает: {_bad} | ? без оценки: {_q}")
            series_lines.append(f"из базы: {_from_lib} | скачано: {_dl} | добрать примерно: {_need} сцен")
            series_lines.append(f"средний балл клипов: {_avg:.1f}/10")
    except Exception:
        pass

    # ── текущие задачи (серия 10, вертикаль) ──
    jobs: list[str] = []
    try:
        _log = Path("/tmp/series10.log").read_text(encoding="utf-8", errors="ignore")
        _fixed = _log.count("ЗАМЕНЕНО")
        _last_itog = ""
        for _ln in _log.splitlines():
            if "ИТОГ:" in _ln:
                _last_itog = _ln.strip()
        if alive("fix_scenes"):
            jobs.append(f"🔧 Серия 10: починка сцен (заменено: {_fixed}) {_last_itog}")
        elif alive("verify_scenes"):
            jobs.append(f"👁 Серия 10: проверка по секундам {_last_itog}")
        else:
            jobs.append(f"Серия 10: пауза/завершено {_last_itog}")
    except Exception:
        pass
    try:
        _vc = len(list(Path("/Volumes/video/broll_library/vertical/local").glob("*.mp4")))
        if alive("vm_v8"):
            jobs.append(f"📥 Вертикаль: перелив коллекции — скопировано {_vc} из ~895")
        else:
            jobs.append(f"📥 Вертикаль: завершено — {_vc} файлов")
    except Exception:
        pass

    stages: list[tuple[str, str, str]] = []  # (статус, имя, детали)

    if alive("fetch_licenses.py"):
        stages.append(("⏳ идёт", "Сертификаты — страницы источников", f"{certs_json} готово из ~{st['total']}"))
    else:
        stages.append(("✅", "Сертификаты — страницы источников", f"{certs_json} документов"))

    if alive("vm_v6c.sh"):
        if alive("redescribe"):
            stages.append(("⏳ идёт", "Переописание Vision (старый проход)", ""))
        elif alive("build_library.py"):
            stages.append(("⏳ идёт", "Добор клипов (вертикаль)", ""))
        else:
            stages.append(("⏳ идёт", "Очередь v6c", "переописание + вертикаль"))
    if alive("vm_v6d.sh"):
        stages.append(("⏳/🕐", "Чистка не-4K + докачка 4K-замен", "в очереди" if not alive("build_library.py --orientation wide") else "идёт"))
    if alive("vm_v6e2.sh"):
        if sh_pids:
            det2 = "идёт полное переописание (3-5 кадров, +/−)"
        elif dl_wide or dl_vert:
            det2 = "идёт докачка 4K-замен"
        else:
            det2 = "идёт чистка/подготовка"
        stages.append(("⏳ идёт", "Чистка низкого битрейта + докачка + переописание ×3 (3-5 кадров, +/− слова)", det2))
    if alive("vm_v6f2.sh"):
        stages.append(("🕐", "Сертификаты: добор новых", "в очереди"))

    # что реально бежит сейчас (по процессам)
    running = []
    if alive("fetch_licenses.py"):
        running.append("📜 Сертификаты (страницы источников)")
    n_shards = sh_pids
    if n_shards:
        running.append(f"👁️ Переописание Vision — {n_shards} потока(ов)")
    if alive('build_library.py --orientation wide'):
        running.append("📥 Докачка клипов (горизонталь 4K)")
    if alive('build_library.py --orientation vertical'):
        running.append("📥 Докачка клипов (вертикаль 4K)")

    # остаток переописания и оценка времени
    shard_active = bool(n_shards) or shard_done > 0
    if shard_active:
        done_use = shard_done
        phase_label = "Полное переописание (3-5 кадров, +/− слова)"
    else:
        done_use = main_done
        phase_label = "Переописание (промежуточный проход; полное — в очереди)"
    remaining = max(0, st["total"] - done_use)
    rate = 0.0
    if hours > 0.5 and done_use > 0:
        rate = done_use / hours
    eta_txt = "—"
    if n_shards and rate > 5:
        eta_txt = f"~{remaining / rate:.1f} ч (по {rate:.0f} клипов/час)"
    elif n_shards:
        eta_txt = "оценка появится через ~1 час работы"

    try:
        _lines = LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
        log_tail = "\n".join(_lines[-12:])
        _recent = _lines[-40:]
    except Exception:
        log_tail = "(журнал недоступен)"
        _recent = []
    err_hits = sum(1 for ln in _recent if ("vision не удался" in ln or "партия пропущена" in ln or "недоступен" in ln))

    # сторож прогресса: сколько минут счётчик не менялся
    import time as _t
    _pstate = OUT / "progress_state.json"
    _prev_done, _prev_ts = -1, 0.0
    try:
        _d = json.loads(_pstate.read_text(encoding="utf-8"))
        _prev_done, _prev_ts = int(_d.get("done", -1)), float(_d.get("ts", 0.0))
    except Exception:
        pass
    if done_use > _prev_done:
        _prev_ts = _t.time()
    stalled_min = int((_t.time() - _prev_ts) / 60) if (_prev_ts and sh_pids) else 0
    try:
        _pstate.write_text(json.dumps({"done": max(done_use, _prev_done), "ts": _prev_ts}), encoding="utf-8")
    except Exception:
        pass

    banner_cls, banner_txt = "ok", "🟢 Работа идёт"
    if err_hits >= 2:
        banner_cls = "err"
        banner_txt = ("🔴 ОПИСАНИЯ (Vision) НЕ РАБОТАЮТ — переописание стоит. "
                      "Что делать: переподключить OpenCode Console (в терминале: opencode auth login) "
                      "или подождать восстановления Gemini (перегрузка 503). Подробности — в журнале внизу.")
    elif stalled_min >= 15:
        banner_cls = "warn"
        banner_txt = f"🟡 Прогресс не идёт уже {stalled_min} мин — возможно, зависло. Скажи ассистенту."
    elif not (sh_pids or dl_wide or dl_vert or alive("fetch_licenses.py")):
        banner_cls = "warn"
        banner_txt = "🟡 Очереди не запущены — скажи ассистенту, чтобы возобновил."

    now = datetime.now().strftime("%H:%M:%S")

    def bar(done_n: int, total_n: int, width: int = 40) -> str:
        total_n = max(1, total_n)
        filled = int(width * min(1.0, done_n / total_n))
        return "█" * filled + "░" * (width - filled)

    rows = "".join(
        f"<tr><td>{s}</td><td>{name}</td><td class='dim'>{det}</td></tr>" for s, name, det in stages
    )
    run_html = "".join(f"<li>{r}</li>" for r in running) or "<li class='dim'>ожидание очереди…</li>"

    html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="30">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>База клипов — прогресс</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f1115;color:#e8e8ea;margin:0;padding:24px}}
 h1{{font-size:22px;margin:0 0 4px}} .dim{{color:#8b8f98}} .big{{font-size:34px;font-weight:700}}
 .card{{background:#171a21;border:1px solid #262a33;border-radius:12px;padding:16px 18px;margin:12px 0}}
 table{{width:100%;border-collapse:collapse}} td,th{{padding:6px 8px;border-bottom:1px solid #22262e;text-align:left;font-size:14px}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}
 .k{{font-size:26px;font-weight:700}} .l{{font-size:12px;color:#8b8f98}}
 pre{{background:#0b0d11;border:1px solid #22262e;border-radius:8px;padding:10px;overflow:auto;font-size:11px;color:#9aa0ab}}
 .bar{{font-family:monospace;color:#57d38c;letter-spacing:1px}}
 .banner{{padding:12px 16px;border-radius:10px;margin:10px 0;font-weight:600;line-height:1.45}}
 .ok{{background:#12351f;color:#7ee2a8;border:1px solid #1d5c36}}
 .warn{{background:#3a3313;color:#ffd76e;border:1px solid #6b5d1c}}
 .err{{background:#3d1414;color:#ff9d9d;border:1px solid #7a2626}}
</style></head><body>
<h1>🎬 База клипов — ход работ</h1>
<div class="banner {banner_cls}">{banner_txt}</div>
<div class="dim">обновлено {now} · авто-обновление каждые 30 секунд</div>

<div class="card"><b>Сейчас работает:</b><ul>{run_html}</ul></div>
<div class="card"><b>Последняя серия:</b><ul>{''.join(f'<li>{l}</li>' for l in series_lines) or '<li class="dim">—</li>'}</ul></div>
<div class="card"><b>Текущие задачи:</b><ul>{''.join(f'<li>{j}</li>' for j in jobs) or '<li class="dim">—</li>'}</ul></div>

<div class="card"><b>Этапы:</b><table><tr><th></th><th>Этап</th><th>Детали</th></tr>{rows}</table></div>

<div class="card"><b>База:</b>
<div class="grid">
 <div><div class="k">{st['total']}</div><div class="l">клипов всего</div></div>
 <div><div class="k">{st['4k']}</div><div class="l">из них 4K</div></div>
 <div><div class="k">{st['desc']}</div><div class="l">с описанием</div></div>
 <div><div class="k">{st['neg']}</div><div class="l">с +/− словами</div></div>
 <div><div class="k">{certs_json}</div><div class="l">сертификатов</div></div>
 <div><div class="k">{st['wide']} / {st['vert']}</div><div class="l">гориз. / верт.</div></div>
</div></div>

<div class="card"><b>{phase_label}:</b>
<div class="big">{min(done_use, st['total'])} / {st['total']}</div>
<div class="bar">{bar(min(done_use, st['total']), st['total'])}</div>
<div class="dim">осталось: {remaining} клипов · время: {eta_txt}</div>
</div>

<div class="card"><b>Журнал (последние строки):</b><pre>{log_tail}</pre></div>
</body></html>"""

    (OUT / "status.html").write_text(html, encoding="utf-8")
    (OUT / "status.json").write_text(json.dumps({
        "updated": now, "base": st, "certificates": certs_json,
        "redescribe_done": done_use, "redescribe_main": main_done, "redescribe_shard": shard_done,
        "redescribe_remaining": remaining,
        "rate_per_hour": round(rate, 1), "eta": eta_txt, "running": running,
        "stages": [{"status": s, "name": n, "details": d} for s, n, d in stages],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    try:  # дублируем на видео-сервер (уже обслуживает порт 8899)
        sb = Path("/tmp/vm_sb_out")
        if sb.exists():
            (sb / "status.html").write_text(html, encoding="utf-8")
            (sb / "status.json").write_text((OUT / "status.json").read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass

    print(f"[{now}] всего {st['total']} | 4K {st['4k']} | описания {st['desc']} | +/− {st['neg']} | "
          f"сертификаты {certs_json} | переописано {min(done_use, st['total'])}/{st['total']} | "
          f"осталось {remaining} | {eta_txt}")
    if running:
        print("работает: " + "; ".join(running))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
