"""为火山引擎 RuntimeTemplate 复用或创建默认 Runtime IAM Role。"""

from __future__ import annotations

import json
import re
from typing import Any

import requests

from _http_client import (
    AgentKitHttpError,
    _canonical_query,
    _env,
    _positive_int_env,
    _response_json,
    resolve_credentials,
    resolve_endpoint,
    resolve_provider,
    sign_headers,
)

DEFAULT_RUNTIME_ROLE_NAME = "AgentKitDefaultRuntimeAccess"
IAM_VERSION = "2018-01-01"
RUNTIME_POLICY = "AgentKitDefaultRuntimeAccess"


class IamHttpClient:
    """使用当前火山账号凭据调用 IAM TOP GET Action。"""

    def __init__(self) -> None:
        self.endpoint = resolve_endpoint()
        self.region = _env("VOLCENGINE_IAM_REGION") or self.endpoint.region
        self.host = _env("VOLCENGINE_IAM_HOST") or "open.volcengineapi.com"
        self.scheme = _env("VOLCENGINE_IAM_SCHEME") or "https"
        if self.scheme not in {"http", "https"}:
            raise RuntimeError("VOLCENGINE_IAM_SCHEME must be http or https")
        self.credentials = resolve_credentials()
        self.timeout = _positive_int_env("AGENTKIT_HTTP_TIMEOUT_SECONDS", 30)

    def call(self, action: str, params: dict[str, str]) -> dict[str, Any]:
        """IAM 写操作不自动重放，网络结果不明确时要求人工检查角色。"""
        query = {"Action": action, "Version": IAM_VERSION, **params}
        headers = sign_headers(
            "GET",
            self.host,
            query,
            b"",
            credentials=self.credentials,
            service="iam",
            region=self.region,
        )
        try:
            response = requests.get(
                f"{self.scheme}://{self.host}/?{_canonical_query(query)}",
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                f"IAM {action} network error ({type(exc).__name__}); "
                "inspect IAM before retrying"
            ) from None
        return _response_json(f"IAM {action}", response)


def _get_role(client: IamHttpClient, role_name: str) -> dict[str, Any] | None:
    try:
        result = client.call("GetRole", {"RoleName": role_name})
    except AgentKitHttpError as exc:
        if exc.status_code == 404 or re.search(
            r"RoleNotExist|NotFound", exc.code, re.IGNORECASE
        ):
            return None
        raise
    role = result.get("Role")
    if not isinstance(role, dict) or role.get("RoleName") != role_name:
        raise RuntimeError("IAM GetRole returned an invalid or mismatched RoleName")
    return role


def _trust_policy(service: str) -> str:
    runtime_service = "vefaas_dev" if "stg" in service.lower() else "vefaas"
    return json.dumps(
        {
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["sts:AssumeRole"],
                    "Principal": {"Service": [runtime_service]},
                }
            ]
        },
        separators=(",", ":"),
    )


def _create_default_role(client: IamHttpClient) -> tuple[str, bool]:
    role_name = DEFAULT_RUNTIME_ROLE_NAME
    if _get_role(client, role_name) is not None:
        return role_name, True

    try:
        result = client.call(
            "CreateRole",
            {
                "RoleName": role_name,
                "DisplayName": role_name,
                "TrustPolicyDocument": _trust_policy(client.endpoint.service),
            },
        )
    except AgentKitHttpError as exc:
        if exc.status_code == 409 and _get_role(client, role_name) is not None:
            return role_name, True
        raise
    role = result.get("Role")
    if not isinstance(role, dict) or role.get("RoleName") != role_name:
        raise RuntimeError("IAM CreateRole returned an invalid Role")

    try:
        client.call(
            "AttachRolePolicy",
            {
                "RoleName": role_name,
                "PolicyName": RUNTIME_POLICY,
                "PolicyType": "System",
            },
        )
    except AgentKitHttpError as exc:
        if exc.status_code != 409:
            raise RuntimeError(
                f"Runtime role {role_name} was created, but policy attachment "
                "failed; inspect IAM before retrying"
            ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"Runtime role {role_name} was created, but policy attachment "
            "failed; inspect IAM before retrying"
        ) from exc
    return role_name, False


def prepare_runtime_role(
    role_name: str | None,
    *,
    dry_run: bool,
) -> tuple[str, bool | None]:
    """准备 RoleName；返回角色名和复用状态，None 表示 dry-run。"""
    configured = (role_name or "").strip()
    if resolve_provider() != "volcengine":
        if not configured:
            raise RuntimeError(
                "pass --role-name or set AGENTKIT_RUNTIME_ROLE_NAME; "
                "automatic IAM role setup is available only for Volcengine TOP"
            )
        return configured, None if dry_run else True

    selected = configured or DEFAULT_RUNTIME_ROLE_NAME
    if dry_run:
        return selected, None

    client = IamHttpClient()
    if configured and configured != DEFAULT_RUNTIME_ROLE_NAME:
        if _get_role(client, configured) is None:
            raise RuntimeError(f"configured Runtime role {configured} does not exist")
        return configured, True
    return _create_default_role(client)
