# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# Licensed under the Apache License, Version 2.0 (the "License").
"""签名领域拥有资质引用、名称风险、私密附件和一次提交的边界。"""

from __future__ import annotations

import copy
import json
import logging
import re
import sys
import time
import uuid
from typing import Any, Mapping, Optional

from action_contracts import ACTION_REGISTRY
from api_client import sanitize_output
from batch_confirmation import BatchConfirmationError, ConfirmationStore, canonical_digest, default_state_directory
from qualification_upload import QualificationUploadError, upload_material_file_bytes


CHANNEL_TYPES = {"CN_OTP": "验证码短信", "CN_NTC": "通知短信", "CN_MKT": "营销短信"}
SOURCES = {1: "公司全称／简称", 2: "App 全称", 3: "商标全称"}
MATERIALS = {
    "other": ("uploadFileList", 6),
    "app": ("appIcpFileList", 16),
    "trademark": ("trademarkFileList", 17),
}
MATERIAL_FILE_TYPES = {
    "other": {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "doc": "application/msword",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "zip": "application/zip",
    },
    "app": {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "pdf": "application/pdf",
        "zip": "application/zip",
    },
    "trademark": {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "pdf": "application/pdf",
        "zip": "application/zip",
    },
}
MAX_FILES = 5
PREFILL_FIELDS = frozenset({
    "content", "source", "purpose", "signatureIdentificationID", "subAccounts",
    "channelTypes", "desc", "projectName", "appIcp", "trademark",
    "from", "isLimitSubAccount",
})
_LOGGER = logging.getLogger("volcengine.sms.signature")
if not _LOGGER.handlers:
    _LOGGER.addHandler(logging.StreamHandler())
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False


class SignatureApplicationError(QualificationUploadError):
    """SignatureApplicationError 只交接公开错误及请求标识，不包含原始材料。"""

    def __init__(self, code: str, message: str, *, remediation=None, state=None, **diagnostics):
        super().__init__(code, message, **diagnostics)
        self.remediation = remediation
        self.state = state


def signature_event(client, phase: str, event: str, **fields) -> None:
    """signature_event 同时输出普通日志和 best-effort 结构化观测，不记录请求内容。"""
    context = getattr(client, "request_context", None)
    payload = {
        "event": event, "phase": phase,
        "operation_id": getattr(context, "operation_id", None), **fields,
    }
    try:
        _LOGGER.info("signature_application %s", json.dumps(payload, ensure_ascii=False))
        sys.stderr.write("SIGNATURE_OBSERVATION " + json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 观测失败不能改变申请或查询结果。


def signature_call(client, action: str, params: dict, *, scope: Optional[str] = None) -> dict:
    """signature_call 是签名领域物理调用的观测边界；不额外重试写请求。"""
    started = time.monotonic()
    signature_event(client, action, "start")
    try:
        options = {
            "use_cli": False,
            "http_attempt_observer": lambda event, fields: signature_event(
                client, "http_attempt", event, action=action, **fields,
            ),
        }
        if scope is not None:
            options["expected_credential_scope"] = scope
        response = client.call(action, params, **options)
    except Exception:
        signature_event(client, action, "end", duration_ms=int((time.monotonic() - started) * 1000),
                        result_class="unknown", error_code="transport_error")
        raise
    error = response.get("error") or {} if isinstance(response, Mapping) else {}
    signature_event(
        client, action, "end", duration_ms=int((time.monotonic() - started) * 1000),
        result_class="success" if isinstance(response, Mapping) and response.get("success") is True else "failed",
        error_code=error.get("code"), request_id=response.get("request_id") if isinstance(response, Mapping) else None,
        log_id=response.get("log_id") if isinstance(response, Mapping) else None,
    )
    return response


def _query_result(response: Any) -> Mapping:
    if not isinstance(response, Mapping) or response.get("success") is not True:
        error = response.get("error") or {} if isinstance(response, Mapping) else {}
        raise SignatureApplicationError(
            error.get("code") or "signature_query_failed", "签名准备查询未完成，请先核对账号和查询结果",
            request_id=response.get("request_id") if isinstance(response, Mapping) else None,
            log_id=response.get("log_id") if isinstance(response, Mapping) else None,
            remediation=error.get("remediation"),
            outcome_unknown=error.get("outcome_unknown") is True,
        )
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise SignatureApplicationError("invalid_response", "签名准备查询未返回完整结果")
    return result


def signature_scope(client) -> str:
    _query_result(signature_call(client, "ListAllSmsProduct", {}))
    scope = client.credential_scope
    if not scope:
        raise SignatureApplicationError("signature_identity_invalid", "无法确认当前登录身份")
    return scope


def _catalog(client, action: str, *, scope: str, filters: Optional[dict] = None) -> list:
    rows = []
    for page in range(1, 101):
        result = _query_result(signature_call(
            client, action, {"pageIndex": page, "pageSize": 100, **(filters or {})}, scope=scope,
        ))
        items, total = result.get("list"), result.get("total")
        if not isinstance(items, list) or type(total) is not int or total < 0:
            raise SignatureApplicationError("invalid_response", "资源列表或分页信息不完整")
        rows.extend(items)
        if len(rows) >= total:
            return rows
        if not items:
            break
    raise SignatureApplicationError("signature_catalog_incomplete", "资源列表未完整读取，请缩小查询范围")


def _qualification(row: Mapping) -> dict:
    if not isinstance(row, Mapping):
        raise SignatureApplicationError("invalid_response", "资质记录格式不完整")
    info = row.get("businessInfo")
    info = info if isinstance(info, Mapping) else {}
    name = info.get("businessCertificateName") or row.get("businessCertificateName")
    if (type(row.get("id")) is not int or row["id"] <= 0
            or row.get("auditStatus") != 3 or row.get("usable") is not True
            or type(row.get("purpose")) is not int or row["purpose"] not in (1, 2)
            or not isinstance(name, str) or not name):
        raise SignatureApplicationError("signature_qualification_unavailable", "所选资质不可用或企业信息不完整")
    return {"id": row["id"], "businessCertificateName": name, "purpose": row["purpose"]}


def _current_qualification(client, material_id: int, scope: str) -> dict:
    rows = _catalog(client, "GetSignatureIdentificationList", scope=scope, filters={"id": material_id})
    matches = [row for row in rows if isinstance(row, Mapping) and row.get("id") == material_id]
    if len(matches) != 1:
        raise SignatureApplicationError("signature_qualification_unavailable", "所选资质不存在或不属于当前账号")
    return _qualification(matches[0])


def _signature_channel_types(params: Mapping) -> list:
    """_signature_channel_types 统一表单与会话的固定类型，缺省补齐且不改写调用方。"""
    values = params.get("channelTypes", list(CHANNEL_TYPES))
    if (not isinstance(values, list) or len(values) != len(CHANNEL_TYPES)
            or any(not isinstance(item, str) for item in values)
            or set(values) != set(CHANNEL_TYPES)):
        raise ValueError("签名申请固定全选验证码、通知和营销三种类型")
    return list(values)


def validate_signature_prefill(params: Any) -> dict:
    """validate_signature_prefill 只允许公开业务参数；附件通过本机页面处理。"""
    if not isinstance(params, dict) or set(params) - PREFILL_FIELDS:
        raise ValueError("签名预填只支持业务信息，附件请在私密页面选择")
    body = copy.deepcopy(params)
    body["channelTypes"] = _signature_channel_types(body)
    for key in ("content", "desc", "projectName"):
        if key in body and not isinstance(body[key], str):
            raise ValueError("签名预填文本字段必须是字符串")
    for key, choices in (("source", SOURCES), ("purpose", {1, 2})):
        if key in body and (type(body[key]) is not int or body[key] not in choices):
            raise ValueError("签名来源或归属类型不正确")
    if "from" in body and body["from"] != "vconsole":
        raise ValueError("签名申请来源标记不正确")
    if "isLimitSubAccount" in body and (
        type(body["isLimitSubAccount"]) is not int or body["isLimitSubAccount"] not in (0, 1)
    ):
        raise ValueError("消息组范围标记不正确")
    if "signatureIdentificationID" in body and (
        type(body["signatureIdentificationID"]) is not int or body["signatureIdentificationID"] <= 0
    ):
        raise ValueError("资质 ID 必须为正整数")
    if "subAccounts" in body and (
        not isinstance(body["subAccounts"], list)
        or any(not isinstance(item, str) or not item for item in body["subAccounts"])
    ):
        raise ValueError("消息组需要使用字符串数组")
    for key, permitted in (
        ("appIcp", {"appIcpFilling"}),
        ("trademark", {"trademarkCn", "trademarkEn", "trademarkNumber"}),
    ):
        if key in body and (not isinstance(body[key], dict) or set(body[key]) - permitted):
            raise ValueError("签名预填不接收原始附件、图片地址或内部 URI")
        if key in body and any(not isinstance(item, str) for item in body[key].values()):
            raise ValueError("备案和商标编号必须是字符串")
    return body


def prepare_signature(client, params: Any, *, scope: str, qualification: Optional[dict] = None) -> tuple:
    """prepare_signature 从当前账号真实资质校验名称风险，不重新解释客户意图。"""
    if not isinstance(params, dict):
        raise ValueError("签名参数必须是对象")
    body = copy.deepcopy(params)
    try:
        body["channelTypes"] = _signature_channel_types(body)
    except ValueError:
        signature_event(client, "channel_types", "path", result_class="rejected",
                        error_code="signature_channel_types_invalid")
        raise
    if set(body) - (PREFILL_FIELDS | {"uploadFileList", "openId"}):
        raise ValueError("签名参数包含当前申请契约之外的字段，请使用原生字段名")
    groups = body.get("subAccounts")
    if not isinstance(groups, list) or any(not isinstance(item, str) or not item for item in groups):
        raise ValueError("请确认适用消息组；空数组表示全部范围")
    if len(set(groups)) != len(groups):
        raise ValueError("消息组不能重复")
    for key, permitted in (
        ("appIcp", {"appIcpFilling", "appIcpFileList"}),
        ("trademark", {"trademarkCn", "trademarkEn", "trademarkNumber", "trademarkFileList"}),
    ):
        if key in body and (not isinstance(body[key], dict) or set(body[key]) - permitted):
            raise ValueError("备案或商标信息字段不符合当前契约")
        if key in body and any(not isinstance(value, str) for name, value in body[key].items()
                               if name not in {"appIcpFileList", "trademarkFileList"}):
            raise ValueError("备案或商标信息文本字段须为字符串")
    for key in ("uploadFileList", "appIcpFileList", "trademarkFileList"):
        owner = body if key == "uploadFileList" else body.get("appIcp" if key == "appIcpFileList" else "trademark", {})
        if key not in owner:
            continue
        files = owner[key]
        if not isinstance(files, list) or len(files) > MAX_FILES:
            raise ValueError("材料列表需要为数组，最多五份")
        for file in files:
            if (not isinstance(file, dict) or set(file) - {"fileType", "fileSuffix", "fileContent", "fileUrl"}
                    or type(file.get("fileType")) is not int
                    or any(not isinstance(value, str) for name, value in file.items() if name != "fileType")
                    or not (file.get("fileContent") or file.get("fileUrl"))):
                raise ValueError("请使用原始文件或有效 URL，材料字段不符合当前契约")
    content, source, material_id = body.get("content"), body.get("source"), body.get("signatureIdentificationID")
    if not isinstance(content, str) or not 2 <= len(content) <= 25 or content != content.strip():
        raise ValueError("签名名称须为 2 至 25 个字符，首尾不能有空白，请填写名称本身")
    # 字符与长度沿用控制台 signatureRules，校验失败时保留输入，不自动去符号。
    if re.fullmatch(r"[\u4e00-\ufa29\ue7c7-\ue7f3a-zA-Z0-9()（）:：\-.'·+&\s]{2,25}", content) is None:
        raise ValueError("签名存在不支持的特殊符号")
    if re.search(r"[\u4e00-\ufa29\ue7c7-\ue7f3a-zA-Z0-9]", content) is None or content.isdecimal():
        raise ValueError("签名至少包含一个中文或英文字母，不能为纯数字或标点")
    if any(item in content for item in ("http:", "https:", "www.", ".com")):
        raise ValueError("签名不能包含网址")
    if re.search(r"1[3-9]\d{9}|0\d{2,3}-\d{5,8}", content):
        raise ValueError("签名不能包含联系方式")
    if type(source) is not int or source not in SOURCES:
        raise ValueError("请选择公司、App 或商标签名来源")
    if type(material_id) is not int or material_id <= 0:
        raise ValueError("请选择已有可用资质")
    if qualification is None:
        material = _current_qualification(client, material_id, scope)
        signature_event(client, "qualification", "path", result_class="queried")
    else:
        # 仅表单内使用已鉴权查询的快照，重复编辑不再次消耗查询额度；提交时仍重新查证。
        if qualification.get("id") != material_id:
            raise ValueError("所选资质与当前查询快照不一致")
        material = copy.deepcopy(qualification)
        signature_event(client, "qualification", "path", result_class="snapshot")
    if "purpose" in body and (type(body["purpose"]) is not int or body["purpose"] != material["purpose"]):
        raise ValueError("自用／他用需要与所选资质一致")
    body["purpose"] = material["purpose"]
    expected_limit = 1 if body["subAccounts"] else 0
    if "isLimitSubAccount" in body and body["isLimitSubAccount"] != expected_limit:
        raise ValueError("消息组范围与所选消息组不一致")
    body["isLimitSubAccount"] = expected_limit
    body["from"] = "vconsole"
    for key in ("desc", "projectName", "openId"):
        if key in body and not isinstance(body[key], str):
            raise ValueError("签名说明、场景和项目必须是字符串")
    if len(body.get("desc", "")) > 300:
        raise ValueError("申请说明最多 300 字")
    if source == 2:
        body.pop("trademark", None)
        app = body.get("appIcp", {})
        if not isinstance(app, dict) or not (app.get("appIcpFilling") or app.get("appIcpFileList")):
            raise ValueError("请补充 App 备案号或备案材料")
    if source == 3:
        body.pop("appIcp", None)
        mark = body.get("trademark", {})
        if not isinstance(mark, dict) or not isinstance(mark.get("trademarkNumber"), str) or not mark["trademarkNumber"]:
            raise ValueError("请填写商标注册号；证明文件可按需补充")
    if source == 1:
        body.pop("appIcp", None)
        body.pop("trademark", None)
    warnings = []
    if content not in material["businessCertificateName"]:
        warnings.append({
            "code": "signature_company_name_mismatch",
            "message": "当前签名未连续出现在营业执照企业名称中，可能存在审核或报备失败风险，建议修改",
        })
    signature_event(client, "preview", "path", result_class="warning" if warnings else "passed")
    return body, material, warnings


def signature_readback(client, body: dict, scope: str) -> bool:
    """signature_readback 的旁路失败不改变已经取得的申请编号。"""
    try:
        query = signature_call(client, "ListSignatureForAgent", {
            "Signature": body["content"], "SubAccounts": body["subAccounts"], "Page": 1, "PageSize": 100,
        }, scope=scope)
        return isinstance(query, Mapping) and query.get("success") is True
    except Exception:
        return False


def submit_signature(client, body: dict, scope: str) -> dict:
    """submit_signature 统一交接有效申请编号、未知结果和回查，保持成功的原生 result。"""
    try:
        response = signature_call(client, "ApplySmsSignatureV2", body, scope=scope)
    except Exception:
        response = {"success": False, "error": {"code": "transport_error", "outcome_unknown": True}}
    if not isinstance(response, Mapping):
        response = {"success": False, "error": {"code": "invalid_response", "outcome_unknown": True}}
    response = copy.deepcopy(response)
    result = response.get("result")
    error = response.get("error")
    error = dict(error) if isinstance(error, Mapping) else {}
    apply_id = result.get("applyId") if isinstance(result, Mapping) else None
    valid_id = (type(apply_id) is int and apply_id > 0
                or isinstance(apply_id, str) and apply_id.isascii() and apply_id.isdecimal() and int(apply_id) > 0)
    if response.get("success") is True and valid_id:
        response["signatureSubmission"] = {
            "status": "signature_submitted", "applyId": apply_id,
            "reviewStatus": result.get("status"), "requestId": response.get("request_id"),
            "logId": response.get("log_id"), "outcomeUnknown": False,
            "queryCompleted": signature_readback(client, body, scope),
        }
    elif (error.get("outcome_unknown") is True or response.get("success") is True
          or not error.get("code")
          or error.get("code") in ACTION_REGISTRY["ApplySmsSignatureV2"].uncertain_error_codes):
        code = error.get("code") or "invalid_response"
        response.update({"success": False, "result": None, "error": {
            **error, "code": code, "message": "签名申请结果未知，请先查询，不要重复提交",
            "outcome_unknown": True, "retryable": False,
        }, "signatureSubmission": {
            "status": "signature_submission_outcome_unknown", "outcomeUnknown": True,
            "requestId": response.get("request_id"), "logId": response.get("log_id"), "code": code,
        }})
    signature_event(client, "submit", "path", result_class=(
        response.get("signatureSubmission", {}).get("status") or "rejected"
    ), error_code=error.get("code"))
    return response


def _confirmation_error(exc: BatchConfirmationError) -> SignatureApplicationError:
    if exc.outcome_unknown:
        return SignatureApplicationError("signature_submission_outcome_unknown",
                                         "本次签名申请结果待核实，请先查询，不要重复提交", outcome_unknown=True)
    if exc.code == "preview_changed":
        return SignatureApplicationError("signature_preview_changed", "缺少本次签名确认记录，请重新预览")
    return SignatureApplicationError(exc.code, "本地确认记录不可用，已停止签名申请")


def _confirmation_store() -> ConfirmationStore:
    # 与群发目录同级，首次创建不会替群发建立权限不符的父目录。
    directory = default_state_directory()
    return ConfirmationStore(directory.with_name(directory.name + "-signature"))


def save_signature_preview(digest: str, scope: str) -> None:
    """save_signature_preview 只保存摘要；重复预览不重置已提交或未知的记录。"""
    store = _confirmation_store()
    try:
        try:
            store.read_preview(digest, scope, digest)
        except BatchConfirmationError as exc:
            if exc.code != "preview_changed":
                raise
            try:
                store.save_preview(digest, scope, digest, "")
            except BatchConfirmationError:
                # 另一个进程可能已保存同一摘要；仅已存在的匹配记录允许继续。
                store.read_preview(digest, scope, digest)
    except BatchConfirmationError as exc:
        raise _confirmation_error(exc) from exc


def signature_digest(body: dict, scope: str, material: dict) -> str:
    """signature_digest 统一页面与会话的账号、参数和企业绑定，避免切换入口重复申请。"""
    return canonical_digest({"action": "ApplySmsSignatureV2", "credentialScope": scope,
                             "params": body, "qualification": material})


def submit_confirmed_signature(client, body: dict, scope: str, digest: str) -> dict:
    """submit_confirmed_signature 在网络写入前原子占用确认，跨 CLI／MCP 进程复用终态。"""
    store = _confirmation_store()
    try:
        apply_id = store.reserve(digest, scope, digest)
    except BatchConfirmationError as exc:
        signature_event(client, "confirmation", "path", result_class="blocked", error_code=exc.code)
        raise _confirmation_error(exc) from exc
    if apply_id is not None:
        signature_event(client, "confirmation", "path", result_class="already_submitted")
        return {
            "success": True, "action": "ApplySmsSignatureV2", "result": {"applyId": apply_id}, "error": None,
            "signatureSubmission": {
                "status": "signature_submitted", "applyId": apply_id, "outcomeUnknown": False,
                "alreadySubmitted": True, "queryCompleted": signature_readback(client, body, scope),
            },
        }
    response = submit_signature(client, body, scope)
    summary = response.get("signatureSubmission")
    try:
        if summary is not None:
            store.finish(digest, str(summary["applyId"]) if not summary["outcomeUnknown"] else None)
        else:
            store.discard_rejected(digest)
    except BatchConfirmationError as exc:
        # 写入已经发生；本地 pending 仍阻止重复申请，返回已有云端结果。
        signature_event(client, "confirmation", "path", result_class="record_failed", error_code=exc.code)
    return response


def _file_content_type(name: str, data: bytes, kind: str) -> tuple:
    if kind not in MATERIALS or not isinstance(name, str) or not name or "." not in name:
        raise ValueError("请选择当前材料支持的文件类型")
    suffix = name.rsplit(".", 1)[1].lower()
    content_type = MATERIAL_FILE_TYPES[kind].get(suffix)
    if content_type is None or not data:
        raise ValueError("请选择当前材料支持的非空文件")
    if suffix == "png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("文件内容与所选格式不一致")
    if suffix in {"jpg", "jpeg"} and not data.startswith(b"\xff\xd8\xff"):
        raise ValueError("文件内容与所选格式不一致")
    if suffix == "gif" and not data.startswith((b"GIF87a", b"GIF89a")):
        raise ValueError("文件内容与所选格式不一致")
    if suffix in {"zip", "docx"} and not data.startswith(b"PK"):
        raise ValueError("文件内容与所选格式不一致")
    if suffix == "doc" and not data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise ValueError("文件内容与所选格式不一致")
    if suffix == "pdf" and not data.startswith(b"%PDF-"):
        raise ValueError("文件内容与所选格式不一致")
    return suffix, content_type


class SignatureDraft:
    """SignatureDraft 持有私密文件和已冻结的预览；业务提交只执行一次。"""

    def __init__(self, client, params: Optional[dict] = None):
        self.client = client
        self.initial = validate_signature_prefill({} if params is None else params)
        self.qualification_locked = "signatureIdentificationID" in self.initial
        self.scope = signature_scope(client)
        self.qualifications = []
        filters = {"status": [3]}
        if "signatureIdentificationID" in self.initial:
            filters["id"] = self.initial["signatureIdentificationID"]
        for row in _catalog(client, "GetSignatureIdentificationList", scope=self.scope, filters=filters):
            try:
                self.qualifications.append(_qualification(row))
            except SignatureApplicationError:
                signature_event(client, "qualification_catalog", "path", result_class="unavailable")
        if not self.qualifications:
            raise SignatureApplicationError("signature_qualification_unavailable", "还没有可用资质，请先办理企业资质")
        rows = _catalog(client, "GetSubAccountListForAgent", scope=self.scope)
        self.groups = [
            {"id": row["subAccountId"], "name": row.get("subAccountName") or row["subAccountId"]}
            for row in rows if isinstance(row, Mapping) and isinstance(row.get("subAccountId"), str)
            and row["subAccountId"]
        ]
        self.initial.setdefault("source", 1)
        if "signatureIdentificationID" not in self.initial and len(self.qualifications) == 1:
            self.initial["signatureIdentificationID"] = self.qualifications[0]["id"]
        selected = next((row for row in self.qualifications if row["id"] == self.initial.get("signatureIdentificationID")), None)
        if "signatureIdentificationID" in self.initial and selected is None:
            raise SignatureApplicationError("signature_qualification_unavailable", "预填资质不存在或当前不可用")
        if selected and "purpose" in self.initial and self.initial["purpose"] != selected["purpose"]:
            raise ValueError("预填归属类型与所选资质不一致")
        if selected and self.initial["source"] == 1 and "content" not in self.initial:
            self.initial["content"] = selected["businessCertificateName"]
        if "subAccounts" not in self.initial and len(self.groups) == 1:
            self.initial["subAccounts"] = [self.groups[0]["id"]]
        self.files = {}
        self.revision = 0
        self.pending = None
        self.submitted = False
        self.result = None
        self.closed = False

    @property
    def metadata(self) -> dict:
        return {
            "initialData": copy.deepcopy(self.initial), "qualifications": copy.deepcopy(self.qualifications),
            "qualificationLocked": self.qualification_locked,
            "messageGroups": copy.deepcopy(self.groups), "maxFiles": MAX_FILES,
        }

    def _editable(self) -> None:
        if self.closed or self.submitted or self.result is not None:
            raise ValueError("本次申请已结束，请先核对结果")

    def add_file(self, kind: str, name: str, data: bytes) -> dict:
        self._editable()
        if len(self.files) >= MAX_FILES:
            raise ValueError("最多上传五份补充材料")
        suffix, content_type = _file_content_type(name, data, kind)
        signature_event(self.client, "material_upload", "start", result_class="uploading")
        try:
            uploaded = upload_material_file_bytes(
                self.client,
                data,
                suffix,
                content_type,
            )
        except Exception as error:
            signature_event(
                self.client,
                "material_upload",
                "end",
                result_class="failed",
                error_code=getattr(error, "code", "material_upload_failed"),
            )
            raise
        signature_event(self.client, "material_upload", "end", result_class="success")
        file_id = uuid.uuid4().hex
        item = {
            "id": file_id,
            "kind": kind,
            "name": name,
            "size": len(data),
            "suffix": uploaded["fileSuffix"],
            "uri": uploaded["fileUri"],
        }
        self.files[file_id] = item
        self.pending = None
        signature_event(self.client, "material", "path", result_class="uploaded")
        return {key: value for key, value in item.items() if key != "uri"}

    def remove_file(self, file_id: str) -> None:
        self._editable()
        if not isinstance(file_id, str) or file_id not in self.files:
            raise ValueError("所选材料不存在")
        del self.files[file_id]
        self.pending = None
        signature_event(self.client, "material", "path", result_class="removed")

    def preview(self, value: Any) -> dict:
        self._editable()
        if not isinstance(value, dict) or set(value) != {"params", "fileIds"}:
            raise ValueError("请核对本次签名和所选材料")
        body = validate_signature_prefill(value["params"])
        ids = value["fileIds"]
        if (not isinstance(ids, list) or any(not isinstance(item, str) for item in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError("材料选择不完整或重复")
        selected = []
        for file_id in ids:
            if not isinstance(file_id, str) or file_id not in self.files:
                raise ValueError("材料已移除或不属于本次表单")
            file = self.files[file_id]
            if (file["kind"] == "app" and body.get("source") != 2
                    or file["kind"] == "trademark" and body.get("source") != 3):
                raise ValueError("材料类型与当前签名来源不一致")
            field, file_type = MATERIALS[file["kind"]]
            target = body if file["kind"] == "other" else body.setdefault("appIcp" if file["kind"] == "app" else "trademark", {})
            target.setdefault(field, []).append({
                "fileType": file_type, "fileSuffix": file["suffix"],
                "fileContent": file["uri"],
            })
            selected.append({key: item for key, item in file.items() if key != "uri"})
        groups = body.get("subAccounts")
        if isinstance(groups, list) and any(group not in {item["id"] for item in self.groups} for group in groups):
            raise ValueError("所选消息组不存在或不属于当前账号")
        selected_material = next((row for row in self.qualifications if row["id"] == body.get("signatureIdentificationID")), None)
        if selected_material is None:
            raise ValueError("请选择本次表单已读取的可用资质")
        body, material, warnings = prepare_signature(
            self.client, body, scope=self.scope, qualification=selected_material,
        )
        digest = signature_digest(body, self.scope, material)
        save_signature_preview(digest, self.scope)
        self.revision += 1
        self.pending = {"body": body, "material": material, "warnings": warnings,
                        "digest": digest, "materialCount": len(selected)}
        return {
            "revision": self.revision, "content": body["content"], "source": body["source"],
            "businessCertificateName": material["businessCertificateName"], "purpose": material["purpose"],
            "signatureIdentificationID": body["signatureIdentificationID"],
            "channelTypes": body["channelTypes"], "subAccounts": body["subAccounts"],
            "warnings": copy.deepcopy(warnings), "materials": selected,
        }

    def create(self, revision: Any, confirmed: Any, accept_risk: Any = False) -> dict:
        if self.result is not None:
            return copy.deepcopy(self.result)
        self._editable()
        if (self.pending is None or type(revision) is not int or revision != self.revision
                or confirmed is not True or type(accept_risk) is not bool):
            raise ValueError("请确认当前预览，修改后需要重新预览")
        if self.pending["warnings"] and not accept_risk:
            signature_event(self.client, "submit", "path", result_class="risk_confirmation_required")
            raise ValueError("请返回修改，或明确确认承担风险后强制提交")
        pending = self.pending
        material = _current_qualification(self.client, pending["body"]["signatureIdentificationID"], self.scope)
        if material != pending["material"]:
            self.qualifications = [material if row["id"] == material["id"] else row for row in self.qualifications]
            self.pending = None
            signature_event(self.client, "qualification", "path", result_class="changed")
            raise SignatureApplicationError("signature_qualification_changed", "企业资料已更新，请核对后重新预览确认",
                                            state=self.metadata)
        body, _, _ = prepare_signature(self.client, pending["body"], scope=self.scope, qualification=material)
        self.submitted = True
        try:
            response = submit_confirmed_signature(self.client, body, self.scope, pending["digest"])
        except SignatureApplicationError as error:
            if not error.outcome_unknown:
                self.submitted = False
                raise
            # 其他入口的提交正在进行或结果未知时，页面也进入待核实终态，不能再次申请。
            self.result = {"status": "signature_submission_outcome_unknown", "outcomeUnknown": True,
                           "code": error.code, "requestId": error.request_id,
                           "materialCount": pending["materialCount"]}
            self.pending = None
            self.files.clear()
            signature_event(self.client, "submit", "path", result_class=self.result["status"])
            return copy.deepcopy(self.result)
        self.result = response.get("signatureSubmission")
        if self.result is None:
            self.submitted = False
            self.pending = None
            error = response.get("error") or {}
            code = error.get("code") or "signature_application_failed"
            signature_event(self.client, "submit", "path", result_class="rejected", error_code=code)
            secrets = tuple(getattr(self.client, "output_secrets", ())) + tuple(
                file["uri"] for file in self.files.values()
            )
            message = error.get("message")
            message = message if isinstance(message, str) and message else "签名申请未完成，请按错误码和请求编号核对原因"
            raise SignatureApplicationError(code, sanitize_output(message, secrets=secrets),
                                            request_id=response.get("request_id"), log_id=response.get("log_id"),
                                            remediation=error.get("remediation"))
        self.result.update({"riskAccepted": bool(pending["warnings"] and accept_risk),
                            "materialCount": pending["materialCount"]})
        self.pending = None
        self.files.clear()
        signature_event(self.client, "submit", "path", result_class=self.result["status"])
        return copy.deepcopy(self.result)

    def close(self, reason: str) -> dict:
        self.closed = True
        self.pending = None
        self.files.clear()
        signature_event(self.client, "close", "path", result_class=reason)
        if self.result is not None:
            return copy.deepcopy(self.result)
        return {
            "status": "signature_submission_outcome_unknown" if self.submitted else "signature_form_closed",
            "outcomeUnknown": self.submitted, "reason": reason,
        }
