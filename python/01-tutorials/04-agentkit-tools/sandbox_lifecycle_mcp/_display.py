"""突出显示 MCP tools/call 的请求和返回；渲染前统一脱敏。"""

import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from _common import redact


def readable_json(value: Any) -> Any:
    """只在展示层展开内嵌 JSON；实际请求和 NDJSON 保留字符串类型。"""
    if isinstance(value, dict):
        return {key: readable_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [readable_json(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        if isinstance(parsed, (dict, list)):
            return readable_json(parsed)
    return value


def exchange(
    config, phase: str, operation: str, payload: Any, *, is_error=False
) -> None:
    event = redact(
        {
            "phase": phase,
            "operation": operation,
            "payload": payload,
            "is_error": is_error,
        }
    )
    if config.json_output:
        print(json.dumps(event, ensure_ascii=False), flush=True)
        return
    label = "请求 REQUEST" if phase == "request" else "返回 RESPONSE"
    style = (
        "bold red" if is_error else "bold cyan" if phase == "request" else "bold green"
    )
    if is_error:
        label += " · 错误"
    console = Console(markup=False, highlight=False)
    console.print()
    console.print(
        Panel(
            Syntax(
                json.dumps(
                    readable_json(event["payload"]), ensure_ascii=False, indent=2
                ),
                "json",
                theme="ansi_dark",
                word_wrap=True,
                background_color="default",
            ),
            title=Text(f"{label} · {event['operation']}", style=style),
            subtitle=Text("已脱敏 · 内嵌 JSON 展开显示", style="dim"),
            border_style=style,
            padding=(1, 2),
        )
    )
    console.print()
