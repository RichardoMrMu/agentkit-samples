# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# Licensed under the Apache License, Version 2.0 (the "License").
"""模板领域拥有控制台参数校验、账号配置与固定引流查询；不改写申请正文。"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from collections import Counter
from typing import Any, Mapping, Optional

from traffic_driving import TrafficDrivingError, prepare_traffic_template


CHANNEL_TYPES = frozenset({"CN_OTP", "CN_NTC", "CN_MKT"})
PARAM_TYPES = frozenset({1, 2, 3, 4, 5})
VARIABLE = re.compile(r"\$\{([^{}]*)\}")
VARIABLE_NAME = re.compile(r"[a-zA-Z0-9_]+")
OTP_CONTENT = re.compile(r"验证码|code\s|code\s+is|注册码|校验码|动态码|动态密码")
MAX_TRAFFIC_PARAMS = {2: 1, 3: 2, 4: 1}
_LOGGER = logging.getLogger("volcengine.sms.template")
if not _LOGGER.handlers:
    _LOGGER.addHandler(logging.StreamHandler())
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False


class TemplateApplicationError(ValueError):
    """TemplateApplicationError 只交接稳定校验码或客户端已脱敏的查询错误。"""

    def __init__(self, message: str, code: str = "argument_error", *, response=None):
        super().__init__(message)
        self.code = code
        self.response = response


def template_event(client, phase: str, event: str, **fields) -> None:
    """template_event 同时输出日志和 best-effort 观测，不记录正文、资源内容及凭据。"""
    context = getattr(client, "request_context", None)
    payload = {
        "event": event, "phase": phase,
        "operation_id": getattr(context, "operation_id", None), **fields,
    }
    try:
        _LOGGER.info("template_application %s", json.dumps(payload, ensure_ascii=False))
        sys.stderr.write("TEMPLATE_OBSERVATION " + json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 观测失败不能改变申请结果。


def template_call(client, action: str, params: dict, *, scope: Optional[str] = None) -> dict:
    """template_call 记录实际调用和每次 HTTP 尝试；写请求仍由既有客户端保证只发一次。"""
    started = time.monotonic()
    template_event(client, action, "start")
    try:
        options = {
            "use_cli": False,
            "http_attempt_observer": lambda event, fields: template_event(
                client, "http_attempt", event, action=action, **fields,
            ),
        }
        if scope is not None:
            options["expected_credential_scope"] = scope
        response = client.call(action, params, **options)
    except Exception:
        template_event(client, action, "end", duration_ms=int((time.monotonic() - started) * 1000),
                       result_class="unknown", error_code="transport_error")
        raise
    error = response.get("error") or {} if isinstance(response, Mapping) else {}
    template_event(
        client, action, "end", duration_ms=int((time.monotonic() - started) * 1000),
        result_class=("success" if isinstance(response, Mapping) and response.get("success") is True
                      else "unknown" if error.get("outcome_unknown") is True else "failed"),
        error_code=error.get("code"), request_id=response.get("request_id") if isinstance(response, Mapping) else None,
        log_id=response.get("log_id") if isinstance(response, Mapping) else None,
    )
    return response


def validate_template_params(params: Any) -> list:
    """validate_template_params 只校验原生字段和已选择类型下的控制台语法，不推断用途或填默认值。"""
    if not isinstance(params, dict):
        raise TemplateApplicationError("模板申请参数必须是对象")
    for field in ("name", "content", "channelType"):
        if not isinstance(params.get(field), str) or not params[field]:
            raise TemplateApplicationError("模板名称、正文和短信类型必须填写")
    if params["channelType"] not in CHANNEL_TYPES:
        raise TemplateApplicationError("请选择国内验证码、通知或营销短信类型")
    for field, maximum in (("name", 50), ("desc", 300)):
        value = params.get(field, "")
        if not isinstance(value, str):
            raise TemplateApplicationError("模板名称和用途必须是字符串")
        try:
            # 与浏览器 maxLength 一致，按 UTF-16 代码单元计数；不截断或归一化文本。
            units = len(value.encode("utf-16-le")) // 2
        except UnicodeEncodeError as exc:
            raise TemplateApplicationError("模板名称或用途包含无法编码的字符") from exc
        if units > maximum:
            raise TemplateApplicationError("模板名称最多 50 字符，用途最多 300 字符")
    if len(params["name"].encode("utf-8")) > 100:
        raise TemplateApplicationError("模板名称不能超过 100 个字节")
    signatures = params.get("signatures")
    if (not isinstance(signatures, list) or not signatures
            or any(not isinstance(item, str) or not item for item in signatures)):
        raise TemplateApplicationError("至少选择一个签名，签名列表不能包含空项")
    if "subAccounts" in params and (
        not isinstance(params["subAccounts"], list)
        or any(not isinstance(item, str) or not item for item in params["subAccounts"])
    ):
        raise TemplateApplicationError("subAccounts 必须是消息组 ID 数组，不能包含空项")
    content = params["content"]
    matches = list(VARIABLE.finditer(content))
    if "${" in VARIABLE.sub("", content):
        raise TemplateApplicationError("变量必须使用完整的 ${name} 格式")
    names = [match.group(1) for match in matches]
    if any(VARIABLE_NAME.fullmatch(name) is None for name in names):
        raise TemplateApplicationError("变量名称只支持英文、数字及下划线")
    if any(name.lower() == "volc_sms_url_token" for name in names):
        raise TemplateApplicationError("不能使用保留变量名 volc_sms_url_token")
    definitions = params.get("templateParams", [])
    if not isinstance(definitions, list):
        raise TemplateApplicationError("templateParams 必须是变量对象数组")
    defined = {}
    for item in definitions:
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                or VARIABLE_NAME.fullmatch(item["name"]) is None):
            raise TemplateApplicationError("每个变量必须有合法的 name")
        if type(item.get("paramType")) is not int or item["paramType"] not in PARAM_TYPES:
            raise TemplateApplicationError("变量 paramType 必须为整数 1、2、3、4 或 5，普通变量为 1")
        if item["name"] in defined:
            raise TemplateApplicationError("同名变量只需定义一次")
        defined[item["name"]] = item["paramType"]
    if set(defined) != set(names):
        raise TemplateApplicationError("正文占位符与 templateParams 的变量名必须一一对应")
    short_url = params.get("shortUrlConfig", {})
    if not isinstance(short_url, dict):
        raise TemplateApplicationError("shortUrlConfig 必须是对象")
    if "isEnabled" in short_url and short_url["isEnabled"] not in ("0", "1"):
        raise TemplateApplicationError("shortUrlConfig.isEnabled 必须为字符串 0 或 1")
    if short_url.get("isEnabled") == "1" and params["channelType"] != "CN_MKT":
        raise TemplateApplicationError("平台短链仅支持营销短信")
    if params["channelType"] == "CN_OTP":
        if len(matches) != 1 or any(value != 1 for value in defined.values()):
            raise TemplateApplicationError("验证码模板必须有且只有一个普通变量，paramType=1")
        if OTP_CONTENT.search(content) is None:
            raise TemplateApplicationError("验证码正文需要包含验证码、注册码、校验码、动态码、动态密码或 code")
        if params.get("templateTrafficDriving") or params.get("fixTemplateParams"):
            raise TemplateApplicationError("验证码模板不支持引流信息")
    else:
        if any(left.end() == right.start() for left, right in zip(matches, matches[1:])):
            raise TemplateApplicationError("通知和营销模板不支持相邻占位符，请保留分隔内容")
        if len(matches) > 10:
            raise TemplateApplicationError("与控制台一致，每个通知或营销模板最多包含 10 个变量占位符")
    return definitions


def prepare_template_application(client, params: dict) -> tuple:
    """prepare_template_application 先校验，再查询本次身份和引流事实；未完成时禁止申请。"""
    started = time.monotonic()
    template_event(client, "prepare", "start")
    outcome = "failed"
    error_code = "invalid_response"
    try:
        definitions = validate_template_params(params)
        response = template_call(client, "ListAllSmsProduct", {})
        if not isinstance(response, Mapping) or response.get("success") is not True:
            raise TemplateApplicationError("账号查询失败，请先核对账号状态", response=response)
        account = response.get("result")
        scope = client.credential_scope
        if not isinstance(account, Mapping) or not isinstance(account.get("userType"), str) or not scope:
            raise TemplateApplicationError("账号类型或身份信息不完整", "invalid_response")
        if params["channelType"] == "CN_MKT" and definitions:
            if type(account.get("mktParam")) is not int:
                raise TemplateApplicationError("账号查询未提供有效的营销变量配置，请先核实", "invalid_response")
            if account["mktParam"] != 1:
                raise TemplateApplicationError("当前账号未允许营销模板使用变量", "template_account_restricted")
        if account["userType"] == "smb" and params["channelType"] == "CN_NTC" and definitions:
            raise TemplateApplicationError("因短信行业风险管控，小微账号暂不支持带变量的通知模板", "template_account_restricted")
        body = prepare_traffic_template(
            client, params, credential_scope=scope,
            call=lambda action, fields: template_call(client, action, fields, scope=scope),
        )
        response = template_call(client, "CheckTrafficDrivingExist", {
            "content": body["content"],
            "useVolcLink": body.get("shortUrlConfig", {}).get("isEnabled") == "1",
        }, scope=scope)
        if not isinstance(response, Mapping) or response.get("success") is not True:
            raise TemplateApplicationError("固定引流信息查询失败，请先核对，不继续申请", response=response)
        result = response.get("result")
        fixed = result.get("list") if isinstance(result, Mapping) else None
        if not isinstance(fixed, list):
            raise TemplateApplicationError("固定引流查询未返回资源列表", "invalid_response")
        for item in fixed:
            if (not isinstance(item, Mapping) or type(item.get("paramType")) is not int
                    or item["paramType"] not in (2, 3) or type(item.get("isExist")) is not bool):
                raise TemplateApplicationError("固定引流查询结果不完整，请先核实", "invalid_response")
            if not item["isExist"]:
                raise TemplateApplicationError("正文中的固定链接或联系号码尚未录入引流信息库，请先录入")
        counts = Counter(item["paramType"] for item in definitions + fixed)
        if any(counts[kind] > maximum for kind, maximum in MAX_TRAFFIC_PARAMS.items()):
            raise TemplateApplicationError("包含固定内容在内，最多 1 个链接、2 个联系号码和 1 个链接 Path")
        if body["channelType"] == "CN_OTP" and fixed:
            raise TemplateApplicationError("验证码模板不支持固定链接或联系号码")
        warnings = []
        if account["userType"] == "smb" and any(item["paramType"] == 2 for item in fixed):
            warnings.append("小微账号带固定链接有审核拒绝风险；引流资源已录入不代表模板审核通过")
        if account["userType"] == "smb" and body["channelType"] == "CN_MKT":
            warnings.append("小微账号营销模板可能受准入限制，是否开放以服务端结果为准；明确受限后停止原样重试")
        if body["channelType"] == "CN_MKT":
            warnings.append("营销正文末尾需要退订提示，具体格式以服务端动态规则为准")
        outcome = "prepared"
        error_code = None
        return body, scope, warnings
    except (TemplateApplicationError, TrafficDrivingError) as exc:
        error_code = getattr(exc, "code", "traffic_template_invalid")
        if isinstance(exc.response, Mapping):
            error_code = (exc.response.get("error") or {}).get("code") or error_code
        raise
    finally:
        template_event(client, "prepare", "end", duration_ms=int((time.monotonic() - started) * 1000),
                       result_class=outcome, error_code=error_code)
