"""协议、鉴权和状态边界测试，不使用云端凭据。"""

import asyncio
import base64
import json
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from mcp.types import CallToolResult, TextContent, Tool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _client import SandboxClient, ToolError, decode_result
from _common import load_state, redact, remember_secret, save_state
from _config import Config
from _identity import get_token, resolve_client_uid
from _workflow import invocation_result, list_pages, wait_session


@pytest.fixture
def config(tmp_path, monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith(("SANDBOX_MCP_", "MCP_", "AGENTKIT_TOOL_ID", "VOLCENGINE_")):
            monkeypatch.delenv(name)
    path = tmp_path / "config.yaml"
    path.write_text(
        "version: 1\nauth:\n  mode: oauth\nmcp:\n  url: https://mcp.example.test/mcp\n"
    )
    return Config(str(path))


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("discover", [False, True])
def test_m2m_identity_contract(config, monkeypatch, nested, discover):
    config.data.update(
        volcengine={"access_key": "test-ak", "secret_key": "test-sk"},
        identity={"user_pool_uid": "pool-test", "client_uid": "client-test"},
    )
    if nested:
        config.data["mcp"] = {
            "keyauth": {
                "url": "https://key.example.test/mcp",
                "api_key": "wrong-for-oauth",
            },
            "oauth": {
                "url": "https://oauth.example.test/mcp",
                "user_pool_uid": "pool-test",
                "client_uid": "client-test",
                "volcengine_access_key": "test-ak",
                "volcengine_secret_key": "test-sk",
            },
        }
        del config.data["identity"]
        del config.data["volcengine"]
    if discover:
        target = config.data["mcp"]["oauth"] if nested else config.data["identity"]
        target.pop("client_uid")
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.host == "open.volcengineapi.com":
            assert "/cn-beijing/id/request" in request.headers["Authorization"]
            body = json.loads(request.content)
            assert body["UserPoolUid"] == "pool-test"
            if request.url.params["Action"] == "GetUserPool":
                result = {"TokenUrl": "https://pool.example.test/oauth/token"}
            elif request.url.params["Action"] == "ListUserPoolClients":
                assert body["Filter"] == {"ClientTypes": ["MACHINE_TO_MACHINE"]}
                assert body["PageSize"] == 2
                result = {
                    "TotalCount": 1,
                    "Data": [
                        {"Uid": "client-test", "ClientType": "MACHINE_TO_MACHINE"}
                    ],
                }
            else:
                assert request.url.params["Action"] == "GetUserPoolClient"
                assert body["ClientUid"] == "client-test"
                result = {
                    "Uid": "client-test",
                    "ClientType": "MACHINE_TO_MACHINE",
                    "ClientSecret": "test-client-secret",
                }
            return httpx.Response(200, json={"Result": result})
        assert request.method == "GET"
        assert request.url.params["grant_type"] == "client_credentials"
        assert (
            request.headers["Authorization"]
            == "Basic " + base64.b64encode(b"client-test:test-client-secret").decode()
        )
        return httpx.Response(
            200,
            json={
                "access_token": "test-oauth-token",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert get_token(config) == "test-oauth-token"
    assert len(calls) == (4 if discover else 3)
    assert redact("test-client-secret test-oauth-token") == "<redacted> <redacted>"
    assert not config.state_path.exists()


def test_direct_m2m_has_no_identity_request(config, monkeypatch):
    config.data["auth"] = {
        "mode": "m2m",
        "token_url": "https://pool.example.test/oauth/token",
        "client_id": "test-client",
        "client_secret": "test-secret",
    }
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, json={"access_token": "test-token", "token_type": "Bearer"}
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert get_token(config) == "test-token"
    assert len(calls) == 1


def test_auth_redirect_does_not_follow(config, monkeypatch):
    config.data["auth"] = {
        "mode": "oauth",
        "token_url": "https://pool.example.test/oauth/token",
        "client_id": "test-client",
        "client_secret": "test-secret",
    }
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            302, headers={"Location": "https://different.example.test/"}, json={}
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    with pytest.raises(RuntimeError, match="HTTP 302"):
        get_token(config)
    assert len(calls) == 1


def test_bearer_does_not_require_identity(config, monkeypatch):
    config.data["auth"] = {"mode": "bearer"}
    monkeypatch.setenv("MCP_TOKEN", "test-bearer")
    assert get_token(config) == "test-bearer"


@pytest.mark.parametrize(
    "url",
    ["https://a.test/mcp?token=x", "https://user:pass@a.test/mcp", "http://a.test/mcp"],
)
def test_unsafe_urls_rejected(config, url):
    config.data["mcp"]["url"] = url
    with pytest.raises(ValueError):
        _ = config.url


def test_state_scope_permissions_and_secret_redaction(config):
    remember_secret("hidden-secret")
    state = {
        "schema_version": 1,
        "scope": config.scope,
        "session_id": "s-test",
        "ClientSecret": "hidden-secret",
        "result": '{"access_token":"hidden-secret"}',
        "url": "https://a.test/?Authorization=hidden-secret",
    }
    save_state(config, state)
    assert stat.S_IMODE(config.state_path.stat().st_mode) == 0o600
    assert "hidden-secret" not in config.state_path.read_text()
    assert load_state(config)["session_id"] == "s-test"
    config.data["mcp"]["url"] = "https://other.example.test/mcp"
    with pytest.raises(ValueError, match="其他 MCP"):
        load_state(config)


def test_arguments_match_discovered_schema(config):
    client = SandboxClient(None, config)
    schema = {
        "type": "object",
        "properties": {
            "_body": {
                "type": "object",
                "properties": {"SessionId": {"type": "string"}},
                "required": ["SessionId"],
                "additionalProperties": False,
            }
        },
        "required": ["_body"],
        "additionalProperties": False,
    }
    client.tools["getSession"] = Tool(name="getSession", inputSchema=schema)
    assert client.arguments("getSession", {"SessionId": "s-test"}) == {
        "_body": {"SessionId": "s-test"}
    }
    with pytest.raises(ToolError, match="schema"):
        client.arguments("getSession", {})
    with pytest.raises(ToolError, match="未暴露"):
        client.arguments("pauseSession", {"SessionId": "s-test"})
    config.data["sandbox"] = {"tool_id": "t-not-accepted"}
    with pytest.raises(ToolError, match="schema"):
        client.arguments("getSession", {"SessionId": "s-test"})


@pytest.mark.parametrize("structured", [True, False])
def test_decode_top_envelope(structured):
    body = {"ResponseMetadata": {}, "Result": {"SessionId": "s-test"}}
    result = CallToolResult(
        content=[] if structured else [TextContent(type="text", text=json.dumps(body))],
        structuredContent=body if structured else None,
    )
    assert decode_result(result, "createSession")["SessionId"] == "s-test"


def test_nested_api_error_and_mcp_error():
    result = CallToolResult(
        content=[],
        structuredContent={
            "ResponseMetadata": {"Error": {"Code": "InvalidResource.NotFound"}}
        },
    )
    with pytest.raises(ToolError) as exc:
        decode_result(result, "getSession")
    assert exc.value.code == "InvalidResource.NotFound"
    with pytest.raises(ToolError, match="isError"):
        decode_result(
            CallToolResult(content=[], structuredContent={}, isError=True), "invokeTool"
        )


def test_invoke_json_and_business_error():
    value = {"Result": '{"success": false, "data": {"status":"error"}}'}
    decoded = decode_result(
        CallToolResult(content=[], structuredContent=value), "invokeTool"
    )
    with pytest.raises(RuntimeError, match="代码执行失败"):
        invocation_result(decoded)


def test_pagination_and_repeated_token():
    requests = []

    async def call(operation, body):
        requests.append(dict(body))
        return {
            "SessionInfos": [{"SessionId": str(len(requests))}],
            "NextToken": "next" if len(requests) == 1 else "",
        }

    values = asyncio.run(
        list_pages(SimpleNamespace(call=call), "listSessions", {}, "SessionInfos")
    )
    assert len(values) == 2
    assert requests[1]["NextToken"] == "next"

    async def repeat(operation, body):
        return {"SessionInfos": [], "NextToken": "same"}

    with pytest.raises(RuntimeError, match="NextToken"):
        asyncio.run(
            list_pages(SimpleNamespace(call=repeat), "listSessions", {}, "SessionInfos")
        )


def test_deletion_requires_deleted_or_not_found(config):
    async def missing(operation, body):
        raise ToolError(operation, "InvalidResource.NotFound")

    assert (
        asyncio.run(
            wait_session(
                SimpleNamespace(call=missing),
                config,
                "s-test",
                {"deleted"},
                lambda *a, **k: None,
            )
        )["Status"]
        == "Deleted"
    )
    with pytest.raises(ToolError):
        asyncio.run(
            wait_session(
                SimpleNamespace(call=missing),
                config,
                "s-test",
                {"ready"},
                lambda *a, **k: None,
            )
        )


def test_wait_checks_session_identity_and_failure(config):
    async def wrong(operation, body):
        return {"SessionId": "s-other", "Status": "Ready"}

    with pytest.raises(RuntimeError, match="SessionId"):
        asyncio.run(
            wait_session(
                SimpleNamespace(call=wrong),
                config,
                "s-test",
                {"ready"},
                lambda *a, **k: None,
            )
        )

    async def failed(operation, body):
        return {"SessionId": "s-test", "Status": "Failed"}

    with pytest.raises(RuntimeError, match="非预期状态"):
        asyncio.run(
            wait_session(
                SimpleNamespace(call=failed),
                config,
                "s-test",
                {"ready"},
                lambda *a, **k: None,
            )
        )


def test_explicit_state_path_overrides_environment(config, monkeypatch):
    monkeypatch.setenv("SANDBOX_MCP_STATE_FILE", "from-env.json")
    assert config.state_path.name == "from-env.json"
    config.state_override = "from-cli.json"
    assert config.state_path == config.path.parent / "from-cli.json"


def test_keyauth_uses_api_key_without_oauth(config, monkeypatch):
    config.auth_override = "keyauth"
    monkeypatch.setenv("MCP_API_KEY", "test-api-key")
    assert get_token(config) == "test-api-key"
    assert redact("test-api-key") == "<redacted>"


def test_oauth_override_does_not_use_api_key(config, monkeypatch):
    config.data["auth"] = {"mode": "keyauth"}
    config.auth_override = "oauth"
    monkeypatch.setenv("MCP_API_KEY", "test-api-key")
    with pytest.raises(ValueError, match="user_pool_uid"):
        get_token(config)


def test_tool_discovery_preserves_schema_but_redacts_credential_defaults():
    value = {
        "inputSchema": {
            "type": "object",
            "properties": {
                "_body": {
                    "type": "object",
                    "properties": {
                        "NextToken": {
                            "type": "string",
                            "description": "Pagination token",
                        },
                        "ClientSecret": {"type": "string", "default": "never-display"},
                    },
                },
            },
        },
        "NextToken": "actual-pagination-token",
    }
    clean = redact(value)
    fields = clean["inputSchema"]["properties"]["_body"]["properties"]
    assert fields["NextToken"]["type"] == "string"
    assert fields["ClientSecret"]["default"] == "<redacted>"
    assert clean["NextToken"] == "<redacted>"


def test_profile_selects_url_and_key_together(config, monkeypatch):
    config.data = {
        "mcp": {
            "keyauth": {
                "url": "https://key.example.test/mcp",
                "api_key": "profile-key",
            },
            "oauth": {
                "url": "https://oauth.example.test/mcp",
                "user_pool_uid": "pool-test",
            },
        }
    }
    assert config.auth_mode == "keyauth"
    assert config.url == "https://key.example.test/mcp"
    assert get_token(config) == "profile-key"
    monkeypatch.setenv("AGENTKIT_TOOL_ID", "unrelated-project-tool")
    assert config.tool_id == ""
    config.auth_override = "oauth"
    assert config.url == "https://oauth.example.test/mcp"
    assert config.require_profile("oauth", "user_pool_uid") == "pool-test"
    with pytest.raises(ValueError, match="mcp.oauth.volcengine_access_key"):
        get_token(config)


def test_missing_profile_url_never_falls_back_to_other_profile(config):
    config.data = {
        "mcp": {
            "url": "https://old.example.test/mcp",
            "keyauth": {"url": "https://key.example.test/mcp"},
            "oauth": {},
        }
    }
    config.auth_override = "oauth"
    with pytest.raises(ValueError, match="mcp.oauth.url"):
        _ = config.url


def test_explicit_url_env_override_and_profile_state_isolation(config, monkeypatch):
    config.data = {
        "mcp": {
            "keyauth": {"url": "https://key.example.test/mcp"},
            "oauth": {"url": "https://oauth.example.test/mcp"},
        }
    }
    save_state(
        config, {"schema_version": 1, "scope": config.scope, "session_id": "s-test"}
    )
    config.auth_override = "oauth"
    with pytest.raises(ValueError, match="其他 MCP"):
        load_state(config)
    monkeypatch.setenv("MCP_URL", "https://override.example.test/mcp")
    assert config.url == "https://override.example.test/mcp"


def test_keyauth_missing_key_does_not_use_oauth_credentials(config):
    config.data = {
        "mcp": {
            "keyauth": {"url": "https://key.example.test/mcp"},
            "oauth": {"client_secret": "oauth-secret"},
        }
    }
    with pytest.raises(ValueError, match="mcp.keyauth.api_key"):
        get_token(config)


def test_mcp_mode_selects_profile_and_cli_override(config, monkeypatch):
    config.data = {
        "mcp": {
            "mode": "oauth",
            "keyauth": {
                "url": "https://key.example.test/mcp",
                "api_key": "test-api-key",
            },
            "oauth": {
                "url": "https://oauth.example.test/mcp",
                "volcengine_access_key": "config-ak",
            },
        }
    }
    assert config.url == "https://oauth.example.test/mcp"
    assert (
        config.require_profile(
            "oauth", "volcengine_access_key", "VOLCENGINE_ACCESS_KEY"
        )
        == "config-ak"
    )
    monkeypatch.setenv("VOLCENGINE_ACCESS_KEY", "override-ak")
    assert (
        config.require_profile(
            "oauth", "volcengine_access_key", "VOLCENGINE_ACCESS_KEY"
        )
        == "override-ak"
    )
    config.auth_override = "keyauth"
    assert config.url == "https://key.example.test/mcp"
    assert get_token(config) == "test-api-key"
    config.auth_override = None
    config.data["mcp"]["mode"] = ""
    with pytest.raises(ValueError, match="mcp.mode"):
        _ = config.auth_mode
    config.auth_override = "oauth"
    assert config.auth_mode == "oauth"


def test_exchange_panels_and_json_redact_without_changing_wire_types(config, capsys):
    from _display import exchange

    payload = {
        "OperationPayload": json.dumps({"code": "print(391)", "api_key": "hidden-key"}),
        "Authorization": "Bearer hidden-token",
    }
    original = json.dumps(payload)
    exchange(config, "request", "invokeTool", payload)
    exchange(
        config,
        "response",
        "invokeTool",
        {"Result": '{"success": false}'},
        is_error=True,
    )
    output = capsys.readouterr().out
    assert "请求 REQUEST" in output and "返回 RESPONSE" in output and "错误" in output
    assert '"code": "print(391)"' in output
    assert "hidden-key" not in output and "hidden-token" not in output
    assert json.dumps(payload) == original
    config.json_output = True
    exchange(config, "request", "invokeTool", payload)
    output = capsys.readouterr().out
    assert "\x1b" not in output
    event = json.loads(output)
    assert event["phase"] == "request"
    assert isinstance(event["payload"]["OperationPayload"], str)
    assert "hidden-key" not in output and "hidden-token" not in output


def test_failed_tool_call_emits_redacted_response(config, capsys):
    result = CallToolResult(
        isError=True,
        content=[
            TextContent(
                type="text", text='{"Error":{"Code":"Denied"},"token":"hidden-token"}'
            )
        ],
    )

    async def call_tool(*args):
        return result

    client = SandboxClient(SimpleNamespace(call_tool=call_tool), config)
    client.tools = {
        "getSession": Tool(name="getSession", inputSchema={"type": "object"})
    }
    config.json_output = True
    with pytest.raises(ToolError, match="Denied"):
        asyncio.run(client.call("getSession", {"SessionId": "s-test"}))
    output = capsys.readouterr().out
    events = [json.loads(line) for line in output.splitlines()]
    assert [event["phase"] for event in events] == ["request", "response"]
    assert events[-1]["is_error"] is True
    assert "hidden-token" not in output


@pytest.mark.parametrize(
    "result, message",
    [
        ({"TotalCount": 0, "Data": []}, "没有"),
        ({"TotalCount": 3, "Data": [{"Uid": "first"}]}, "多个"),
        ({"TotalCount": 1, "Data": []}, "数量不一致"),
        ({"Data": [{"Uid": "first"}]}, "Data / TotalCount"),
        (
            {
                "TotalCount": 1,
                "Data": [{"Uid": "web", "ClientType": "WEB_APPLICATION"}],
            },
            "有效 M2M",
        ),
    ],
)
def test_client_discovery_rejects_ambiguous_or_invalid_results(
    config, monkeypatch, result, message
):
    monkeypatch.setattr("_identity.identity_call", lambda *args: result)
    with pytest.raises(ValueError, match=message):
        resolve_client_uid(None, config, "pool-test")
