# VideoMaker API | 2026.09.23-r57-api.1 | 2026-09-23
# CHANGED:
#   r57-api: новый файл. Описание API в формате OpenAPI 3.0 — чтобы скрипты,
#            Orchestrator и другие агенты видели контракт без чтения кода.
#   r57-api.1: 14 → 17 путей: добавлены /api/job и оба вебхука.
# PREV: (нет файла)
# NEW FILE: video_maker/api/openapi.py

"""Машиночитаемое описание HTTP-пульта VideoMaker."""
from __future__ import annotations

_DESC = """Пульт VideoMaker: запуск сборки видео, живой статус, очередь серий, логи.

Авторизация: токен в заголовке `X-Api-Key`, `Authorization: Bearer <токен>`
или (legacy) в параметре `?key=<токен>`; query-token может попасть в историю браузера и внешние логи, поэтому предпочтительны заголовки. Веб-страница очищает адресную строку после bootstrap и выполняет последующие запросы через `X-Api-Key`; сервер маскирует секреты query-параметров в access-логах. Токен хранится в `~/video_maker/api.json` и показывается в блоке пульта.

JSON body должен быть объектом размером до 2 MiB. Ошибочный JSON или неверный Content-Length отклоняется с HTTP 400, превышение лимита — с HTTP 413. Boolean поля `lan` и `enabled` принимают только JSON true/false, чтобы строка `"false"` не включала доступ из сети.

Доступ из сети выключен по умолчанию: запросы не с localhost отклоняются,
пока не включён `lan` (`POST /api/config {"lan": true}`).
"""

_PATHS: dict = {
    "/api/health": {
        "get": {
            "summary": "Живость пульта (без токена)",
            "responses": {"200": {"description": "OK"}},
        }
    },
    "/api/status": {
        "get": {
            "summary": "Текущее состояние: стадия, прогресс, прошло, осталось, действие",
            "parameters": [{
                "name": "log_tail", "in": "query", "required": False,
                "schema": {"type": "integer", "default": 30},
            }],
            "responses": {"200": {"description": "Снимок состояния"}},
        }
    },
    "/api/run": {
        "post": {
            "summary": "Запустить сборку видео (с текущими настройками или с переопределением)",
            "requestBody": {
                "required": False,
                "content": {"application/json": {"schema": {
                    "type": "object",
                    "properties": {
                        "audio": {"type": "string", "description": "файл или папка с аудио"},
                        "output": {"type": "string", "description": "папка вывода"},
                        "series": {"type": "string", "description": "название серии"},
                        "voice_enhance": {"type": "boolean"},
                        "keep_temp_files": {"type": "boolean"},
                        "bgm_youtube_enable": {"type": "boolean"},
                    },
                }}},
            },
            "responses": {
                "200": {"description": "Запущено"},
                "400": {"description": "Проверка настроек не пройдена (в ответе errors)"},
                "409": {"description": "Уже выполняется"},
            },
        }
    },
    "/api/cancel": {
        "post": {"summary": "Отменить текущий прогон", "responses": {"200": {"description": "Отменено"}}}
    },
    "/api/log": {
        "get": {
            "summary": "Хвост лога или строки после seq",
            "parameters": [
                {"name": "tail", "in": "query", "schema": {"type": "integer", "default": 200}},
                {"name": "since", "in": "query", "schema": {"type": "integer", "default": 0}},
            ],
            "responses": {"200": {"description": "{seq, entries}"}},
        }
    },
    "/api/log/stream": {
        "get": {"summary": "Живой поток логов (Server-Sent Events)",
                "responses": {"200": {"description": "text/event-stream"}}}
    },
    "/api/settings": {
        "get": {"summary": "Текущие настройки (секреты замаскированы)",
                "responses": {"200": {"description": "Настройки"}}},
        "post": {"summary": "Изменить настройки и сохранить их",
                 "requestBody": {"content": {"application/json": {"schema": {
                     "type": "object",
                     "properties": {
                         "remove_long_pauses": {
                             "type": "boolean",
                             "default": False,
                             "description": "Сокращать длинные межсловные паузы; по умолчанию выключено",
                         },
                         "align_speech_rate": {
                             "type": "boolean",
                             "default": True,
                             "description": "Выравнивать темп речи (трим тишины и подтяжка к 120–150 WPM); по умолчанию включено",
                         },
                         "target_lufs": {
                             "type": "number",
                             "minimum": -36,
                             "maximum": -5,
                             "default": -14,
                             "description": "Целевой уровень только для финального микса с BGM; voice-only выход не получает дополнительный loudnorm-проход",
                         },
                     },
                     "additionalProperties": True,
                 }}}},
                 "responses": {"200": {"description": "Что применилось"}}},
    },
    "/api/config": {
        "get": {"summary": "Адреса пульта, токен, вебхук",
                "responses": {"200": {"description": "Конфигурация пульта"}}},
        "post": {
            "summary": "Изменить конфигурацию пульта (lan/enabled/port/webhook_url)",
            "requestBody": {"content": {"application/json": {"schema": {
                "type": "object",
                "properties": {
                    "lan": {"type": "boolean", "description": "разрешить доступ из сети"},
                    "enabled": {"type": "boolean"},
                    "port": {"type": "integer"},
                    "webhook_url": {"type": "string"},
                },
            }}}},
            "responses": {"200": {"description": "Изменено"}},
        },
    },
    "/api/queue": {
        "get": {"summary": "Очередь серий", "responses": {"200": {"description": "Очередь"}}},
        "post": {
            "summary": "Добавить серию (или список серий) в очередь",
            "requestBody": {"content": {"application/json": {"schema": {
                "type": "object",
                "properties": {
                    "audio": {"type": "string"},
                    "output": {"type": "string"},
                    "series": {"type": "string"},
                    "note": {"type": "string"},
                    "items": {"type": "array", "items": {"type": "object"}},
                },
            }}}},
            "responses": {"200": {"description": "Добавлено"}},
        },
    },
    "/api/queue/{id}": {
        "delete": {
            "summary": "Удалить запись очереди",
            "parameters": [{"name": "id", "in": "path", "required": True,
                            "schema": {"type": "string"}}],
            "responses": {"200": {"description": "Удалено"},
                          "409": {"description": "Запись выполняется"}},
        }
    },
    "/api/queue/{id}/retry": {
        "post": {
            "summary": "Повторить завершённую серию",
            "parameters": [{"name": "id", "in": "path", "required": True,
                            "schema": {"type": "string"}}],
            "responses": {"200": {"description": "В очереди снова"}},
        }
    },
    "/api/queue/pause": {
        "post": {
            "summary": "Пауза/продолжение очереди",
            "requestBody": {"content": {"application/json": {"schema": {
                "type": "object", "properties": {"paused": {"type": "boolean"}}}}}},
            "responses": {"200": {"description": "Изменено"}},
        }
    },
    "/api/queue/clear": {
        "post": {"summary": "Убрать завершённые записи",
                 "responses": {"200": {"description": "Сколько убрано"}}}
    },
    "/api/results": {
        "get": {
            "summary": "Что готово в папке серии",
            "parameters": [
                {"name": "series", "in": "query", "schema": {"type": "string"}},
                {"name": "output", "in": "query", "schema": {"type": "string"}},
                {"name": "since", "in": "query", "schema": {"type": "number"}},
            ],
            "responses": {"200": {"description": "Файлы выдачи"}},
        }
    },
    "/api/job": {
        "get": {
            "summary": "Задача в формате панели Orchestrator (как /webapp/api/job)",
            "responses": {"200": {"description": "{job: {id, kind, title, total, done, "
                                              "percent, status, message, elapsed, eta}}"}},
        }
    },
    "/webhook/videomaker/render": {
        "post": {
            "summary": "Принять задачу рендера (формат n8n-движка Orchestrator)",
            "requestBody": {"content": {"application/json": {"schema": {
                "type": "object",
                "properties": {
                    "audio": {"type": "string", "description": "файл или папка"},
                    "output": {"type": "string"},
                    "series": {"type": "string"},
                    "title": {"type": "string"},
                    "note": {"type": "string"},
                    "overrides": {"type": "object"},
                },
            }}}},
            "responses": {
                "200": {"description": "{id, url, state: queued}"},
                "400": {"description": "не указан audio"},
            },
        }
    },
    "/webhook/videomaker/render/{id}": {
        "get": {
            "summary": "Состояние задачи, поставленной через вебхук",
            "parameters": [{"name": "id", "in": "path", "required": True,
                            "schema": {"type": "string"}}],
            "responses": {
                "200": {"description": "{id, state: pending|running|done|failed, error, result}"},
                "404": {"description": "нет такой задачи"},
            },
        }
    },
}


def openapi_spec(api=None) -> dict:
    """Спецификация OpenAPI 3.0 для пульта."""
    port = 8765
    if api is not None:
        port = int(api.conf.get("port") or 8765)
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "VideoMaker API",
            "version": "r57-api",
            "description": _DESC,
        },
        "servers": [{"url": f"http://127.0.0.1:{port}"}],
        "components": {
            "securitySchemes": {
                "ApiKey": {"type": "apiKey", "in": "header", "name": "X-Api-Key"},
                "Bearer": {"type": "http", "scheme": "bearer"},
            }
        },
        "security": [{"ApiKey": []}, {"Bearer": []}],
        "paths": _PATHS,
    }
