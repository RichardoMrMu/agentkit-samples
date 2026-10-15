"""从控制面资源创建到数据面对话及回收的确定性工作流。"""

from __future__ import annotations

import time
from typing import Any

from _clients import DataPlaneClient, HarnessInfraError, TopClient
from _common import (
    emit,
    report_resource,
    resource_info,
    save_state,
    update_resource,
    wait_until,
    wait_progress,
)

CLOUD_ENVIRONMENT_CONFIG = {
    "Type": "Cloud",
    "Resources": {"CpuMilli": 2000, "MemoryMb": 4096},
}
RUNTIME_RESOURCES = {
    "CpuMilli": 1000,
    "MemoryMiB": 2048,
    "MinReplicas": 1,
    "MaxReplicas": 1,
}

NOT_FOUND_CODES = {
    "InvalidApiKey.NotFound",
    "InvalidEnvironment.NotFound",
    "InvalidHarness.NotFound",
    "ResourceNotFound",
}


def _not_found(exc: HarnessInfraError) -> bool:
    return exc.status_code == 404 or exc.code in NOT_FOUND_CODES


def _list_all(
    client: TopClient,
    action: str,
    body: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """完整遍历控制面 Items/NextToken 分页。"""

    request = {**(body or {}), "MaxResults": 100}
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    while True:
        result = client.call(action, request)
        page = result.get("Items")
        token = result.get("NextToken")
        if not isinstance(page, list) or not all(
            isinstance(item, dict) for item in page
        ):
            raise RuntimeError(f"{action} response is missing Items[]")
        if token is not None and not isinstance(token, str):
            raise RuntimeError(f"{action} returned an invalid NextToken")
        items.extend(page)
        if not token:
            return items
        if token in seen:
            raise RuntimeError(f"{action} returned a repeated NextToken")
        seen.add(token)
        request["NextToken"] = token


def _prepare_endpoint(
    client: TopClient,
    state: dict[str, Any],
    *,
    endpoint_id: str,
    json_output: bool,
) -> dict[str, Any]:
    """复用唯一 Ready Endpoint；无 Endpoint 时同步创建。"""

    with wait_progress("查询 Endpoint", json_output=json_output):
        endpoints = _list_all(client, "ListEndpoints")
    if endpoint_id:
        selected = [
            endpoint
            for endpoint in endpoints
            if endpoint.get("EndpointId") == endpoint_id
        ]
        if len(selected) != 1:
            raise RuntimeError(f"EndpointId is not visible: {endpoint_id}")
        endpoint = selected[0]
        reused = True
    elif not endpoints:
        with wait_progress("创建 Endpoint", json_output=json_output):
            endpoint = client.call("CreateEndpoint", {})
        reused = False
    elif len(endpoints) == 1:
        endpoint = endpoints[0]
        reused = True
    else:
        raise RuntimeError("multiple Endpoints are visible; rerun with --endpoint-id")
    if endpoint.get("Status") != "ready":
        raise RuntimeError(f"Endpoint is not ready: {endpoint.get('Status') or '-'}")
    value = {
        "id": endpoint.get("EndpointId"),
        "name": endpoint.get("Name"),
        "base_url": endpoint.get("BaseUrl"),
        "status": endpoint.get("Status"),
        "owned": not reused,
    }
    if not isinstance(value["id"], str) or not isinstance(value["base_url"], str):
        raise RuntimeError("Endpoint response is missing EndpointId/BaseUrl")
    update_resource(state, "endpoint", value)
    report_resource(state, "endpoint", json_output=json_output)
    return endpoint


def _wait_environment_resource(
    client: TopClient,
    state: dict[str, Any],
    environment_id: str,
    *,
    json_output: bool,
) -> dict[str, Any]:
    """等待 Cloud Environment 自动资源出现并收敛到 Ready。"""

    deadline = time.monotonic() + 15 * 60
    resource: dict[str, Any] | None = None
    with wait_progress("Cloud 自动资源出现", json_output=json_output) as update:
        while time.monotonic() < deadline:
            items = _list_all(
                client,
                "ListEnvironmentResources",
                {"EnvironmentId": environment_id},
            )
            active = [item for item in items if item.get("Status") != "Deleted"]
            if len(active) > 1:
                raise RuntimeError(
                    "Cloud Environment returned multiple active resources"
                )
            if active:
                resource = active[0]
                break
            update(None)
            time.sleep(5)
    if resource is None or not isinstance(resource.get("ResourceId"), str):
        raise TimeoutError("Cloud Environment resource did not appear")
    resource_id = resource["ResourceId"]
    ready = wait_until(
        lambda: client.call("GetEnvironmentResource", {"ResourceId": resource_id}),
        lambda value: value.get("Status") == "Ready"
        and value.get("DesiredGeneration") == value.get("ObservedGeneration"),
        lambda value: (
            str(value.get("ErrorCode") or value.get("Status"))
            if value.get("Status") in {"Failed", "DeleteFailed"}
            or value.get("ErrorCode")
            else None
        ),
        description="EnvironmentResource Ready",
        json_output=json_output,
        report_result=False,
    )
    update_resource(
        state,
        "environment_resource",
        {
            "id": resource_id,
            "name": ready.get("Name") or resource.get("Name"),
            "status": ready.get("Status"),
            "revision": ready.get("Revision"),
            "desired_generation": ready.get("DesiredGeneration"),
            "observed_generation": ready.get("ObservedGeneration"),
            "owned": True,
        },
    )
    report_resource(state, "environment_resource", json_output=json_output)
    return ready


def create_resources(
    client: TopClient,
    state: dict[str, Any],
    *,
    endpoint_id: str = "",
    json_output: bool = False,
) -> dict[str, Any]:
    """创建 Endpoint 之外的全部控制面资源并等待异步资源就绪。"""

    emit("step", "准备 Endpoint", json_output=json_output, step=2)
    _prepare_endpoint(
        client,
        state,
        endpoint_id=endpoint_id,
        json_output=json_output,
    )
    names = state["names"]
    tokens = state["tokens"]
    config = state["config"]

    emit(
        "request",
        "创建 Cloud Environment",
        json_output=json_output,
        step=3,
    )
    with wait_progress("创建 Cloud Environment", json_output=json_output):
        environment = client.call(
            "CreateEnvironment",
            {
                "Name": names["environment"],
                "Description": "Harness Infra resource-to-chat HTTP tutorial",
                "Config": CLOUD_ENVIRONMENT_CONFIG,
                "Metadata": {"purpose": "resource-to-chat-tutorial"},
                "ClientToken": tokens["environment_create"],
            },
        )
    environment_id = environment.get("EnvironmentId")
    if not isinstance(environment_id, str) or not environment_id:
        raise RuntimeError("CreateEnvironment response is missing EnvironmentId")
    update_resource(
        state,
        "environment",
        {
            "id": environment_id,
            "name": environment.get("Name") or names["environment"],
            "parameters": CLOUD_ENVIRONMENT_CONFIG,
            "status": environment.get("Status"),
            "owned": True,
        },
    )
    environment = wait_until(
        lambda: client.call("GetEnvironment", {"EnvironmentId": environment_id}),
        lambda value: value.get("Status") == "Active",
        lambda value: (
            str(value.get("Status")) if value.get("Status") == "Archived" else None
        ),
        description="Environment Active",
        json_output=json_output,
        report_result=False,
    )
    update_resource(
        state,
        "environment",
        {"id": environment_id, "status": "Active", "owned": True},
    )
    report_resource(state, "environment", json_output=json_output)
    emit("step", "准备 Cloud 自动资源", json_output=json_output, step=4)
    _wait_environment_resource(
        client,
        state,
        environment_id,
        json_output=json_output,
    )

    emit(
        "request",
        "创建 Harness",
        json_output=json_output,
        step=5,
    )
    with wait_progress("创建 Harness", json_output=json_output):
        harness = client.call(
            "CreateHarness",
            {
                "Name": names["harness"],
                "Description": "Harness Infra resource-to-chat HTTP tutorial",
                "Model": {
                    "Id": config["model_id"],
                    "BaseUrl": config["model_base_url"],
                },
                "AgentLoop": config["agent_loop"],
                "System": "请简洁回答用户问题。不要声称调用了未配置的工具。",
                "Tools": [],
                "McpServers": [],
                "Skills": [],
                "Metadata": {"purpose": "resource-to-chat-tutorial"},
                "ClientToken": tokens["harness_create"],
            },
        )
    harness_id = harness.get("HarnessId")
    if not isinstance(harness_id, str) or harness.get("Status") != "Active":
        raise RuntimeError("CreateHarness did not return an active Harness")
    update_resource(
        state,
        "harness",
        {
            "id": harness_id,
            "name": harness.get("Name") or names["harness"],
            "version": harness.get("HarnessVersion"),
            "status": harness.get("Status"),
            "owned": True,
        },
    )

    report_resource(state, "harness", json_output=json_output)

    emit(
        "request",
        "创建 RuntimeTemplate",
        json_output=json_output,
        step=6,
    )
    with wait_progress("创建 RuntimeTemplate", json_output=json_output):
        runtime = client.call(
            "CreateRuntimeTemplate",
            {
                "Name": names["runtime_template"],
                "Description": "Harness Infra resource-to-chat HTTP tutorial",
                "RoleName": config["role_name"],
                "Resources": RUNTIME_RESOURCES,
                "Meta": {"purpose": "resource-to-chat-tutorial"},
                "ClientToken": tokens["runtime_create"],
            },
        )
    runtime_id = runtime.get("RuntimeTemplateId")
    if not isinstance(runtime_id, str) or not runtime_id:
        raise RuntimeError(
            "CreateRuntimeTemplate response is missing RuntimeTemplateId"
        )
    update_resource(
        state,
        "runtime_template",
        {
            "id": runtime_id,
            "name": runtime.get("Name") or names["runtime_template"],
            "parameters": RUNTIME_RESOURCES,
            "status": runtime.get("Status"),
            "owned": True,
        },
    )
    runtime = wait_until(
        lambda: client.call("GetRuntimeTemplate", {"RuntimeTemplateId": runtime_id}),
        lambda value: value.get("Status") == "Ready",
        lambda value: (
            str(value.get("Status")) if value.get("Status") == "Failed" else None
        ),
        description="RuntimeTemplate Ready",
        json_output=json_output,
        report_result=False,
    )
    update_resource(
        state,
        "runtime_template",
        {"id": runtime_id, "status": "Ready", "owned": True},
    )
    report_resource(state, "runtime_template", json_output=json_output)
    state["status"] = "resources_ready"
    save_state(state)
    emit(
        "completed",
        "控制面资源全部就绪",
        json_output=json_output,
        step=7,
        resources=[
            resource_info(state, key, compact=True)
            for key in (
                "endpoint",
                "environment",
                "environment_resource",
                "harness",
                "runtime_template",
            )
        ],
        status=state["status"],
    )
    return state


def _extract_text(event: dict[str, Any]) -> str:
    content = event.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _delete_session(
    data: DataPlaneClient,
    session_id: str,
    *,
    json_output: bool,
) -> None:
    """归档并异步删除 Session，直到 Get 返回 404。"""

    with wait_progress("Session 删除完成", json_output=json_output):
        try:
            data.call("POST", f"/v1/sessions/{session_id}/archive")
        except HarnessInfraError as exc:
            if not _not_found(exc):
                raise
        try:
            data.call("DELETE", f"/v1/sessions/{session_id}")
        except HarnessInfraError as exc:
            if not _not_found(exc):
                raise
        deadline = time.monotonic() + 10 * 60
        while time.monotonic() < deadline:
            try:
                data.call("GET", f"/v1/sessions/{session_id}")
            except HarnessInfraError as exc:
                if _not_found(exc):
                    break
                raise
            time.sleep(3)
        else:
            raise TimeoutError("timed out waiting for Session deletion")


def create_key_and_chat(
    client: TopClient,
    state: dict[str, Any],
    *,
    ark_api_key_name: str,
    prompt: str,
    json_output: bool = False,
) -> str:
    """创建一次性环境 Key，完成真实模型轮次，再删除 Session 和 Key。"""

    if state.get("status") != "resources_ready":
        raise RuntimeError("control resources are not ready")
    resources = state["resources"]
    endpoint = resources["endpoint"]
    environment = resources["environment"]
    harness = resources["harness"]
    runtime = resources["runtime_template"]
    tokens = state["tokens"]

    emit(
        "request",
        "创建一次性 Environment API Key",
        json_output=json_output,
        step=1,
    )
    with wait_progress("创建一次性 Environment API Key", json_output=json_output):
        key_result = client.call(
            "CreateAPIKey",
            {
                "Name": state["names"]["api_key"],
                "Description": "Temporary key for resource-to-chat tutorial",
                "EnvironmentId": environment["id"],
                "EndpointId": endpoint["id"],
                "ClientToken": tokens["api_key_create"],
            },
        )
    api_key_id = key_result.get("ApiKeyId")
    environment_key = key_result.get("Key")
    if not isinstance(api_key_id, str) or not api_key_id:
        raise RuntimeError("CreateAPIKey response is missing ApiKeyId")
    update_resource(
        state,
        "api_key",
        {
            "id": api_key_id,
            "name": key_result.get("Name") or state["names"]["api_key"],
            "status": "active",
            "owned": True,
        },
    )
    if not isinstance(environment_key, str) or not environment_key:
        raise RuntimeError(
            "CreateAPIKey did not return Key; a replay cannot recover plaintext"
        )

    report_resource(state, "api_key", json_output=json_output)

    data = DataPlaneClient(endpoint["base_url"], environment_key)
    session_id = ""
    answer = ""
    primary_error: BaseException | None = None
    try:
        emit("step", "创建 Session", json_output=json_output, step=2)
        with wait_progress("创建 Session", json_output=json_output):
            session = data.call(
                "POST",
                "/v1/sessions",
                body={
                    "agent": {"type": "agent", "id": harness["id"]},
                    "environment_id": environment["id"],
                    "runtime_template_id": runtime["id"],
                    "runtime_type": "agentkit_runtime",
                    "title": "Harness Infra HTTP tutorial",
                    "initial_events": [],
                },
                headers={
                    "x-ark-key-name": ark_api_key_name,
                    "Idempotency-Key": tokens["session_create"],
                },
            )
        session_id = str(session.get("id") or "")
        if not session_id:
            raise RuntimeError("Create Session response is missing id")
        if session.get("ark_api_key_name") != ark_api_key_name:
            raise RuntimeError("Session did not preserve ark_api_key_name")
        update_resource(
            state,
            "session",
            {
                "id": session_id,
                "name": session.get("name"),
                "title": session.get("title") or "Harness Infra HTTP tutorial",
                "parameters": {
                    "runtime_type": "agentkit_runtime",
                    "ark_api_key_name": ark_api_key_name,
                },
                "status": session.get("status"),
                "owned": True,
            },
        )
        report_resource(state, "session", json_output=json_output)

        emit("step", "发送用户问题", json_output=json_output, step=3)
        event_id = tokens["event_send"]
        with wait_progress("发送用户问题", json_output=json_output):
            sent = data.call(
                "POST",
                f"/v1/sessions/{session_id}/events",
                body={
                    "events": [
                        {
                            "id": event_id,
                            "type": "user.message",
                            "content": [{"type": "text", "text": prompt}],
                        }
                    ]
                },
            )
        events = sent.get("data")
        if not isinstance(events, list):
            raise RuntimeError("Send Events response is missing data[]")
        cursor = max(
            (
                int(event.get("sequence") or 0)
                for event in events
                if isinstance(event, dict)
            ),
            default=0,
        )
        report_resource(
            state,
            "session",
            json_output=json_output,
            parameters={"event_id": event_id, "event_type": "user.message"},
            prompt=prompt,
            result="问题已发送",
        )
        emit("step", "等待模型回复", json_output=json_output, step=4)
        deadline = time.monotonic() + 5 * 60
        saw_answer = False
        turn_completed = False
        with wait_progress("模型回复", json_output=json_output):
            while time.monotonic() < deadline:
                page = data.call(
                    "GET",
                    f"/v1/sessions/{session_id}/events",
                    params={
                        "after_sequence": cursor,
                        "limit": 100,
                        "order": "asc",
                    },
                )
                new_events = page.get("data")
                if not isinstance(new_events, list):
                    raise RuntimeError("List Events response is missing data[]")
                for event in new_events:
                    if not isinstance(event, dict):
                        continue
                    sequence = int(event.get("sequence") or 0)
                    if sequence <= cursor:
                        continue
                    cursor = sequence
                    event_type = event.get("type")
                    if json_output:
                        emit(
                            "event",
                            "收到持久化事件",
                            json_output=json_output,
                            sequence=sequence,
                            event_type=event_type,
                        )
                    if event_type in {"agent.error", "session.error"}:
                        raise RuntimeError(f"conversation failed: {event_type}")
                    if event_type == "agent.message":
                        text = _extract_text(event)
                        if text:
                            answer = text
                            saw_answer = True
                    if event_type == "session.status_idle":
                        stop = event.get("stop_reason")
                        stop = stop if isinstance(stop, dict) else {}
                        if saw_answer and stop.get("type") == "end_turn":
                            turn_completed = True
                            break
                        if stop.get("type") == "requires_action":
                            raise RuntimeError(
                                "conversation requires an unsupported client action"
                            )
                if turn_completed:
                    break
                time.sleep(2)
            else:
                raise TimeoutError("timed out waiting for agent.message and end_turn")
        state["status"] = "chat_completed"
        save_state(state)
        update_resource(state, "session", {"status": "idle"})
        report_resource(
            state,
            "session",
            json_output=json_output,
            parameters={"stop_reason": "end_turn"},
            answer=answer,
        )
        return answer
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        emit("step", "回收 Session 和一次性 API Key", json_output=json_output, step=5)
        cleanup_errors: list[str] = []
        if session_id:
            try:
                _delete_session(data, session_id, json_output=json_output)
                update_resource(
                    state,
                    "session",
                    {"id": session_id, "status": "deleted", "owned": True},
                )
                report_resource(
                    state, "session", json_output=json_output, result="已删除"
                )
            except Exception as exc:
                cleanup_errors.append(f"Session cleanup: {exc}")
        try:
            with wait_progress("删除一次性 API Key", json_output=json_output):
                client.call(
                    "DeleteAPIKey",
                    {
                        "ApiKeyId": api_key_id,
                        "ClientToken": tokens["api_key_delete"],
                    },
                )
            update_resource(
                state,
                "api_key",
                {"id": api_key_id, "status": "deleted", "owned": True},
            )
            report_resource(state, "api_key", json_output=json_output, result="已删除")
        except Exception as exc:
            cleanup_errors.append(f"API Key cleanup: {exc}")
        environment_key = ""
        if cleanup_errors and primary_error is None:
            raise RuntimeError("; ".join(cleanup_errors))
        if cleanup_errors and primary_error is not None:
            emit(
                "warning",
                "对话失败且部分即时清理未完成",
                json_output=json_output,
                details="; ".join(cleanup_errors),
            )


def _delete_top_and_wait_not_found(
    client: TopClient,
    *,
    delete_action: str,
    delete_body: dict[str, Any],
    get_action: str,
    get_body: dict[str, Any],
    description: str,
    json_output: bool,
) -> None:
    """删除控制面对象并用 Get 404 确认终态。"""

    with wait_progress(f"{description} 删除完成", json_output=json_output):
        try:
            client.call(delete_action, delete_body)
        except HarnessInfraError as exc:
            if not _not_found(exc):
                raise
        deadline = time.monotonic() + 15 * 60
        while time.monotonic() < deadline:
            try:
                client.call(get_action, get_body)
            except HarnessInfraError as exc:
                if _not_found(exc):
                    break
                raise
            time.sleep(5)
        else:
            raise TimeoutError(f"timed out waiting for {description} deletion")


def cleanup_resources(
    client: TopClient,
    state: dict[str, Any],
    *,
    json_output: bool = False,
) -> None:
    """按引用关系回收模板、自动资源和环境，并归档 Harness。"""

    resources = state.get("resources")
    if not isinstance(resources, dict):
        raise RuntimeError("state resources are invalid")
    tokens = state["tokens"]

    emit("step", "检查 Session 并清理残留 API Key", json_output=json_output, step=1)
    session = resources.get("session") or {}
    api_key = resources.get("api_key") or {}
    report_resource(
        state, "session", json_output=json_output, result="检查 Session 状态"
    )
    if session.get("status") not in {None, "deleted"}:
        raise RuntimeError(
            "Session is not deleted; rerun 02_chat.py while its one-time key is available"
        )
    if api_key.get("status") not in {None, "deleted"}:
        try:
            with wait_progress("删除一次性 API Key", json_output=json_output):
                client.call(
                    "DeleteAPIKey",
                    {
                        "ApiKeyId": api_key["id"],
                        "ClientToken": tokens["api_key_delete"],
                    },
                )
        except HarnessInfraError as exc:
            if not _not_found(exc):
                raise

        update_resource(state, "api_key", {"status": "deleted"})
        report_resource(state, "api_key", json_output=json_output, result="已清理")
    else:
        report_resource(
            state, "api_key", json_output=json_output, result="无需清理，跳过"
        )

    emit("step", "删除 RuntimeTemplate", json_output=json_output, step=2)
    runtime = resources.get("runtime_template") or {}
    if runtime.get("id") and runtime.get("status") != "deleted":
        _delete_top_and_wait_not_found(
            client,
            delete_action="DeleteRuntimeTemplate",
            delete_body={
                "RuntimeTemplateId": runtime["id"],
                "Reason": "Resource-to-chat tutorial cleanup",
                "ClientToken": tokens["runtime_delete"],
            },
            get_action="GetRuntimeTemplate",
            get_body={"RuntimeTemplateId": runtime["id"]},
            description="RuntimeTemplate",
            json_output=json_output,
        )
        update_resource(
            state,
            "runtime_template",
            {**runtime, "status": "deleted"},
        )
        report_resource(
            state, "runtime_template", json_output=json_output, result="已删除"
        )
    else:
        report_resource(
            state,
            "runtime_template",
            json_output=json_output,
            result="不存在或已删除，跳过",
        )

    emit("step", "删除 EnvironmentResource", json_output=json_output, step=3)
    resource = resources.get("environment_resource") or {}
    if resource.get("id") and resource.get("status") != "Deleted":
        try:
            with wait_progress("删除 EnvironmentResource", json_output=json_output):
                client.call(
                    "DeleteEnvironmentResource",
                    {
                        "ResourceId": resource["id"],
                        "ClientToken": tokens["resource_delete"],
                    },
                )
        except HarnessInfraError as exc:
            if not _not_found(exc):
                raise
        deleted = wait_until(
            lambda: client.call(
                "GetEnvironmentResource", {"ResourceId": resource["id"]}
            ),
            lambda value: value.get("Status") == "Deleted",
            lambda value: (
                str(value.get("ErrorCode") or value.get("Status"))
                if value.get("Status") in {"Failed", "DeleteFailed"}
                or value.get("ErrorCode")
                else None
            ),
            description="EnvironmentResource Deleted",
            json_output=json_output,
            report_result=False,
        )
        update_resource(
            state,
            "environment_resource",
            {
                **resource,
                "status": deleted.get("Status"),
                "revision": deleted.get("Revision"),
            },
        )
        report_resource(
            state, "environment_resource", json_output=json_output, result="已删除"
        )
    else:
        report_resource(
            state,
            "environment_resource",
            json_output=json_output,
            result="不存在或已删除，跳过",
        )

    emit("step", "删除 Environment", json_output=json_output, step=4)
    environment = resources.get("environment") or {}
    if environment.get("id") and environment.get("status") != "deleted":
        _delete_top_and_wait_not_found(
            client,
            delete_action="DeleteEnvironment",
            delete_body={
                "EnvironmentId": environment["id"],
                "ClientToken": tokens["environment_delete"],
            },
            get_action="GetEnvironment",
            get_body={"EnvironmentId": environment["id"]},
            description="Environment",
            json_output=json_output,
        )
        update_resource(
            state,
            "environment",
            {**environment, "status": "deleted"},
        )
        report_resource(state, "environment", json_output=json_output, result="已删除")
    else:
        report_resource(
            state, "environment", json_output=json_output, result="不存在或已删除，跳过"
        )

    emit("step", "归档 Harness", json_output=json_output, step=5)
    harness = resources.get("harness") or {}
    if harness.get("id") and harness.get("status") not in {"Archived", "not_found"}:
        try:
            with wait_progress("归档 Harness", json_output=json_output):
                archived = client.call(
                    "ArchiveHarness",
                    {
                        "HarnessId": harness["id"],
                        "ClientToken": tokens["harness_archive"],
                    },
                )
        except HarnessInfraError as exc:
            if not _not_found(exc):
                raise
            update_resource(state, "harness", {"status": "not_found"})
        else:
            update_resource(
                state,
                "harness",
                {
                    **harness,
                    "status": archived.get("Status"),
                },
            )
        report_resource(
            state, "harness", json_output=json_output, result="归档操作完成"
        )
    else:
        report_resource(
            state, "harness", json_output=json_output, result="不存在或已归档，跳过"
        )

    state["status"] = "cleaned"
    state["cleaned"] = True
    state["cleaned_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_state(state)
    emit(
        "completed",
        "本轮资源清理完成（保留 Endpoint）",
        json_output=json_output,
        step=6,
        resources=[
            {
                **resource_info(state, key, compact=True),
                **({"result": "保留"} if key == "endpoint" else {}),
            }
            for key in (
                "endpoint",
                "environment",
                "environment_resource",
                "harness",
                "runtime_template",
                "api_key",
                "session",
            )
        ],
        status=state["status"],
    )
