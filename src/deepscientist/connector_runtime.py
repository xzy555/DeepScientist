from __future__ import annotations

from typing import Any


def infer_connector_transport(name: str, config: dict[str, Any] | None) -> str:
    normalized = str(name or "").strip().lower()
    payload = config or {}
    explicit = str(payload.get("transport") or "").strip().lower()
    if explicit:
        return explicit

    relay_url = str(payload.get("relay_url") or "").strip()
    mode = str(payload.get("mode") or "").strip().lower()
    public_callback_url = str(payload.get("public_callback_url") or "").strip()

    if normalized == "qq":
        return "gateway_direct"
    if normalized == "telegram":
        if relay_url and mode == "relay":
            return "relay"
        if public_callback_url or str(payload.get("webhook_secret") or "").strip():
            return "legacy_webhook"
        return "polling"
    if normalized == "discord":
        if relay_url and mode == "relay":
            return "relay"
        if str(payload.get("public_interactions_url") or "").strip() or str(payload.get("public_key") or "").strip():
            return "legacy_interactions"
        return "gateway"
    if normalized == "slack":
        if relay_url and mode == "relay":
            return "relay"
        if str(payload.get("app_token") or "").strip():
            return "socket_mode"
        if public_callback_url or str(payload.get("signing_secret") or "").strip():
            return "legacy_events_api"
        return "socket_mode"
    if normalized == "feishu":
        if relay_url and mode == "relay":
            return "relay"
        if (
            public_callback_url
            or str(payload.get("verification_token") or "").strip()
            or str(payload.get("encrypt_key") or "").strip()
        ):
            return "legacy_webhook"
        return "long_connection"
    if normalized == "whatsapp":
        provider = str(payload.get("provider") or "").strip().lower()
        if relay_url and mode == "relay" and provider == "relay":
            return "relay"
        if (
            provider == "meta"
            or str(payload.get("access_token") or "").strip()
            or str(payload.get("phone_number_id") or "").strip()
            or str(payload.get("verify_token") or "").strip()
            or public_callback_url
        ):
            return "legacy_meta_cloud"
        return "local_session"
    if relay_url and mode == "relay":
        return "relay"
    return "direct"


def parse_conversation_id(conversation_id: Any) -> dict[str, str] | None:
    raw = str(conversation_id or "").strip()
    parts = raw.split(":", 2)
    if len(parts) != 3:
        return None
    connector, chat_type, chat_id = parts
    if not connector or not chat_type or not chat_id:
        return None
    return {
        "conversation_id": raw,
        "connector": connector,
        "chat_type": chat_type,
        "chat_id": chat_id,
    }


def normalize_conversation_id(conversation_id: Any) -> str:
    raw = str(conversation_id or "").strip()
    if not raw:
        return "local:default"
    lowered = raw.lower()
    if lowered in {"web", "cli", "api", "command", "local", "local-ui", "tui-ink", "web-react", "tui-local"}:
        return "local:default"
    parsed = parse_conversation_id(raw)
    if parsed is not None:
        return f"{parsed['connector'].lower()}:{parsed['chat_type'].lower()}:{parsed['chat_id']}"
    if ":" in raw:
        return raw
    return f"{lowered}:default"


def conversation_identity_key(conversation_id: Any) -> str:
    normalized = normalize_conversation_id(conversation_id)
    parsed = parse_conversation_id(normalized)
    if parsed is None:
        return normalized.lower()
    return f"{parsed['connector'].lower()}:{parsed['chat_type'].lower()}:{parsed['chat_id'].lower()}"


def build_discovered_target(
    conversation_id: Any,
    *,
    source: str,
    is_default: bool = False,
    label: str | None = None,
    quest_id: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any] | None:
    parsed = parse_conversation_id(conversation_id)
    if parsed is None:
        return None
    target = {
        **parsed,
        "source": source,
        "sources": [source],
        "label": label or f"{parsed['chat_type']} · {parsed['chat_id']}",
    }
    if is_default:
        target["is_default"] = True
    if quest_id:
        target["quest_id"] = quest_id
    if updated_at:
        target["updated_at"] = updated_at
    return target


def merge_discovered_targets(items: list[dict[str, Any] | None]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        conversation_id = str(item.get("conversation_id") or "").strip()
        if not conversation_id:
            continue
        existing = merged.get(conversation_id)
        if existing is None:
            merged[conversation_id] = dict(item)
            continue
        sources = list(existing.get("sources") or [])
        for source in item.get("sources") or []:
            normalized = str(source or "").strip()
            if normalized and normalized not in sources:
                sources.append(normalized)
        existing["sources"] = sources
        existing["is_default"] = bool(existing.get("is_default")) or bool(item.get("is_default"))
        if not existing.get("quest_id") and item.get("quest_id"):
            existing["quest_id"] = item["quest_id"]
        if not existing.get("updated_at") and item.get("updated_at"):
            existing["updated_at"] = item["updated_at"]
        elif item.get("updated_at") and str(item["updated_at"]) > str(existing.get("updated_at") or ""):
            existing["updated_at"] = item["updated_at"]
        if existing.get("label") == f"{existing.get('chat_type')} · {existing.get('chat_id')}" and item.get("label"):
            existing["label"] = item["label"]
        if not existing.get("source") and item.get("source"):
            existing["source"] = item["source"]

    return sorted(
        merged.values(),
        key=lambda item: (
            0 if item.get("is_default") else 1,
            0 if str(item.get("chat_type") or "") == "direct" else 1,
            str(item.get("conversation_id") or ""),
        ),
    )
