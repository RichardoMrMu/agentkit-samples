"""Volcengine HMAC-SHA256；沿用 resource_to_chat_http 的签名实现。"""

import datetime as dt
import hashlib
import hmac
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

API_VERSION = "2025-10-30"


@dataclass(frozen=True)
class ControlConfig:
    """控制面签名及请求入口配置。"""

    access_key: str
    secret_key: str
    region: str
    service: str
    host: str
    scheme: str = "https"
    session_token: str = ""
    api_version: str = API_VERSION


def _hmac_sha256(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def _canonical_query(query: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in sorted(query.items()):
        values = value if isinstance(value, list) else [value]
        for item in values:
            parts.append(
                f"{quote(str(key), safe='-_.~')}={quote(str(item), safe='-_.~')}"
            )
    return "&".join(parts)


def sign_headers(
    method: str,
    host: str,
    query: dict[str, Any],
    body: bytes,
    *,
    config: ControlConfig,
    path: str = "/",
) -> dict[str, str]:
    """按 Volcengine HMAC-SHA256 规范生成控制面请求头。"""

    now = dt.datetime.now(dt.UTC)
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    date = timestamp[:8]
    payload_hash = hashlib.sha256(body).hexdigest()
    signed = {
        "content-type": "application/json",
        "host": host,
        "x-content-sha256": payload_hash,
        "x-date": timestamp,
    }
    if config.session_token:
        signed["x-security-token"] = config.session_token
    signed_names = ";".join(sorted(signed))
    canonical_headers = "".join(f"{name}:{signed[name]}\n" for name in sorted(signed))
    canonical_request = "\n".join(
        [
            method.upper(),
            path,
            _canonical_query(query),
            canonical_headers,
            signed_names,
            payload_hash,
        ]
    )
    scope = f"{date}/{config.region}/{config.service}/request"
    string_to_sign = "\n".join(
        [
            "HMAC-SHA256",
            timestamp,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signing_key = _hmac_sha256(
        _hmac_sha256(
            _hmac_sha256(
                _hmac_sha256(config.secret_key.encode("utf-8"), date),
                config.region,
            ),
            config.service,
        ),
        "request",
    )
    signature = hmac.new(
        signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Host": host,
        "X-Date": timestamp,
        "X-Content-Sha256": payload_hash,
        "Authorization": (
            f"HMAC-SHA256 Credential={config.access_key}/{scope}, "
            f"SignedHeaders={signed_names}, Signature={signature}"
        ),
    }
    if config.session_token:
        headers["X-Security-Token"] = config.session_token
    return headers
