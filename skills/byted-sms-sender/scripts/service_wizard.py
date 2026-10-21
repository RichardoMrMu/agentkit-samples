# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# Licensed under the Apache License, Version 2.0 (the "License").
"""国内短信服务开通的本机确认页面。"""

from __future__ import annotations

import secrets
import threading
import time
from http import HTTPStatus
from pathlib import Path
from typing import Callable, Mapping, Optional
from urllib.parse import urlsplit

from api_client import SmsApiClient, sanitize_output
from local_form import LocalFormHandler, LocalFormLifecycle, serve_local_form
from qualification_display import browser_display_adapter
from qualification_upload import QualificationUploadError
import service_onboarding


ASSETS = Path(__file__).resolve().parents[1] / "assets"
VENDOR = ASSETS / "qualification_wizard_vendor"
CONTEXT_HEADER = "X-Service-Onboarding-Context"
STATIC_ASSETS = {
    "/service-static/react.production.min.js": VENDOR / "react.production.min.js",
    "/service-static/react-dom.production.min.js": VENDOR / "react-dom.production.min.js",
    "/service-static/arco.min.js": VENDOR / "arco.min.js",
    "/service-static/arco.min.css": VENDOR / "arco.min.css",
    "/service-static/arco-icon.min.js": VENDOR / "arco-icon.min.js",
    "/service-static/service_wizard.css": ASSETS / "service_wizard.css",
    "/service-static/service_wizard.js": ASSETS / "service_wizard.js",
}
DISCONNECT_TIMEOUT_SECONDS = 5 * 60
DETACH_GRACE_SECONDS = 60
IDLE_TIMEOUT_SECONDS = 30 * 60


def _page_presentation(status: str) -> dict:
    if status == "protocol_confirmation_required":
        return {
            "title": "重新签署短信服务协议",
            "subtitle": "短信服务协议已更新，请阅读并重新签署后继续使用国内短信服务。",
            "sectionTitle": "更新协议",
            "hint": "请逐项阅读以下更新后的协议。",
            "consentText": "我已阅读并同意以上更新协议。",
            "submitLabel": "同意并重新签署",
            "cancelTitle": "已取消重新签署",
            "cancelDescription": "本次未重新签署更新后的短信服务协议。",
            "successTitle": "短信服务协议已重新签署",
            "successDescription": "更新后的协议已生效，可继续使用国内短信服务。",
            "failureTitle": "短信服务协议重新签署未完成",
        }
    return {
        "title": "开通国内短信服务",
        "subtitle": "阅读并同意相关协议后开通服务。",
        "sectionTitle": "服务协议",
        "hint": "请逐项阅读当前版本的协议。",
        "consentText": "我已阅读并同意以上全部协议，并确认开通国内短信服务。",
        "submitLabel": "同意协议并开通",
        "cancelTitle": "已取消开通",
        "cancelDescription": "本次未提交国内短信服务开通请求。",
        "successTitle": "国内短信服务已开通",
        "successDescription": "服务与相关协议已经生效。",
        "failureTitle": "国内短信服务开通未完成",
    }


def _public_failure(
    error: service_onboarding.ServiceOnboardingError,
    client: SmsApiClient,
    presentation: Mapping,
) -> dict:
    response = error.response if isinstance(error.response, Mapping) else {}
    response_error = response.get("error") if isinstance(response.get("error"), Mapping) else {}
    message = response_error.get("message") or str(error)
    request_id = error.request_id or response.get("request_id")
    outcome_unknown = bool(
        error.outcome_unknown or response_error.get("outcome_unknown")
    )
    return {
        "status": "service_onboarding_failed",
        "code": response_error.get("code") or error.code,
        "message": sanitize_output(message, secrets=tuple(client.output_secrets)),
        "requestId": request_id,
        "outcomeUnknown": outcome_unknown,
        "display": {
            "status": "warning" if outcome_unknown else "error",
            "title": (
                "操作结果待核实"
                if outcome_unknown
                else presentation["failureTitle"]
            ),
            "description": sanitize_output(
                message,
                secrets=tuple(client.output_secrets),
            ),
        },
    }


def _public_success(result: Mapping, presentation: Mapping) -> dict:
    available = result.get("available") is True
    return {
        "status": (
            "service_onboarding_completed"
            if available
            else str(result.get("status") or "service_onboarding_submitted")
        ),
        "available": available,
        "verified": result.get("verified") is True,
        "requestId": result.get("requestId"),
        "outcomeUnknown": False,
        "display": {
            "status": "success",
            "title": presentation["successTitle"] if available else "请求已提交",
            "description": (
                presentation["successDescription"]
                if available
                else "系统正在处理，请稍后查询服务状态。"
            ),
        },
    }


def run_service_wizard(
    client: SmsApiClient,
    *,
    display=None,
    cancel_event: Optional[threading.Event] = None,
    on_display_ready: Optional[Callable[[], None]] = None,
    disconnect_timeout_seconds: float = DISCONNECT_TIMEOUT_SECONDS,
    detach_grace_seconds: float = DETACH_GRACE_SECONDS,
    idle_timeout_seconds: float = IDLE_TIMEOUT_SECONDS,
) -> dict:
    """打开国内短信服务确认页，并在客户确认后执行现有开通契约。"""
    display = display or browser_display_adapter()
    cancel_event = cancel_event or threading.Event()
    lifecycle = LocalFormLifecycle(
        disconnect_timeout_seconds,
        detach_grace_seconds,
        idle_timeout_seconds,
    )
    preview = service_onboarding.preview(client)
    if not preview.get("can_submit"):
        raise service_onboarding.ServiceOnboardingError(
            "当前账号不能提交国内短信服务开通",
            str(preview.get("status") or "service_state_unknown"),
            outcome_unknown=preview.get("status") == "submission_outcome_unknown",
        )

    token = secrets.token_urlsafe(24)
    ready = False
    result = None
    submitted = False
    document = (ASSETS / "service_wizard.html").read_text(encoding="utf-8")
    presentation = _page_presentation(str(preview["status"]))
    state = {
        "presentation": {
            key: presentation[key]
            for key in (
                "title",
                "subtitle",
                "sectionTitle",
                "hint",
                "consentText",
                "submitLabel",
            )
        },
        "product": {
            "name": preview["product"].get("productName") or "国内短信",
            "description": "支持向中国大陆号码发送验证码短信、通知短信、推广/营销短信。",
        },
        "protocols": [
            {"title": item["title"], "url": item["url"]}
            for item in preview["protocols"]
        ],
    }

    class Handler(LocalFormHandler):
        context_header = CONTEXT_HEADER
        context_token = token
        static_assets = STATIC_ASSETS
        allows_embedding = display.allows_embedding

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/":
                self._html(document)
            elif self._asset(path):
                return
            elif not self._authorized():
                self.send_error(HTTPStatus.NOT_FOUND)
            elif path == "/api/state":
                lifecycle.touch(active=True)
                self._json(HTTPStatus.OK, {"success": True, "state": state})
            else:
                self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            nonlocal ready, result, submitted
            if not self._authorized():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                path = urlsplit(self.path).path
                value = self._document()
                if path == "/api/heartbeat":
                    lifecycle.touch(active=value.get("active") is True)
                    if not ready:
                        ready = True
                        if on_display_ready is not None:
                            on_display_ready()
                    self._json(HTTPStatus.OK, {"success": True})
                elif path == "/api/detach":
                    lifecycle.detach()
                    self._json(HTTPStatus.OK, {"success": True})
                elif path == "/api/submit":
                    if value != {"confirmed": True}:
                        raise ValueError("请先阅读并同意全部协议")
                    if submitted:
                        raise ValueError("请求已经提交，请勿重复操作")
                    submitted = True
                    try:
                        submitted_result = service_onboarding.submit(
                            client,
                            preview["digest"],
                            preview["digest"],
                        )
                    except service_onboarding.ServiceOnboardingError as error:
                        result = _public_failure(error, client, presentation)
                        self._json(
                            HTTPStatus.CONFLICT,
                            {
                                "success": False,
                                "message": result["message"],
                                "result": result,
                            },
                        )
                    else:
                        result = _public_success(submitted_result, presentation)
                        self._json(
                            HTTPStatus.OK,
                            {"success": True, "result": result},
                        )
                elif path == "/api/abandon":
                    result = {
                        "status": "service_onboarding_abandoned",
                        "available": False,
                        "outcomeUnknown": False,
                        "display": {
                            "status": "info",
                            "title": presentation["cancelTitle"],
                            "description": presentation["cancelDescription"],
                        },
                    }
                    self._json(
                        HTTPStatus.OK,
                        {"success": True, "result": result},
                    )
                else:
                    self.send_error(HTTPStatus.NOT_FOUND)
            except ValueError as error:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"success": False, "message": str(error)},
                )
            except Exception:
                result = {
                    "status": "service_onboarding_failed",
                    "code": "internal_error",
                    "available": False,
                    "outcomeUnknown": submitted,
                    "display": {
                        "status": "warning" if submitted else "error",
                        "title": (
                            "操作结果待核实"
                            if submitted
                            else presentation["failureTitle"]
                        ),
                        "description": (
                            "请先查询服务状态，不要重复提交。"
                            if submitted
                            else "本次请求未能完成，请关闭页面后重试。"
                        ),
                    },
                }
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "success": False,
                        "message": result["display"]["description"],
                        "result": result,
                    },
                )

    def completed() -> bool:
        return result is not None

    try:
        reason = serve_local_form(
            Handler,
            display,
            token,
            lifecycle,
            completed=completed,
            cancel_event=cancel_event,
        )
    except QualificationUploadError:
        # 浏览器未启动时本机服务已关闭，未收到页面确认，也没有执行开通或协议签署。
        reason = "not_displayed"
    if result is not None:
        return result
    return {
        "status": "service_onboarding_closed",
        "reason": reason or "closed",
        "available": False,
        "outcomeUnknown": submitted,
    }
