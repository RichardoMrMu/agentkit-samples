"""每个入口只运行一个步骤；本地状态保留已受理操作供后续恢复。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from builtins import BaseExceptionGroup
from datetime import UTC, datetime

import httpx

from _client import ToolError, connect
from _common import emit, load_state, redact, remember_secret, save_state
from _config import Config

NOT_FOUND = {"InvalidResource.NotFound", "ResourceNotFound", "SessionNotFound"}
READY = {"ready", "running", "available", "active", "succeeded", "success"}


def now() -> str:
    return datetime.now(UTC).isoformat()


def require_id(value: dict, name: str) -> str:
    result = value.get(name)
    if not isinstance(result, str) or not result.strip():
        raise RuntimeError(f"缺少 {name}")
    return result


def check_session(value: dict, session_id: str) -> None:
    if value.get("SessionId") and value["SessionId"] != session_id:
        raise RuntimeError("响应 SessionId 与请求不一致")


async def wait_session(client, config, session_id: str, targets: set, report) -> dict:
    deadline = time.monotonic() + config.integer("wait_timeout_seconds", 600)
    interval = config.integer("poll_interval_seconds", 5)
    previous = None
    while True:
        try:
            value = await client.call("getSession", {"SessionId": session_id})
        except ToolError as exc:
            if targets == {"deleted"} and exc.code in NOT_FOUND:
                return {"SessionId": session_id, "Status": "Deleted"}
            raise
        check_session(value, session_id)
        status = str(value.get("Status", "")).lower()
        if status in targets:
            return value
        if status in {"failed", "error", "deleted", "terminated"}:
            raise RuntimeError(f"Session 进入非预期状态：{status}")
        if status != previous:
            report(
                "等待 Session",
                phase="等待",
                session_id=session_id,
                status=status or "未知",
            )
            previous = status
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("等待 Session 超时；已受理操作保留在状态文件中")
        await asyncio.sleep(min(interval, remaining))


async def list_pages(client, operation: str, body: dict, field: str) -> list:
    values, seen = [], set()
    request = {**body, "MaxResults": 100}
    while True:
        page = await client.call(operation, request)
        items = page.get(field)
        if not isinstance(items, list):
            raise TypeError(f"{operation} 缺少 {field} 列表；请检查服务响应契约")
        values.extend(items)
        token = page.get("NextToken")
        if not token:
            return values
        if not isinstance(token, str) or token in seen:
            raise RuntimeError(f"{operation} 返回无效或重复 NextToken")
        seen.add(token)
        request["NextToken"] = token


def invocation_result(value: dict) -> dict:
    result = value.get("Result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            raise RuntimeError("InvokeTool.Result 不是 JSON") from None
    if not isinstance(result, dict):
        raise TypeError("InvokeTool 缺少 Result 对象")
    data = result.get("data") or {}
    if result.get("success") is False or (
        isinstance(data, dict)
        and str(data.get("status", "")).lower() in {"error", "failed"}
    ):
        raise RuntimeError("沙箱代码执行失败；查看脱敏执行结果")
    return result


TOOL_STEPS = {
    "create": "createSession",
    "list": "listSessions",
    "get": "getSession",
    "invoke": "invokeTool",
    "delete": "deleteSession",
}
PLANS = {step: [tool] for step, tool in TOOL_STEPS.items()}


async def execute(step: str, args, config) -> None:
    def report(message, **fields):
        emit(message, json_output=args.json_output, **fields)

    if args.dry_run:
        report(
            "执行计划",
            phase="dry-run",
            step=step,
            auth_mode=config.auth_mode,
            actions=PLANS[step],
            state_file=str(config.state_path),
            note="未访问网络、未获取凭据、未写状态",
        )
        return
    state = load_state(config, required=False)
    session_id = args.session_id or state.get("session_id", "")
    if step in {"get", "invoke", "delete"} and not session_id:
        raise ValueError("请先创建 Session 或传 --session-id")
    if step == "create" and state and state.get("status") != "deleted":
        raise ValueError(
            "已有未清理记录；请确认删除完成，或使用独立 --state-file 保留原实例记录"
        )
    if step in {"invoke", "delete"} and state.get("session_id") != session_id:
        if state and state.get("status") != "deleted":
            raise ValueError("--session-id 与状态不一致；请用独立 --state-file")
        state = {"schema_version": 1, "scope": config.scope, "session_id": session_id}

    report("连接 MCP 并发现工具", phase="01", auth_mode=config.auth_mode)
    async with connect(config) as client:
        operation = TOOL_STEPS[step]
        if step == "list":
            values = await list_pages(client, operation, {}, "SessionInfos")
            report(
                operation,
                phase="02",
                **(
                    {"sessions": values} if args.json_output else {"count": len(values)}
                ),
            )
            return
        if step == "get":
            if args.wait:
                targets = READY if args.wait == "ready" else {"deleted"}
                value = await wait_session(client, config, session_id, targets, report)
            else:
                value = await client.call(operation, {"SessionId": session_id})
            check_session(value, session_id)
            # 仅同步当前跟踪实例；查询其他实例不覆盖已有记录。
            if state.get("session_id") == session_id:
                status = str(value.get("Status", "")).lower()
                if status:
                    state.update(
                        status="ready" if status in READY else status, updated_at=now()
                    )
                    save_state(config, state)
            report(
                operation,
                phase="02",
                **(
                    {"session": value}
                    if args.json_output
                    else {
                        "session_id": session_id,
                        "status": value.get("Status", "unknown"),
                    }
                ),
            )
            return
        if step == "create":
            body = {
                "Ttl": config.integer("sandbox.ttl_seconds", 28800),
                "TtlUnit": "second",
                "UserSessionId": args.user_session_id
                or f"mcp-demo-{uuid.uuid4().hex[:16]}",
            }
            client.arguments(operation, body)
            state = {
                "schema_version": 1,
                "scope": config.scope,
                "status": "create_requested",
                "user_session_id": body["UserSessionId"],
                "created_at": now(),
            }
            save_state(config, state)
            value = await client.call(operation, body)
            session_id = require_id(value, "SessionId")
            state.update(session_id=session_id, status="creating")
        elif step == "invoke":
            body = {
                "SessionId": session_id,
                "OperationType": "RunCode",
                "OperationPayload": json.dumps(
                    {
                        "code": args.code
                        if args.code is not None
                        else config.get(
                            "sandbox.code", "print('Hello from AgentKit sandbox!')"
                        ),
                        "kernel_name": config.get("sandbox.kernel_name", "python3"),
                        "timeout": config.integer("sandbox.invoke_timeout_seconds", 30),
                    },
                    ensure_ascii=False,
                ),
            }
            value = await client.call(operation, body)
            check_session(value, session_id)
            if args.json_output:
                report("代码执行返回", phase="02", result=value)
            invocation_result(value)
            state["invoked_at"] = now()
        else:
            body = {"SessionId": session_id}
            client.arguments(operation, body)
            state["status"] = "delete_requested"
            save_state(config, state)
            try:
                value = await client.call(operation, body)
            except ToolError as exc:
                if exc.code not in NOT_FOUND:
                    raise
                value = {"SessionId": session_id, "Status": "Deleted"}
                state["status"] = "deleted"
            check_session(value, session_id)
        state["updated_at"] = now()
        save_state(config, state)
        report(
            operation,
            phase="完成"
            if step == "invoke" or state.get("status") == "deleted"
            else "已受理",
            session_id=session_id,
            status=state.get("status", "unknown"),
            **({"result": value} if args.json_output else {}),
            state_file=str(config.state_path),
        )


def main(step: str) -> None:
    cli = argparse.ArgumentParser(description=" → ".join(PLANS[step]))
    cli.add_argument("--config", help="YAML 配置路径")
    cli.add_argument(
        "--state-file", help="覆盖状态文件路径；相对路径以配置文件目录为基准"
    )
    cli.add_argument(
        "--json", dest="json_output", action="store_true", help="输出脱敏 JSON 事件"
    )
    cli.add_argument(
        "--dry-run", action="store_true", help="只显示计划，不访问网络或写状态"
    )
    if step in {"get", "invoke", "delete"}:
        cli.add_argument(
            "--session-id", default="", help="API 返回的 SessionId；默认读取状态"
        )
    if step == "get":
        cli.add_argument(
            "--wait",
            choices=("ready", "deleted"),
            help="轮询同一个 getSession 工具直到就绪或删除完成",
        )
    cli.add_argument(
        "--auth",
        choices=("keyauth", "oauth", "bearer"),
        help="本次调用的鉴权方式；默认读取配置",
    )
    if step == "create":
        cli.add_argument(
            "--user-session-id", default="", help="逻辑会话 ID；默认生成唯一值"
        )
    if step == "invoke":
        cli.add_argument("--code", help="在沙箱执行的 Python 代码")
    cli.set_defaults(session_id="", wait=None)
    args = cli.parse_args()
    try:
        config = Config(args.config)
        config.auth_override = args.auth
        config.json_output = args.json_output
        if args.state_file:
            config.state_override = args.state_file
        for name, value in os.environ.items():
            if any(
                word in name.upper() for word in ("SECRET", "TOKEN", "KEY", "PASSWORD")
            ):
                remember_secret(value)
        asyncio.run(execute(step, args, config))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:  # noqa: BLE001 -- CLI 边界统一脱敏 MCP/anyio 异常。
        # anyio 的 TaskGroup 会包装原始异常；不输出 SDK 请求对象/任意 traceback。
        while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
            exc = exc.exceptions[0]
        if isinstance(exc, httpx.HTTPStatusError):
            emit(
                f"MCP HTTP {exc.response.status_code}；检查完整 MCP URL、token 是否过期及网关入站鉴权",
                phase="错误",
                json_output=args.json_output,
            )
            sys.exit(1)
        message = (
            str(exc)
            if isinstance(exc, (TypeError, ValueError, RuntimeError, OSError))
            and not isinstance(exc, httpx.HTTPError)
            else f"连接或协议失败（{type(exc).__name__}）；检查 MCP URL、鉴权和工具 schema"
        )
        emit(str(redact(message)), phase="错误", json_output=args.json_output)
        sys.exit(1)
