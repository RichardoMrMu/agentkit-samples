# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# Licensed under the Apache License, Version 2.0 (the "License").
"""国内短信开通的确认、一次提交和状态核实；协议来自安装包的环境配置。"""

from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from typing import Mapping

import runtime_environment


PRODUCT = "sms"
READ_ACTION = "ListAllSmsProduct"
PROTOCOL_ACTION = "ConfirmProtocolDocument"
ACTIVATION_ACTION = "ActivateSmsProductInOneStep"
VERIFICATION_DELAYS = (0, 1, 2)


class ServiceOnboardingError(ValueError):
    def __init__(self, message, code="service_state_unknown", *, response=None,
                 outcome_unknown=False, request_id=None, remediation=None):
        super().__init__(message)
        self.code = code
        self.response = response
        self.outcome_unknown = outcome_unknown
        self.request_id = request_id
        self.remediation = remediation


def _catalog():
    catalog = runtime_environment.CONFIG.get("sms_onboarding")
    if not isinstance(catalog, Mapping) or not catalog.get("protocols") or not catalog.get("pricing_url"):
        raise ServiceOnboardingError("安装包缺少当前环境的开通协议，请使用官方控制台", "service_catalog_missing")
    return catalog


def _snapshot(client, *, scope=None):
    response = client.call(READ_ACTION, {}, use_cli=False, expected_credential_scope=scope)
    if response.get("success") is not True:
        raise ServiceOnboardingError("账号状态查询失败", response=response)
    account = response.get("result")
    if not isinstance(account, Mapping) or type(account.get("isNewAccount")) is not bool:
        raise ServiceOnboardingError("账号状态字段不完整，请到控制台核对")
    products = account.get("list")
    products = [item for item in products if isinstance(item, Mapping) and item.get("product") == PRODUCT] if isinstance(products, list) else []
    if len(products) != 1:
        raise ServiceOnboardingError("国内短信商品缺失或不唯一，请到控制台核对")
    product = products[0]
    if (type(account.get("status")) is not int or type(product.get("status")) is not int
            or type(product.get("userStatus")) is not int or type(product.get("isAllowed")) is not bool):
        raise ServiceOnboardingError("账号或商品状态字段不完整，请到控制台核对")
    documents = account.get("documents")
    if not isinstance(documents, list) or any(type(item) is not int for item in documents):
        raise ServiceOnboardingError("协议状态字段不完整，请到控制台核对")
    catalog = _catalog()
    protocols = catalog["protocols"]
    pending = [item for item in protocols if item["document_id"] in documents]
    known_documents = {item["document_id"] for item in protocols} | set(catalog["other_product_protocol_ids"])
    unrecognized = sorted(set(documents) - known_documents)
    account_fields = ("businessName", "userType", "businessType", "isNewAccount", "status")
    product_fields = ("product", "productName", "isAllowed", "status", "userStatus")
    view = {
        "environment": runtime_environment.NAME,
        "console_url": runtime_environment.CONSOLE_URL,
        "account": {key: account.get(key) for key in account_fields},
        "product": {key: product.get(key) for key in product_fields},
        "protocols": pending if product["status"] == 2 else protocols,
        "unrecognized_documents": unrecognized,
        "billing": {"url": catalog["pricing_url"], "summary": catalog["billing_summary"]},
    }
    # 新账号默认值为 0；存量账号和已开通商品必须有明确的启用状态。
    if not product["isAllowed"]:
        state = "product_not_allowed"
    elif product["userStatus"] == 2:
        state = "product_forced_closed"
    elif not account["isNewAccount"] and account["status"] != 1:
        state = "account_disabled" if account["status"] in (2, 3) else "service_state_unknown"
    elif product["userStatus"] not in (0, 1):
        state = "service_state_unknown"
    elif unrecognized:
        state = "protocol_state_unknown"
    elif product["status"] in (0, 5):
        state = "activation_required" if product["status"] == 0 else "reopening_required"
    elif product["status"] == 1:
        state = "activation_pending"
    elif product["status"] == 2 and product["userStatus"] == 1 and not account["isNewAccount"]:
        state = "protocol_confirmation_required" if pending else "available"
    else:
        state = {3: "arrears_closed", 4: "expired_closed"}.get(product["status"], "service_state_unknown")
    view.update({"status": state, "available": state == "available",
                 "can_submit": state in ("activation_required", "reopening_required", "protocol_confirmation_required")})
    if not client.credential_scope:
        raise ServiceOnboardingError("无法绑定当前登录账号", "confirmation_identity_unavailable")
    return view, client.credential_scope


def _reservation_path(scope):
    owner = hashlib.sha256(getpass.getuser().encode()).hexdigest()[:16]
    directory = Path(tempfile.gettempdir()) / f"volcengine-sms-{runtime_environment.NAME}-onboarding-{owner}"
    return directory / (scope + ".pending")


def _reserve(path):
    try:
        path.parent.mkdir(mode=0o700, exist_ok=True)
        info = path.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or (os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077)):
            raise OSError("confirmation directory is not private")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
    except FileExistsError as exc:
        raise ServiceOnboardingError("已有开通请求待核实，请查询状态或联系支持", "submission_outcome_unknown", outcome_unknown=True) from exc
    except OSError as exc:
        raise ServiceOnboardingError("本地提交记录不可用，已停止开通", "local_confirmation_unavailable") from exc


def status(client):
    view, scope = _snapshot(client)
    path = _reservation_path(scope)
    if view["available"]:
        # 已查明远端可用；本地残留记录清理失败不改变这个结果。
        try:
            path.unlink(missing_ok=True)
        except OSError:
            view["local_record_cleanup_pending"] = True
    elif view["can_submit"] and path.exists():
        view.update({"status": "submission_outcome_unknown", "can_submit": False})
    return view


def preview(client):
    view = status(client)
    if view["can_submit"]:
        value = {"scope": client.credential_scope, "preview": view}
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        view = {**view, "digest": hashlib.sha256(encoded).hexdigest()}
    return view


def _release_rejected(path, response):
    error = response.get("error") or {}
    # 只释放明确未送达或参数、权限、接口版本被拒绝的请求。系统错误可能已经写入。
    if error.get("request_sent") is False or error.get("code") in {
        "1001", "1023", "1024", "InvalidActionOrVersion", "InvalidAction", "InvalidVersion",
    }:
        path.unlink(missing_ok=True)


def _verify(client, scope, request_id):
    latest = None
    last_error = None
    for delay in VERIFICATION_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            latest, _ = _snapshot(client, scope=scope)
        except ServiceOnboardingError as exc:
            if exc.response and (exc.response.get("error") or {}).get("code") == "confirmation_identity_changed":
                raise ServiceOnboardingError("提交后登录身份已变化，请用原账号核对开通结果", "submission_outcome_unknown", outcome_unknown=True, request_id=request_id) from exc
            error = (exc.response or {}).get("error") or {}
            last_error = {"code": error.get("code", exc.code),
                          "message": error.get("message", str(exc)),
                          "requestId": (exc.response or {}).get("request_id")}
            continue
        last_error = None
        if latest["available"] or latest["status"] in ("product_forced_closed", "account_disabled", "arrears_closed", "expired_closed"):
            break
    return latest, last_error


def submit(client, preview_digest, authorization_digest):
    if not hmac.compare_digest(preview_digest, authorization_digest):
        raise ServiceOnboardingError("授权未绑定当前开通预览", "authorization_mismatch")
    current = preview(client)
    if current["available"]:
        return current
    if not current["can_submit"]:
        raise ServiceOnboardingError("当前状态不能提交开通，请核对返回状态", current["status"],
                                     outcome_unknown=current["status"] in ("submission_outcome_unknown", "activation_pending"))
    if not hmac.compare_digest(preview_digest, current["digest"]):
        raise ServiceOnboardingError("账号、商品或协议清单已变化，请重新预览", "digest_mismatch")
    scope = client.credential_scope
    path = _reservation_path(scope)
    _reserve(path)
    documents = [item["document_id"] for item in current["protocols"]]
    agreement = client.call(PROTOCOL_ACTION, {"product": PRODUCT, "documents": documents},
                            use_cli=False, expected_credential_scope=scope)
    if agreement.get("success") is not True:
        _release_rejected(path, agreement)
        raise ServiceOnboardingError("协议签署失败", response=agreement)
    signed = agreement.get("result") or {}
    if (not isinstance(signed.get("success"), list) or not isinstance(signed.get("fail"), list)
            or any(type(item) is not int for item in signed["success"] + signed["fail"])
            or set(signed["success"]) | set(signed["fail"]) != set(documents)
            or set(signed["success"]) & set(signed["fail"])):
        raise ServiceOnboardingError("协议签署响应不完整，开通尚未提交，请核对签署结果", "submission_outcome_unknown", outcome_unknown=True, request_id=agreement.get("request_id"))
    if signed["fail"]:
        path.unlink(missing_ok=True)
        raise ServiceOnboardingError("部分协议签署失败，开通尚未提交，请重新核对协议", "protocol_confirmation_failed", request_id=agreement.get("request_id"),
                                     remediation={"failed_protocols": [item for item in current["protocols"] if item["document_id"] in signed["fail"]]})
    order = None
    if current["status"] != "protocol_confirmation_required":
        order = client.call(ACTIVATION_ACTION, {"product": [PRODUCT]}, use_cli=False,
                            expected_credential_scope=scope)
        if order.get("success") is not True:
            _release_rejected(path, order)
            if (order.get("error") or {}).get("request_sent") is False:
                raise ServiceOnboardingError("开通尚未发送", response=order)
    latest, verification_error = _verify(client, scope, (order or agreement).get("request_id"))
    if latest and latest["available"]:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            latest["local_record_cleanup_pending"] = True
        return {**latest, "requestId": (order or agreement).get("request_id"), "verified": True}
    if order and order.get("success") is not True:
        raise ServiceOnboardingError("开通请求结果待核实", response=order)
    details = (order or {}).get("result", {}).get("list", [])
    sms_orders = [item for item in details if isinstance(item, Mapping) and item.get("product") == PRODUCT] if isinstance(details, list) else []
    if order and (len(sms_orders) != 1 or type(sms_orders[0].get("orderStatus")) is not int
                  or sms_orders[0]["orderStatus"] != 1):
        reason = sms_orders[0].get("orderDetail") if len(sms_orders) == 1 else "响应中缺少国内短信开通结果"
        raise ServiceOnboardingError(str(reason), "service_activation_failed", outcome_unknown=True, request_id=order.get("request_id"))
    pending = "activation_pending" if order else "protocol_confirmation_pending"
    if latest is None:
        pending = "submission_outcome_unknown"
    elif latest["status"] not in ("activation_required", "reopening_required", "protocol_confirmation_required"):
        pending = latest["status"]
    return {**(latest or current), "available": False, "can_submit": False,
            "status": pending,
            "requestId": (order or agreement).get("request_id"), "verified": False,
            "verification_error": verification_error}
