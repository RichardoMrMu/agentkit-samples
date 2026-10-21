# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
# Licensed under the Apache License, Version 2.0 (the "License").
"""Loopback UI for recording traffic links and private phone ownership data."""

from __future__ import annotations

import secrets
import threading
import time
from http import HTTPStatus
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

from local_form import LocalFormHandler, LocalFormLifecycle, serve_local_form
from qualification_display import browser_display_adapter
from qualification_upload import QualificationUploadError
from traffic_driving import TrafficDrivingDraft


ASSETS = Path(__file__).resolve().parents[1] / "assets"
VENDOR = ASSETS / "qualification_wizard_vendor"
CONTEXT_HEADER = "X-Traffic-Context"
STATIC_ASSETS = {
    "/traffic-static/react.production.min.js": VENDOR / "react.production.min.js",
    "/traffic-static/react-dom.production.min.js": VENDOR / "react-dom.production.min.js",
    "/traffic-static/arco.min.js": VENDOR / "arco.min.js",
    "/traffic-static/arco.min.css": VENDOR / "arco.min.css",
    "/traffic-static/traffic_wizard.css": ASSETS / "traffic_wizard.css",
    "/traffic-static/traffic_wizard.js": ASSETS / "traffic_wizard.js",
}


def run_traffic_wizard(
    draft: TrafficDrivingDraft, *, display=None,
    cancel_event: Optional[threading.Event] = None,
    on_display_ready: Optional[Callable[[], None]] = None,
    render_timeout_seconds: float = 5 * 60,
    disconnect_timeout_seconds: float = 5 * 60,
    detach_grace_seconds: float = 60,
    idle_timeout_seconds: float = 30 * 60,
) -> dict:
    display = display or browser_display_adapter()
    cancel_event = cancel_event or threading.Event()
    lifecycle = LocalFormLifecycle(disconnect_timeout_seconds, detach_grace_seconds, idle_timeout_seconds)
    if render_timeout_seconds <= 0:
        raise ValueError("Render timeout must be positive")
    token = secrets.token_urlsafe(24)
    ready = False
    result = None
    started = time.monotonic()
    document = (ASSETS / "traffic_wizard.html").read_text(encoding="utf-8")

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
                elif path == "/api/preview":
                    lifecycle.touch(active=True)
                    self._json(HTTPStatus.OK, {"success": True, "preview": draft.preview(value)})
                elif path == "/api/create":
                    result = draft.create(value.get("revision"), value.get("confirmed"))
                    self._json(HTTPStatus.OK, {"success": True, "result": result})
                elif path == "/api/abandon":
                    result = draft.close("abandoned")
                    self._json(HTTPStatus.OK, {"success": True, "result": result})
                else:
                    self.send_error(HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"success": False, "message": str(exc)})
            except Exception:
                result = draft.close("error")
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                    "success": False, "message": "本次表单已结束，请按返回状态核对录入结果", "result": result,
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
    return result if result is not None else final_state
