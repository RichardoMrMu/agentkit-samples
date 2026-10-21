# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
# Licensed under the Apache License, Version 2.0 (the "License").
"""Private traffic-resource creation and explicit resource-ID binding."""

from __future__ import annotations

import copy
import re
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlsplit


LINK_FIELDS = frozenset({
    "useVolcLink", "protocol", "link", "linkDomainIcp", "linkDomainIcpBody",
    "linkDomain", "linkPath", "jumpLink", "jumpLinkDomainIcp", "jumpLinkDomainIcpBody",
    "jumpLinkDomain", "jumpLinkPath",
})
LINK_PREFILL_FIELDS = LINK_FIELDS - {
    "linkDomain", "linkPath", "jumpLinkDomain", "jumpLinkPath",
}
PHONE_PREFILL_FIELDS = frozenset({
    "numberType", "number", "company",
    "numberProvince", "numberCity",
})
PHONE_FIELDS = PHONE_PREFILL_FIELDS | {"numberPerson", "personType", "personId"}
OPERATIONS = {
    "link": ("GetTobTrafficDrivingLinkListForAgent", "BulkCreateTobTrafficDrivingLinkForAgent", LINK_FIELDS, 10),
    "phone": ("GetTobTrafficDrivingPhoneListForAgent", "BulkCreateTobTrafficDrivingPhoneForAgent", PHONE_FIELDS, 100),
}
PROTOCOLS = frozenset({"http", "https", "缺省"})
NUMBER_PATTERNS = {
    1: (re.compile(r"^1[3456789]\d{9}$"), "请输入正确的手机号"),
    2: (re.compile(r"^400\d{7}$"), "请输入正确的400号码"),
    3: (re.compile(r"^700\d+$"), "请输入正确的700号码"),
    4: (re.compile(r"^800\d{7}$"), "请输入正确的800号码"),
    5: (re.compile(r"^9[56]\d{3}$"), "请输入正确的95或者96号码"),
    6: (re.compile(r"^12\d{3}$"), "请输入正确的12号码"),
    7: (re.compile(r"^\d{10,12}$"), "请输入正确的固话号码"),
}


class TrafficDrivingError(ValueError):
    def __init__(self, message: str, response: Optional[Mapping[str, Any]] = None):
        super().__init__(message)
        self.response = response


def prepare_traffic_template(
    client, params: Mapping[str, Any], *, credential_scope: Optional[str] = None,
    call: Optional[Callable[[str, dict], dict]] = None,
) -> dict:
    """prepare_traffic_template 回查显式资源 ID；call 由申请生命周期提供并绑定同一身份。"""
    body = copy.deepcopy(dict(params))
    groups = body.get("templateTrafficDriving", [])
    if groups is None:
        return body
    if not isinstance(groups, list):
        raise TrafficDrivingError("templateTrafficDriving 必须是数组")
    resources = {}
    for group in groups:
        if not isinstance(group, list):
            raise TrafficDrivingError("每组引流变量必须是数组")
        for item in group:
            if not isinstance(item, dict) or type(item.get("paramType")) is not int or item["paramType"] not in (2, 3):
                raise TrafficDrivingError("引流变量的 paramType 必须为链接 2 或号码 3")
            content = item.get("content")
            if content is not None and not isinstance(content, str):
                raise TrafficDrivingError("引流变量 content 必须是字符串")
            if "id" not in item:
                if not content:
                    raise TrafficDrivingError("引流变量需要已有资源 id 或完整 content")
                continue
            resource_id = item["id"]
            if type(resource_id) is not int or resource_id <= 0:
                raise TrafficDrivingError("引流资源 id 必须是正整数")
            kind = "link" if item["paramType"] == 2 else "phone"
            cache_key = (kind, resource_id)
            if cache_key not in resources:
                if call is None:
                    response = client.call(
                        OPERATIONS[kind][0], {"id": resource_id}, use_cli=False,
                        expected_credential_scope=credential_scope,
                    )
                else:
                    response = call(OPERATIONS[kind][0], {"id": resource_id})
                if response.get("success") is not True:
                    raise TrafficDrivingError("引流资源查询失败", response)
                result = response.get("result")
                rows = result.get("list") if isinstance(result, Mapping) else None
                if not isinstance(rows, list):
                    raise TrafficDrivingError("引流查询未返回资源列表")
                matches = [row for row in rows if isinstance(row, Mapping) and row.get("id") == resource_id]
                if len(matches) != 1:
                    raise TrafficDrivingError("所选引流资源不存在或不属于当前账号")
                resources[cache_key] = matches[0]
            resource = resources[cache_key]
            if kind == "link" and type(resource.get("useVolcLink")) is not bool:
                raise TrafficDrivingError("链接资源缺少短链类型，无法确定绑定内容")
            field = ("jumpLink" if resource["useVolcLink"] else "link") if kind == "link" else "number"
            original = resource.get(field)
            if not isinstance(original, str) or not original:
                raise TrafficDrivingError("所选引流资源缺少可绑定的原始内容")
            permitted = [original]
            if kind == "link" and resource.get("jumpLink"):
                permitted.append(resource["jumpLink"])
            if content and content not in permitted:
                raise TrafficDrivingError("引流 content 与所选资源 id 不一致，请重新核对")
            if not content:
                item["content"] = original
    return body


def _validate_link(row: Mapping[str, Any], *, prefill: bool) -> None:
    if prefill:
        return
    use_volc_link = row.get("useVolcLink")
    if type(use_volc_link) is not bool:
        raise ValueError("请选择是否使用火山短链")
    if use_volc_link:
        required = ("jumpLink", "jumpLinkDomainIcp", "jumpLinkDomainIcpBody")
    else:
        required = ("protocol", "link", "linkDomainIcp", "linkDomainIcpBody")
        if row.get("protocol") not in PROTOCOLS:
            raise ValueError("文案链接协议必须为 http、https 或缺省")
        link = row.get("link")
        expected = (
            "https" if link.startswith("https://")
            else "http" if link.startswith("http://")
            else "缺省"
        )
        if row["protocol"] != expected:
            raise ValueError("协议要与文案链接保持一致")
    for field in required:
        if not row.get(field):
            raise ValueError("请补充完整的引流链接及域名备案信息")
    for field in ("link", "jumpLink"):
        link = row.get(field)
        if not link:
            continue
        parsed = urlsplit(link if "://" in link else "https://" + link)
        if not parsed.hostname:
            raise ValueError("请输入正确的{}链接".format("文案" if field == "link" else "跳转"))
        # 域名和 Path 是页面展示值，服务端从原始链接生成；不同解析器的差异不应阻断录入。


def _validate_phone(row: Mapping[str, Any], *, prefill: bool) -> None:
    if prefill:
        return
    number_type = row.get("numberType")
    number = row.get("number")
    if type(number_type) is not int or number_type not in NUMBER_PATTERNS or not number:
        raise ValueError("请填写号码并选择号码类型")
    pattern, message = NUMBER_PATTERNS[number_type]
    if pattern.fullmatch(number) is None:
        raise ValueError(message)
    if number_type == 1:
        if row.get("company"):
            raise ValueError("手机号不允许填写所属企业")
        # 原生整数零值表示未填写证件类型。
        if "personType" in row and row["personType"] not in (0, 1):
            raise ValueError("所属人证件类型仅支持居民身份证")
    elif any(row.get(field) for field in ("numberPerson", "personType", "personId")):
        raise ValueError("非手机号不允许填写所属人及证件信息")


def validate_creation_body(kind: str, value: Any, *, prefill: bool = False) -> dict:
    if kind not in OPERATIONS:
        raise ValueError("请选择链接或号码")
    if not isinstance(value, dict) or set(value) != {"data"}:
        raise ValueError("录入参数必须包含 data 数组")
    rows = value["data"]
    _, _, fields, maximum = OPERATIONS[kind]
    if prefill:
        fields = LINK_PREFILL_FIELDS if kind == "link" else PHONE_PREFILL_FIELDS
    if not isinstance(rows, list) or not 1 <= len(rows) <= maximum:
        raise ValueError("本次录入数量必须为 1 至 {} 条".format(maximum))
    identities = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) - fields:
            raise ValueError(
                (
                    "号码预填仅支持号码、类型、企业和归属地，个人材料请在页面填写"
                    if kind == "phone"
                    else "链接域名和 Path 由页面自动识别，不通过预填参数传递"
                )
                if prefill else "录入字段不符合当前接口"
            )
        for key, item in row.items():
            if key == "useVolcLink":
                valid = type(item) is bool
            elif key in {"numberType", "personType"}:
                valid = type(item) is int
            else:
                valid = isinstance(item, str)
            if not valid:
                raise ValueError("录入字段类型错误")
        if kind == "link":
            _validate_link(row, prefill=prefill)
            identity = (
                row.get("useVolcLink"),
                (row.get("jumpLink") if row.get("useVolcLink") else row.get("link", "")).lower(),
            )
        else:
            _validate_phone(row, prefill=prefill)
            identity = row.get("number")
        if not prefill and identity in identities:
            raise ValueError("本次录入包含重复的{}信息".format("链接" if kind == "link" else "号码"))
        identities.add(identity)
    return copy.deepcopy(value)


class TrafficDrivingDraft:
    """One private form owns its preview, single write and safe reconciliation."""

    def __init__(self, client, kind: str, params: Optional[dict] = None):
        if kind not in OPERATIONS:
            raise ValueError("请选择链接或号码")
        self.client = client
        self.kind = kind
        self.initial = validate_creation_body(kind, params, prefill=True)["data"] if params is not None else [{}]
        identity = client.call("ListAllSmsProduct", {}, use_cli=False)
        if identity.get("success") is not True:
            raise TrafficDrivingError("无法确认当前短信账号", identity)
        self._scope = client.credential_scope
        identity_data = identity.get("result")
        if not self._scope or not isinstance(identity_data, Mapping) or not isinstance(identity_data.get("businessName"), str) or not identity_data["businessName"]:
            raise TrafficDrivingError("当前账号主体信息不完整，请重新确认登录身份")
        self.business_name = identity_data["businessName"]
        self._pending = None
        self._revision = 0
        self._submitted = False
        self._result = None
        self._closed = False

    @property
    def metadata(self) -> dict:
        return {"kind": self.kind, "businessName": self.business_name, "initialData": copy.deepcopy(self.initial), "maxRecords": OPERATIONS[self.kind][3]}

    def preview(self, value: Any) -> dict:
        if self._closed or self._submitted or self._result is not None:
            raise ValueError("本次录入已结束，请先核对结果")
        self._pending = validate_creation_body(self.kind, value)
        self._revision += 1
        return {"revision": self._revision, "data": copy.deepcopy(self._pending["data"])}

    def create(self, revision: Any, confirmed: Any) -> dict:
        if self._result is not None:
            return copy.deepcopy(self._result)
        if self._closed or self._submitted or self._pending is None:
            raise ValueError("请先预览本次录入")
        if type(revision) is not int or revision != self._revision or confirmed is not True:
            raise ValueError("请确认当前预览，修改后需要重新预览")
        body = self._pending
        self._submitted = True
        _, action, _, _ = OPERATIONS[self.kind]
        try:
            response = self.client.call(action, body, use_cli=False, expected_credential_scope=self._scope)
        except Exception:
            response = {"success": False, "error": {"code": "transport_error", "outcome_unknown": True}}
        if not isinstance(response, Mapping):
            response = {"success": False, "error": {"code": "invalid_response", "outcome_unknown": True}}
        if response.get("success") is True and response.get("result") != "":
            response = {
                "success": False, "request_id": response.get("request_id"),
                "error": {"code": "invalid_response", "outcome_unknown": True},
            }
        if response.get("success") is not True:
            error = response.get("error") or {}
            if error.get("code") == "confirmation_identity_changed":
                self._result = {
                    "status": "traffic_identity_changed", "kind": self.kind,
                    "outcomeUnknown": False, "created": False, "requestSent": False,
                }
            elif error.get("outcome_unknown") is not True and (
                error.get("request_sent") is False or error.get("code") == "1001"
            ):
                self._submitted = False
                self._pending = None
                raise ValueError("录入未完成（{}），请核对输入后重新预览".format(error.get("code", "service_error")))
            else:
                self._result = {
                    "status": "traffic_creation_outcome_unknown", "kind": self.kind,
                    "outcomeUnknown": True, "requestId": response.get("request_id"),
                    "code": error.get("code"),
                }
        else:
            ids = self._reconcile(body["data"])
            self._result = {
                "status": "traffic_created" if len(ids) == len(body["data"]) else "traffic_created_unverified",
                "kind": self.kind, "outcomeUnknown": False, "created": True,
                "requestId": response.get("request_id"), "submittedRecordCount": len(body["data"]),
                "resourceIds": ids,
            }
        self._pending = None
        self.initial = []
        return copy.deepcopy(self._result)

    def _reconcile(self, rows: list) -> list:
        ids = []
        for row in rows:
            if self.kind == "phone":
                filters = {"number": row["number"]}
            else:
                filters = {key: row[key] for key in ("link", "jumpLink") if row.get(key)}
            if not filters:
                continue
            try:
                response = self.client.call(OPERATIONS[self.kind][0], filters, use_cli=False, expected_credential_scope=self._scope)
                result = response.get("result")
                candidates = result.get("list") if isinstance(result, Mapping) else None
                if response.get("success") is not True or not isinstance(candidates, list):
                    continue
                matches = [item for item in candidates if isinstance(item, Mapping) and all(item.get(key) == value for key, value in filters.items())]
                if len(matches) == 1 and type(matches[0].get("id")) is int and matches[0]["id"] > 0:
                    ids.append(matches[0]["id"])
            except Exception:
                # The write already succeeded. A read failure must never trigger another write.
                continue
        return ids

    def close(self, reason: str) -> dict:
        self._closed = True
        self._pending = None
        self.initial = []
        if self._result is not None:
            return copy.deepcopy(self._result)
        if self._submitted:
            return {"status": "traffic_creation_outcome_unknown", "kind": self.kind, "outcomeUnknown": True}
        return {"status": "traffic_form_closed", "reason": reason, "kind": self.kind, "outcomeUnknown": False}
