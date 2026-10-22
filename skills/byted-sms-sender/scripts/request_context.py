"""Skill 来源只用于统计，不参与客户身份、业务参数或幂等校验。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


METADATA_PATH = Path(__file__).with_name("skill_metadata.json")


def _header_value(value: Any) -> Optional[str]:
    # HTTP 边界拒绝非法值；不改写平台产品名或包内版本。
    if not isinstance(value, str) or not value or len(value) > 256:
        return None
    if any(ord(character) < 32 or character == "\x7f" for character in value):
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return value


@dataclass(frozen=True)
class RequestContext:
    """每次操作持有自己的平台声明和本机关联编号；表单复用创建时的上下文。"""

    operation_id: str
    skill_version: Optional[str]
    platform: Optional[str] = None

    @classmethod
    def create(cls, *, platform: Any = None) -> RequestContext:
        try:
            metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            metadata = {}
        version = _header_value(metadata.get("version")) if isinstance(metadata, Mapping) else None
        return cls(uuid.uuid4().hex, version, _header_value(platform))

    def headers(self) -> Dict[str, str]:
        values = {"X-Request-Source": "skill", "X-Platform": self.platform or "unknown"}
        if self.skill_version is not None:
            values["X-Skill-Version"] = self.skill_version
        return values

    def diagnostics(self) -> Dict[str, Any]:
        """本机保留操作编号及包版本可用性；不额外向短信接口发送。"""
        return {
            "operation_id": self.operation_id,
            "source_metadata_available": self.skill_version is not None,
        }
