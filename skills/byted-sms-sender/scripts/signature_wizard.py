# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# Licensed under the Apache License, Version 2.0 (the "License").
"""签名申请复用已有本机表单传输和展示生命周期。"""

from __future__ import annotations

import secrets
import threading
import time
from http import HTTPStatus
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import unquote, urlsplit

from local_form import LocalFormHandler, LocalFormLifecycle, serve_local_form
from qualification_display import browser_display_adapter
from qualification_upload import QualificationUploadError
from signature_application import SignatureApplicationError, SignatureDraft, signature_event


ASSETS = Path(__file__).resolve().parents[1] / "assets"
VENDOR = ASSETS / "qualification_wizard_vendor"
CONTEXT_HEADER = "X-Signature-Context"
STATIC_ASSETS = {
    "/signature-static/react.production.min.js": VENDOR / "react.production.min.js",
    "/signature-static/react-dom.production.min.js": VENDOR / "react-dom.production.min.js",
    "/signature-static/arco.min.js": VENDOR / "arco.min.js",
    "/signature-static/arco.min.css": VENDOR / "arco.min.css",
    "/signature-static/signature_wizard.css": ASSETS / "signature_wizard.css",
    "/signature-static/signature_wizard.js": ASSETS / "signature_wizard.js",
}


def run_signature_wizard(
    draft: SignatureDraft, *, display=None,
    cancel_event: Optional[threading.Event] = None,
    on_display_ready: Optional[Callable[[], None]] = None,
    render_timeout_seconds: float = 5 * 60,
    disconnect_timeout_seconds: float = 5 * 60,
    detach_grace_seconds: float = 60,
    idle_timeout_seconds: float = 30 * 60,
) -> dict:
    """run_signature_wizard 只在当前页面确认后提交，关闭时清理本机材料。"""
    display = display or browser_display_adapter()
    cancel_event = cancel_event or threading.Event()
    lifecycle = LocalFormLifecycle(disconnect_timeout_seconds, detach_grace_seconds, idle_timeout_seconds)
    if render_timeout_seconds <= 0:
        raise ValueError("Render timeout must be positive")
    token = secrets.token_urlsafe(24)
    ready = False
    result = None
    started = time.monotonic()
    document = (ASSETS / "signature_wizard.html").read_text(encoding="utf-8")
    signature_event(draft.client, "form", "start")

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
                self._json(HTTPStatus.OK, {"success": True, "state": draft.metadata})
            else:
                self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            nonlocal ready, result
            if not self._authorized():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            path = urlsplit(self.path).path
            try:
                lifecycle.touch(active=True)
                if path == "/api/file":
                    content_length = int(self.headers.get("Content-Length", "0"))
                    file = draft.add_file(
                        self.headers.get("X-Material-Kind", ""),
                        unquote(self.headers.get("X-File-Name", "")),
                        self._body(max(1, content_length)),
                    )
                    self._json(HTTPStatus.OK, {"success": True, "file": file})
                    return
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
                elif path == "/api/file/remove":
                    draft.remove_file(value.get("id"))
                    self._json(HTTPStatus.OK, {"success": True})
                elif path == "/api/preview":
                    self._json(HTTPStatus.OK, {"success": True, "preview": draft.preview(value)})
                elif path == "/api/create":
                    result = draft.create(value.get("revision"), value.get("confirmed"), value.get("acceptRisk", False))
                    self._json(HTTPStatus.OK, {"success": True, "result": result})
                elif path == "/api/abandon":
                    result = draft.close("abandoned")
                    self._json(HTTPStatus.OK, {"success": True, "result": result})
                else:
                    self.send_error(HTTPStatus.NOT_FOUND)
            except (ValueError, SignatureApplicationError) as exc:
                signature_event(draft.client, path, "path", result_class="rejected",
                                error_code=getattr(exc, "code", "argument_error"))
                self._json(HTTPStatus.BAD_REQUEST, {
                    "success": False, "message": str(exc), "code": getattr(exc, "code", "argument_error"),
                    "requestId": getattr(exc, "request_id", None),
                    "state": getattr(exc, "state", None),
                })
            except Exception:
                result = draft.close("error")
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                    "success": False, "message": "本次表单已结束，请按返回状态核对申请结果", "result": result,
                })

    def completed() -> bool:
        nonlocal result
        if result is None and not ready and time.monotonic() - started >= render_timeout_seconds:
            result = draft.close("not_displayed")
        return result is not None

    try:
        try:
            reason = serve_local_form(Handler, display, token, lifecycle, completed=completed, cancel_event=cancel_event)
        except QualificationUploadError:
            result = draft.close("not_displayed")
        else:
            if reason is not None:
                result = draft.close(reason)
    finally:
        final_state = draft.close("closed")
        signature_event(draft.client, "form", "end", duration_ms=int((time.monotonic() - started) * 1000),
                        result_class=final_state["status"])
    return result if result is not None else final_state
