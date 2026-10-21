"""独立 YAML 配置；环境变量优先，不读取其他教程的凭据。"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parent
LEGACY_PROFILE_FIELDS = {
    "url": "mcp.url",
    "api_key": "auth.api_key",
    "token": "auth.token",
    "token_url": "auth.token_url",
    "client_id": "auth.client_id",
    "client_secret": "auth.client_secret",
    "user_pool_uid": "identity.user_pool_uid",
    "client_uid": "identity.client_uid",
    "host": "identity.host",
    "api_version": "identity.api_version",
    "region": "volcengine.region",
    "volcengine_access_key": "volcengine.access_key",
    "volcengine_secret_key": "volcengine.secret_key",
    "volcengine_session_token": "volcengine.session_token",
    "tool_id": "sandbox.tool_id",
}


def checked_url(value: str, label: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{label} 必须是无凭据、query 和 fragment 的 HTTP(S) URL")
    if parsed.scheme == "http" and parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError(f"{label} 非本机地址必须使用 HTTPS")
    return value


class Config:
    def __init__(self, path: str | None = None) -> None:
        self.state_override: str | None = None
        self.auth_override: str | None = None
        self.json_output = False
        self.path = (
            Path(path or os.getenv("SANDBOX_MCP_CONFIG") or ROOT / "config.yaml")
            .expanduser()
            .resolve()
        )
        try:
            self.data = yaml.safe_load(self.path.read_text()) or {}
        except FileNotFoundError:
            if path or os.getenv("SANDBOX_MCP_CONFIG"):
                raise ValueError(f"配置文件不存在：{self.path}") from None
            self.data = {}
        except yaml.YAMLError:
            raise ValueError("配置不是有效 YAML") from None
        if not isinstance(self.data, dict) or self.data.get("version", 1) != 1:
            raise ValueError("配置必须是 version: 1 的 YAML 对象")

    def get(self, key: str, default: str = "", env: str = "") -> str:
        env_name = env or "SANDBOX_MCP_" + key.upper().replace(".", "_")
        if env_name in os.environ:
            return os.environ[env_name].strip()
        value = self.data
        for part in key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        return default if value is None else str(value).strip()

    def integer(self, key: str, default: int) -> int:
        try:
            value = int(self.get(key, str(default)))
        except ValueError:
            raise ValueError(f"{key} 必须是正整数") from None
        if value <= 0:
            raise ValueError(f"{key} 必须是正整数")
        return value

    def require(self, key: str, env: str = "") -> str:
        value = self.get(key, env=env)
        if not value or value.startswith(("your-", "<")):
            raise ValueError(f"请配置 {key}" + (f" 或 {env}" if env else ""))
        return value

    @property
    def auth_mode(self) -> str:
        mode = self.auth_override or self.get(
            "mcp.mode", self.get("auth.mode", "keyauth")
        )
        if mode not in {"keyauth", "oauth", "m2m", "bearer"}:
            raise ValueError(
                "请配置 mcp.mode 为 keyauth 或 oauth；也可通过 --auth 指定"
            )
        return mode

    @property
    def has_auth_profiles(self) -> bool:
        mcp = self.data.get("mcp")
        return isinstance(mcp, dict) and any(key in mcp for key in ("keyauth", "oauth"))

    @property
    def auth_profile(self) -> str:
        mode = self.auth_mode
        if mode not in {"keyauth", "oauth", "m2m", "bearer"}:
            raise ValueError("鉴权方式必须是 keyauth、oauth、m2m 或 bearer")
        return "keyauth" if mode == "keyauth" else "oauth"

    def profile_path(self, profile: str, name: str) -> str:
        if self.has_auth_profiles:
            return f"mcp.{profile}.{name}"
        return LEGACY_PROFILE_FIELDS[name]

    def profile_value(
        self, profile: str, name: str, default: str = "", env: str = ""
    ) -> str:
        return self.get(self.profile_path(profile, name), default, env)

    def require_profile(self, profile: str, name: str, env: str = "") -> str:
        return self.require(self.profile_path(profile, name), env)

    @property
    def url(self) -> str:
        path = self.profile_path(self.auth_profile, "url")
        return checked_url(self.require(path, "MCP_URL"), path)

    @property
    def tool_id(self) -> str:
        # 新模板为已绑定 Tool 的网关；不继承其他项目的通用 Tool ID。
        if self.has_auth_profiles:
            return self.profile_value(self.auth_profile, "tool_id")
        return self.get("sandbox.tool_id", env="AGENTKIT_TOOL_ID")

    @property
    def scope(self) -> dict[str, str]:
        return {
            "mcp_url": self.url,
            "tool_id": self.tool_id,
        }

    @property
    def state_path(self) -> Path:
        path = Path(
            self.state_override or self.get("state_file", ".sandbox_mcp_state.json")
        ).expanduser()
        return path if path.is_absolute() else self.path.parent / path
