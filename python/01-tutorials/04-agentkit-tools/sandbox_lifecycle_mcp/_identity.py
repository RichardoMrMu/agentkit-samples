"""GetUserPool → GetUserPoolClient → M2M token，token 只在内存中使用。"""

import json

import httpx

from _common import remember_secret
from _config import checked_url
from _signing import ControlConfig, sign_headers


def decode(response: httpx.Response, operation: str) -> dict:
    try:
        value = response.json()
    except ValueError:
        raise RuntimeError(
            f"{operation}: HTTP {response.status_code}，响应不是 JSON"
        ) from None
    if not isinstance(value, dict):
        raise TypeError(f"{operation}: 响应必须是对象")
    metadata = value.get("ResponseMetadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    error = metadata.get("Error") or value.get("error")
    if not response.is_success or error:
        # 不把第三方响应正文或 HTTP request 对象输出到错误中。
        code = error.get("Code", "APIError") if isinstance(error, dict) else "HTTPError"
        raise RuntimeError(
            f"{operation}: HTTP {response.status_code} {code}；检查权限、区域和配置"
        )
    return value


def identity_call(client, config, action: str, body: dict) -> dict:
    control = ControlConfig(
        access_key=remember_secret(
            config.require_profile(
                "oauth", "volcengine_access_key", "VOLCENGINE_ACCESS_KEY"
            )
        ),
        secret_key=remember_secret(
            config.require_profile(
                "oauth", "volcengine_secret_key", "VOLCENGINE_SECRET_KEY"
            )
        ),
        session_token=remember_secret(
            config.profile_value(
                "oauth", "volcengine_session_token", env="VOLCENGINE_SESSION_TOKEN"
            )
        ),
        region=config.profile_value(
            "oauth", "region", "cn-beijing", "VOLCENGINE_REGION"
        ),
        service="id",
        host=config.profile_value("oauth", "host", "open.volcengineapi.com"),
        api_version=config.profile_value("oauth", "api_version", "2025-10-30"),
    )
    url = checked_url(f"https://{control.host}/", "identity.host")
    query = {"Action": action, "Version": control.api_version}
    body_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    response = client.post(
        url,
        params=query,
        content=body_bytes,
        headers=sign_headers("POST", control.host, query, body_bytes, config=control),
    )
    value = decode(response, action).get("Result")
    if not isinstance(value, dict):
        raise TypeError(f"{action}: 缺少 Result 对象")
    return value


def resolve_client_uid(client, config, pool_uid: str) -> str:
    configured = config.profile_value("oauth", "client_uid")
    if configured:
        return configured
    # 只需区分零个、一个和多个；取两条，结合 TotalCount 避免分页误选。
    result = identity_call(
        client,
        config,
        "ListUserPoolClients",
        {
            "UserPoolUid": pool_uid,
            "PageSize": 2,
            "PageNumber": 1,
            "Filter": {"ClientTypes": ["MACHINE_TO_MACHINE"]},
        },
    )
    items = result.get("Data")
    count = result.get("TotalCount")
    if not isinstance(items, list) or type(count) is not int or count < 0:
        raise ValueError(
            "ListUserPoolClients 缺少有效 Data / TotalCount，无法自动选择 Client"
        )
    if count == 0 and not items:
        raise ValueError("用户池没有 MACHINE_TO_MACHINE Client，请先创建 M2M Client")
    if count > 1 or len(items) > 1:
        raise ValueError(
            "用户池有多个 MACHINE_TO_MACHINE Client，请显式配置 mcp.oauth.client_uid"
        )
    if count != 1 or len(items) != 1 or not isinstance(items[0], dict):
        raise ValueError("ListUserPoolClients 返回数量不一致，无法自动选择 Client")
    uid = items[0].get("Uid")
    if (
        items[0].get("ClientType") != "MACHINE_TO_MACHINE"
        or not isinstance(uid, str)
        or not uid
    ):
        raise ValueError("ListUserPoolClients 未返回有效 M2M Client UID")
    return uid


def get_token(config) -> str:
    mode = config.auth_mode
    if mode == "keyauth":
        return remember_secret(
            config.require_profile("keyauth", "api_key", "MCP_API_KEY")
        )
    if mode == "bearer":
        return remember_secret(config.require_profile("oauth", "token", "MCP_TOKEN"))
    if mode not in {"oauth", "m2m"}:
        raise ValueError("auth.mode 只能是 keyauth、oauth（兼容 m2m）或 bearer")
    with httpx.Client(
        timeout=config.integer("http_timeout_seconds", 60), follow_redirects=False
    ) as client:
        token_url = config.profile_value("oauth", "token_url")
        client_id = config.profile_value("oauth", "client_id")
        client_secret = remember_secret(config.profile_value("oauth", "client_secret"))
        if bool(client_id) != bool(client_secret):
            raise ValueError("OAuth client_id 与 client_secret 必须同时提供")
        if not token_url or not client_id:
            pool_uid = config.require_profile("oauth", "user_pool_uid")
            if not token_url:
                pool = identity_call(
                    client, config, "GetUserPool", {"UserPoolUid": pool_uid}
                )
                token_url = pool.get("TokenUrl")
                if not isinstance(token_url, str) or not token_url:
                    raise RuntimeError("GetUserPool 缺少 TokenUrl")
            if not client_id:
                detail = identity_call(
                    client,
                    config,
                    "GetUserPoolClient",
                    {
                        "UserPoolUid": pool_uid,
                        "ClientUid": resolve_client_uid(client, config, pool_uid),
                    },
                )
                if detail.get("ClientType") != "MACHINE_TO_MACHINE":
                    raise ValueError("UserPool Client 必须是 MACHINE_TO_MACHINE")
                client_id = detail.get("Uid")
                client_secret = remember_secret(detail.get("ClientSecret", ""))
                if not client_id or not client_secret:
                    raise RuntimeError("GetUserPoolClient 缺少 Uid / ClientSecret")
        checked_url(token_url, "TokenUrl")
        # 参考文档是 GET + query grant_type + HTTP Basic，不是 /invoke 请求。
        response = client.get(
            token_url,
            params={"grant_type": "client_credentials"},
            auth=httpx.BasicAuth(client_id, client_secret),
            headers={"Accept": "application/json"},
        )
        value = decode(response, "OAuth token")
        token = value.get("access_token")
        if (
            not isinstance(token, str)
            or not token
            or str(value.get("token_type", "")).lower() != "bearer"
        ):
            raise RuntimeError("OAuth 响应缺少 Bearer access_token")
        return remember_secret(token)
