# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
# Licensed under the Apache License, Version 2.0 (the "License").
"""查询实际短信正文中的引流资源，保留资源与报备查询的不同口径。"""

from __future__ import annotations

import hashlib
import json

from typing import Iterable, Mapping, Optional

from api_client import emit_json


def check_traffic_contents(client, contents: Iterable[tuple[int, str]], *,
                           use_volc_link: bool = False, max_contents: int = 100,
                           report_params: Optional[Mapping] = None,
                           credential_scope: Optional[str] = None) -> dict:
    """相同正文复用查询；输出行号和查询结果，不输出收件人或完整正文。"""
    if type(max_contents) is not int or max_contents < 1:
        raise ValueError("max-contents 必须为正整数")
    if report_params is not None and "Content" in report_params:
        raise ValueError("报备查询的 Content 由本机实际正文提供，不在 report-params 中填写")
    cache = {}
    checks = []
    total_rows = checked_rows = missing_rows = failed_rows = 0
    scope = credential_scope
    for row_number, content in contents:
        total_rows += 1
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if digest not in cache:
            if len(cache) >= max_contents:
                continue
            response = client.call(
                "CheckTrafficDrivingExist", {"content": content, "useVolcLink": use_volc_link},
                use_cli=False, expected_credential_scope=scope,
            )
            if scope is None:
                scope = getattr(client, "credential_scope", None)
            result = response.get("result")
            resources = result.get("list") if isinstance(result, Mapping) else None
            valid = response.get("success") is True and isinstance(resources, list)
            if valid:
                valid = all(isinstance(item, Mapping) and type(item.get("isExist")) is bool
                            and item.get("paramType") in (2, 3) for item in resources)
            entry = {
                "rowCount": 0, "rowNumbers": [], "responseValid": valid,
                "resources": json.loads(emit_json(
                    {**response, "action": "CheckTrafficDrivingExist"},
                    secrets=getattr(client, "output_secrets", ()),
                )),
            }
            if valid and resources and report_params is not None:
                report_response = client.call(
                    "AgentGetSignatureTemplateReportStatus", {**report_params, "Content": content},
                    use_cli=False, expected_credential_scope=scope,
                )
                entry["reportStatus"] = json.loads(emit_json(
                    {**report_response, "action": "AgentGetSignatureTemplateReportStatus"},
                    secrets=getattr(client, "output_secrets", ()),
                ))
            cache[digest] = entry
            if not valid or resources:
                checks.append(entry)
        entry = cache[digest]
        entry["rowCount"] += 1
        if len(entry["rowNumbers"]) < 20:
            entry["rowNumbers"].append(row_number)
        if not entry["responseValid"] or ("reportStatus" in entry and entry["reportStatus"].get("success") is not True):
            failed_rows += 1
        else:
            checked_rows += 1
        result = entry["resources"].get("result")
        if entry["responseValid"] and any(item["isExist"] is False for item in result["list"]):
            missing_rows += 1
    return {
        "totalRows": total_rows, "checkedRows": checked_rows, "failedRows": failed_rows,
        "missingResourceRows": missing_rows, "checkedContents": len(cache),
        "maxContents": max_contents, "complete": total_rows > 0 and checked_rows == total_rows,
        "checks": checks,
    }
