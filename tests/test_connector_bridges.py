from __future__ import annotations

import json
from pathlib import Path

from deepscientist.bridges.base import BaseConnectorBridge
from deepscientist.bridges.connectors import QQConnectorBridge
from deepscientist.config import ConfigManager
from deepscientist.daemon.app import DaemonApp
from deepscientist.home import ensure_home_layout, repo_root
from deepscientist.shared import write_yaml
from deepscientist.skills import SkillInstaller
from deepscientist.quest import QuestService
from deepscientist.connector.weixin_support import remember_weixin_context_token


def test_base_connector_bridge_render_text_ignores_machine_metadata_attachments() -> None:
    rendered = BaseConnectorBridge.render_text(
        "Assistant reply.",
        [
            {
                "kind": "runner_result",
                "run_id": "run-123",
                "history_root": "/tmp/history",
            }
        ],
    )

    assert rendered == "Assistant reply."


def test_base_connector_bridge_render_text_keeps_human_visible_attachment_paths() -> None:
    rendered = BaseConnectorBridge.render_text(
        "Graph refreshed.",
        [
            {"kind": "path", "path": "/tmp/graph.svg"},
            {"kind": "link", "url": "https://example.com/report"},
        ],
    )

    assert "Attachments:" in rendered
    assert "/tmp/graph.svg" in rendered
    assert "https://example.com/report" in rendered


class _FakeResponse:
    def __init__(self, payload: str, status: int = 200) -> None:
        self._payload = payload.encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _setup_app(temp_home: Path, *, connector_name: str, extra: dict | None = None) -> tuple[DaemonApp, str]:
    ensure_home_layout(temp_home)
    manager = ConfigManager(temp_home)
    manager.ensure_files()
    connectors = manager.load_named("connectors")
    connectors[connector_name]["enabled"] = True
    connectors[connector_name]["auto_bind_dm_to_active_quest"] = True
    if extra:
        connectors[connector_name].update(extra)
    write_yaml(manager.path_for("connectors"), connectors)
    quest = QuestService(temp_home, skill_installer=SkillInstaller(repo_root(), temp_home)).create(f"{connector_name} bridge quest")
    return DaemonApp(temp_home), quest["quest_id"]


def test_bridge_direct_outbound_telegram(monkeypatch, temp_home: Path) -> None:
    app, _quest_id = _setup_app(
        temp_home,
        connector_name="telegram",
        extra={"bot_token": "telegram-token"},
    )

    captured: list[tuple[str, dict, str]] = []

    def fake_urlopen(request, timeout=8):  # noqa: ANN001
        body = json.loads(request.data.decode("utf-8"))
        captured.append((request.full_url, dict(request.header_items()), body.get("text") if isinstance(body.get("text"), str) else json.dumps(body.get("text", {}), ensure_ascii=False)))
        return _FakeResponse('{"ok":true}', status=200)

    monkeypatch.setattr("deepscientist.bridges.connectors.urlopen", fake_urlopen)

    telegram_result = app.channels["telegram"].send(
        {
            "conversation_id": "telegram:direct:1001",
            "message": "Bridge outbound hello",
        }
    )
    assert telegram_result["delivery"]["transport"] == "telegram-http"
    assert any(url.startswith("https://api.telegram.org/bottelegram-token/sendMessage") for url, _headers, _body in captured)


def test_bridge_direct_outbound_qq_uses_openid_and_group_openid(monkeypatch, temp_home: Path) -> None:
    QQConnectorBridge._token_cache = {}
    _app, _quest_id = _setup_app(
        temp_home,
        connector_name="qq",
        extra={"app_id": "1903299925", "app_secret": "qq-secret"},
    )
    app = DaemonApp(temp_home)

    captured: list[tuple[str, dict, dict]] = []

    def fake_urlopen(request, timeout=8):  # noqa: ANN001
        body = json.loads(request.data.decode("utf-8")) if request.data else {}
        captured.append((request.full_url, dict(request.header_items()), body))
        if request.full_url == "https://bots.qq.com/app/getAppAccessToken":
            return _FakeResponse('{"access_token":"qq-access-token","expires_in":7200}', status=200)
        return _FakeResponse('{"id":"msg-1","timestamp":"1741440000"}', status=200)

    monkeypatch.setattr("deepscientist.bridges.connectors.urlopen", fake_urlopen)

    direct_result = app.channels["qq"].send(
        {
            "conversation_id": "qq:direct:user-openid-1",
            "message": "QQ direct hello",
        }
    )
    group_result = app.channels["qq"].send(
        {
            "conversation_id": "qq:group:group-openid-1",
            "message": "QQ group hello",
        }
    )

    assert direct_result["delivery"]["transport"] == "qq-http"
    assert group_result["delivery"]["transport"] == "qq-http"
    assert sum(1 for url, _headers, _body in captured if url == "https://bots.qq.com/app/getAppAccessToken") == 1
    assert any(url.endswith("/v2/users/user-openid-1/messages") for url, _headers, _body in captured)
    assert any(url.endswith("/v2/groups/group-openid-1/messages") for url, _headers, _body in captured)
    send_headers = [headers for url, headers, _body in captured if url.startswith("https://api.sgroup.qq.com/v2/")]
    assert send_headers
    assert all(headers.get("Authorization") == "QQBot qq-access-token" for headers in send_headers)


def test_bridge_direct_outbound_qq_supports_markdown_mode(monkeypatch, temp_home: Path) -> None:
    QQConnectorBridge._token_cache = {}
    _app, _quest_id = _setup_app(
        temp_home,
        connector_name="qq",
        extra={
            "app_id": "1903299925",
            "app_secret": "qq-secret",
            "enable_markdown_send": True,
        },
    )
    app = DaemonApp(temp_home)

    captured: list[tuple[str, dict, dict]] = []

    def fake_urlopen(request, timeout=8):  # noqa: ANN001
        body = json.loads(request.data.decode("utf-8")) if request.data else {}
        captured.append((request.full_url, dict(request.header_items()), body))
        if request.full_url == "https://bots.qq.com/app/getAppAccessToken":
            return _FakeResponse('{"access_token":"qq-access-token","expires_in":7200}', status=200)
        return _FakeResponse('{"id":"msg-markdown","timestamp":"1741440001"}', status=200)

    monkeypatch.setattr("deepscientist.bridges.connectors.urlopen", fake_urlopen)

    result = app.channels["qq"].send(
        {
            "conversation_id": "qq:direct:user-openid-2",
            "message": "## Title\n- item",
            "connector_hints": {"qq": {"render_mode": "markdown"}},
        }
    )

    assert result["delivery"]["ok"] is True
    markdown_requests = [
        body
        for url, _headers, body in captured
        if url.endswith("/v2/users/user-openid-2/messages")
    ]
    assert markdown_requests
    assert markdown_requests[-1]["msg_type"] == 2
    assert markdown_requests[-1]["markdown"]["content"] == "## Title\n- item"


def test_bridge_direct_outbound_qq_supports_image_and_file_upload(monkeypatch, temp_home: Path) -> None:
    QQConnectorBridge._token_cache = {}
    _app, _quest_id = _setup_app(
        temp_home,
        connector_name="qq",
        extra={
            "app_id": "1903299925",
            "app_secret": "qq-secret",
            "enable_file_upload_experimental": True,
        },
    )
    app = DaemonApp(temp_home)

    image_path = temp_home / "image.png"
    file_path = temp_home / "report.pdf"
    image_path.write_bytes(b"png-data")
    file_path.write_bytes(b"%PDF-1.7")

    captured: list[tuple[str, dict, dict]] = []

    def fake_urlopen(request, timeout=8):  # noqa: ANN001
        body = json.loads(request.data.decode("utf-8")) if request.data else {}
        captured.append((request.full_url, dict(request.header_items()), body))
        if request.full_url == "https://bots.qq.com/app/getAppAccessToken":
            return _FakeResponse('{"access_token":"qq-access-token","expires_in":7200}', status=200)
        if request.full_url.endswith("/files"):
            return _FakeResponse('{"file_info":"FILE_INFO_123","ttl":3600}', status=200)
        return _FakeResponse('{"id":"msg-media","timestamp":"1741440002"}', status=200)

    monkeypatch.setattr("deepscientist.bridges.connectors.urlopen", fake_urlopen)

    image_result = app.channels["qq"].send(
        {
            "conversation_id": "qq:direct:user-openid-3",
            "message": "Image upload test",
            "attachments": [
                {
                    "kind": "path",
                    "path": str(image_path),
                    "content_type": "image/png",
                    "connector_delivery": {"qq": {"media_kind": "image"}},
                }
            ],
        }
    )
    file_result = app.channels["qq"].send(
        {
            "conversation_id": "qq:direct:user-openid-4",
            "message": "File upload test",
            "attachments": [
                {
                    "kind": "path",
                    "path": str(file_path),
                    "content_type": "application/pdf",
                    "connector_delivery": {"qq": {"media_kind": "file"}},
                }
            ],
        }
    )

    assert image_result["delivery"]["ok"] is True
    assert file_result["delivery"]["ok"] is True
    image_uploads = [body for url, _headers, body in captured if url.endswith("/v2/users/user-openid-3/files")]
    file_uploads = [body for url, _headers, body in captured if url.endswith("/v2/users/user-openid-4/files")]
    image_media_messages = [body for url, _headers, body in captured if url.endswith("/v2/users/user-openid-3/messages")]
    file_media_messages = [body for url, _headers, body in captured if url.endswith("/v2/users/user-openid-4/messages")]
    assert image_uploads and file_uploads
    assert image_uploads[-1]["file_type"] == 1
    assert file_uploads[-1]["file_type"] == 4
    assert file_uploads[-1]["file_name"] == "report.pdf"
    assert any(body.get("msg_type") == 7 for body in image_media_messages)
    assert any(body.get("msg_type") == 7 for body in file_media_messages)


def test_qq_channel_auto_uses_recent_inbound_message_as_reply_target(monkeypatch, temp_home: Path) -> None:
    QQConnectorBridge._token_cache = {}
    _app, _quest_id = _setup_app(
        temp_home,
        connector_name="qq",
        extra={"app_id": "1903299925", "app_secret": "qq-secret"},
    )
    app = DaemonApp(temp_home)

    app.channels["qq"].ingest(
        {
            "chat_type": "direct",
            "sender_id": "user-openid-5",
            "sender_name": "Tester",
            "message_id": "inbound-msg-5",
            "text": "Hello from QQ",
        }
    )

    captured: list[tuple[str, dict, dict]] = []

    def fake_urlopen(request, timeout=8):  # noqa: ANN001
        body = json.loads(request.data.decode("utf-8")) if request.data else {}
        captured.append((request.full_url, dict(request.header_items()), body))
        if request.full_url == "https://bots.qq.com/app/getAppAccessToken":
            return _FakeResponse('{"access_token":"qq-access-token","expires_in":7200}', status=200)
        return _FakeResponse('{"id":"msg-reply","timestamp":"1741440003"}', status=200)

    monkeypatch.setattr("deepscientist.bridges.connectors.urlopen", fake_urlopen)

    result = app.channels["qq"].send(
        {
            "conversation_id": "qq:direct:user-openid-5",
            "message": "Reply target test",
        }
    )

    assert result["delivery"]["ok"] is True
    message_requests = [body for url, _headers, body in captured if url.endswith("/v2/users/user-openid-5/messages")]
    assert message_requests
    assert message_requests[-1]["msg_id"] == "inbound-msg-5"


def test_bridge_direct_outbound_weixin_uses_context_token_for_text(monkeypatch, temp_home: Path) -> None:
    _app, _quest_id = _setup_app(
        temp_home,
        connector_name="weixin",
        extra={"bot_token": "wx-token", "account_id": "wx-bot-1@im.bot"},
    )
    app = DaemonApp(temp_home)
    remember_weixin_context_token(
        temp_home / "logs" / "connectors" / "weixin",
        user_id="wx-user-1@im.wechat",
        context_token="ctx-token-1",
        account_id="wx-bot-1@im.bot",
    )

    captured: list[dict] = []

    def fake_send_weixin_message(*, base_url, token, body, route_tag=None, timeout_ms=15_000):  # noqa: ANN001
        captured.append({"base_url": base_url, "token": token, "body": body})
        return {}

    monkeypatch.setattr("deepscientist.bridges.connectors.send_weixin_message", fake_send_weixin_message)

    result = app.channels["weixin"].send(
        {
            "conversation_id": "weixin:direct:wx-user-1@im.wechat",
            "message": "hello weixin",
        }
    )

    assert result["delivery"]["ok"] is True
    assert result["delivery"]["transport"] == "weixin-ilink"
    assert len(captured) == 1
    assert captured[0]["body"]["msg"]["context_token"] == "ctx-token-1"
    assert captured[0]["body"]["msg"]["client_id"].startswith("openclaw-weixin:")
    assert captured[0]["body"]["msg"]["item_list"][0]["text_item"]["text"] == "hello weixin"


def test_bridge_direct_outbound_weixin_supports_image_and_file_upload(monkeypatch, temp_home: Path) -> None:
    _app, _quest_id = _setup_app(
        temp_home,
        connector_name="weixin",
        extra={"bot_token": "wx-token", "account_id": "wx-bot-1@im.bot"},
    )
    app = DaemonApp(temp_home)
    remember_weixin_context_token(
        temp_home / "logs" / "connectors" / "weixin",
        user_id="wx-user-2@im.wechat",
        context_token="ctx-token-2",
        account_id="wx-bot-1@im.bot",
    )

    image_path = temp_home / "chart.bin"
    video_path = temp_home / "clip.bin"
    file_path = temp_home / "report.pdf"
    image_path.write_bytes(b"png-data")
    video_path.write_bytes(b"mp4-data")
    file_path.write_bytes(b"%PDF-1.7")

    uploads: list[dict] = []
    sends: list[dict] = []

    def fake_upload_local_media_to_weixin(*, file_path, to_user_id, base_url, cdn_base_url, token, media_type, route_tag=None, timeout_ms=15_000):  # noqa: ANN001
        uploads.append({"file_path": str(file_path), "to_user_id": to_user_id, "media_type": media_type})
        return {
            "download_param": f"download-{Path(file_path).name}",
            "aes_key_base64": "YWJjZGVmZ2hpamtsbW5vcA==",
            "ciphertext_size": 128,
            "file_size": Path(file_path).stat().st_size,
        }

    def fake_send_weixin_message(*, base_url, token, body, route_tag=None, timeout_ms=15_000):  # noqa: ANN001
        sends.append(body)
        return {}

    monkeypatch.setattr("deepscientist.bridges.connectors.upload_local_media_to_weixin", fake_upload_local_media_to_weixin)
    monkeypatch.setattr("deepscientist.bridges.connectors.send_weixin_message", fake_send_weixin_message)

    result = app.channels["weixin"].send(
        {
            "conversation_id": "weixin:direct:wx-user-2@im.wechat",
            "message": "media upload test",
            "attachments": [
                {
                    "path": str(image_path),
                    "connector_delivery": {"weixin": {"media_kind": "image"}},
                },
                {
                    "path": str(video_path),
                    "connector_delivery": {"weixin": {"media_kind": "video"}},
                },
                {"path": str(file_path)},
            ],
        }
    )

    assert result["delivery"]["ok"] is True
    assert [item["media_type"] for item in uploads] == [1, 2, 3]
    assert len(sends) == 4
    assert result["delivery"]["partial"] is False
    assert [body["msg"]["item_list"][0]["type"] for body in sends] == [2, 5, 4, 1]
    assert sends[0]["msg"]["item_list"][0]["image_item"]["media"]["encrypt_query_param"] == "download-chart.bin"
    assert sends[1]["msg"]["item_list"][0]["video_item"]["media"]["encrypt_query_param"] == "download-clip.bin"
    assert sends[2]["msg"]["item_list"][0]["file_item"]["file_name"] == "report.pdf"
    assert sends[3]["msg"]["item_list"][0]["text_item"]["text"] == "media upload test"


def test_bridge_direct_outbound_weixin_accepts_source_path_for_native_media(monkeypatch, temp_home: Path) -> None:
    _app, _quest_id = _setup_app(
        temp_home,
        connector_name="weixin",
        extra={"bot_token": "wx-token", "account_id": "wx-bot-1@im.bot"},
    )
    app = DaemonApp(temp_home)
    remember_weixin_context_token(
        temp_home / "logs" / "connectors" / "weixin",
        user_id="wx-user-3@im.wechat",
        context_token="ctx-token-3",
        account_id="wx-bot-1@im.bot",
    )

    artifact_path = temp_home / "artifacts" / "summary.png"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(b"png-data")

    uploads: list[dict] = []
    sends: list[dict] = []

    def fake_upload_local_media_to_weixin(*, file_path, to_user_id, base_url, cdn_base_url, token, media_type, route_tag=None, timeout_ms=15_000):  # noqa: ANN001
        uploads.append({"file_path": str(file_path), "to_user_id": to_user_id, "media_type": media_type})
        return {
            "download_param": "download-summary.png",
            "aes_key_base64": "YWJjZGVmZ2hpamtsbW5vcA==",
            "ciphertext_size": 64,
            "file_size": Path(file_path).stat().st_size,
        }

    def fake_send_weixin_message(*, base_url, token, body, route_tag=None, timeout_ms=15_000):  # noqa: ANN001
        sends.append(body)
        return {}

    monkeypatch.setattr("deepscientist.bridges.connectors.upload_local_media_to_weixin", fake_upload_local_media_to_weixin)
    monkeypatch.setattr("deepscientist.bridges.connectors.send_weixin_message", fake_send_weixin_message)

    result = app.channels["weixin"].send(
        {
            "conversation_id": "weixin:direct:wx-user-3@im.wechat",
            "message": "artifact path fallback",
            "attachments": [
                {
                    "kind": "runner_result",
                    "source_path": str(artifact_path),
                    "connector_delivery": {"weixin": {"media_kind": "image"}},
                }
            ],
        }
    )

    assert result["delivery"]["ok"] is True
    assert uploads == [{"file_path": str(artifact_path), "to_user_id": "wx-user-3@im.wechat", "media_type": 1}]
    assert len(sends) == 2
    assert [body["msg"]["item_list"][0]["type"] for body in sends] == [2, 1]


def test_bridge_direct_outbound_weixin_does_not_send_text_before_attachment_preflight(monkeypatch, temp_home: Path) -> None:
    _app, _quest_id = _setup_app(
        temp_home,
        connector_name="weixin",
        extra={"bot_token": "wx-token", "account_id": "wx-bot-1@im.bot"},
    )
    app = DaemonApp(temp_home)
    remember_weixin_context_token(
        temp_home / "logs" / "connectors" / "weixin",
        user_id="wx-user-4@im.wechat",
        context_token="ctx-token-4",
        account_id="wx-bot-1@im.bot",
    )

    sends: list[dict] = []

    def fake_send_weixin_message(*, base_url, token, body, route_tag=None, timeout_ms=15_000):  # noqa: ANN001
        sends.append(body)
        return {}

    monkeypatch.setattr("deepscientist.bridges.connectors.send_weixin_message", fake_send_weixin_message)

    result = app.channels["weixin"].send(
        {
            "conversation_id": "weixin:direct:wx-user-4@im.wechat",
            "message": "should stay unsent",
            "attachments": [
                {
                    "path": str(temp_home / "missing.png"),
                    "connector_delivery": {"weixin": {"media_kind": "image"}},
                }
            ],
        }
    )

    assert result["delivery"]["ok"] is False
    assert "does not exist" in str(result["delivery"]["error"] or "")
    assert sends == []


def test_bridge_direct_outbound_weixin_retries_file_send_after_ret_minus_2(monkeypatch, temp_home: Path) -> None:
    _app, _quest_id = _setup_app(
        temp_home,
        connector_name="weixin",
        extra={"bot_token": "wx-token", "account_id": "wx-bot-1@im.bot"},
    )
    app = DaemonApp(temp_home)
    remember_weixin_context_token(
        temp_home / "logs" / "connectors" / "weixin",
        user_id="wx-user-5@im.wechat",
        context_token="ctx-token-5",
        account_id="wx-bot-1@im.bot",
    )

    file_path = temp_home / "report.md"
    file_path.write_bytes(b"# retry")

    uploads: list[dict] = []
    sends: list[dict] = []

    def fake_upload_local_media_to_weixin(*, file_path, to_user_id, base_url, cdn_base_url, token, media_type, route_tag=None, timeout_ms=15_000):  # noqa: ANN001
        uploads.append({"file_path": str(file_path), "to_user_id": to_user_id, "media_type": media_type})
        return {
            "download_param": "download-report.md",
            "aes_key_base64": "YWJjZGVmZ2hpamtsbW5vcA==",
            "ciphertext_size": 32,
            "file_size": Path(file_path).stat().st_size,
        }

    def fake_send_weixin_message(*, base_url, token, body, route_tag=None, timeout_ms=15_000):  # noqa: ANN001
        sends.append(body)
        item_type = int(body["msg"]["item_list"][0]["type"])
        file_attempts = sum(1 for item in sends if int(item["msg"]["item_list"][0]["type"]) == 4)
        if item_type == 4 and file_attempts == 1:
            raise RuntimeError("Weixin sendmessage failed with ret=-2 errcode=0")
        return {}

    monkeypatch.setattr("deepscientist.bridges.connectors.upload_local_media_to_weixin", fake_upload_local_media_to_weixin)
    monkeypatch.setattr("deepscientist.bridges.connectors.send_weixin_message", fake_send_weixin_message)
    monkeypatch.setattr("deepscientist.bridges.connectors.time.sleep", lambda _seconds: None)

    result = app.channels["weixin"].send(
        {
            "conversation_id": "weixin:direct:wx-user-5@im.wechat",
            "message": "retry file upload test",
            "attachments": [
                {
                    "path": str(file_path),
                    "connector_delivery": {"weixin": {"media_kind": "file"}},
                }
            ],
        }
    )

    assert result["delivery"]["ok"] is True
    assert uploads == [{"file_path": str(file_path), "to_user_id": "wx-user-5@im.wechat", "media_type": 3}]
    assert [body["msg"]["item_list"][0]["type"] for body in sends] == [4, 4, 1]
