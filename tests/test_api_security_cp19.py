"""CP19 API hardening regressions: secret redaction and bounded JSON bodies."""
from __future__ import annotations

import io
from email.message import Message
from unittest.mock import patch

import pytest

from video_maker.api.server import (
    MAX_REQUEST_BODY_BYTES,
    ApiRequestError,
    _make_handler,
)
from video_maker.api.webui import render


@pytest.fixture()
def handler_type():
    return _make_handler(None)


def bare_handler(handler_type, *, headers=None, body=b""):
    handler = object.__new__(handler_type)
    handler.headers = headers or Message()
    handler.rfile = io.BytesIO(body)
    return handler


def test_header_credential_wins_over_legacy_query_token(handler_type):
    headers = Message()
    headers["X-Api-Key"] = "header-secret"
    handler = bare_handler(handler_type, headers=headers)
    assert handler._key({"key": ["attacker-or-stale-query-value"]}) == "header-secret"


def test_legacy_token_is_redacted_from_access_logs(handler_type):
    handler = bare_handler(handler_type)
    with patch("video_maker.api.server.log.debug") as debug:
        handler.log_message("request: %s", "GET /api/status?key=DO_NOT_LOG&x=1 HTTP/1.1")
    rendered = str(debug.call_args)
    assert "DO_NOT_LOG" not in rendered
    assert "[REDACTED]" in rendered
    assert "x=1" in rendered


def test_web_ui_strips_token_from_location_and_uses_header_auth():
    html = render()
    assert "history.replaceState" in html
    assert 'cleanUrl.searchParams.delete("key")' in html
    assert 'var url = path;' in html
    assert 'Object.assign({"X-Api-Key": KEY}' in html
    assert 'path + sep + "key="' not in html


def test_body_accepts_valid_json_object(handler_type):
    raw = b'{"paused": true}'
    headers = Message()
    headers["Content-Length"] = str(len(raw))
    handler = bare_handler(handler_type, headers=headers, body=raw)
    assert handler._body() == {"paused": True}


@pytest.mark.parametrize("value", ["not-an-int", "-1"])
def test_body_rejects_invalid_content_length(handler_type, value):
    headers = Message()
    headers["Content-Length"] = value
    handler = bare_handler(handler_type, headers=headers)
    with pytest.raises(ApiRequestError) as exc:
        handler._body()
    assert exc.value.status == 400


def test_body_rejects_oversized_payload_before_reading(handler_type):
    headers = Message()
    headers["Content-Length"] = str(MAX_REQUEST_BODY_BYTES + 1)
    handler = bare_handler(handler_type, headers=headers)
    with pytest.raises(ApiRequestError) as exc:
        handler._body()
    assert exc.value.status == 413


@pytest.mark.parametrize("body", [b"{broken", b"[]", b'"string"', b"null"])
def test_body_rejects_malformed_or_non_object_json(handler_type, body):
    headers = Message()
    headers["Content-Length"] = str(len(body))
    handler = bare_handler(handler_type, headers=headers, body=body)
    with pytest.raises(ApiRequestError) as exc:
        handler._body()
    assert exc.value.status == 400


def test_body_rejects_truncated_request(handler_type):
    headers = Message()
    headers["Content-Length"] = "20"
    handler = bare_handler(handler_type, headers=headers, body=b"{}")
    with pytest.raises(ApiRequestError) as exc:
        handler._body()
    assert exc.value.status == 400


def test_request_responses_include_no_referrer_and_nosniff():
    source = open("video_maker/api/server.py", encoding="utf-8").read()
    assert 'self.send_header("Referrer-Policy", "no-referrer")' in source
    assert 'self.send_header("X-Content-Type-Options", "nosniff")' in source


def test_config_rejects_string_false_without_enabling_lan():
    from video_maker.api.server import VideoMakerAPI

    api = object.__new__(VideoMakerAPI)
    api.conf = {"enabled": True, "port": 8765, "lan": False, "token": "test-token"}
    with patch("video_maker.api.server.save_config") as save, patch.object(api, "start") as start:
        status, result = api.do_config_set({"lan": "false"})
    assert status == 400
    assert result["ok"] is False
    assert api.conf["lan"] is False
    save.assert_not_called()
    start.assert_not_called()


def test_config_rejects_partial_mutation_when_boolean_type_invalid():
    from video_maker.api.server import VideoMakerAPI

    api = object.__new__(VideoMakerAPI)
    api.conf = {"enabled": True, "port": 8765, "lan": False, "token": "test-token"}
    original = dict(api.conf)
    with patch("video_maker.api.server.save_config") as save, patch.object(api, "start") as start:
        status, _ = api.do_config_set({"port": 8766, "lan": "false"})
    assert status == 400
    assert api.conf == original
    save.assert_not_called()
    start.assert_not_called()


def test_load_config_fails_closed_on_malformed_network_values(tmp_path):
    import json
    from video_maker.api import server

    config_path = tmp_path / "api.json"
    config_path.write_text(json.dumps({
        "enabled": "false", "port": True, "lan": "false",
        "token": "a-valid-existing-test-token", "webhook_url": 42,
    }), encoding="utf-8")
    with patch.object(server, "CONFIG_PATH", str(config_path)):
        config = server.load_config()
    assert config["lan"] is False
    assert config["enabled"] is False
    assert config["port"] == server.DEFAULT_PORT
    assert config["webhook_url"] == ""
    assert config["token"] == "a-valid-existing-test-token"


def test_webhook_query_secrets_are_redacted_before_logging():
    from video_maker.api.server import _safe_url_for_log

    safe = _safe_url_for_log("https://hooks.example/callback?access_token=SECRET123&event=done")
    assert "SECRET123" not in safe
    assert "[REDACTED]" in safe
    assert "event=done" in safe
    safe_userinfo = _safe_url_for_log("https://user:SECRET-PASS@hooks.example/callback")
    assert "SECRET-PASS" not in safe_userinfo
    assert "[REDACTED]@hooks.example" in safe_userinfo


def test_live_http_auth_health_and_body_size_limit():
    """Exercise real socket/HTTP framing, not only private handler helpers."""
    import json
    import threading
    import time
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    class FakeAPI:
        conf = {"enabled": True, "lan": False, "token": "live-test-secret", "port": 0}
        started_at = time.time()

        @staticmethod
        def public_urls():
            return {"port": 0, "lan": False, "enabled": True}

        @staticmethod
        def check_key(provided, remote):
            import hmac
            if remote not in ("127.0.0.1", "::1", "localhost"):
                return False, "LAN disabled"
            if not provided or not hmac.compare_digest(provided, FakeAPI.conf["token"]):
                return False, "invalid key"
            return True, ""

        @staticmethod
        def status_payload(_tail=0):
            return {"ok": True, "status": "idle"}

    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(FakeAPI()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/api/health", timeout=2) as resp:
            health = json.loads(resp.read().decode())
            assert resp.status == 200
        assert "token" not in json.dumps(health).lower()

        req = urllib.request.Request(
            base + "/api/status?key=wrong-query-secret",
            headers={"X-Api-Key": "live-test-secret"},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            assert resp.status == 200
            assert json.loads(resp.read().decode())["status"] == "idle"

        req = urllib.request.Request(base + "/api/status")
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("unauthenticated status should be rejected")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
            exc.read()

        # Send only the headers with an oversized Content-Length, so the server
        # can prove it rejects before allocating/reading the declared body.
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        conn.putrequest("POST", "/api/config")
        conn.putheader("X-Api-Key", "live-test-secret")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(MAX_REQUEST_BODY_BYTES + 1))
        conn.endheaders()
        response = conn.getresponse()
        assert response.status == 413
        response.read()
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
