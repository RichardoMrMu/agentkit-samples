# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
"""Stdio MCP server for SMS actions and the qualification MCP App."""

from __future__ import annotations

import io
import json
import queue
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, TextIO

from api_client import SmsApiClient, sanitize_output
from request_context import RequestContext
from action_contracts import PUBLIC_QUERY_ACTIONS
from traffic_driving import TrafficDrivingDraft, TrafficDrivingError, validate_creation_body
from traffic_wizard import run_traffic_wizard
from batch_wizard import run_batch_wizard
from qualification_display import browser_display_adapter, callback_display_adapter
from qualification_upload import QualificationUploadError
from qualification_wizard import run_qualification_wizard
from service_wizard import run_service_wizard
from signature_application import CHANNEL_TYPES, SignatureDraft, validate_signature_prefill
from signature_wizard import run_signature_wizard
import service_onboarding
import sms_cli


APP_VERSION = "1.12.0"
APP_RESOURCE_URI = "ui://volcengine-sms/qualification-v3.html"
APP_MIME_TYPE = "text/html;profile=mcp-app"
APP_TOOL_META = {
    "ui": {"resourceUri": APP_RESOURCE_URI},
    "ui/resourceUri": APP_RESOURCE_URI,
    "workbuddy": {"ui": {"launchSurface": "panel"}},
}
APP_TEMPLATE = (
    Path(__file__).resolve().parents[1] / "assets" / "private_form_mcp_app.html"
).read_text(encoding="utf-8")


def form_app_html(display_key: str, ready_event: str, finished_event: str, result_tool: str) -> str:
    config = {
        "displayKey": display_key, "readyEvent": ready_event,
        "finishedEvent": finished_event, "resultTool": result_tool,
        "version": APP_VERSION,
    }
    return APP_TEMPLATE.replace("__FORM_CONFIG_JSON__", json.dumps(config, ensure_ascii=False))


APP_HTML = form_app_html(
    "qualificationDisplay", "qualification:wizard-ready",
    "qualification:wizard-finished", "get_qualification_application_result",
)
BATCH_RESOURCE_URI = "ui://volcengine-sms/batch-creation-v1.html"
BATCH_APP_HTML = form_app_html(
    "batchDisplay", "batch:wizard-ready", "batch:wizard-finished",
    "get_batch_task_creation_result",
)
BATCH_TOOL_META = {
    "ui": {"resourceUri": BATCH_RESOURCE_URI},
    "ui/resourceUri": BATCH_RESOURCE_URI,
    "workbuddy": {"ui": {"launchSurface": "panel"}},
}
TRAFFIC_RESOURCE_URI = "ui://volcengine-sms/traffic-creation-v1.html"
TRAFFIC_APP_HTML = form_app_html(
    "trafficDisplay", "traffic:wizard-ready", "traffic:wizard-finished",
    "get_traffic_driving_creation_result",
)
TRAFFIC_TOOL_META = {
    "ui": {"resourceUri": TRAFFIC_RESOURCE_URI},
    "ui/resourceUri": TRAFFIC_RESOURCE_URI,
    "workbuddy": {"ui": {"launchSurface": "panel"}},
}
SERVICE_RESOURCE_URI = "ui://volcengine-sms/service-onboarding-v1.html"
SERVICE_APP_HTML = form_app_html(
    "serviceDisplay", "service:wizard-ready", "service:wizard-finished",
    "get_sms_service_onboarding_result",
)
SERVICE_TOOL_META = {
    "ui": {"resourceUri": SERVICE_RESOURCE_URI},
    "ui/resourceUri": SERVICE_RESOURCE_URI,
    "workbuddy": {"ui": {"launchSurface": "panel"}},
}
SIGNATURE_RESOURCE_URI = "ui://volcengine-sms/signature-application.html"
SIGNATURE_APP_HTML = form_app_html(
    "signatureDisplay", "signature:wizard-ready", "signature:wizard-finished",
    "get_signature_application_result",
)
SIGNATURE_TOOL_META = {
    "ui": {"resourceUri": SIGNATURE_RESOURCE_URI},
    "ui/resourceUri": SIGNATURE_RESOURCE_URI,
    "workbuddy": {"ui": {"launchSurface": "panel"}},
}
SIGNATURE_FLOW_PROTOCOL = {
    "failed": "signature_application_failed",
    "not_found": {"status": "signature_application_not_found", "code": "signature_flow_not_found"},
    "not_ready": {"status": "signature_application_not_ready", "code": "signature_flow_not_ready"},
    "panel_unavailable": "signature_panel_not_visible",
    "browser_unavailable": "signature_browser_not_visible",
    "cancelling": "signature_application_cancelling",
    "browser_tool": "open_signature_application_in_browser",
}
FORM_RESOURCES = {
    APP_RESOURCE_URI: ("火山引擎短信资质表单", APP_HTML),
    BATCH_RESOURCE_URI: ("火山引擎短信群发任务表单", BATCH_APP_HTML),
    TRAFFIC_RESOURCE_URI: ("火山引擎短信引流信息表单", TRAFFIC_APP_HTML),
    SERVICE_RESOURCE_URI: ("火山引擎国内短信服务确认", SERVICE_APP_HTML),
    SIGNATURE_RESOURCE_URI: ("短信签名申请表单", SIGNATURE_APP_HTML),
}
QUALIFICATION_FLOW_PROTOCOL = {
    "failed": "qualification_application_failed",
    "not_found": {"status": "qualification_application_not_found", "code": "qualification_flow_not_found"},
    "not_ready": {"status": "qualification_application_not_ready", "code": "qualification_flow_not_ready"},
    "panel_unavailable": "qualification_panel_not_visible",
    "browser_unavailable": "qualification_browser_not_visible",
    "cancelling": "qualification_application_cancelling",
    "browser_tool": "open_qualification_application_in_browser",
}
BATCH_FLOW_PROTOCOL = {
    "failed": "batch_creation_failed",
    "not_found": {"status": "batch_form_not_found", "code": "batch_flow_not_found"},
    "not_ready": {"status": "batch_form_not_ready", "code": "batch_flow_not_ready"},
    "panel_unavailable": "batch_panel_not_visible",
    "browser_unavailable": "batch_browser_not_visible",
    "cancelling": "batch_form_cancelling",
    "browser_tool": "open_batch_task_creation_in_browser",
}
TRAFFIC_FLOW_PROTOCOL = {
    "failed": "traffic_creation_failed",
    "not_found": {"status": "traffic_form_not_found", "code": "traffic_flow_not_found"},
    "not_ready": {"status": "traffic_form_not_ready", "code": "traffic_flow_not_ready"},
    "panel_unavailable": "traffic_panel_not_visible",
    "browser_unavailable": "traffic_browser_not_visible",
    "cancelling": "traffic_form_cancelling",
    "browser_tool": "open_traffic_driving_creation_in_browser",
}
SERVICE_FLOW_PROTOCOL = {
    "failed": "service_onboarding_failed",
    "not_found": {
        "status": "service_onboarding_not_found",
        "code": "service_flow_not_found",
    },
    "not_ready": {
        "status": "service_onboarding_not_ready",
        "code": "service_flow_not_ready",
    },
    "panel_unavailable": "service_onboarding_panel_not_visible",
    "browser_unavailable": "service_onboarding_browser_not_visible",
    "cancelling": "service_onboarding_cancelling",
    "browser_tool": "open_sms_service_onboarding_in_browser",
}
FLOW_RESULT_TTL_SECONDS = 30 * 60
FLOW_SHUTDOWN_WAIT_SECONDS = 2
DISPLAY_READY_WAIT_SECONDS = 8
READ_ACTIONS = {
    "account-info",
    "api-read",
    "auth-doctor",
    "runtime-info",
    "service-status",
    "service-preview",
    "batch-content-check",
    "batch-detail",
    "batch-list",
    "batch-precheck",
    "batch-template-demo",
    "list-message-groups",
    "list-qualifications",
    "list-signatures",
    "list-templates",
    "match-template",
    "message-group-detail",
    "send-preview",
    "send-status",
    "template-preview",
    "traffic-check",
}
WRITE_ACTIONS = {
    "service-submit",
    "batch-cancel",
    "batch-create",
    "batch-launch-preview",
    "batch-launch-submit",
    "send-submit",
    "signature-preview",
    "signature-submit",
    "template-submit",
}


class ClientFactory(Protocol):
    """ClientFactory 在创建客户端时绑定所属操作，不依赖线程共享的当前任务。"""

    def __call__(self, *, request_context: RequestContext) -> SmsApiClient: ...


def _emit_mcp_app_event(event: str, **fields: Any) -> None:
    payload = {"event": event}
    payload.update(fields)
    sys.stderr.write(
        "QUALIFICATION_MCP_APP "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    )
    sys.stderr.flush()


@dataclass
class QualificationFlow:
    flow_id: str
    ready: "queue.Queue[object]" = field(default_factory=queue.Queue)
    thread: Optional[threading.Thread] = None
    url: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    display_ready: threading.Event = field(default_factory=threading.Event)
    display_mode: str = "mcp-app"
    finished_at: Optional[float] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    request_context: RequestContext = field(default_factory=lambda: RequestContext.create("mcp"))


class PrivateFormFlowManager:
    def __init__(self, client_factory: ClientFactory, *, runner: Callable, protocol: Mapping[str, Any]) -> None:
        self._client_factory = client_factory
        self._runner = runner
        self._protocol = protocol
        self._flows: Dict[str, QualificationFlow] = {}
        self._lock = threading.Lock()

    def _prune_finished(self) -> None:
        cutoff = time.monotonic() - FLOW_RESULT_TTL_SECONDS
        with self._lock:
            stale = [
                flow_id
                for flow_id, flow in self._flows.items()
                if flow.finished_at is not None and flow.finished_at <= cutoff
            ]
            for flow_id in stale:
                self._flows.pop(flow_id, None)

    def _active_flow(self) -> Optional[QualificationFlow]:
        for flow in self._flows.values():
            if (
                flow.thread is not None
                and flow.thread.is_alive()
                and flow.url
                and not flow.cancel_event.is_set()
            ):
                return flow
        return None

    def start(
        self,
        parameters: Optional[Mapping[str, Any]] = None,
        *,
        request_context: Optional[RequestContext] = None,
    ) -> QualificationFlow:
        parameters = dict(parameters or {})
        self._prune_finished()
        with self._lock:
            active = self._active_flow()
            if active is not None:
                if active.parameters != parameters:
                    raise ValueError("Another form with different parameters is already open")
                _emit_mcp_app_event("flow_reused", flowId=active.flow_id)
                return active
            flow = QualificationFlow(
                uuid.uuid4().hex, parameters=parameters,
                request_context=request_context or RequestContext.create("mcp"),
            )
            self._flows[flow.flow_id] = flow
            _emit_mcp_app_event(
                "flow_created", flowId=flow.flow_id, operation_id=flow.request_context.operation_id,
            )

        def present(url: str) -> None:
            with self._lock:
                flow.url = url
            _emit_mcp_app_event("flow_ready", flowId=flow.flow_id)
            flow.ready.put(url)

        def display_ready() -> None:
            if flow.display_ready.is_set():
                return
            flow.display_ready.set()
            _emit_mcp_app_event(
                "display_ready",
                flowId=flow.flow_id,
                mode=flow.display_mode,
            )

        def run() -> None:
            client = None
            try:
                client = self._client_factory(request_context=flow.request_context)
                result = self._runner(
                    client,
                    **flow.parameters,
                    display=callback_display_adapter(
                        present,
                        name="mcp-app",
                        allows_embedding=True,
                    ),
                    cancel_event=flow.cancel_event,
                    on_display_ready=display_ready,
                )
                with self._lock:
                    flow.result = dict(result)
            except (QualificationUploadError, sms_cli.CliError) as error:
                with self._lock:
                    flow.error = {
                        "status": self._protocol["failed"],
                        "code": error.code,
                        "requestId": error.request_id,
                        "logId": error.log_id,
                        "outcomeUnknown": error.outcome_unknown,
                    }
                if flow.url is None:
                    # The worker owns the credentials; redact before handing an error to MCP.
                    secrets = tuple(getattr(client, "output_secrets", ()))
                    message = sanitize_output(str(error), secrets=secrets)
                    diagnostics = {
                        "request_id": error.request_id, "log_id": error.log_id,
                        "outcome_unknown": error.outcome_unknown,
                    }
                    public_error = sms_cli.CliError(
                        message, error.code, retryable=getattr(error, "retryable", False),
                        remediation=sanitize_output(getattr(error, "remediation", None), secrets=secrets),
                        **diagnostics,
                    )
                    flow.ready.put(public_error)
            except Exception:
                with self._lock:
                    flow.error = {
                        "status": self._protocol["failed"],
                        "code": "internal_error",
                        "outcomeUnknown": False,
                    }
                if flow.url is None:
                    flow.ready.put(RuntimeError("Private form failed"))
            finally:
                with self._lock:
                    flow.url = None
                    flow.finished_at = time.monotonic()

        flow.thread = threading.Thread(target=run, daemon=True)
        flow.thread.start()
        try:
            ready = flow.ready.get(timeout=60)
        except queue.Empty:
            flow.cancel_event.set()
            raise
        if isinstance(ready, BaseException):
            raise ready
        return flow

    def result(
        self,
        flow_id: str,
        *,
        wait_for_display_seconds: float = 0,
    ) -> Dict[str, Any]:
        self._prune_finished()
        with self._lock:
            flow = self._flows.get(flow_id)
        if flow is None:
            return {**self._protocol["not_found"], "completed": True}
        if wait_for_display_seconds > 0 and not flow.display_ready.is_set():
            flow.display_ready.wait(timeout=wait_for_display_seconds)
        with self._lock:
            context_fields = flow.request_context.diagnostics()
            if flow.result is not None:
                return {**flow.result, **context_fields, "completed": True}
            if flow.error is not None:
                return {**flow.error, **context_fields, "completed": True}
            return {
                **context_fields,
                "completed": False,
                "status": (
                    "waiting_for_customer"
                    if flow.display_ready.is_set()
                    else "waiting_for_display"
                ),
                "displayStatus": (
                    "opened" if flow.display_ready.is_set() else "waiting_for_host"
                ),
                "displayMode": flow.display_mode,
            }

    def ensure_visible(self, flow_id: str) -> Dict[str, Any]:
        result = self.result(
            flow_id,
            wait_for_display_seconds=DISPLAY_READY_WAIT_SECONDS,
        )
        if result.get("status") != "waiting_for_display":
            return result
        return {
            **result,
            "status": self._protocol["panel_unavailable"],
            "displayStatus": "not_opened",
            "displayMode": "mcp-app",
            "code": self._protocol["panel_unavailable"],
            "preferredFallback": {
                "displayMode": "host",
                "requires": "local_url_preview",
                "cancelCurrentFlow": True,
            },
            "browserFallbackTool": self._protocol["browser_tool"],
            "browserFallbackArguments": {"flowId": flow_id},
        }

    def open_in_browser(self, flow_id: str) -> Dict[str, Any]:
        self._prune_finished()
        with self._lock:
            flow = self._flows.get(flow_id)
            if flow is None:
                return {**self._protocol["not_found"], "completed": True}
            if flow.finished_at is not None:
                if flow.result is not None:
                    return {**flow.result, **flow.request_context.diagnostics(), "completed": True}
                if flow.error is not None:
                    return {**flow.error, **flow.request_context.diagnostics(), "completed": True}
            url = flow.url
            if not url:
                return dict(self._protocol["not_ready"])
            flow.display_mode = "browser"

        try:
            browser_display_adapter().present(url)
        except QualificationUploadError:
            _emit_mcp_app_event("browser_open_failed", flowId=flow_id)
            result = self.result(flow_id)
        else:
            _emit_mcp_app_event("browser_open_requested", flowId=flow_id)
            result = self.result(
                flow_id,
                wait_for_display_seconds=DISPLAY_READY_WAIT_SECONDS,
            )
        if result.get("status") != "waiting_for_display":
            return result
        return {
            **result,
            "status": self._protocol["browser_unavailable"],
            "displayStatus": "not_opened",
            "displayMode": "browser",
            "code": self._protocol["browser_unavailable"],
        }

    def cancel(self, flow_id: str) -> Dict[str, Any]:
        with self._lock:
            flow = self._flows.get(flow_id)
            if flow is None:
                return {**self._protocol["not_found"], "completed": True}
            if flow.finished_at is not None:
                if flow.result is not None:
                    return {**flow.result, **flow.request_context.diagnostics(), "completed": True}
                if flow.error is not None:
                    return {**flow.error, **flow.request_context.diagnostics(), "completed": True}
            flow.cancel_event.set()
            worker = flow.thread
        if worker is not None:
            worker.join(timeout=FLOW_SHUTDOWN_WAIT_SECONDS)
        if flow.finished_at is not None:
            return self.result(flow_id)
        return {
            **flow.request_context.diagnostics(), "status": self._protocol["cancelling"],
            "completed": False, "fallbackAllowed": False,
        }

    def cancel_all(self) -> None:
        with self._lock:
            flows = list(self._flows.values())
            for flow in flows:
                flow.cancel_event.set()
        for flow in flows:
            if flow.thread is not None and flow.thread.is_alive():
                flow.thread.join(timeout=FLOW_SHUTDOWN_WAIT_SECONDS)


class QualificationFlowManager(PrivateFormFlowManager):
    def __init__(self, client_factory: ClientFactory = SmsApiClient) -> None:
        super().__init__(client_factory, runner=run_qualification_wizard, protocol=QUALIFICATION_FLOW_PROTOCOL)


def _run_service_form(client: SmsApiClient, *, display, cancel_event, on_display_ready):
    try:
        return run_service_wizard(
            client,
            display=display,
            cancel_event=cancel_event,
            on_display_ready=on_display_ready,
        )
    except service_onboarding.ServiceOnboardingError as error:
        response = error.response if isinstance(error.response, Mapping) else {}
        response_error = (
            response.get("error")
            if isinstance(response.get("error"), Mapping)
            else {}
        )
        raise sms_cli.CliError(
            response_error.get("message") or str(error),
            response_error.get("code") or error.code,
            outcome_unknown=bool(
                error.outcome_unknown or response_error.get("outcome_unknown")
            ),
            request_id=error.request_id or response.get("request_id"),
            remediation=error.remediation,
        ) from error


def _service_form_tools() -> List[Dict[str, Any]]:
    tools = [{
        "name": "open_sms_service_onboarding",
        "title": "确认国内短信服务",
        "description": "打开国内短信服务确认页，按当前状态完成服务开通或更新协议重新签署。",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        "_meta": SERVICE_TOOL_META,
    }]
    for name, title, read_only, destructive, idempotent in (
        (
            "ensure_sms_service_onboarding_visible",
            "确认国内短信服务页面可见",
            True,
            False,
            True,
        ),
        (
            "open_sms_service_onboarding_in_browser",
            "在浏览器继续国内短信服务确认",
            False,
            False,
            False,
        ),
        (
            "get_sms_service_onboarding_result",
            "查询国内短信服务确认结果",
            True,
            False,
            True,
        ),
        (
            "cancel_sms_service_onboarding",
            "关闭国内短信服务确认页",
            False,
            True,
            True,
        ),
    ):
        tools.append({
            "name": name,
            "title": title,
            "description": title + "；页面只处理国内短信商品及其当前协议。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "flowId": {"type": "string", "minLength": 1},
                },
                "required": ["flowId"],
                "additionalProperties": False,
            },
            "annotations": {
                "readOnlyHint": read_only,
                "destructiveHint": destructive,
                "idempotentHint": idempotent,
                "openWorldHint": False,
            },
        })
    return tools


def _run_batch_form(client: SmsApiClient, *, display, cancel_event, on_display_ready, **parameters):
    flags = {
        "subAccount": "--sub-account", "taskName": "--task-name",
        "signature": "--signature", "templateId": "--template-id",
        "content": "--content", "sendTime": "--send-time",
    }
    if set(parameters) - (set(flags) | {"scheduled", "checkTraffic"}):
        raise ValueError("Unknown batch form fields")
    argv = ["batch-wizard"]
    for name, flag in flags.items():
        if name in parameters:
            if not isinstance(parameters[name], str) or not parameters[name]:
                raise ValueError("Batch form fields must be non-empty strings")
            argv.append(flag + "=" + parameters[name])
    if "scheduled" in parameters:
        scheduled = parameters["scheduled"]
        if not isinstance(scheduled, bool):
            raise ValueError("scheduled must be boolean")
        argv.append("--scheduled" if scheduled else "--immediate")
    if "checkTraffic" in parameters:
        if type(parameters["checkTraffic"]) is not bool:
            raise ValueError("checkTraffic must be boolean")
        if parameters["checkTraffic"]:
            argv.append("--check-traffic")
    args = sms_cli.build_parser().parse_args(argv)
    draft = sms_cli.BatchFormDraft(client, args)
    return run_batch_wizard(draft, display=display, cancel_event=cancel_event, on_display_ready=on_display_ready)


def _batch_form_tools() -> List[Dict[str, Any]]:
    properties = {
        "subAccount": {"type": "string", "minLength": 1,
                       "description": "消息组 ID，取模板 SubAccounts；All 范围时从 GetSubAccountListForAgent 返回的 subAccountId 选择"},
        "taskName": {"type": "string", "minLength": 1},
        "signature": {"type": "string", "minLength": 1},
        "templateId": {"type": "string", "minLength": 1},
        "content": {"type": "string", "minLength": 1},
        "checkTraffic": {"type": "boolean", "description": "CSV 在本机渲染，实际正文提交接口检查引流资源；展示覆盖范围，查询结果不等于报备通过"},
        "scheduled": {"type": "boolean", "description": "会话中确认的发送方式：false 立即发送，true 定时发送"},
        "sendTime": {"type": "string", "description": "会话确认的定时时间，ISO 8601（Asia/Shanghai，+08:00）；定时发送时传入，页面可核对和修改"},
    }
    tools = [{
        "name": "open_batch_task_creation",
        "title": "打开群发任务私密表单",
        "description": "按已确认的模板、正文、消息组及发送时间预填群发表单，客户上传 CSV、核对并确认。定时发送传 scheduled=true 和 sendTime；TaskFields 声明正文时传 content。",
        "inputSchema": {
            "type": "object", "properties": properties,
            "required": ["subAccount", "taskName", "signature", "templateId"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
        "_meta": BATCH_TOOL_META,
    }]
    for name, title, description, read_only, destructive, idempotent in (
        ("ensure_batch_task_creation_visible", "确认群发表单可见", "确认页面就绪；未显示时按返回的展示入口继续。", True, False, True),
        ("open_batch_task_creation_in_browser", "在浏览器继续群发表单", "复用同一私密表单在系统浏览器展示。", False, False, False),
        ("get_batch_task_creation_result", "查询群发表单结果", "返回任务状态、人数和文件摘要。", True, False, True),
        ("cancel_batch_task_creation", "关闭群发表单", "关闭本机草稿；fallbackAllowed=true 时可转入文件回退，已有任务按 taskId 处理。", False, True, True),
    ):
        tools.append({
            "name": name, "title": title, "description": description,
            "inputSchema": {"type": "object", "properties": {"flowId": {"type": "string", "minLength": 1}}, "required": ["flowId"], "additionalProperties": False},
            "annotations": {"readOnlyHint": read_only, "destructiveHint": destructive, "idempotentHint": idempotent, "openWorldHint": False},
        })
    return tools


def _run_traffic_form(client: SmsApiClient, *, kind, params=None, display, cancel_event, on_display_ready):
    try:
        draft = TrafficDrivingDraft(client, kind, params)
    except TrafficDrivingError as exc:
        if exc.response is not None:
            sms_cli._require_query_success(exc.response)
        raise sms_cli.CliError(str(exc), "traffic_identity_invalid") from exc
    return run_traffic_wizard(draft, display=display, cancel_event=cancel_event, on_display_ready=on_display_ready)


def _traffic_form_tools() -> List[Dict[str, Any]]:
    tools = [{
        "name": "open_traffic_driving_creation", "title": "打开引流信息录入表单",
        "description": (
            "录入短信链接或号码。先核实链接备案，按 traffic-driving.md 预填已知字段；"
            "客户补缺并确认，所属人及证件只在私密页面填写。"
        ),
        "inputSchema": {
            "type": "object", "properties": {
                "kind": {"type": "string", "enum": ["link", "phone"]},
                "params": {
                    "type": "object",
                    "properties": {"data": {"type": "array", "items": {"type": "object"}, "minItems": 1}},
                    "required": ["data"], "additionalProperties": False,
                },
            }, "required": ["kind"], "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
        "_meta": TRAFFIC_TOOL_META,
    }]
    for name, title, read_only, destructive, idempotent in (
        ("ensure_traffic_driving_creation_visible", "确认引流录入表单可见", True, False, True),
        ("open_traffic_driving_creation_in_browser", "在浏览器继续引流录入", False, False, False),
        ("get_traffic_driving_creation_result", "查询引流录入结果", True, False, True),
        ("cancel_traffic_driving_creation", "关闭引流录入表单", False, True, True),
    ):
        tools.append({
            "name": name, "title": title,
            "description": title + "；只返回流程状态与资源 ID，材料不进入对话。关闭表单不删除已提交的资源。",
            "inputSchema": {"type": "object", "properties": {"flowId": {"type": "string", "minLength": 1}}, "required": ["flowId"], "additionalProperties": False},
            "annotations": {"readOnlyHint": read_only, "destructiveHint": destructive, "idempotentHint": idempotent, "openWorldHint": False},
        })
    return tools



def _run_signature_form(client: SmsApiClient, *, params=None, display, cancel_event, on_display_ready):
    try:
        draft = SignatureDraft(client, params)
    except ValueError as exc:
        raise sms_cli.CliError(str(exc), "argument_error") from exc
    return run_signature_wizard(draft, display=display, cancel_event=cancel_event, on_display_ready=on_display_ready)


def _signature_form_tools() -> List[Dict[str, Any]]:
    text = {"type": "string"}
    properties = {
        "content": text, "source": {"type": "integer", "enum": [1, 2, 3]},
        "purpose": {"type": "integer", "enum": [1, 2]},
        "signatureIdentificationID": {"type": "integer", "minimum": 1},
        "channelTypes": {
            "type": "array", "items": {"type": "string", "enum": list(CHANNEL_TYPES)},
            "minItems": len(CHANNEL_TYPES), "maxItems": len(CHANNEL_TYPES), "uniqueItems": True,
        },
        "subAccounts": {"type": "array", "items": {"type": "string", "minLength": 1},
                        "description": "消息组 ID 数组，空数组表示全部消息组范围"},
        "desc": text, "projectName": text,
        "appIcp": {"type": "object", "properties": {"appIcpFilling": text}, "additionalProperties": False},
        "trademark": {"type": "object", "properties": {
            "trademarkCn": text, "trademarkEn": text, "trademarkNumber": text,
        }, "additionalProperties": False},
    }
    tools = [{
        "name": "open_signature_application", "title": "打开预填签名申请表单",
        "description": "预填签名及消息组，发送类型固定全选验证码、通知、营销；客户核对资质、材料和范围后确认提交。",
        "inputSchema": {"type": "object", "properties": {
            "params": {"type": "object", "properties": properties, "additionalProperties": False},
        }, "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
        "_meta": SIGNATURE_TOOL_META,
    }]
    for name, title, read_only, destructive, idempotent in (
        ("ensure_signature_application_visible", "确认签名表单可见", True, False, True),
        ("open_signature_application_in_browser", "在浏览器继续签名申请", False, False, False),
        ("get_signature_application_result", "查询签名表单结果", True, False, True),
        ("cancel_signature_application", "关闭签名申请表单", False, True, True),
    ):
        tools.append({
            "name": name, "title": title,
            "description": title + "；返回流程状态和申请编号。",
            "inputSchema": {"type": "object", "properties": {"flowId": {"type": "string", "minLength": 1}},
                            "required": ["flowId"], "additionalProperties": False},
            "annotations": {"readOnlyHint": read_only, "destructiveHint": destructive,
                            "idempotentHint": idempotent, "openWorldHint": False},
        })
    return tools


class QualificationMcpServer:
    def __init__(
        self,
        flow_manager: Optional[QualificationFlowManager] = None,
        batch_flow_manager: Optional[PrivateFormFlowManager] = None,
        traffic_flow_manager: Optional[PrivateFormFlowManager] = None,
        service_flow_manager: Optional[PrivateFormFlowManager] = None,
        signature_flow_manager: Optional[PrivateFormFlowManager] = None,
    ) -> None:
        self._client_info: Optional[Mapping[str, Any]] = None
        self._flows = flow_manager or QualificationFlowManager()
        self._batch_flows = batch_flow_manager or PrivateFormFlowManager(
            SmsApiClient, runner=_run_batch_form, protocol=BATCH_FLOW_PROTOCOL
        )
        self._traffic_flows = traffic_flow_manager or PrivateFormFlowManager(
            SmsApiClient, runner=_run_traffic_form, protocol=TRAFFIC_FLOW_PROTOCOL
        )
        self._service_flows = service_flow_manager or PrivateFormFlowManager(
            SmsApiClient,
            runner=_run_service_form,
            protocol=SERVICE_FLOW_PROTOCOL,
        )
        self._signature_flows = signature_flow_manager or PrivateFormFlowManager(
            SmsApiClient, runner=_run_signature_form, protocol=SIGNATURE_FLOW_PROTOCOL,
        )

    @staticmethod
    def _tool_result(
        text: str,
        structured: Mapping[str, Any],
        *,
        private_meta: Optional[Mapping[str, Any]] = None,
        is_error: bool = False,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "content": [{"type": "text", "text": text}],
            "structuredContent": dict(structured),
        }
        if is_error:
            result["isError"] = True
        if private_meta is not None:
            result["_meta"] = dict(private_meta)
        return result

    @staticmethod
    def _tools() -> List[Dict[str, Any]]:
        return [
            {
                "name": "open_qualification_application",
                "title": "打开火山引擎短信资质表单",
                "description": "在私密表单中创建短信资质；敏感材料不会进入对话。",
                "inputSchema": {"type": "object", "properties": {}},
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": False,
                    "idempotentHint": False,
                    "openWorldHint": True,
                },
                "_meta": dict(APP_TOOL_META),
            },
            {
                "name": "ensure_qualification_application_visible",
                "title": "确认短信资质表单可见",
                "description": "等待并确认右侧面板是否已加载，不打开其他窗口。",
                "inputSchema": {
                    "type": "object",
                    "properties": {"flowId": {"type": "string"}},
                    "required": ["flowId"],
                    "additionalProperties": False,
                },
                "annotations": {
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "open_qualification_application_in_browser",
                "title": "在系统浏览器打开短信资质表单",
                "description": (
                    "仅当 MCP App 面板不可用且宿主没有本机 URL 内置预览能力时，"
                    "在系统默认浏览器打开同一个本机私密资质表单。"
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {"flowId": {"type": "string"}},
                    "required": ["flowId"],
                    "additionalProperties": False,
                },
                "annotations": {
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": False,
                    "openWorldHint": True,
                },
            },
            {
                "name": "get_qualification_application_result",
                "title": "查询资质表单结果",
                "description": "读取当前私密资质表单的安全状态。",
                "inputSchema": {
                    "type": "object",
                    "properties": {"flowId": {"type": "string"}},
                    "required": ["flowId"],
                    "additionalProperties": False,
                },
                "annotations": {
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "cancel_qualification_application",
                "title": "取消短信资质表单",
                "description": "关闭尚未提交的私密资质表单并清除本地草稿。",
                "inputSchema": {
                    "type": "object",
                    "properties": {"flowId": {"type": "string"}},
                    "required": ["flowId"],
                    "additionalProperties": False,
                },
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": True,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
            },
            {
                "name": "execute_sms_read_action",
                "title": "执行短信查询或预览",
                "description": (
                    "执行火山引擎短信只读查询、本地校验或不写入数据的操作预览。"
                    "查询传 action 和接口原生 params；本地预览等命令使用 argv。"
                    "argv 首个命令可取：" + "、".join(sorted(READ_ACTIONS)) + "。"
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": sorted(PUBLIC_QUERY_ACTIONS)},
                        "params": {"type": "object"},
                        "argv": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        }
                    },
                    "oneOf": [{"required": ["argv"]}, {"required": ["action", "params"]}],
                    "additionalProperties": False,
                },
                "annotations": {
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": True,
                },
            },
            {
                "name": "execute_sms_write_action",
                "title": "执行已授权的短信写操作",
                "description": (
                    "执行短信预览所需的名单上传、本机确认记录保存，或已按 Skill 完成预览和客户确认的"
                    "服务开通、协议签署、短信申请、发送或任务写操作。"
                    "argv 使用 Skill 中 sms_cli.py 命令之后的参数。"
                    "argv 首个命令可取：" + "、".join(sorted(WRITE_ACTIONS)) + "。"
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "argv": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        }
                    },
                    "required": ["argv"],
                    "additionalProperties": False,
                },
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": True,
                    "idempotentHint": False,
                    "openWorldHint": True,
                },
            },
        ] + _service_form_tools() + _batch_form_tools() + _traffic_form_tools() + _signature_form_tools()

    @staticmethod
    def _execute_sms_action(
        arguments: Mapping[str, Any],
        allowed_actions: set[str],
        request_context: RequestContext,
    ) -> Dict[str, Any]:
        if "action" in arguments or "params" in arguments:
            action, params = arguments.get("action"), arguments.get("params")
            if (
                "api-read" not in allowed_actions or "argv" in arguments
                or not isinstance(action, str) or action not in PUBLIC_QUERY_ACTIONS
                or not isinstance(params, dict)
            ):
                raise ValueError("provide a public query Action and its params object")
            argv = ["api-read", "--action", action, "--params", json.dumps(params, ensure_ascii=False, allow_nan=False)]
        else:
            argv = arguments.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(value, str) or not value for value in argv)
        ):
            raise ValueError("argv must be a non-empty string array")
        action = argv[0]
        if action not in allowed_actions:
            raise ValueError("action is not allowed by this tool")
        stdout = io.StringIO()
        stderr = io.StringIO()
        exit_code = sms_cli.main(
            argv, request_context=request_context, stdout=stdout, stderr=stderr,
        )
        payload = stdout.getvalue().strip() or stderr.getvalue().strip()
        try:
            result = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError("SMS action returned invalid JSON") from exc
        if not isinstance(result, Mapping):
            raise RuntimeError("SMS action returned a non-object result")
        return QualificationMcpServer._tool_result(
            payload,
            result,
            is_error=exit_code != 0,
        )

    def _call_tool(
        self, params: Mapping[str, Any], *, request_context: Optional[RequestContext] = None,
    ) -> Dict[str, Any]:
        name = str(params.get("name") or "")
        arguments = params.get("arguments")
        if not isinstance(arguments, Mapping):
            arguments = {}
        request_context = request_context or RequestContext.create("mcp", client_info=self._client_info)
        if name == "open_signature_application":
            if set(arguments) - {"params"}:
                raise ValueError("签名表单只接收 params 业务参数")
            validate_signature_prefill(arguments.get("params", {}))
            flow = self._signature_flows.start(arguments, request_context=request_context)
            if flow.finished_at is not None:
                return self._signature_form_result(self._signature_flows.result(flow.flow_id))
            return self._tool_result(
                "正在打开预填签名表单，请确认页面可见后由客户核对和补充材料。",
                {"flowId": flow.flow_id, **flow.request_context.diagnostics(),
                 "status": "waiting_for_display", "completed": False, "displayMode": "mcp-app"},
                private_meta={**SIGNATURE_TOOL_META, "signatureDisplay": {"flowId": flow.flow_id, "url": flow.url}},
            )
        signature_operations = {
            "ensure_signature_application_visible": self._signature_flows.ensure_visible,
            "open_signature_application_in_browser": self._signature_flows.open_in_browser,
            "get_signature_application_result": self._signature_flows.result,
            "cancel_signature_application": self._signature_flows.cancel,
        }
        if name in signature_operations:
            if set(arguments) != {"flowId"} or not isinstance(arguments["flowId"], str) or not arguments["flowId"]:
                raise ValueError("flowId is required")
            return self._signature_form_result(signature_operations[name](arguments["flowId"]))
        if name == "open_sms_service_onboarding":
            if arguments:
                raise ValueError("service onboarding does not accept arguments")
            flow = self._service_flows.start(
                request_context=request_context,
            )
            if flow.finished_at is not None:
                return self._service_form_result(
                    self._service_flows.result(flow.flow_id)
                )
            return self._tool_result(
                "正在打开国内短信服务确认页，请客户阅读并同意当前协议。",
                {
                    "flowId": flow.flow_id,
                    **flow.request_context.diagnostics(),
                    "status": "waiting_for_display",
                    "completed": False,
                    "displayMode": "mcp-app",
                },
                private_meta={
                    **SERVICE_TOOL_META,
                    "serviceDisplay": {
                        "flowId": flow.flow_id,
                        "url": flow.url,
                    },
                },
            )
        service_operations = {
            "ensure_sms_service_onboarding_visible":
                self._service_flows.ensure_visible,
            "open_sms_service_onboarding_in_browser":
                self._service_flows.open_in_browser,
            "get_sms_service_onboarding_result":
                self._service_flows.result,
            "cancel_sms_service_onboarding":
                self._service_flows.cancel,
        }
        if name in service_operations:
            flow_id = arguments.get("flowId")
            if not isinstance(flow_id, str) or not flow_id:
                raise ValueError("flowId is required")
            return self._service_form_result(
                service_operations[name](flow_id)
            )
        if name == "open_traffic_driving_creation":
            kind = arguments.get("kind")
            if set(arguments) - {"kind", "params"} or kind not in {"link", "phone"}:
                raise ValueError("kind must be link or phone")
            if "params" in arguments:
                validate_creation_body(kind, arguments["params"], prefill=True)
            flow = self._traffic_flows.start(arguments, request_context=request_context)
            if flow.finished_at is not None:
                return self._traffic_form_result(self._traffic_flows.result(flow.flow_id))
            return self._tool_result(
                "正在打开引流信息私密表单，请继续确认页面可见。",
                {"flowId": flow.flow_id, **flow.request_context.diagnostics(),
                 "status": "waiting_for_display", "completed": False, "displayMode": "mcp-app"},
                private_meta={**TRAFFIC_TOOL_META, "trafficDisplay": {"flowId": flow.flow_id, "url": flow.url}},
            )
        traffic_operations = {
            "ensure_traffic_driving_creation_visible": self._traffic_flows.ensure_visible,
            "open_traffic_driving_creation_in_browser": self._traffic_flows.open_in_browser,
            "get_traffic_driving_creation_result": self._traffic_flows.result,
            "cancel_traffic_driving_creation": self._traffic_flows.cancel,
        }
        if name in traffic_operations:
            flow_id = arguments.get("flowId")
            if not isinstance(flow_id, str) or not flow_id:
                raise ValueError("flowId is required")
            return self._traffic_form_result(traffic_operations[name](flow_id))
        if name == "open_batch_task_creation":
            try:
                flow = self._batch_flows.start(arguments, request_context=request_context)
            except sms_cli.CliError as error:
                result = {
                    **request_context.diagnostics(),
                    "status": "batch_creation_failed", "code": error.code,
                    "completed": True, "fallbackAllowed": False,
                    "outcomeUnknown": error.outcome_unknown,
                    "requestId": error.request_id, "logId": error.log_id,
                }
                if error.remediation is not None:
                    result["remediation"] = error.remediation
                return self._tool_result("群发表单准备失败：" + str(error), result, is_error=True)
            if flow.finished_at is not None:
                result = self._batch_flows.result(flow.flow_id)
                return self._batch_form_result(result)
            return self._tool_result(
                "正在打开群发任务私密表单，请确认页面可见后让客户在页面选择文件。",
                {"flowId": flow.flow_id, **flow.request_context.diagnostics(),
                 "status": "waiting_for_display", "completed": False, "displayMode": "mcp-app"},
                private_meta={**BATCH_TOOL_META, "batchDisplay": {"flowId": flow.flow_id, "url": flow.url}},
            )
        batch_operations = {
            "ensure_batch_task_creation_visible": self._batch_flows.ensure_visible,
            "open_batch_task_creation_in_browser": self._batch_flows.open_in_browser,
            "get_batch_task_creation_result": self._batch_flows.result,
            "cancel_batch_task_creation": self._batch_flows.cancel,
        }
        if name in batch_operations:
            flow_id = arguments.get("flowId")
            if not isinstance(flow_id, str) or not flow_id:
                raise ValueError("flowId is required")
            return self._batch_form_result(batch_operations[name](flow_id))
        if name == "open_qualification_application":
            flow = self._flows.start(request_context=request_context)
            return self._tool_result(
                f"正在打开私密资质表单（流程 ID：{flow.flow_id}）。"
                "请继续调用 ensure_qualification_application_visible 确认展示。",
                {
                    "flowId": flow.flow_id,
                    **flow.request_context.diagnostics(),
                    "status": "waiting_for_display",
                    "displayStatus": "waiting_for_host",
                    "displayMode": "mcp-app",
                },
                private_meta={
                    **APP_TOOL_META,
                    "qualificationDisplay": {
                        "flowId": flow.flow_id,
                        "url": flow.url,
                    }
                },
            )
        if name == "ensure_qualification_application_visible":
            flow_id = arguments.get("flowId")
            if not isinstance(flow_id, str) or not flow_id:
                raise ValueError("flowId is required")
            result = self._flows.ensure_visible(flow_id)
            if result.get("displayStatus") == "opened":
                text = "资质表单已在右侧面板加载，可以请客户继续填写。"
            elif result.get("displayStatus") == "not_opened":
                text = (
                    "右侧面板未加载。宿主有本机 URL 内置预览能力时，请先取消当前流程，"
                    "再使用 qualification-wizard --display host 在右侧预览；"
                    "宿主没有内置预览能力时，才调用 "
                    "open_qualification_application_in_browser。"
                )
            else:
                status = str(result.get("status") or "unknown")
                text = f"资质表单状态：{status}。"
            return self._tool_result(text, result)
        if name == "open_qualification_application_in_browser":
            flow_id = arguments.get("flowId")
            if not isinstance(flow_id, str) or not flow_id:
                raise ValueError("flowId is required")
            result = self._flows.open_in_browser(flow_id)
            if result.get("displayStatus") == "opened":
                text = "资质表单已在系统浏览器加载，可以请客户继续填写。"
            elif result.get("displayStatus") == "not_opened":
                text = "系统浏览器未能加载资质表单。"
            else:
                status = str(result.get("status") or "unknown")
                text = f"资质表单状态：{status}。"
            return self._tool_result(text, result)
        if name == "get_qualification_application_result":
            flow_id = arguments.get("flowId")
            if not isinstance(flow_id, str) or not flow_id:
                raise ValueError("flowId is required")
            result = self._flows.result(flow_id)
            display_status = result.get("displayStatus")
            if display_status == "opened":
                text = "资质表单已加载，正在等待客户填写。"
            elif display_status == "waiting_for_host":
                text = "资质表单尚未完成展示确认。"
            else:
                status = str(result.get("status") or "unknown")
                references = []
                if result.get("qualificationId"):
                    references.append(f"资质 ID：{result['qualificationId']}")
                if result.get("requestId"):
                    references.append(f"Request ID：{result['requestId']}")
                if result.get("logId"):
                    references.append(f"Log ID：{result['logId']}")
                text = "资质表单状态：{}{}。".format(
                    status,
                    "；" + "；".join(references) if references else "",
                )
            return self._tool_result(text, result)
        if name == "cancel_qualification_application":
            flow_id = arguments.get("flowId")
            if not isinstance(flow_id, str) or not flow_id:
                raise ValueError("flowId is required")
            result = self._flows.cancel(flow_id)
            return self._tool_result("资质表单正在关闭。", result)
        if name == "execute_sms_read_action":
            return self._execute_sms_action(arguments, READ_ACTIONS, request_context)
        if name == "execute_sms_write_action":
            return self._execute_sms_action(arguments, WRITE_ACTIONS, request_context)
        raise ValueError("unknown tool")

    @staticmethod
    def _signature_form_result(result: Mapping[str, Any]) -> Dict[str, Any]:
        if result.get("outcomeUnknown") is True:
            text = "签名申请结果待核实，请按请求编号查询原申请。"
        elif result.get("status") == "signature_submitted":
            text = "签名申请已提交，申请编号 {}；审核和报备进度请继续查询。".format(result.get("applyId"))
        elif result.get("displayStatus") == "not_opened":
            text = "签名表单未显示，请按返回的展示入口继续同一流程。"
        elif result.get("completed") is True:
            text = "签名表单已结束，请按返回状态继续处理。"
        else:
            text = "正在等待客户核对签名并按需补充材料。"
        return QualificationMcpServer._tool_result(
            text, result, is_error=result.get("outcomeUnknown") is True or result.get("status") == "signature_application_failed",
        )

    @staticmethod
    def _traffic_form_result(result: Mapping[str, Any]) -> Dict[str, Any]:
        if result.get("status") == "traffic_identity_changed":
            text = "登录身份已变化，本次录入没有发送。请确认所需账号后重新打开表单。"
        elif result.get("status") == "traffic_created":
            text = "引流信息已提交并回查，可使用资源 ID 申请模板，报备进度另行查询。"
        elif result.get("created") is True:
            text = "引流信息已提交，请按资源 ID 查询确认。"
        elif result.get("outcomeUnknown") is True:
            text = "录入结果待核实，请按原资源和请求编号查询。"
        elif result.get("displayStatus") == "not_opened":
            text = "引流表单未显示，请按返回的展示方式继续同一表单。"
        elif result.get("completed") is True:
            text = "引流录入表单已结束，请按返回状态继续处理。"
        else:
            text = "正在等待客户核对并完成引流信息录入。"
        return QualificationMcpServer._tool_result(text, result)

    @staticmethod
    def _service_form_result(result: Mapping[str, Any]) -> Dict[str, Any]:
        display = result.get("display")
        display = display if isinstance(display, Mapping) else {}
        if result.get("completed") is True and display.get("title"):
            text = str(display["title"])
            if display.get("description"):
                text += "：" + str(display["description"])
        elif result.get("outcomeUnknown") is True:
            text = "操作结果待核实，请查询服务状态。"
        elif result.get("displayStatus") == "opened":
            text = "国内短信服务确认页已加载，正在等待客户阅读并同意协议。"
        elif result.get("displayStatus") == "not_opened":
            text = "国内短信服务确认页未显示，请按返回的展示方式继续同一流程。"
        elif result.get("completed") is True:
            text = "国内短信服务确认页已结束，请按返回状态继续处理。"
        else:
            text = "正在等待客户完成国内短信服务确认。"
        return QualificationMcpServer._tool_result(text, result)

    @staticmethod
    def _batch_form_result(result: Mapping[str, Any]) -> Dict[str, Any]:
        if result.get("status") == "batch_task_confirmed":
            text = "群发任务 {} 已创建并确认启动；短信服务校验通过的号码数 {}，重复号码数 {}。".format(
                result.get("taskId"), result.get("totalCount"), result.get("dupCount"),
            )
        elif result.get("outcomeUnknown") is True:
            text = "群发任务结果待核实，请按原任务 ID 查询。"
        elif result.get("displayStatus") == "opened":
            text = "群发表单已加载，请客户在页面选择名单、核对已预填的发送时间并最终确认。"
        elif result.get("displayStatus") == "not_opened":
            text = "群发表单未显示，请按返回的网页入口继续；展示不可用时按 Skill 回退。"
        elif result.get("fallbackAllowed") is True:
            text = "原表单已关闭，可按文件回退流程继续，名单由脚本读取本机文件。"
        elif result.get("completed") is True:
            text = "群发表单已结束，请按返回的任务编号和状态继续处理。"
        else:
            text = "正在等待客户完成群发表单。"
        return QualificationMcpServer._tool_result(text, result)

    def handle(self, message: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        request_id = message.get("id")
        request_context = None
        method = message.get("method")
        if not isinstance(method, str):
            return self._error(request_id, -32600, "Invalid Request")
        if method.startswith("notifications/"):
            return None
        try:
            if method == "initialize":
                params = message.get("params")
                client_info = params.get("clientInfo") if isinstance(params, Mapping) else None
                self._client_info = (
                    {"name": client_info.get("name"), "version": client_info.get("version")}
                    if isinstance(client_info, Mapping) else None
                )
                protocol_version = (
                    params.get("protocolVersion")
                    if isinstance(params, Mapping)
                    else "2025-06-18"
                )
                capabilities = (
                    params.get("capabilities")
                    if isinstance(params, Mapping)
                    else None
                )
                extensions = (
                    capabilities.get("extensions")
                    if isinstance(capabilities, Mapping)
                    else None
                )
                _emit_mcp_app_event(
                    "initialize",
                    protocolVersion=protocol_version,
                    uiCapability=bool(
                        isinstance(extensions, Mapping)
                        and "io.modelcontextprotocol/ui" in extensions
                    ),
                )
                result = {
                    "protocolVersion": protocol_version,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"listChanged": False},
                        "extensions": {
                            "io.modelcontextprotocol/ui": {
                                "mimeTypes": [APP_MIME_TYPE],
                            }
                        },
                    },
                    "serverInfo": {
                        "name": "volcengine-sms-qualification",
                        "version": APP_VERSION,
                    },
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                _emit_mcp_app_event("tools_list")
                result = {"tools": self._tools()}
            elif method == "tools/call":
                params = message.get("params")
                if not isinstance(params, Mapping):
                    raise ValueError("params are required")
                request_context = RequestContext.create("mcp", client_info=self._client_info)
                result = self._call_tool(params, request_context=request_context)
            elif method == "resources/list":
                result = {
                    "resources": [
                        {"uri": uri, "name": name, "mimeType": APP_MIME_TYPE}
                        for uri, (name, document) in FORM_RESOURCES.items()
                    ]
                }
            elif method == "resources/templates/list":
                result = {"resourceTemplates": []}
            elif method == "resources/read":
                params = message.get("params")
                uri = params.get("uri") if isinstance(params, Mapping) else None
                _emit_mcp_app_event(
                    "resource_read",
                    matched=uri in FORM_RESOURCES,
                )
                if uri not in FORM_RESOURCES:
                    return self._error(request_id, -32002, "Resource not found")
                result = {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": APP_MIME_TYPE,
                            "text": FORM_RESOURCES[uri][1],
                            "_meta": {
                                "ui": {
                                    "csp": {
                                        "frameDomains": ["http://127.0.0.1:*"]
                                    }
                                }
                            },
                        }
                    ]
                }
            else:
                return self._error(request_id, -32601, "Method not found")
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except sms_cli.CliError as error:
            return {"jsonrpc": "2.0", "id": request_id, "result": self._tool_result(
                str(error), {
                    **(request_context.diagnostics() if request_context is not None else {}),
                    "code": error.code, "requestId": error.request_id, "logId": error.log_id,
                    "outcomeUnknown": error.outcome_unknown, "completed": True,
                }, is_error=True,
            )}
        except (ValueError, queue.Empty):
            return self._error(request_id, -32602, "Invalid params")
        except QualificationUploadError as error:
            return self._error(request_id, -32000, str(error))
        except Exception:
            return self._error(request_id, -32603, "Internal error")

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    def run(self, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
        try:
            for line in stdin:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    response = self._error(None, -32700, "Parse error")
                else:
                    response = (
                        self.handle(message)
                        if isinstance(message, Mapping)
                        else self._error(None, -32600, "Invalid Request")
                    )
                if response is None:
                    continue
                stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
                stdout.flush()
        finally:
            self._flows.cancel_all()
            self._batch_flows.cancel_all()
            self._traffic_flows.cancel_all()
            self._signature_flows.cancel_all()


def main() -> int:
    QualificationMcpServer().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
