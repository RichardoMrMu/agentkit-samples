#!/usr/bin/env python3
"""使用 Ark SDK 调用 Harness Infra Session/Event 数据面。"""

from __future__ import annotations

import time
import uuid
from typing import Any

from _common import (
    emit,
    parser,
    run,
    wait_progress,
)
from _config import validated_config
from _workflow import REQUIRED_SANDBOX_TOOLS, emit_tool_executions, missing_tool_names


def _sdk_error(exc: Exception) -> RuntimeError:
    status = getattr(exc, "status_code", None)
    suffix = f" (HTTP {status})" if isinstance(status, int) else ""
    return RuntimeError(f"Ark SDK request failed: {type(exc).__name__}{suffix}")


def _json_object(value: Any, operation: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"Ark SDK {operation} response is not a JSON object")
    return value


def _event_text(event: dict[str, Any]) -> str:
    content = event.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def main() -> None:
    cli = parser(__doc__)
    cli.add_argument("--prompt", help="默认读取 config.yaml")
    cli.add_argument("--expected-substring", help="默认读取 config.yaml")
    args = cli.parse_args()
    config = validated_config(
        require_model_key_name=False,
        require_credentials=False,
    )
    prompt = args.prompt if args.prompt is not None else config.session.prompt
    expected_substring = (
        args.expected_substring
        if args.expected_substring is not None
        else config.session.expected_substring
    )
    if args.dry_run:
        emit(
            "dry_run",
            "Ark SDK Harness 数据面对话计划",
            json_output=args.json_output,
            actions=[
                "读取 config.yaml 中的 Session 和 Environment API Key",
                "Ark.post 发送 Event",
                "Ark.get 轮询持久化 Events",
            ],
            session_id=config.session.id or "<config.yaml:session.id>",
            prompt=prompt,
            status="dry_run（未访问网络或状态文件）",
        )
        return

    try:
        from volcenginesdkarkruntime import Ark
    except ImportError:
        raise RuntimeError(
            "Ark SDK is not installed; run uv pip install -r requirements.txt"
        ) from None

    environment_key = config.environment.env_api_key_value
    session_id = config.session.id
    if not config.session.endpoint_base_url or not environment_key or not session_id:
        raise RuntimeError(
            "Session data is unavailable; run 01_create_resources.py first"
        )

    sdk = Ark(
        api_key=environment_key,
        base_url=config.session.endpoint_base_url,
        max_retries=0,
        timeout=60,
    )
    event_id = f"event-send-{uuid.uuid4().hex}"
    try:
        emit(
            "request",
            "Ark SDK 发起对话",
            json_output=args.json_output,
            id=session_id,
            prompt=prompt,
        )
        started_at = time.monotonic()
        try:
            sdk.post(
                f"/v1/sessions/{session_id}/events",
                cast_to=object,
                body={
                    "events": [
                        {
                            "id": event_id,
                            "type": "user.message",
                            "content": [{"type": "text", "text": prompt}],
                        }
                    ]
                },
                options={"headers": {"x-api-key": environment_key}},
            )
        except Exception as exc:
            if isinstance(exc, RuntimeError):
                raise
            raise _sdk_error(exc) from None

        answer = ""
        emitted_tool_uses: set[str] = set()
        successful_tools: set[str] = set()
        deadline = time.monotonic() + 5 * 60
        with wait_progress("Ark SDK 智能体回复", json_output=args.json_output):
            while time.monotonic() < deadline:
                try:
                    page = _json_object(
                        sdk.get(
                            f"/v1/sessions/{session_id}/events",
                            cast_to=object,
                            params=[("limit", "100"), ("order", "desc")],
                            options={"headers": {"x-api-key": environment_key}},
                        ),
                        "List Events",
                    )
                except Exception as exc:
                    if isinstance(exc, RuntimeError):
                        raise
                    raise _sdk_error(exc) from None
                raw_events = page.get("data")
                if not isinstance(raw_events, list):
                    raise TypeError("List Events response is missing data[]")
                events = [event for event in raw_events if isinstance(event, dict)]
                event_ids = [event.get("id") for event in events]
                if event_id not in event_ids:
                    time.sleep(2)
                    continue
                turn_events = list(reversed(events[: event_ids.index(event_id)]))
                errors = [
                    event
                    for event in turn_events
                    if event.get("type") in {"agent.error", "session.error"}
                ]
                if errors:
                    raise RuntimeError(f"conversation failed: {errors[-1].get('type')}")
                successful_tools = emit_tool_executions(
                    turn_events,
                    emitted_tool_uses,
                    json_output=args.json_output,
                )
                messages = [
                    _event_text(event)
                    for event in turn_events
                    if event.get("type") == "agent.message"
                ]
                if messages:
                    answer = messages[-1]
                completed = any(
                    event.get("type") == "session.status_idle"
                    and isinstance(event.get("stop_reason"), dict)
                    and event["stop_reason"].get("type") == "end_turn"
                    for event in turn_events
                )
                if answer and completed:
                    break
                time.sleep(2)
            else:
                raise TimeoutError("timed out waiting for agent.message and end_turn")
        verify_tools = (
            config.session.verify_sandbox_tools and prompt == config.session.prompt
        )
        if verify_tools:
            missing = missing_tool_names(successful_tools)
            if missing:
                raise RuntimeError(
                    "sandbox tool-result validation failed; "
                    "missing successful tools: " + ", ".join(missing)
                )
        if expected_substring and expected_substring not in answer:
            raise RuntimeError(f"model reply did not contain {expected_substring!r}")
        emit(
            "verified",
            "Ark SDK Harness 对话校验通过",
            json_output=args.json_output,
            id=session_id,
            prompt=prompt,
            answer=answer,
            tool_names=sorted(successful_tools),
            verification={
                "required_tools": list(REQUIRED_SANDBOX_TOOLS) if verify_tools else [],
                "expected_substring": expected_substring,
                "stop_reason": "end_turn",
                "elapsed_seconds": round(time.monotonic() - started_at, 1),
            },
            status="completed",
        )
    finally:
        environment_key = ""
        sdk.close()


if __name__ == "__main__":
    run(main)
