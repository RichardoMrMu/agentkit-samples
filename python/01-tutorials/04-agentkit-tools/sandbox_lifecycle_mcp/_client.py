"""标准 MCP Streamable HTTP 客户端；发现 schema 后才发 tools/call。"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
import jsonschema
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from _display import exchange
from _identity import get_token


class ToolError(RuntimeError):
    def __init__(self, operation: str, code: str) -> None:
        self.code = code
        super().__init__(f"{operation}: {code}")


def result_payload(result, operation: str) -> dict:
    payload = result.structuredContent
    if payload is None:
        blocks = [item.text for item in result.content if item.type == "text"]
        if len(blocks) != 1:
            raise ToolError(operation, "需要单个 JSON text 或 structuredContent")
        try:
            payload = json.loads(blocks[0])
        except ValueError:
            raise ToolError(operation, "MCP 工具返回非 JSON 内容") from None
    if not isinstance(payload, dict):
        raise ToolError(operation, "MCP 工具结果必须是对象")
    return payload


def decode_result(result, operation: str) -> dict:
    payload = result_payload(result, operation)
    metadata = payload.get("ResponseMetadata") or {}
    error = metadata.get("Error") if isinstance(metadata, dict) else None
    error = error or payload.get("Error") or payload.get("error")
    if error:
        code = (
            error.get("Code", error.get("code", "ToolError"))
            if isinstance(error, dict)
            else "ToolError"
        )
        raise ToolError(operation, str(code))
    if result.isError:
        raise ToolError(operation, "MCP isError=true")
    if "ResponseMetadata" in payload:
        payload = payload.get("Result")
        if not isinstance(payload, dict):
            raise ToolError(operation, "缺少 Result 对象")
    return payload


class SandboxClient:
    def __init__(self, session, config):
        self.session = session
        self.config = config
        self.tools = {}

    async def discover(self) -> None:
        cursor = None
        seen = set()
        while True:
            page = await self.session.list_tools(cursor=cursor)
            self.tools.update({tool.name: tool for tool in page.tools})
            cursor = page.nextCursor
            if not cursor:
                return
            if cursor in seen:
                raise RuntimeError("tools/list 返回重复 cursor")
            seen.add(cursor)

    def tool(self, operation: str):
        name = self.config.get(f"tools.{operation}", operation)
        if name not in self.tools:
            raise ToolError(
                operation,
                f"服务未暴露 {name}；检查 config.yaml 的 tools 映射和服务工具列表",
            )
        return self.tools[name]

    def arguments(self, operation: str, body: dict) -> dict:
        tool = self.tool(operation)
        # 网关绑定 Tool 时无需 ToolId；多 Tool 网关可在配置里显式指定。
        tool_id = self.config.tool_id
        body = {**({"ToolId": tool_id} if tool_id else {}), **body}
        schema = tool.inputSchema
        arguments = {"_body": body} if "_body" in schema.get("properties", {}) else body
        try:
            jsonschema.validate(arguments, schema)
        except (jsonschema.ValidationError, jsonschema.SchemaError):
            raise ToolError(
                operation,
                "参数不符合服务 schema；检查 _body、ToolId 与服务字段要求",
            ) from None
        return arguments

    async def call(self, operation: str, body: dict) -> dict:
        arguments = self.arguments(operation, body)
        exchange(
            self.config,
            "request",
            operation,
            {
                "method": "tools/call",
                "params": {"name": self.tool(operation).name, "arguments": arguments},
            },
        )
        # 不重试 tools/call；连接失败时无法确定远端 mutation 是否已经执行。
        payload = None
        try:
            result = await self.session.call_tool(self.tool(operation).name, arguments)
            payload = result_payload(result, operation)
            value = decode_result(result, operation)
        except Exception as exc:
            exchange(
                self.config,
                "response",
                operation,
                {
                    "error": exc.code
                    if isinstance(exc, ToolError)
                    else type(exc).__name__,
                    "response": payload,
                },
                is_error=True,
            )
            raise
        exchange(self.config, "response", operation, payload)
        return value


def make_http_client(headers=None, timeout=None, auth=None):
    # MCP SDK 允许注入 factory，防止重定向时将凭据发送到另一入口。
    return httpx.AsyncClient(
        headers=headers, timeout=timeout, auth=auth, follow_redirects=False
    )


@asynccontextmanager
async def connect(config):
    url = config.url
    token = get_token(config)
    timeout = config.integer("http_timeout_seconds", 60)
    async with streamablehttp_client(  # noqa: SIM117 -- 第二个上下文依赖传输返回值。
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=timedelta(seconds=timeout),
        sse_read_timeout=timedelta(seconds=timeout),
        httpx_client_factory=make_http_client,
    ) as (read, write, _):
        async with ClientSession(
            read, write, read_timeout_seconds=timedelta(seconds=timeout)
        ) as session:
            await session.initialize()
            client = SandboxClient(session, config)
            await client.discover()
            yield client
