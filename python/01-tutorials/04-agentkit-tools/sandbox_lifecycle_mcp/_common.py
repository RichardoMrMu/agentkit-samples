"""编号输出、凭据脱敏及原子状态持久化。"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

SECRETS: set[str] = set()
SENSITIVE_NAME = re.compile(
    r"secret|token|password|authorization|cookie|api.?key|access.?key", re.IGNORECASE
)


def remember_secret(value: str) -> str:
    if value:
        SECRETS.add(value)
    return value


def redact_schema(value: Any, *, sensitive: bool = False) -> Any:
    """保留 NextToken 等字段的 schema；敏感字段的默认值和示例仍需隐藏。"""
    if isinstance(value, list):
        return [redact_schema(item, sensitive=sensitive) for item in value]
    if not isinstance(value, dict):
        return redact(value)
    result = {}
    for key, item in value.items():
        if key == "properties" and isinstance(item, dict):
            result[key] = {
                name: redact_schema(schema, sensitive=bool(SENSITIVE_NAME.search(name)))
                for name, schema in item.items()
            }
        elif sensitive and key in {"default", "const", "example", "examples", "enum"}:
            result[key] = "<redacted>"
        else:
            result[key] = redact_schema(item, sensitive=sensitive)
    return result


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: redact_schema(item)
            if key in {"inputSchema", "outputSchema"}
            else "<redacted>"
            if SENSITIVE_NAME.search(str(key))
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        # MCP text 和 InvokeTool.Result 可能是内嵌的 JSON 字符串。
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            return json.dumps(redact(parsed), ensure_ascii=False)
        for secret in sorted(SECRETS, key=len, reverse=True):
            value = value.replace(secret, "<redacted>")
        value = re.sub(r"(?i)\b(Bearer|Basic)\s+[^\s\"'<>]+", r"\1 <redacted>", value)
        value = re.sub(
            r"(?i)([?&](?:authorization|token|api_key|signature)=)[^&#\s]+",
            r"\1<redacted>",
            value,
        )
        return value
    return value


def emit(message: str, *, json_output: bool = False, **fields: Any) -> None:
    event = redact({"message": message, **fields})
    if json_output:
        print(json.dumps(event, ensure_ascii=False), flush=True)
    else:
        print(f"[{event.pop('phase', '完成')}] {event.pop('message')}", flush=True)
        for key, value in event.items():
            print(
                f"  {key}: {json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value}",
                flush=True,
            )


def load_state(config, *, required: bool = True) -> dict:
    path = config.state_path
    if not path.exists() and not required:
        return {}
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        raise ValueError(
            f"状态不存在或无效：{path}；先运行 01_create_session.py"
        ) from None
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise ValueError("不支持的状态文件格式")
    if state.get("scope") != config.scope:
        raise ValueError("状态属于其他 MCP URL / Tool；请指定另一个 state_file")
    return state


def save_state(config, state: dict) -> None:
    path: Path = config.state_path
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(redact(state), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
