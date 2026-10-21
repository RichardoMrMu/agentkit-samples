"""仅供本地协议测试的 MCP Server；不连接 AgentKit。"""

import base64
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

server = Server("sandbox-contract-test")
sessions = {}


def createSession(_body: dict) -> dict:
    session_id = f"s-test-{len(sessions) + 1}"
    sessions[session_id] = {
        "SessionId": session_id,
        "Status": "Ready",
        "UserSessionId": _body["UserSessionId"],
    }
    return {"ResponseMetadata": {}, "Result": sessions[session_id]}


def listSessions(_body: dict) -> dict:
    return {"SessionInfos": list(sessions.values())}


def getSession(_body: dict) -> dict:
    return sessions.get(_body["SessionId"]) or {
        "ResponseMetadata": {"Error": {"Code": "InvalidResource.NotFound"}}
    }


def invokeTool(_body: dict) -> dict:
    payload = json.loads(_body["OperationPayload"])
    assert _body["OperationType"] == "RunCode"
    assert isinstance(payload["code"], str)
    assert payload["kernel_name"] == "python3"
    # 模拟结果，不执行任意代码；失败分支测试 CLI 非零退出。
    success = payload["code"] != "fail-test"
    return {
        "SessionId": _body["SessionId"],
        "Result": json.dumps(
            {
                "success": success,
                "data": {
                    "status": "success" if success else "error",
                    "outputs": ["mock output"],
                },
            }
        ),
    }


def deleteSession(_body: dict) -> dict:
    sessions.pop(_body["SessionId"], None)
    return {}


TOOLS = {
    fn.__name__: fn
    for fn in (
        createSession,
        listSessions,
        getSession,
        invokeTool,
        deleteSession,
    )
}


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name=name,
            inputSchema={
                "type": "object",
                "required": ["_body"],
                "properties": {"_body": {"type": "object"}},
                "additionalProperties": False,
            },
        )
        for name in TOOLS
    ]


@server.call_tool()
async def call_tool(name, arguments):
    with Path(os.environ["TEST_MCP_CALLS"]).open("a") as log:  # noqa: ASYNC230 -- 测试中记录一行工具名。
        log.write(name + "\n")
    return [TextContent(type="text", text=json.dumps(TOOLS[name](arguments["_body"])))]


manager = StreamableHTTPSessionManager(server)


class MCPApp:
    async def __call__(self, scope, receive, send):
        if dict(scope["headers"]).get(b"authorization") != b"Bearer local-test-token":
            await Response(status_code=401)(scope, receive, send)
            return
        await manager.handle_request(scope, receive, send)


@asynccontextmanager
async def lifespan(app):
    async with manager.run():
        yield


async def oauth_token(request):
    expected = "Basic " + base64.b64encode(b"test-client:test-secret").decode()
    if (
        request.headers.get("Authorization") != expected
        or request.query_params.get("grant_type") != "client_credentials"
    ):
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    return JSONResponse(
        {"access_token": "local-test-token", "token_type": "Bearer", "expires_in": 3600}
    )


if __name__ == "__main__":
    app = Starlette(
        routes=[
            Route("/mcp", MCPApp(), methods=["GET", "POST", "DELETE"]),
            Route("/oauth/token", oauth_token, methods=["GET"]),
        ],
        lifespan=lifespan,
    )
    uvicorn.run(
        app, host="127.0.0.1", port=int(os.environ["TEST_MCP_PORT"]), log_level="error"
    )
