"""来源元数据只用于请求归因，不参与客户身份、业务参数或幂等校验。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from urllib.parse import quote


METADATA_PATH = Path(__file__).with_name("skill_metadata.json")


def _header_value(value: Any) -> Optional[str]:
    # 来源字段的协议边界拒绝控制字符；保留原值，不执行 trim 或大小写转换。
    if not isinstance(value, str) or not value or len(value) > 256:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return value


@dataclass(frozen=True)
class SkillMetadata:
    """SkillMetadata 由打包器从源入口和发布渠道生成，运行时不猜测身份。"""

    name: str
    version: str
    channel: str

    @classmethod
    def load(cls) -> Optional[SkillMetadata]:
        try:
            value = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(value, Mapping):
            return None
        values = [_header_value(value.get(key)) for key in ("name", "version", "channel")]
        if any(item is None for item in values):
            return None
        return cls(*values)


@dataclass(frozen=True)
class RequestContext:
    """RequestContext 按操作持有；后台表单复用同一实例，不借用进程全局状态。"""

    operation_id: str
    execution_mode: str
    skill: Optional[SkillMetadata]
    host_name: Optional[str] = None
    host_version: Optional[str] = None

    @classmethod
    def create(
        cls,
        execution_mode: str,
        *,
        client_info: Optional[Mapping[str, Any]] = None,
    ) -> RequestContext:
        if execution_mode not in {"cli", "mcp"}:
            raise ValueError("execution_mode must be cli or mcp")
        info = client_info if isinstance(client_info, Mapping) else {}
        name = _header_value(info.get("name"))
        return cls(
            operation_id=uuid.uuid4().hex,
            execution_mode=execution_mode,
            skill=SkillMetadata.load(),
            host_name=name,
            host_version=_header_value(info.get("version")) if name is not None else None,
        )

    def headers(self) -> Dict[str, str]:
        # Header 值按 UTF-8 百分号编码，避免宿主自报的 Unicode 值导致 HTTP 库编码失败。
        values = {
            "X-Skill-Operation-Id": self.operation_id,
            "X-Skill-Execution-Mode": self.execution_mode,
        }
        if self.skill is not None:
            values.update({
                "X-Skill-Name": self.skill.name,
                "X-Skill-Version": self.skill.version,
                "X-Skill-Channel": self.skill.channel,
            })
        if self.host_name is not None:
            values["X-Skill-Host-Name"] = self.host_name
        if self.host_version is not None:
            values["X-Skill-Host-Version"] = self.host_version
        return {name: quote(value, safe="-._~") for name, value in values.items()}

    def diagnostics(self) -> Dict[str, Any]:
        """返回本机诊断字段；来源缺失不冒充已知身份，也不改变业务结果。"""
        return {
            "operation_id": self.operation_id,
            "source_metadata_available": self.skill is not None,
        }
