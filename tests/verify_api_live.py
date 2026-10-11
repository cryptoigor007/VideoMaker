# VideoMaker | 2026.09.23-r57-api.1 | 2026-09-23
# CHANGED:
#   r57-api.1: проверка живого пульта (74 проверки). Требует запущенного
#              приложения: python tests/verify_api_live.py <порт> <токен> [IP].
# PREV: (нет файла)
# NEW FILE: tests/verify_api_live.py

"""Полная проверка пульта VideoMaker на живом приложении.

Запускается снаружи: поднимает своё приложение не умеет — ждёт готовый порт.
Проверяет все маршруты, все способы авторизации, все ошибки, сеть, нагрузку.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = f"http://127.0.0.1:{sys.argv[1] if len(sys.argv) > 1 else 18765}"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else ""
LAN_IP = sys.argv[3] if len(sys.argv) > 3 else ""

OK, BAD = [], []


def check(name: str, cond: bool, detail: str = "") -> bool:
    (OK if cond else BAD).append(name)
    mark = "  ok  " if cond else " ПЛОХО"
    print(f"{mark} {name}" + (f"  [{detail}]" if detail else ""))
    return cond


def call(method: str, path: str, body=None, key: str | None = None,
         headers: dict | None = None, host: str | None = None, raw: str | None = None,
         timeout: float = 30.0):
    """→ (код, разобранный ответ, заголовки). Сетевые ошибки → (0, текст, {})."""
    url = (host or BASE) + path
    data = None
    hdrs = dict(headers or {})
    if raw is not None:
        data = raw.encode()
        hdrs.setdefault("Content-Type", "application/json")
    elif body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    if key is not None:
        hdrs["X-Api-Key"] = key
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = text
            return r.status, parsed, dict(r.headers)
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = text
        return e.code, parsed, dict(e.headers)
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}", {}


print("=" * 78)
print("1. ОТКРЫТЫЕ МАРШРУТЫ И УТЕЧКИ")
print("=" * 78)
code, page, hdrs = call("GET", "/")
check("GET / — страница отдаётся", code == 200 and "<html" in str(page), f"код {code}")
check("GET / — тип text/html", "text/html" in hdrs.get("Content-Type", ""), hdrs.get("Content-Type", ""))
code, embed, _ = call("GET", "/embed")
check("GET /embed — без заголовка h1", code == 200 and "<h1>" not in str(embed))
code, health, _ = call("GET", "/api/health")
check("GET /api/health без токена", code == 200 and health.get("ok") is True, f"код {code}")
check("health НЕ содержит токен", TOKEN not in json.dumps(health, ensure_ascii=False),
      json.dumps(health.get("api"), ensure_ascii=False))
check("health отдаёт порт/lan/enabled",
      all(k in (health.get("api") or {}) for k in ("port", "lan", "enabled")))
code, spec, _ = call("GET", "/api/openapi.json")
check("GET /api/openapi.json без токена", code == 200 and "paths" in spec, f"код {code}")

print()
print("=" * 78)
print("2. АВТОРИЗАЦИЯ — ПЯТЬ СПОСОБОВ И ОТКАЗЫ")
print("=" * 78)
code, payload, _ = call("GET", "/api/status")
check("без токена → 403", code == 403, f"код {code}")
check("без токена — понятная причина", "токен" in str(payload.get("error", "")), str(payload)[:70])
code, _, _ = call("GET", "/api/status", key="wrong-token-xyz")
check("чужой токен → 403", code == 403, f"код {code}")
code, _, _ = call("GET", f"/api/status?key={TOKEN}")
check("?key= в адресе", code == 200, f"код {code}")
code, _, _ = call("GET", "/api/status", headers={"X-Api-Key": TOKEN})
check("X-Api-Key", code == 200, f"код {code}")
code, _, _ = call("GET", "/api/status", headers={"X-Webapp-Key": TOKEN})
check("X-Webapp-Key (панель Orchestrator)", code == 200, f"код {code}")
code, _, _ = call("GET", "/api/status", headers={"X-N8N-Token": TOKEN})
check("X-N8N-Token (движок n8n)", code == 200, f"код {code}")
code, _, _ = call("GET", "/api/status", headers={"Authorization": f"Bearer {TOKEN}"})
check("Authorization: Bearer", code == 200, f"код {code}")
code, _, _ = call("GET", "/api/status", headers={"Authorization": "Bearer nope"})
check("Bearer с мусором → 403", code == 403, f"код {code}")
code, _, h = call("OPTIONS", "/api/status")
check("OPTIONS (preflight) → 204 без токена", code == 204, f"код {code}")
allow = h.get("Access-Control-Allow-Headers", "")
check("CORS разрешает X-Webapp-Key", "X-Webapp-Key" in allow, allow)
check("CORS разрешает X-N8N-Token", "X-N8N-Token" in allow, allow)
check("CORS разрешает Authorization", "Authorization" in allow, allow)

print()
print("=" * 78)
print("3. ВСЕ МАРШРУТЫ — ОТВЕТЫ")
print("=" * 78)
code, st, _ = call("GET", "/api/status", key=TOKEN)
check("GET /api/status", code == 200 and "stage" in st and "eta_sec" in st, f"код {code}")
check("status: стадия/прогресс/время/действие",
      all(k in st for k in ("stage", "progress", "elapsed_human", "eta_human", "activity", "gui_activity")))
code, job, _ = call("GET", "/api/job", key=TOKEN)
check("GET /api/job — форма Orchestrator", code == 200 and "job" in job, f"код {code}")
code, lg, _ = call("GET", "/api/log?tail=5", key=TOKEN)
check("GET /api/log", code == 200 and isinstance(lg.get("entries"), list), f"код {code}")
check("log: не больше запрошенного", len(lg.get("entries") or []) <= 5, str(len(lg.get("entries") or [])))
code, lg2, _ = call("GET", f"/api/log?since={lg.get('seq', 0)}", key=TOKEN)
check("GET /api/log?since= (новых нет)", code == 200 and lg2.get("entries") == [], f"код {code}")
code, s2, _ = call("GET", "/api/settings", key=TOKEN)
check("GET /api/settings", code == 200 and isinstance(s2.get("settings"), dict), f"код {code}")
secret = json.dumps(s2.get("settings") or {}, ensure_ascii=False)
check("settings: ключи Gemini замаскированы",
      "***" in secret or "gemini_api_key" not in secret, secret[:60])
code, cfg, _ = call("GET", "/api/config", key=TOKEN)
check("GET /api/config — адреса и токен", code == 200 and cfg.get("token") == TOKEN, f"код {code}")
check("config: сеть выключена по умолчанию", (cfg.get("api") or {}).get("lan") is False)
code, q, _ = call("GET", "/api/queue", key=TOKEN)
check("GET /api/queue", code == 200 and "items" in (q.get("queue") or {}), f"код {code}")
code, res, _ = call("GET", "/api/results?series=7", key=TOKEN)
check("GET /api/results", code == 200 and "result" in res, f"код {code}")

print()
print("=" * 78)
print("4. ОШИБОЧНЫЕ ПУТИ — НИ ОДНОГО 500")
print("=" * 78)
bad_inputs = [
    ("GET", "/api/log?tail=abc", "tail=abc"),
    ("GET", "/api/log?since=abc", "since=abc"),
    ("GET", "/api/status?log_tail=abc", "log_tail=abc"),
    ("GET", "/api/status?log_tail=-5", "log_tail=-5"),
    ("GET", "/api/status?log_tail=999999999", "log_tail=огромный"),
    ("GET", "/api/results?since=abc", "since=abc (results)"),
]
for method, path, label in bad_inputs:
    code, payload, _ = call(method, path, key=TOKEN, timeout=5)
    check(f"мусор не роняет: {label}", code in (200, 400) and code != 500, f"код {code}")

host, port = BASE.replace("http://", "").rsplit(":", 1)
try:
    with socket.create_connection((host, int(port)), timeout=5) as sk:
        sk.sendall(f"GET /api/log/stream?since=abc&key={TOKEN} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
        head = sk.recv(200).decode("utf-8", "replace")
    check("SSE с мусорным since отвечает 200", head.startswith("HTTP/1.1 200"),
          head.splitlines()[0] if head else "пусто")
except Exception as exc:  # noqa: BLE001
    check("SSE с мусорным since отвечает 200", False, str(exc))

code, payload, _ = call("GET", "/api/no-such-route", key=TOKEN)
check("неизвестный GET-путь → 404", code == 404, f"код {code}")
code, payload, _ = call("POST", "/api/no-such-route", key=TOKEN, body={})
check("неизвестный POST-путь → 404", code == 404, f"код {code}")
code, payload, _ = call("DELETE", "/api/no-such-route", key=TOKEN)
check("неизвестный DELETE-путь → 404", code == 404, f"код {code}")
code, payload, _ = call("POST", "/api/queue", key=TOKEN, body={})
check("очередь без audio → 400", code == 400, f"код {code}: {str(payload)[:60]}")
code, payload, _ = call("POST", "/api/queue", key=TOKEN, body={"items": []})
check("очередь с пустым items → 400", code == 400, f"код {code}")
code, payload, _ = call("POST", "/api/queue", key=TOKEN, body={"items": [{"audio": ""}, {"audio": "/tmp/o1.m4a"}]})
check("пачка: битую отбило, годную приняло",
      code == 200 and len(payload.get("added") or []) == 1 and len(payload.get("rejected") or []) == 1,
      f"код {code}, добавлено {len(payload.get('added') or [])}, отказ {len(payload.get('rejected') or [])}")
code, payload, _ = call("POST", "/api/config", key=TOKEN, body={"lan": "не bool", "port": "abc"})
check("config: port=abc → 400", code == 400, f"код {code}")
code, payload, _ = call("POST", "/api/config", key=TOKEN, body={"port": 99999})
check("config: port вне диапазона → 400", code == 400, f"код {code}")
code, payload, _ = call("POST", "/api/config", key=TOKEN, body={"чепуха": 1})
check("config: неизвестный параметр → 400", code == 400, f"код {code}")
code, payload, _ = call("DELETE", "/api/queue/no-such-id", key=TOKEN)
check("удалить несуществующую → 404", code == 404, f"код {code}")
code, payload, _ = call("POST", "/api/queue/no-such-id/retry", key=TOKEN, body={})
check("повторить несуществующую → 409", code == 409, f"код {code}")
code, payload, _ = call("POST", "/api/queue", key=TOKEN, raw="{это не json")
check("битый JSON в теле → не 500", code in (200, 400), f"код {code}")
code, payload, _ = call("GET", "/webhook/videomaker/render/no-such-id", key=TOKEN)
check("статус несуществующей задачи → 404", code == 404, f"код {code}")
code, payload, _ = call("POST", "/webhook/videomaker/render", key=TOKEN, body={})
check("вебхук без audio → 400", code == 400, f"код {code}")

print()
print("=" * 78)
print("5. ОЧЕРЕДЬ")
print("=" * 78)
code, q0, _ = call("GET", "/api/queue", key=TOKEN)
for it in (q0.get("queue") or {}).get("items") or []:
    call("DELETE", f"/api/queue/{it['id']}", key=TOKEN)
code, q0, _ = call("GET", "/api/queue", key=TOKEN)
check("очередь очищена перед проверкой", q0["queue"]["total"] == 0, f"осталось {q0['queue']['total']}")
call("POST", "/api/queue/pause", key=TOKEN, body={"paused": True})
code, r1, _ = call("POST", "/api/queue", key=TOKEN,
                   body={"audio": "/tmp/проверка1.m4a", "series": "101"})
check("добавить серию", code == 200 and r1["added"][0]["series"] == "101", f"код {code}")
item_id = r1["added"][0]["id"]
code, r2, _ = call("POST", "/api/queue", key=TOKEN, body={"audio": "/tmp/проверка1.m4a", "series": "101"})
check("дубликат не добавляется дважды", r2["added"][0]["id"] == item_id)
code, q, _ = call("GET", "/api/queue", key=TOKEN)
check("очередь: 1 запись", (q["queue"]["total"]) == 1, str(q["queue"]["total"]))
code, wp, _ = call("POST", "/webhook/videomaker/render", key=TOKEN,
                   body={"audio": "/tmp/проверка2.m4a", "title": "102"})
check("вебхук принимает задачу", code == 200 and wp.get("state") == "queued" and wp.get("id"), str(wp)[:70])
code, ws, _ = call("GET", f"/webhook/videomaker/render/{wp['id']}", key=TOKEN)
check("состояние задачи по id", code == 200 and ws.get("state") == "pending", str(ws)[:70])
code, q, _ = call("GET", "/api/queue", key=TOKEN)
check("очередь: 2 записи", q["queue"]["total"] == 2, str(q["queue"]["total"]))
code, rr, _ = call("POST", f"/api/queue/{wp['id']}/retry", key=TOKEN, body={})
check("повторить ожидающую → 409", code == 409, f"код {code}")
code, rm, _ = call("DELETE", f"/api/queue/{wp['id']}", key=TOKEN)
check("удалить запись", code == 200 and rm["queue"]["total"] == 1, f"код {code}")
code, pc, _ = call("POST", "/api/queue/clear", key=TOKEN, body={})
check("убрать завершённые (их нет)", code == 200 and pc["removed"] == 0, f"код {code}")
code, q, _ = call("GET", "/api/queue", key=TOKEN)
check("пауза сохранена", q["queue"]["paused"] is True)

print()
print("=" * 78)
print("6. НАГРУЗКА И ПОТОКИ")
print("=" * 78)
results: list[int] = []
lock = threading.Lock()


def hammer(n: int) -> None:
    for _ in range(n):
        c, _, _ = call("GET", "/api/status?log_tail=5", key=TOKEN)
        with lock:
            results.append(c)


threads = [threading.Thread(target=hammer, args=(6,)) for _ in range(6)]
t0 = time.time()
for t in threads:
    t.start()
for t in threads:
    t.join()
dur = time.time() - t0
check(f"36 параллельных запросов за {dur:.1f} с — все 200",
      len(results) == 36 and all(c == 200 for c in results),
      f"кодов: {sorted(set(results))}, ответов {len(results)}")

# SSE под нагрузкой
stream_lines: list[str] = []


def read_stream() -> None:
    req = urllib.request.Request(f"{BASE}/api/log/stream?key={TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            deadline = time.time() + 6
            while time.time() < deadline:
                line = r.readline().decode("utf-8", "replace")
                if line.strip():
                    stream_lines.append(line.strip())
    except Exception as exc:  # noqa: BLE001
        stream_lines.append(f"ОШИБКА: {exc}")


st_thread = threading.Thread(target=read_stream, daemon=True)
st_thread.start()
time.sleep(1)
import logging

logging.getLogger("проверка.потока").info("тестовая строка для SSE")
hammer(1)
st_thread.join(timeout=10)
check("SSE отдаёт события", len(stream_lines) > 0, f"строк {len(stream_lines)}")
check("SSE без ошибок", not any("ОШИБКА" in s for s in stream_lines), str(stream_lines[:1]))

print()
print("=" * 78)
print("7. СЕТЬ (ДОСТУП ИЗ ЛОКАЛЬНОЙ СЕТИ)")
print("=" * 78)
if LAN_IP:
    code, payload, _ = call("GET", "/api/health", host=f"http://{LAN_IP}:{BASE.rsplit(':', 1)[1]}")
    check("lan=false: снаружи не достучаться", code == 0, f"код {code} ({str(payload)[:40]})")
    code, payload, _ = call("POST", "/api/config", key=TOKEN, body={"lan": True})
    check("включить сеть → перезапуск ок", code == 200 and (payload.get("api") or {}).get("lan") is True,
          f"код {code}")
    time.sleep(0.6)
    code, payload, _ = call("GET", "/api/health", host=f"http://{LAN_IP}:{BASE.rsplit(':', 1)[1]}")
    check("lan=true: снаружи health доступен", code == 200, f"код {code}")
    code, payload, _ = call("GET", "/api/status", host=f"http://{LAN_IP}:{BASE.rsplit(':', 1)[1]}")
    check("lan=true, без токена → 403", code == 403, f"код {code}")
    code, payload, _ = call("GET", "/api/status", key=TOKEN,
                            host=f"http://{LAN_IP}:{BASE.rsplit(':', 1)[1]}")
    check("lan=true, с токеном → 200", code == 200, f"код {code}")
    code, payload, _ = call("POST", "/api/config", key=TOKEN, body={"lan": False})
    check("выключить сеть обратно", code == 200 and (payload.get("api") or {}).get("lan") is False)
    time.sleep(0.6)
    code, payload, _ = call("GET", "/api/health", host=f"http://{LAN_IP}:{BASE.rsplit(':', 1)[1]}")
    check("lan=false снова закрыт", code == 0, f"код {code}")
else:
    print("  (IP не передан — пропуск)")

print()
print("=" * 78)
print(f"ИТОГ: успешно {len(OK)}, провалено {len(BAD)}")
if BAD:
    print("ПРОВАЛЫ:")
    for name in BAD:
        print("   -", name)
print("=" * 78)
sys.exit(1 if BAD else 0)
