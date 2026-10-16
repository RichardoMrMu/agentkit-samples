"""回填或清空仓库中固定 curl 脚本的 Session 连接配置。"""

from __future__ import annotations

import os
import re
import shlex
import tempfile
from pathlib import Path
from urllib.parse import quote

from _config import validated_config


def configure_session_curl_scripts() -> tuple[Path, Path]:
    """只更新三项连接配置，保留用户编辑的请求正文及其他脚本逻辑。"""
    config = validated_config(
        require_model_key_name=False,
        require_credentials=False,
    )
    session = config.session
    key = config.environment.env_api_key_value
    if not session.endpoint_base_url or not session.id or not key:
        raise RuntimeError(
            "Session data is unavailable; run 01_create_resources.py first"
        )
    return _update_session_curl_scripts(
        {
            "ENDPOINT": session.endpoint_base_url.rstrip("/"),
            "SESSION_ID": quote(session.id, safe=""),
            "ENV_API_KEY": key,
        }
    )


def clear_session_curl_scripts() -> tuple[Path, Path]:
    """清空连接配置，保留请求正文及其他脚本逻辑。"""
    return _update_session_curl_scripts(
        {"ENDPOINT": "", "SESSION_ID": "", "ENV_API_KEY": ""}
    )


def _update_session_curl_scripts(values: dict[str, str]) -> tuple[Path, Path]:
    script_dir = Path(__file__).resolve().parent
    invoke_path = script_dir / "02_invoke_session.sh"
    sse_path = script_dir / "02_sse_session.sh"
    updated = []
    for path in (invoke_path, sse_path):
        try:
            script = path.read_text(encoding="utf-8")
        except OSError:
            raise RuntimeError(f"cannot read fixed curl script: {path}") from None
        for name, value in values.items():
            script, count = re.subn(
                rf"^{name}=.*$",
                lambda match, name=name, value=value: f"{name}={shlex.quote(value)}",
                script,
                flags=re.MULTILINE,
            )
            if count != 1:
                raise RuntimeError(
                    f"{path.name} must contain exactly one {name}= assignment"
                )
        updated.append((path, script))
    for path, script in updated:
        _write_script(path, script)
    return invoke_path, sse_path


def _write_script(path: Path, script: str) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.stem}-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o700)
            stream.write(script)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
