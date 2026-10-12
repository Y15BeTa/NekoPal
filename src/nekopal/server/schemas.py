"""请求体校验（手工解析，避免把 Pydantic 模型散落各处）。

契约见设计文档 4.2 / 4.3。
"""

from __future__ import annotations

from typing import Any, Dict

from nekopal.core.models import ReadRequest


class InvalidRequest(ValueError):
    """对应 400 ``INVALID_REQUEST``。"""


def parse_read_request(data: Any, default_voice: str) -> ReadRequest:
    if not isinstance(data, dict):
        raise InvalidRequest("请求体必须是 JSON 对象")

    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        raise InvalidRequest("`text` 不能为空")

    voice = data.get("voice")
    if voice is not None and not isinstance(voice, str):
        raise InvalidRequest("`voice` 必须是字符串")

    source = data.get("source") or {}
    if not isinstance(source, dict):
        raise InvalidRequest("`source` 必须是对象")

    options = data.get("options") or {}
    if not isinstance(options, dict):
        raise InvalidRequest("`options` 必须是对象")

    client_id = data.get("client_id")
    if client_id is not None and not isinstance(client_id, str):
        raise InvalidRequest("`client_id` 必须是字符串")

    return ReadRequest(
        text=text,
        voice=voice or default_voice,
        source=dict(source),
        options=dict(options),
        client_id=client_id,
    )


def parse_append_request(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise InvalidRequest("请求体必须是 JSON 对象")
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        raise InvalidRequest("`text` 不能为空")
    return {"text": text, "options": dict(data.get("options") or {})}
