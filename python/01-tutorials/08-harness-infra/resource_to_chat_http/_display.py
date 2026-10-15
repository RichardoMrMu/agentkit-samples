"""面向终端的展示层；输入必须已经脱敏，JSON 输出不经过此模块。"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from rich import box
from rich.console import Console
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console(markup=False, highlight=False)
error_console = Console(stderr=True, markup=False, highlight=False)
_shown_parameters: dict[tuple[str, str], dict[str, Any]] = {}

LABELS = {
    "name": "名称",
    "id": "ID",
    "title": "标题",
    "result": "处理",
    "state_file": "状态文件",
    "run_id": "运行 ID",
    "agent_loop": "Agent Loop",
    "model_id": "模型",
    "model_base_url": "模型地址",
    "base_url": "访问地址",
    "role_name": "执行角色",
    "version": "版本",
    "reused": "复用已有资源",
    "Type": "类型",
    "CpuMilli": "CPU (milli)",
    "MemoryMb": "内存 (MB)",
    "MemoryMiB": "内存 (MiB)",
    "MinReplicas": "最小副本数",
    "MaxReplicas": "最大副本数",
    "environment": "Environment",
    "endpoint": "Endpoint",
    "harness": "Harness",
    "runtime_template": "RuntimeTemplate",
    "runtime_type": "运行时类型",
    "ark_api_key_name": "模型 Key 名称",
    "event_id": "事件 ID",
    "event_type": "事件类型",
    "stop_reason": "结束原因",
    "expected_substring": "预期回复包含",
    "revision": "修订版本",
    "desired_generation": "目标版本",
    "observed_generation": "已应用版本",
    "details": "详情",
    "sequence": "事件序号",
}

STATUSES = {
    "ready": ("就绪", "green"),
    "active": ("可用", "green"),
    "idle": ("空闲", "green"),
    "deleted": ("已删除", "green"),
    "archived": ("已归档", "green"),
    "not_found": ("不存在", "dim"),
    "resources_ready": ("资源全部就绪", "green"),
    "cleaned": ("清理完成", "green"),
    "chat_completed": ("对话完成", "green"),
    "initializing": ("初始化", "cyan"),
    "building": ("构建中", "yellow"),
    "creating": ("创建中", "yellow"),
    "deleting": ("删除中", "yellow"),
    "failed": ("失败", "red"),
    "deletefailed": ("删除失败", "red"),
}


def status_text(value: Any) -> Text:
    raw = str(value)
    label, style = STATUSES.get(raw.lower(), (raw, "dim"))
    marker = "✓" if style == "green" else "×" if style == "red" else "·"
    return Text(f"{marker} {label}", style=style)


def progress_text(description: str, status: Any = None) -> Text:
    text = Text(f"  {description}…", style="cyan")
    if status is not None:
        text.append("  ")
        text.append_text(status_text(status))
    return text


def _value(value: Any, *, style: str = "") -> Text:
    if isinstance(value, bool):
        value = "是" if value else "否"
    elif isinstance(value, list):
        value = "、".join(str(item) for item in value) if value else "无"
    return Text(str(value), style=style, overflow="fold")


def _rows(fields: dict[str, Any]) -> Iterator[tuple[str, Text]]:
    for key, value in fields.items():
        if value is None:
            continue
        label = LABELS.get(key, key)
        if isinstance(value, dict):
            if "id" in value:
                identity = Text(overflow="fold")
                if value.get("name"):
                    identity.append(str(value["name"]) + "\n", style="dim")
                identity.append(str(value["id"]), style="cyan")
                yield label, identity
            else:
                yield from _rows(value)
        else:
            yield label, _value(value, style="cyan" if key.endswith("id") else "")


def _fields(fields: dict[str, Any]) -> None:
    if not fields:
        return
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", width=16, overflow="fold")
    table.add_column(overflow="fold")
    for label, value in _rows(fields):
        table.add_row(label, value)
    console.print(Padding(table, (0, 2), expand=False))


def _resource(fields: dict[str, Any]) -> None:
    title = Text(str(fields["resource"]), style="bold")
    if fields.get("status"):
        title.append("  ")
        title.append_text(status_text(fields["status"]))
    console.print(Padding(title, (0, 2), expand=False))
    _fields({key: fields[key] for key in ("name", "id", "title") if fields.get(key)})
    identity = (str(fields["resource"]), str(fields.get("id", "")))
    previous = _shown_parameters.setdefault(identity, {})
    parameters = fields.get("parameters") or {}
    changed = {
        key: value
        for key, value in parameters.items()
        if key not in previous or previous[key] != value
    }
    if changed:
        label = "本步参数" if previous else "核心配置"
        console.print(Padding(Text(label, style="dim"), (1, 2, 0, 2), expand=False))
        _fields(changed)
        previous.update(parameters)
    status_label = STATUSES.get(str(fields.get("status", "")).lower(), ("", ""))[0]
    if fields.get("result") and fields["result"] != status_label:
        console.print(
            Padding(Text(str(fields["result"]), style="dim"), (0, 2), expand=False)
        )


def _summary(resources: list[dict[str, Any]]) -> None:
    # Name and ID share a column so complete identifiers fit ordinary 80-column terminals.
    table = Table(
        box=box.SIMPLE_HEAD,
        expand=True,
        padding=(0, 1),
        leading=1,
        header_style="bold",
        border_style="dim",
        pad_edge=False,
    )
    table.add_column("资源", max_width=19, overflow="fold", style="bold")
    table.add_column("名称 / ID", ratio=1, overflow="fold")
    table.add_column("状态 / 处理", max_width=14, overflow="fold")
    for item in resources:
        identity = Text(overflow="fold")
        if item.get("name"):
            identity.append(str(item["name"]) + "\n")
        identity.append(str(item.get("id") or "未记录"), style="cyan")
        if item.get("title"):
            identity.append("\n" + str(item["title"]), style="dim")
        status = status_text(item.get("status", "未记录"))
        if item.get("result"):
            status.append("\n" + str(item["result"]), style="dim")
        table.add_row(Text(str(item.get("resource", ""))), identity, status)
    console.print(Padding(table, (0, 2)))


def display_event(
    phase: str, message: str, fields: dict[str, Any], *, step: int | None, heading: bool
) -> None:
    if phase == "poll":
        line = Text("  · " + message, style="dim")
        if fields.get("status"):
            line.append("  ")
            line.append_text(status_text(fields["status"]))
        console.print(line)
        return
    if heading:
        console.print()
        title = Text(
            f"{step:02d}  {message}" if step is not None else message, style="bold cyan"
        )
        console.rule(title, align="left", style="dim")
        console.print()
    if fields.get("resource"):
        _resource(fields)
    elif "resources" in fields:
        _summary(fields["resources"])
    else:
        ordinary = {
            k: v
            for k, v in fields.items()
            if k not in {"actions", "parameters", "status", "prompt", "answer"}
        }
        _fields({**ordinary, **(fields.get("parameters") or {})})
        if fields.get("status"):
            console.print(Padding(status_text(fields["status"]), (0, 2)))
    for index, action in enumerate(fields.get("actions", []), 1):
        console.print(Text(f"  {index:02d}  {action}"))
    for key, label, color in (
        ("prompt", "问 · 用户", "blue"),
        ("answer", "答 · 助手", "green"),
    ):
        if key in fields:
            console.print(
                Padding(
                    Panel(
                        _value(fields[key]),
                        title=Text(label),
                        title_align="left",
                        border_style=color,
                        box=box.ROUNDED,
                        padding=(0, 1),
                    ),
                    (0, 2),
                )
            )


def display_error(message: str) -> None:
    # Plain text objects prevent returned names, prompts, or errors being parsed as markup.
    error_console.print(Text("\n错误  " + message, style="bold red"), soft_wrap=True)
