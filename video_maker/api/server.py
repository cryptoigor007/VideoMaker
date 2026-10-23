# VideoMaker API | 2026.09.23-r57-api.1 | 2026-09-23
# CHANGED:
#   r57-api: новый файл. HTTP-сервер пульта (stdlib, без зависимостей):
#            status/run/cancel/log/log-stream/settings/queue/results/config,
#            токен (заголовок или ?key=), CORS для панели Orchestrator,
#            веб-страница, SSE-поток логов, вебхук по завершении серии.
#   r57-api.1: health без токена; bind только на 127.0.0.1 пока нет сети;
#              мусор в параметрах → значение по умолчанию, не 500; config проверяет
#              значения и перезапускает сервер; CORS пускает X-Webapp-Key/X-N8N-Token;
#              снят мёртвый дубль /api/run-log; своя очередь соединений (128)
#              и тихий лог обрывов от телефона; убран обратный DNS при старте
#              (он висел 3 с и «замораживал» галочку доступа из сети).
# PREV: (нет файла)
# NEW FILE: video_maker/api/server.py

"""HTTP-пульт VideoMaker.

Рассчитан на два сценария сразу:
  1) локально/с телефона — веб-страница `/` со статусом, очередью и логами;
  2) из Orchestrator — JSON API по токену (`X-Api-Key`, `Authorization: Bearer`
     или `?key=`), с CORS, чтобы страница панели могла звать напрямую.
"""
from __future__ import annotations

import contextlib
import hmac
import json
import logging
import os
import re
import secrets
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import webui
from ..engines import memory_guard
from .control import (
    apply_settings,
    cancel_pipeline,
    find_results,
    get_settings_schema,
    read_settings,
    start_pipeline,
    sync_settings_from_gui,
    validate,
)
from .openapi import openapi_spec

log = logging.getLogger(__name__)

CONFIG_DIR = os.path.expanduser("~/video_maker")
CONFIG_PATH = os.path.join(CONFIG_DIR, "api.json")
DEFAULT_PORT = 8765
SSE_MAX_SECONDS = 1800
MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024
_SECRET_QUERY_RE = re.compile(r"(?i)([?&](?:key|token|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|secret|password|signature|auth|credential)=)[^&\s\"\']*")


class ApiRequestError(ValueError):
    """Expected client error while parsing an HTTP request body."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _safe_url_for_log(url: str) -> str:
    """Remove common query credential values before writing URLs to logs."""
    safe = _SECRET_QUERY_RE.sub(r"\1[REDACTED]", str(url or ""))
    # URLs can also carry basic-auth userinfo; don't copy those credentials
    # into warning logs when webhook delivery fails.
    return re.sub(r"(?i)(https?://)[^/@\s]+@", r"\1[REDACTED]@", safe)


def _q_int(query: dict, name: str, default: int, lo: int = 0, hi: int = 100000) -> int:
    """Целое из query-параметра. Мусор и выход за границы → default (без 500)."""
    raw = (query.get(name) or [None])[0]
    if raw in (None, ""):
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        log.warning("[API] параметр %s=%r не число — беру %s", name, raw, default)
        return default
    return max(lo, min(hi, value))


def _q_float(query: dict, name: str, default: float = 0.0) -> float:
    """Число с плавающей точкой из query-параметра (мусор → default)."""
    raw = (query.get(name) or [None])[0]
    if raw in (None, ""):
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        log.warning("[API] параметр %s=%r не число — беру %s", name, raw, default)
        return default


# ── конфигурация пульта ──────────────────────────────────────────────────
def load_config() -> dict:
    """Настройки пульта: порт, токен, разрешён ли доступ из сети."""
    conf = {
        "enabled": True,
        "port": DEFAULT_PORT,
        "lan": False,
        "token": "",
        "webhook_url": "",
        "webhook_token": "",
    }
    try:
        if os.path.isfile(CONFIG_PATH):
            with open(CONFIG_PATH, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                conf.update(loaded)
            else:
                log.warning("[API] корень %s должен быть JSON-объектом; использованы безопасные настройки по умолчанию", CONFIG_PATH)
                conf["enabled"] = False
    except (OSError, ValueError, TypeError) as exc:
        log.warning("[API] не удалось прочитать %s: %s", CONFIG_PATH, exc)
        conf["enabled"] = False

    # Treat malformed persisted values conservatively: a string such as
    # "false" must never enable binding to 0.0.0.0 by Python truthiness.
    if type(conf.get("lan")) is not bool:
        conf["lan"] = False
    if type(conf.get("enabled")) is not bool:
        conf["enabled"] = False  # malformed value must not enable API access
    raw_port = conf.get("port", DEFAULT_PORT)
    if isinstance(raw_port, bool) or not isinstance(raw_port, int) or not 1 <= raw_port <= 65535:
        conf["port"] = DEFAULT_PORT
    if not isinstance(conf.get("webhook_url", ""), str):
        conf["webhook_url"] = ""
    need_save = False
    token_value = conf.get("token")
    if (not isinstance(token_value, str) or not token_value.isascii()
            or not 24 <= len(token_value) <= 256):
        conf["token"] = secrets.token_urlsafe(24)
        need_save = True
        log.info("[API] сгенерирован токен доступа")
    webhook_token = conf.get("webhook_token")
    if (not isinstance(webhook_token, str) or not webhook_token.isascii()
            or not 16 <= len(webhook_token) <= 256):
        # Отдельный секрет для исходящих вебхуков: основной токен пульта
        # открывает весь HTTP-пульт и в чужие запросы не отправляется.
        conf["webhook_token"] = secrets.token_urlsafe(18)
        need_save = True
    if need_save:
        save_config(conf)
    return conf


def save_config(conf: dict) -> None:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(conf, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except OSError:
            pass
    except OSError as exc:
        log.warning("[API] не удалось сохранить %s: %s", CONFIG_PATH, exc)


def local_ip() -> str:
    """Адрес Mac в локальной сети (для ссылки на телефоне)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


# ── сам пульт ────────────────────────────────────────────────────────────
class VideoMakerAPI:
    """Связывает HTTP-запросы с работающим приложением."""

    def __init__(self, app, bridge, status, queue, conf: dict):
        self.app = app
        self.bridge = bridge
        self.status = status
        self.queue = queue
        self.conf = conf
        self.started_at = time.time()
        self.queue.on_finish = self._on_queue_finish
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ── запуск/остановка ─────────────────────────────────────────────────
    def start(self) -> str:
        """Start listening only when explicitly enabled; safe for reconfiguration."""
        if self.conf.get("enabled") is not True:
            self.stop()
            log.info("[API] пульт отключён настройкой enabled=false")
            return ""
        # Do not create a second listener when called by a settings refresh.
        if self._httpd is not None and self._thread is not None and self._thread.is_alive():
            port = int(self.conf.get("port") or DEFAULT_PORT)
            return f"http://127.0.0.1:{port}/?key={self.conf['token']}"

        host = "0.0.0.0" if self.conf.get("lan") is True else "127.0.0.1"
        port = int(self.conf.get("port") or DEFAULT_PORT)
        handler = _make_handler(self)
        try:
            httpd = _PultServer((host, port), handler)
        except OSError as exc:
            self._httpd = None
            self._thread = None
            log.error("[API] порт %s занят или недоступен: %s", port, exc)
            return ""
        thread = threading.Thread(target=httpd.serve_forever, name="vm-api", daemon=True)
        try:
            thread.start()
        except Exception:
            with contextlib.suppress(Exception):
                httpd.server_close()
            self._httpd = None
            self._thread = None
            log.exception("[API] не удалось запустить поток HTTP-сервера")
            return ""
        self._httpd = httpd
        self._thread = thread
        url = f"http://127.0.0.1:{port}/?key={self.conf['token']}"
        log.info("[API] пульт слушает %s:%s (сеть: %s)", host, port,
                 "разрешена" if self.conf.get("lan") is True else "запрещена")
        # Never write the bearer token to application logs. The URL returned
        # to the GUI still contains the legacy bootstrap key for compatibility.
        log.info("[API] панель локально доступна: http://127.0.0.1:%s/ (токен не журналируется)", port)
        if self.conf.get("lan") is True:
            log.info("[API] панель в локальной сети доступна на порту %s (токен не журналируется)", port)
        return url

    def stop(self) -> None:
        httpd, thread = self._httpd, self._thread
        self._httpd = None
        self._thread = None
        if httpd is None:
            return
        if thread is not None and thread.is_alive():
            with contextlib.suppress(Exception):
                httpd.shutdown()
        with contextlib.suppress(Exception):
            httpd.server_close()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def urls(self) -> dict:
        port = int(self.conf.get("port") or DEFAULT_PORT)
        return {
            "local": f"http://127.0.0.1:{port}/?key={self.conf['token']}",
            "network": (f"http://{local_ip()}:{port}/?key={self.conf['token']}"
                        if self.conf.get("lan") else ""),
            "port": port,
            "lan": bool(self.conf.get("lan")),
            "enabled": bool(self.conf.get("enabled", True)),
        }

    def public_urls(self) -> dict:
        """Сводка для /api/health и внешних проверок — БЕЗ токена."""
        return {
            "port": int(self.conf.get("port") or DEFAULT_PORT),
            "lan": bool(self.conf.get("lan")),
            "enabled": bool(self.conf.get("enabled", True)),
        }

    # ── авторизация ──────────────────────────────────────────────────────
    def check_key(self, provided: str, remote: str) -> tuple[bool, str]:
        if not self.conf.get("enabled", True):
            return False, "пульт выключен в настройках"
        loopback = remote in ("127.0.0.1", "::1", "localhost")
        if not loopback and not self.conf.get("lan"):
            return False, "доступ из сети запрещён (включите LAN в настройках пульта)"
        if not provided:
            return False, "нет токена: ?key=, X-Api-Key или Authorization: Bearer"
        # Сравнение байтами: compare_digest падает на не-ASCII строках, а ключ
        # приходит из запроса и может содержать любые юникод-символы.
        try:
            provided_bytes = provided.encode("utf-8")
            token_bytes = str(self.conf.get("token", "")).encode("utf-8")
        except UnicodeEncodeError:
            return False, "неверный токен"
        if not hmac.compare_digest(provided_bytes, token_bytes):
            return False, "неверный токен"
        return True, ""

    # ── действия ─────────────────────────────────────────────────────────
    def status_payload(self, log_tail: int = 30) -> dict:
        try:
            snap = self.status.snapshot(log_tail=log_tail)
        except RuntimeError as exc:
            # Запрос пришёл в момент закрытия окна: это не сбой, клиенту нужен
            # честный 503 вместо traceback в журнале.
            if "TkBridge" in str(exc):
                raise ApiRequestError(503, "приложение закрывается") from exc
            raise
        snap["queue"] = self.queue.snapshot()
        snap["api"] = self.urls()
        return snap

    def do_run(self, body: dict) -> tuple[int, dict]:
        snap = self.status.snapshot(log_tail=0)
        if snap.get("running"):
            return 409, {"ok": False, "error": "пайплайн уже выполняется"}

        overrides = dict(body or {})
        audio = overrides.pop("audio", None)
        output = overrides.pop("output", None)
        series = overrides.pop("series", None)
        if audio:
            overrides["audio_path"] = audio
        if output:
            overrides["output_folder"] = output
        if series:
            overrides["series_name"] = series

        result: dict = {}

        def _go():
            applied, unknown = apply_settings(self.app, overrides)
            # app.settings ← GUI-поля (как кнопка «Создать видео»): запуск через API
            # не должен зависеть от того, нажимали ли кнопку в GUI до этого.
            sync_settings_from_gui(self.app)
            errors = validate(self.app)
            result["applied"] = applied
            result["unknown"] = unknown
            result["errors"] = errors
            result["started"] = False
            result["memory_paused"] = False
            result["memory_pause_message"] = ""
            result["resumed_from_memory_pause"] = False
            if not errors:
                # Headless «Продолжить»: /api/run после паузы по памяти тоже
                # проверяет RAM и не запускает рендер, пока приложения не закрыты.
                if bool(getattr(self.app, "_memory_pause_active", False)):
                    ok, memory_msg = memory_guard.can_continue()
                    result["memory_pause_message"] = memory_msg
                    if not ok:
                        result["memory_paused"] = True
                        return
                    result["resumed_from_memory_pause"] = True
                start_pipeline(self.app)
                # App._start has additional checks (B-roll and collected audio)
                # that may return without raising or starting the worker.
                result["started"] = bool(getattr(self.app, "running", False))

        self.bridge.call(_go, timeout=120)
        if result.get("memory_paused"):
            return 409, {"ok": False,
                         "error": result.get("memory_pause_message") or
                                  "[MEM] Недостаточно памяти — закройте приложения и повторите",
                         "memory_pause": True,
                         "started": False,
                         "status": self.status_payload(0)}
        if result.get("errors"):
            log.error("[API] запуск отклонён: %s", "; ".join(result["errors"]))
            return 400, {"ok": False, "error": "проверка не пройдена",
                         "errors": result["errors"],
                         "unknown_fields": result.get("unknown") or []}
        if not result.get("started"):
            return 409, {"ok": False,
                         "error": "приложение не запустило обработку; проверьте источники и B-roll",
                         "started": False,
                         "applied": result.get("applied") or [],
                         "unknown_fields": result.get("unknown") or [],
                         "status": self.status_payload(0)}
        time.sleep(0.05)
        return 200, {"ok": True, "started": True,
                     "resumed_from_memory_pause": bool(result.get("resumed_from_memory_pause")),
                     "applied": result.get("applied") or [],
                     "unknown_fields": result.get("unknown") or [],
                     "status": self.status_payload(0)}

    def do_cancel(self) -> tuple[int, dict]:
        snap = self.status.snapshot(log_tail=0)
        if not snap.get("running"):
            return 409, {"ok": False, "error": "нечего отменять — пайплайн не запущен"}
        self.queue.note_cancel()
        self.bridge.call(lambda: cancel_pipeline(self.app), timeout=30)
        return 200, {"ok": True, "canceled": True}

    def do_settings_set(self, body: dict) -> tuple[int, dict]:
        applied, unknown = self.bridge.call(lambda: apply_settings(self.app, body or {}), timeout=60)

        def _save():
            try:
                self.app._save_settings()
            except Exception as exc:  # noqa: BLE001
                log.warning("[API] не удалось сохранить настройки: %s", exc)

        self.bridge.call(_save, timeout=30)
        return 200, {"ok": True, "applied": applied, "unknown_fields": unknown,
                     "settings": self.bridge.call(lambda: read_settings(self.app), timeout=30)}

    def do_config_set(self, body: dict) -> tuple[int, dict]:
        body = body or {}
        unknown = [k for k in body if k not in ("lan", "enabled", "port", "webhook_url")]
        if unknown:
            return 400, {"ok": False, "error": f"неизвестный параметр: {', '.join(unknown)}"}

        for key in ("lan", "enabled"):
            if key in body and type(body[key]) is not bool:
                return 400, {"ok": False, "error": f"{key} должен быть boolean (true/false), а не строкой или числом"}
        if "webhook_url" in body and not isinstance(body["webhook_url"], str):
            return 400, {"ok": False, "error": "webhook_url должен быть строкой"}

        old_conf = dict(self.conf)
        new_conf = dict(old_conf)
        changed: list[str] = []
        rebind = False
        if "port" in body:
            raw_port = body["port"]
            if isinstance(raw_port, bool):
                return 400, {"ok": False, "error": "port должен быть целым числом 1..65535"}
            try:
                port = int(raw_port)
            except (TypeError, ValueError):
                return 400, {"ok": False, "error": "port должен быть целым числом 1..65535"}
            if not 1 <= port <= 65535 or str(raw_port).strip() not in (str(port), f"+{port}"):
                return 400, {"ok": False, "error": "port должен быть целым числом 1..65535"}
            new_conf["port"] = port
            changed.append("port")
            rebind = True
        for key in ("lan", "enabled"):
            if key in body:
                new_conf[key] = body[key]
                changed.append(key)
                rebind = True
        if "webhook_url" in body:
            new_conf["webhook_url"] = body["webhook_url"].strip()
            changed.append("webhook_url")

        if not rebind:
            self.conf.clear()
            self.conf.update(new_conf)
            save_config(self.conf)
            return 200, {"ok": True, "changed": changed, "api": self.urls()}

        # Rebind transactionally: on failure restore the previous config and
        # attempt to restore the previous listener rather than leaving API down.
        old_was_listening = bool(self._httpd is not None and self._thread is not None and self._thread.is_alive())
        self.stop()
        self.conf.clear()
        self.conf.update(new_conf)

        if self.conf.get("enabled") is True:
            url = self.start()
            if not url:
                self.stop()
                self.conf.clear()
                self.conf.update(old_conf)
                save_config(self.conf)
                restored = bool(self.start()) if old_was_listening and self.conf.get("enabled") is True else False
                return 500, {"ok": False,
                             "error": "не удалось применить новые настройки; восстановлена предыдущая конфигурация",
                             "changed": [], "rolled_back": True,
                             "previous_listener_restored": restored,
                             "api": self.public_urls()}
        else:
            # Disable must actually close the listening socket; do not call start().
            self.stop()

        save_config(self.conf)
        return 200, {"ok": True, "changed": changed, "api": self.public_urls() if self.conf.get("enabled") is not True else self.urls()}

    def do_queue_add(self, body: dict) -> tuple[int, dict]:
        body = body or {}
        rejected: list[str] = []
        if isinstance(body.get("items"), list):
            if not body["items"]:
                return 400, {"ok": False, "error": "пустой список items"}
            items, rejected = self.queue.add_many(body["items"])
        else:
            try:
                items = [self.queue.add(
                    audio=body.get("audio", ""),
                    output=body.get("output", ""),
                    series=body.get("series", ""),
                    note=body.get("note", ""),
                    overrides=body.get("overrides") or {},
                )]
            except ValueError as exc:
                return 400, {"ok": False, "error": str(exc), "queue": self.queue.snapshot()}
        if not items:
            return 400, {"ok": False, "error": "; ".join(rejected[:3]) or "нечего добавлять",
                         "rejected": rejected, "queue": self.queue.snapshot()}
        return 200, {"ok": True, "added": items, "rejected": rejected,
                     "queue": self.queue.snapshot()}

    def do_queue_remove(self, item_id: str) -> tuple[int, dict]:
        ok = self.queue.remove(item_id)
        return (200, {"ok": True, "queue": self.queue.snapshot()}) if ok else \
               (404, {"ok": False, "error": "нет такой записи"})

    def do_queue_retry(self, item_id: str) -> tuple[int, dict]:
        ok = self.queue.retry(item_id)
        return (200, {"ok": True, "queue": self.queue.snapshot()}) if ok else \
               (409, {"ok": False, "error": "повтор возможен только для завершённых"})

    def do_results(self, query: dict) -> tuple[int, dict]:
        series = (query.get("series") or [""])[0]
        since = _q_float(query, "since")
        output = (query.get("output") or [""])[0]
        if not output:
            output = self.bridge.call(
                lambda: getattr(self.app.settings, "output_folder", "") or "", timeout=20)
        return 200, {"ok": True, "result": find_results(output, series, since)}

    # ── форма задачи как в панели Orchestrator (GET /webapp/api/job) ─────
    def job_payload(self) -> dict:
        """Задача в том же виде, что отдаёт jobs.py оркестратора.

        {id, kind, title, total, done, percent, status, message, elapsed, eta}
        — чтобы панель Orchestrator могла опрашивать VideoMaker своим кодом.
        """
        snap = self.status.snapshot(log_tail=0)
        q = self.queue.snapshot()
        if snap.get("running"):
            cur = q.get("current")
            return {"job": {
                "id": f"vm-{cur['id']}" if cur else "vm-manual",
                "kind": "videomaker",
                "title": snap.get("series") or "",
                "total": 100,
                "done": int(snap.get("progress") or 0),
                "percent": round(float(snap.get("progress") or 0), 1),
                "status": "running",
                "message": (snap.get("stage") or {}).get("name") or snap.get("activity", ""),
                "elapsed": snap.get("elapsed_sec") or 0,
                "eta": snap.get("eta_sec") or 0,
                "elapsed_human": snap.get("elapsed_human") or "",
                "eta_human": snap.get("eta_human") or "",
            }}
        for item in reversed(q.get("items") or []):
            if item.get("status") in ("done", "failed", "canceled"):
                ok = item["status"] == "done"
                return {"job": {
                    "id": f"vm-{item['id']}",
                    "kind": "videomaker",
                    "title": item.get("series") or os.path.basename(item.get("audio", "")),
                    "total": 100,
                    "done": 100 if ok else 0,
                    "percent": 100.0 if ok else 0.0,
                    "status": "done" if ok else "error",
                    "message": item.get("error") or "готово",
                    "elapsed": item.get("took_sec") or 0,
                    "eta": 0,
                }}
        return {"job": None}

    # ── n8n-совместимый триггер (как engines/n8n_engine.py) ──────────────
    def do_webhook_render(self, body: dict) -> tuple[int, dict]:
        """Принять задачу и вернуть id сразу — как ждёт N8nEngine.

        Ответ {id, url, state} — ровно те поля, что читает n8n_engine.py.
        """
        body = body or {}
        if "allow_duplicate" in body and type(body["allow_duplicate"]) is not bool:
            return 400, {"error": "allow_duplicate должен быть boolean (true/false)", "id": "", "state": "error"}
        audio = (body.get("audio") or body.get("media_path") or "").strip()
        if not audio:
            return 400, {"error": "не указан audio (или media_path)", "id": "", "state": "error"}
        try:
            item = self.queue.add(
                audio=audio,
                output=(body.get("output") or "").strip(),
                series=(body.get("series") or body.get("title") or "").strip(),
                note=(body.get("note") or "").strip(),
                overrides=body.get("overrides") or {},
                allow_duplicate=body.get("allow_duplicate", False),
            )
        except ValueError as exc:
            return 400, {"error": str(exc), "id": "", "state": "error"}
        return 200, {"id": item["id"], "url": None, "state": "queued",
                     "queue": {"pending": self.queue.snapshot()["pending"]}}

    def do_webhook_render_status(self, item_id: str) -> tuple[int, dict]:
        for item in self.queue.snapshot().get("items") or []:
            if item["id"] == item_id:
                return 200, {
                    "id": item_id,
                    "state": item["status"],
                    "error": item.get("error", ""),
                    "result": item.get("result") or {},
                    "series": item.get("series") or os.path.basename(item.get("audio", "")),
                }
        return 404, {"error": "нет такой задачи", "id": item_id, "state": "unknown"}

    # ── вебхук ───────────────────────────────────────────────────────────
    def _on_queue_finish(self, item: dict) -> None:
        url = (self.conf.get("webhook_url") or "").strip()
        if not url:
            return

        def _post():
            import urllib.request
            payload = json.dumps({
                "event": "series_finished",
                "status": item.get("status"),
                "series": item.get("series") or os.path.basename(item.get("audio", "")),
                "audio": item.get("audio"),
                "output": item.get("output"),
                "error": item.get("error", ""),
                "result": item.get("result") or {},
                "finished_at": item.get("finished_human", ""),
            }, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url, data=payload, method="POST",
                headers={"Content-Type": "application/json", "X-Webhook-Token": self.conf.get("webhook_token", "")})
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    log.info("[API] вебхук %s → HTTP %s", _safe_url_for_log(url), resp.status)
            except Exception as exc:  # noqa: BLE001
                log.warning("[API] вебхук %s не доставлен: %s", _safe_url_for_log(url), exc)

        threading.Thread(target=_post, name="vm-webhook", daemon=True).start()


# ── HTTP-обработчик ──────────────────────────────────────────────────────
class _PultServer(ThreadingHTTPServer):
    """Сервер пульта.

    Отличия от стандартного: очередь соединений больше пяти (телефон и
    Orchestrator стучатся одновременно), оборванный клиент не печатает
    трейсбек в лог (при плохой связи это обычное дело) и нет обратного
    DNS-запроса при старте.
    """

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def server_bind(self) -> None:
        """Привязать порт БЕЗ socket.getfqdn().

        Стандартный HTTPServer при старте зовёт getfqdn() — обратный DNS.
        Если сеть его не отдаёт, старт сервера висит секундами (на этом Mac
        замерено 3,1 с), и переключатель «доступ из сети» выглядит зависшим.
        Имя сервера нам нужно только для заголовка Host, хватит самого адреса.
        """
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError,
                            ConnectionAbortedError, TimeoutError)):
            log.debug("[API] клиент %s отключился: %s", client_address[0], exc)
            return
        log.exception("[API] сбой обработки запроса от %s", client_address[0])


def _make_handler(api: VideoMakerAPI):
    class Handler(BaseHTTPRequestHandler):
        server_version = "VideoMakerAPI/1.0"
        protocol_version = "HTTP/1.1"

        # ── утилиты ──────────────────────────────────────────────────────
        def log_message(self, fmt, *args):
            # BaseHTTPRequestHandler's default log line includes the full request
            # target. Redact legacy query-token parameters before anything reaches
            # the logger (including debug logs redirected to files).
            try:
                message = fmt % args
            except (TypeError, ValueError):
                message = str(fmt)
            message = _SECRET_QUERY_RE.sub(r"\1[REDACTED]", message)
            log.debug("[API] %s", message)

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers",
                             "Content-Type, X-Api-Key, X-Webapp-Key, X-N8N-Token, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Max-Age", "600")

        def _send(self, code: int, payload, ctype: str = "application/json; charset=utf-8") -> None:
            if isinstance(payload, (dict, list)):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            elif isinstance(payload, str):
                body = payload.encode("utf-8")
            else:
                body = payload or b""
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self._cors()
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _query(self) -> dict:
            return parse_qs(urlparse(self.path).query)

        def _key(self, query: dict) -> str:
            # Prefer headers: query-string credentials are legacy-only because
            # URLs may be retained by browser history, proxies or access logs.
            provided = self.headers.get("X-Api-Key", "") or ""
            if provided:
                return provided
            # как в панели Orchestrator: X-Webapp-Key
            provided = self.headers.get("X-Webapp-Key", "") or ""
            if provided:
                return provided
            # как в n8n-движке оркестратора: X-N8N-Token
            provided = self.headers.get("X-N8N-Token", "") or ""
            if provided:
                return provided
            auth = self.headers.get("Authorization", "") or ""
            if auth.lower().startswith("bearer "):
                return auth[7:].strip()
            # Compatibility path for older Orchestrator clients.
            provided = (query.get("key") or [""])[0]
            if provided:
                return provided
            return ""

        def _body(self) -> dict:
            raw_length = self.headers.get("Content-Length") or "0"
            try:
                length = int(raw_length)
            except (TypeError, ValueError):
                raise ApiRequestError(400, "некорректный Content-Length") from None
            if length < 0:
                raise ApiRequestError(400, "некорректный Content-Length")
            if length > MAX_REQUEST_BODY_BYTES:
                raise ApiRequestError(413, f"тело запроса превышает {MAX_REQUEST_BODY_BYTES} байт")
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ApiRequestError(400, "тело запроса прочитано не полностью")
            try:
                data = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise ApiRequestError(400, "тело запроса должно содержать корректный JSON UTF-8") from None
            if not isinstance(data, dict):
                raise ApiRequestError(400, "корнем JSON-тела должен быть объект")
            return data

        def _guard(self, query: dict) -> bool:
            ok, why = api.check_key(self._key(query), self.client_address[0])
            if not ok:
                self._send(403, {"ok": False, "error": why})
            return ok

        # ── маршруты ─────────────────────────────────────────────────────
        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path or "/")
            query = self._query()

            if path in ("/", "/index.html"):
                return self._send(200, webui.render(embed=False), "text/html; charset=utf-8")
            if path == "/embed":
                return self._send(200, webui.render(embed=True), "text/html; charset=utf-8")
            if path == "/api/health":
                return self._send(200, {
                    "ok": True, "app": "VideoMaker", "pid": os.getpid(),
                    "api": api.public_urls(), "uptime_sec": round(time.time() - api.started_at, 1),
                })
            if path == "/api/openapi.json":
                return self._send(200, openapi_spec(api))
            if path == "/api/log/stream":
                if not self._guard(query):
                    return
                return self._stream_logs(query)

            if not self._guard(query):
                return
            try:
                if path == "/api/status":
                    tail = _q_int(query, "log_tail", 30, lo=0, hi=2000)
                    return self._send(200, api.status_payload(tail))
                if path == "/api/job":
                    return self._send(200, api.job_payload())
                if path.startswith("/webhook/videomaker/render/"):
                    code, payload = api.do_webhook_render_status(path.rsplit("/", 1)[-1])
                    return self._send(code, payload)
                if path == "/api/log":
                    tail = _q_int(query, "tail", 200, lo=1, hi=5000)
                    since = _q_int(query, "since", 0)
                    if since:
                        seq, lines = api.status.logbuf.since(since)
                        return self._send(200, {"ok": True, "seq": seq, "entries": lines})
                    lines = api.status.logbuf.tail(tail)
                    return self._send(200, {"ok": True, "seq": api.status.logbuf.count(),
                                            "entries": lines})
                if path == "/api/settings/schema":
                    # r57-api.26: схема формы настроек для пульта (без Tk — чистая).
                    return self._send(200, {"ok": True, "schema": get_settings_schema()})
                if path == "/api/settings":
                    data = api.bridge.call(lambda: read_settings(api.app), timeout=30)
                    return self._send(200, {"ok": True, "settings": data})
                if path == "/api/queue":
                    return self._send(200, {"ok": True, "queue": api.queue.snapshot()})
                if path == "/api/config":
                    return self._send(200, {"ok": True, "api": api.urls(),
                                            "webhook_url": api.conf.get("webhook_url", ""),
                                            "token": api.conf.get("token", "")})
                if path == "/api/results":
                    code, payload = api.do_results(query)
                    return self._send(code, payload)
            except TimeoutError as exc:
                return self._send(504, {"ok": False, "error": str(exc)})
            except Exception as exc:
                log.exception("[API] GET %s", path)
                return self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            self._send(404, {"ok": False, "error": f"нет маршрута {path}"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path or "/")
            query = self._query()
            if not self._guard(query):
                return
            try:
                body = self._body()
            except ApiRequestError as exc:
                return self._send(exc.status, {"ok": False, "error": str(exc)})
            try:
                if path == "/api/run":
                    code, payload = api.do_run(body)
                elif path == "/api/cancel":
                    code, payload = api.do_cancel()
                elif path == "/api/settings":
                    code, payload = api.do_settings_set(body)
                elif path == "/api/config":
                    code, payload = api.do_config_set(body)
                elif path == "/api/queue":
                    code, payload = api.do_queue_add(body)
                elif path == "/api/queue/pause":
                    paused = body.get("paused", True)
                    if type(paused) is not bool:
                        code, payload = 400, {"ok": False, "error": "paused должен быть boolean (true/false)"}
                    else:
                        api.queue.pause(paused)
                        code, payload = 200, {"ok": True, "queue": api.queue.snapshot()}
                elif path == "/api/queue/clear":
                    removed = api.queue.clear_finished()
                    code, payload = 200, {"ok": True, "removed": removed,
                                          "queue": api.queue.snapshot()}
                elif path == "/webhook/videomaker/render":
                    code, payload = api.do_webhook_render(body)
                elif path.startswith("/api/queue/") and path.endswith("/retry"):
                    code, payload = api.do_queue_retry(path.split("/")[3])
                else:
                    code, payload = 404, {"ok": False, "error": f"нет маршрута {path}"}
            except TimeoutError as exc:
                return self._send(504, {"ok": False, "error": str(exc)})
            except Exception as exc:
                log.exception("[API] POST %s", path)
                return self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            self._send(code, payload)

        def do_DELETE(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path or "/")
            if not self._guard(self._query()):
                return
            try:
                if path.startswith("/api/queue/"):
                    code, payload = api.do_queue_remove(path.split("/")[3])
                else:
                    code, payload = 404, {"ok": False, "error": f"нет маршрута {path}"}
            except ValueError as exc:
                code, payload = 409, {"ok": False, "error": str(exc)}
            except Exception as exc:
                log.exception("[API] DELETE %s", path)
                code, payload = 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            self._send(code, payload)

        # ── живой поток логов (SSE) ──────────────────────────────────────
        def _stream_logs(self, query: dict) -> None:
            since = _q_int(query, "since", 0)
            if not since:
                since = max(0, api.status.logbuf.count() - 50)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "keep-alive")
            self._cors()
            self.end_headers()
            deadline = time.time() + SSE_MAX_SECONDS
            try:
                while time.time() < deadline:
                    seq, lines = api.status.logbuf.since(since)
                    if lines:
                        since = seq
                        for line in lines:
                            self.wfile.write(f"data: {line}\n\n".encode())
                        self.wfile.flush()
                    else:
                        api.status.logbuf.wait_new(since, timeout=15.0)
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    return Handler


# ── публичная точка входа ────────────────────────────────────────────────
class ApiRuntime:
    """Живой пульт: владеет мостом, статусом, очередью и сервером."""

    def __init__(self, app, bridge, status, queue, api: VideoMakerAPI):
        self.app = app
        self.bridge = bridge
        self.status = status
        self.queue = queue
        self.api = api

    def stop(self) -> None:
        self.queue.stop()
        self.api.stop()
        self.bridge.stop()
