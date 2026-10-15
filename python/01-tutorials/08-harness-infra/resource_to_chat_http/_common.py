"""CLI、脱敏输出、状态文件和轮询公共能力。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from _clients import HarnessInfraError, TopClient

SAFE_IDENTIFIER_KEYS = {
    "api_key_id",
    "ark_api_key_name",
    "client_token",
    "endpoint_id",
    "idempotency_key",
}


def redact(value: Any) -> Any:
    """递归隐藏凭据字段和当前进程中的密钥值。"""

    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", str(key)).lower()
            if normalized not in SAFE_IDENTIFIER_KEYS and re.search(
                r"(^|_)(key|secret|token|authorization|password)($|_)",
                normalized,
            ):
                result[key] = "<redacted>"
            elif normalized in {"env_vars", "envs"} and isinstance(item, dict):
                result[key] = {name: "<redacted>" for name in item}
            else:
                result[key] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        redacted = value
        for name, secret in os.environ.items():
            if secret and re.search(r"KEY|SECRET|TOKEN|PASSWORD", name):
                redacted = redacted.replace(secret, "<redacted>")
        return redacted
    return value


def print_json(value: Any) -> None:
    print(json.dumps(redact(value), indent=2, ensure_ascii=False), flush=True)


def hint(message: str) -> None:
    print(redact(message), flush=True)


def emit(
    phase: str,
    message: str,
    *,
    json_output: bool = False,
    **fields: Any,
) -> None:
    """输出稳定的 JSON 事件，或简洁中文进度。"""

    event = {"phase": phase, "message": message, **fields}
    if json_output:
        print_json(event)
    else:
        hint(f"[{message}]")
        for key, value in fields.items():
            if value is not None:
                hint(f"{key}: {value}")


def required_setting(
    name: str,
    *aliases: str,
    dry_run: bool = False,
    default: str = "",
) -> str:
    """按主变量和别名解析必填配置，dry-run 使用占位值。"""

    for candidate in (name, *aliases):
        value = os.getenv(candidate, "").strip()
        if value:
            return value
    if default:
        return default
    if dry_run:
        return f"<{name}>"
    raise RuntimeError(f"set {name}")


def positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return value


def state_path() -> Path:
    configured = os.getenv("HARNESS_INFRA_STATE", "").strip()
    return (
        Path(configured).expanduser().resolve()
        if configured
        else Path(__file__).with_name(".environment_harness_chat_state.json")
    )


def _write_state(path: Path, state: dict[str, Any]) -> None:
    """原子写入状态，并限制为当前用户可读写。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def save_state(state: dict[str, Any]) -> None:
    """只保存资源标识、名称和幂等信息，不接受环境 Key 明文。"""

    forbidden = {"Key", "key", "api_key", "environment_key", "secret_key"}
    if forbidden.intersection(state):
        raise RuntimeError("refusing to persist a secret field")
    _write_state(state_path(), state)


def load_state(client: TopClient | None = None) -> dict[str, Any]:
    path = state_path()
    if not path.is_file():
        raise RuntimeError(
            f"state file is missing: {path}; run 01_create_resources.py first"
        )
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuntimeError(f"state file is invalid: {path}") from None
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise RuntimeError("unsupported state file")
    if client is not None and state.get("control_scope") != client.scope:
        raise RuntimeError(
            "state belongs to another control endpoint; use another HARNESS_INFRA_STATE"
        )
    return state


def new_state(
    client: TopClient,
    *,
    prefix: str,
    model_id: str,
    model_base_url: str,
    role_name: str,
    agent_loop: str,
) -> dict[str, Any]:
    """生成一次运行的确定性名称与幂等 token，并在首个请求前保存。"""

    path = state_path()
    if path.exists():
        existing = load_state()
        if not existing.get("cleaned"):
            raise RuntimeError(
                f"state already contains an active run: {path}; clean it first "
                "or set HARNESS_INFRA_STATE to another path"
            )
    run_id = time.strftime("%Y%m%d%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
    base = f"{prefix}-{run_id}".lower()
    state = {
        "schema_version": 1,
        "control_scope": client.scope,
        "run_id": run_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {
            "model_id": model_id,
            "model_base_url": model_base_url,
            "role_name": role_name,
            "agent_loop": agent_loop,
        },
        "names": {
            "environment": f"{base}-env",
            "harness": f"{base}-harness",
            "runtime_template": f"{base}-runtime",
            "api_key": f"{base}-key",
        },
        "tokens": {
            name: f"{name}-{uuid.uuid4().hex[:24]}"
            for name in (
                "environment_create",
                "harness_create",
                "runtime_create",
                "api_key_create",
                "session_create",
                "event_send",
                "session_delete",
                "api_key_delete",
                "runtime_delete",
                "resource_delete",
                "environment_delete",
                "harness_archive",
            )
        },
        "resources": {},
        "status": "initializing",
        "cleaned": False,
    }
    save_state(state)
    return state


def update_resource(
    state: dict[str, Any],
    name: str,
    value: dict[str, Any],
) -> None:
    """更新一个资源的公开标识和状态，不保存完整响应。"""

    state.setdefault("resources", {})[name] = value
    save_state(state)


def wait_until(
    fetch: Callable[[], dict[str, Any]],
    complete: Callable[[dict[str, Any]], bool],
    failed: Callable[[dict[str, Any]], str | None],
    *,
    description: str,
    json_output: bool = False,
) -> dict[str, Any]:
    """以统一超时轮询异步控制面资源。"""

    timeout = positive_int_env("HARNESS_INFRA_WAIT_TIMEOUT_SECONDS", 15 * 60)
    interval = positive_int_env("HARNESS_INFRA_POLL_INTERVAL_SECONDS", 5)
    deadline = time.monotonic() + timeout
    while True:
        value = fetch()
        emit(
            "poll",
            f"等待{description}",
            json_output=json_output,
            status=value.get("Status"),
        )
        error = failed(value)
        if error:
            raise RuntimeError(f"{description} failed: {error}")
        if complete(value):
            return value
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for {description}")
        time.sleep(min(interval, remaining))


def parser(description: str, *, dry_run: bool = True) -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=description)
    result.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="输出脱敏 JSON 事件",
    )
    if dry_run:
        result.add_argument(
            "--dry-run",
            action="store_true",
            help="只显示请求计划，不访问网络或写状态",
        )
    return result


def run(main: Callable[[], None]) -> None:
    """统一处理公开错误，避免 traceback 或敏感正文进入输出。"""

    try:
        main()
    except HarnessInfraError as exc:
        print(f"[错误] {redact(str(exc))}", file=sys.stderr)
        raise SystemExit(1) from None
    except (RuntimeError, ValueError, OSError, TimeoutError) as exc:
        print(f"[错误] {redact(str(exc))}", file=sys.stderr)
        raise SystemExit(1) from None
