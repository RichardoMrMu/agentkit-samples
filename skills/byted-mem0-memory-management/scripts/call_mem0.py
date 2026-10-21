#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
# SPDX-License-Identifier: Apache-2.0
"""Volcengine Mem0: read-only control plane and memory data-plane operations."""

import argparse
import datetime
import hashlib
import hmac
import http.client
import json
import math
import os
from pathlib import Path
import shlex
import sys
import urllib.error
import urllib.parse
import urllib.request


CONTROL_ACTIONS = frozenset({
    "DescribeMemoryProjects", "DescribeMemoryProjectDetail", "DescribeAPIKeyDetail",
})
CREDENTIAL_NAMES = (
    "VOLCENGINE_ACCESS_KEY", "VOLCENGINE_SECRET_KEY",  # @opensource-lint-ignore: environment variable names only
    "MEM0_API_KEY",
)


class Mem0Error(Exception):
    """An actionable configuration, transport, or API error."""


def load_credentials(env_file=None):
    """Read literal assignments only; never execute a credential shell file."""
    values = {name: os.getenv(name, "") for name in CREDENTIAL_NAMES}
    if env_file:
        for line in Path(env_file).expanduser().read_text(encoding="utf-8").splitlines():
            tokens = shlex.split(line, comments=True)
            if tokens and tokens[0] == "export":
                tokens = tokens[1:]
            for token in tokens:
                name, sep, value = token.partition("=")
                if sep and name in values and not values[name]:
                    if "$" in value or "`" in value:
                        raise ValueError(f"{name} 必须是字面量；请在调用前自行加载动态环境配置")
                    values[name] = value
    return values


def endpoint_origin(value):
    """Validate and canonicalize an HTTPS origin before comparing or using it."""
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("endpoint 不能包含空白或控制字符")
    if "://" not in value:
        value = "https://" + value
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("endpoint 必须是 HTTPS 域名或 origin，不含路径、凭证、查询参数")
    port = parsed.port
    if parsed.netloc.endswith(":") or port == 0:
        raise ValueError("endpoint 端口必须为 1～65535 的整数")
    host = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    if not host or "%" in host:
        raise ValueError("endpoint 包含无效域名")
    if ":" in host:
        host = "[" + host + "]"
    return "https://" + host + (f":{port}" if port not in (None, 443) else "")


def project_endpoint(address):
    """Build an origin from a control-plane VisitAddrs entry."""
    value = address["Address"]
    origin = endpoint_origin(value)
    port = address.get("Port")
    if port not in (None, ""):
        number = int(port)
        if not 1 <= number <= 65535:
            raise ValueError("项目连接地址包含无效端口")
        explicit_port = urllib.parse.urlsplit(value if "://" in value else "https://" + value).port
        if explicit_port is not None and explicit_port != number:
            raise ValueError("项目连接地址与 Port 字段不一致")
        parsed = urllib.parse.urlsplit(origin)
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        origin = endpoint_origin(f"https://{host}:{number}")
    return origin


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Mem0Client:
    def __init__(self, region="cn-beijing", credentials=None, timeout=60):
        if region not in ("cn-beijing", "cn-shanghai"):
            raise ValueError("region 仅支持 cn-beijing、cn-shanghai")
        self.region = region
        self.credentials = credentials if credentials is not None else load_credentials()
        self.timeout = timeout
        self.secrets = [v for v in self.credentials.values() if v]
        self._project_key_origins = {}
        self.opener = urllib.request.build_opener(NoRedirect())

    def redact(self, data):
        """Mask returned API keys and any known credential echoed by the server."""
        if isinstance(data, dict):
            return {k: "[REDACTED]" if k.lower() in (
                "apikeyvalue", "api_key", "authorization", "secret_key", "access_key",  # @opensource-lint-ignore: response field names to redact
            ) else self.redact(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self.redact(v) for v in data]
        if isinstance(data, str):
            for secret in self.secrets:
                data = data.replace(secret, "[REDACTED]")  # @opensource-lint-ignore: redaction marker, not a credential
        return data

    def _request(self, method, url, headers, body=None):
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
            raise Mem0Error(f"网络请求失败（写操作不会自动重试）: {self.redact(str(exc))}") from None
        text = raw.decode("utf-8", errors="replace")
        try:
            result = json.loads(text) if text.strip() else None
        except json.JSONDecodeError:
            raise Mem0Error(f"HTTP {status}: 返回非 JSON 内容: {self.redact(text[:1000])}") from None
        error = result.get("ResponseMetadata", {}).get("Error") if isinstance(result, dict) else None
        if not 200 <= status < 300 or error:
            detail = json.dumps(self.redact(result), ensure_ascii=False)
            raise Mem0Error(f"HTTP {status}: {detail}")
        return result

    def control(self, action, payload):
        # This allowlist is enforced even when called outside the CLI.
        if action not in CONTROL_ACTIONS:
            raise ValueError(f"控制面仅支持只读接口: {action}")
        ak = self.credentials.get("VOLCENGINE_ACCESS_KEY")
        sk = self.credentials.get("VOLCENGINE_SECRET_KEY")
        if not ak or not sk:
            raise ValueError("请设置 VOLCENGINE_ACCESS_KEY 和 VOLCENGINE_SECRET_KEY，或使用 --env-file")
        host = f"mem0.{self.region}.volcengineapi.com"
        query = urllib.parse.urlencode({"Action": action, "Version": "2025-10-10"})
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        headers = {"Content-Type": "application/json; charset=utf-8", "Host": host, "X-Date": date}
        canonical_values = {k.lower(): v.strip() for k, v in headers.items()}
        names = sorted(canonical_values)
        signed_headers = ";".join(names)
        canonical_headers = "".join(f"{name}:{canonical_values[name]}\n" for name in names)
        canonical_request = "\n".join((
            "POST", "/", query, canonical_headers, signed_headers, hashlib.sha256(body).hexdigest(),
        ))
        scope = f"{date[:8]}/{self.region}/mem0/request"
        string_to_sign = "\n".join((
            "HMAC-SHA256", date, scope, hashlib.sha256(canonical_request.encode()).hexdigest(),
        ))
        signing_key = sk.encode()
        for component in (date[:8], self.region, "mem0", "request"):
            signing_key = hmac.new(signing_key, component.encode(), hashlib.sha256).digest()
        signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
        headers["Authorization"] = (
            f"HMAC-SHA256 Credential={ak}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
        )
        return self._request("POST", f"https://{host}/?{query}", headers, body)

    def list_projects(self, page_number=1, page_size=10, resource_project=None,
                      tag_filters=None, all_pages=False, memory_project_name=None):
        payload = {"RegionId": self.region, "PageNumber": page_number, "PageSize": page_size}
        if resource_project is not None:
            payload["ProjectName"] = resource_project
        if tag_filters is not None:
            payload["TagFilters"] = tag_filters
        if memory_project_name is not None:
            payload["PageNumber"] = 1
        response = self.control("DescribeMemoryProjects", payload)
        result = response["Result"]
        projects = list(result.get("MemProjectInfos") or [])
        while all_pages or memory_project_name is not None:
            if (not result.get("MemProjectInfos")
                    or payload["PageNumber"] * page_size >= result["TotalMemProjectsNum"]):
                break
            payload["PageNumber"] += 1
            result = self.control("DescribeMemoryProjects", payload)["Result"]
            projects.extend(result.get("MemProjectInfos") or [])
        if memory_project_name is not None:
            projects = [p for p in projects if p.get("MemProjectName") == memory_project_name]
            response["Result"]["MatchedMemProjectsNum"] = len(projects)
        response["Result"]["MemProjectInfos"] = projects
        return response

    def describe_project(self, project_id):
        return self.control("DescribeMemoryProjectDetail", {"MemoryProjectId": project_id})

    def describe_api_key(self, project_id, api_key_id):
        response = self.control("DescribeAPIKeyDetail", {
            "MemoryProjectId": project_id, "APIKeyId": api_key_id,
        })
        value = response.get("Result", {}).get("APIKeyValue")
        if value:
            self.secrets.append(value)
        return response

    def data_connection(self, project_id=None, endpoint=None, api_key_id=None):
        """Bind automatically retrieved credentials to this project's VisitAddrs."""
        key = self.credentials.get("MEM0_API_KEY")
        if endpoint:
            endpoint = endpoint_origin(endpoint)
        if not endpoint or not key:
            if not project_id:
                raise ValueError("自动发现需要 --project-id；或同时设置 MEM0_ENDPOINT 与 MEM0_API_KEY")
            project = self.describe_project(project_id)["Result"]
            addresses = [(address, project_endpoint(address))
                         for address in project.get("VisitAddrs") or [] if address.get("Address")]
            allowed_origins = {origin for _, origin in addresses}
            if not endpoint:
                public_origins = {origin for address, origin in addresses if address.get("AddrType") == "Public"}
                if len(public_origins) != 1:
                    raise ValueError("无法唯一确定公网连接地址，请用 --data-endpoint 指定控制台提供的 HTTPS 地址")
                endpoint = next(iter(public_origins))
            if not key:
                if endpoint not in allowed_origins:
                    raise ValueError(
                        "指定 endpoint 不属于该项目的 VisitAddrs，不会读取或发送项目 API Key；"
                        "请使用项目连接地址。自定义域名需同时提供 MEM0_ENDPOINT 和 MEM0_API_KEY"
                    )
                keys = [k for k in project.get("APIKeyInfos") or [] if k.get("Status") == "Ready"]
                if api_key_id:
                    keys = [k for k in keys if k.get("APIKeyId") == api_key_id]
                if len(keys) != 1:
                    raise ValueError("没有唯一可用的既有 API Key，请用 --api-key-id 或 MEM0_API_KEY 指定；不会创建 Key")
                key = self.describe_api_key(project_id, keys[0]["APIKeyId"])["Result"].get("APIKeyValue")
                if key:
                    # Enforce the binding again in data(), including direct client calls.
                    previous = self._project_key_origins.get(key)
                    self._project_key_origins[key] = allowed_origins if previous is None else previous & allowed_origins
        if not key:
            raise ValueError("数据面 API Key 为空")
        self.secrets.append(key)
        return endpoint, key

    def data(self, endpoint, api_key, method, path, query=None, payload=None):
        origin = endpoint_origin(endpoint)
        allowed_origins = self._project_key_origins.get(api_key)
        if allowed_origins is not None and origin not in allowed_origins:
            raise ValueError("不会将自动读取的项目 API Key 发送到 VisitAddrs 之外的地址")
        url = origin + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        self.secrets.append(api_key)
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return self._request(method, url, {
            "Authorization": f"Token {api_key}", "Content-Type": "application/json",  # @opensource-lint-ignore: runtime credential, no literal value
        }, body)


def integer_in_range(low, high=None):
    def parse(value):
        number = int(value)
        if number < low or (high is not None and number > high):
            raise argparse.ArgumentTypeError(f"取值范围: {low} ~ {high or '正整数'}")
        return number
    return parse


def positive_seconds(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("timeout 必须是有限正数")
    return number


def json_argument(value, expected_type):
    if value.startswith("@"):
        value = sys.stdin.read() if value == "@-" else Path(value[1:]).read_text(encoding="utf-8")
    result = json.loads(value)
    if not isinstance(result, expected_type):
        raise ValueError(f"JSON 必须为 {expected_type.__name__}")
    return result


def nonempty(value):
    if not value.strip():
        raise argparse.ArgumentTypeError("参数不能为空")
    return value


def build_parser():
    # Suppress shared defaults so options work on either side of the subcommand.
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--region", "-r", choices=("cn-beijing", "cn-shanghai"))
    common.add_argument("--env-file", help="读取 AK/SK/API Key 的字面量赋值文件，不执行 shell")
    common.add_argument("--project-id", type=nonempty, help="Mem0 MemoryProjectId")
    common.add_argument("--api-key-id", type=nonempty, help="既有 API Key ID；多个 Key 时用于选择")
    common.add_argument("--data-endpoint", help="数据面 HTTPS origin；默认从项目详情发现")
    common.add_argument("--timeout", type=positive_seconds, help="单次请求超时秒数，默认 60")
    common.add_argument("--output", "-o", help="另存脱敏 JSON 响应文件")
    parser = argparse.ArgumentParser(description=__doc__, parents=[common])
    actions = parser.add_subparsers(dest="action", required=True)

    def sub(name, help_text):
        return actions.add_parser(name, help=help_text, parents=[common])

    p = sub("list-projects", "控制面：查询记忆项目列表")
    p.add_argument("--page-number", type=integer_in_range(1), default=1)
    p.add_argument("--page-size", type=integer_in_range(1, 1000), default=10)
    p.add_argument("--all-pages", action="store_true")
    p.add_argument("--memory-project-name", type=nonempty, help="MemProjectName 精确匹配，自动遍历全部页")
    p.add_argument("--resource-project", help="火山资源项目 ProjectName（如 default）")
    p.add_argument("--tag-filters", help='JSON 数组或 @文件，如 [{"Key":"team","Values":["dev"]}]')
    sub("describe-project", "控制面：查询记忆项目详情")
    sub("describe-api-key", "控制面：查询既有 API Key 详情（值始终脱敏）")  # @opensource-lint-ignore: CLI action name and help text only
    p = sub("ping", "数据面：探活与 API Key 权限校验")
    p.add_argument("--org-id", type=nonempty)

    def scope(p):
        for flag in ("user-id", "agent-id", "run-id"):
            p.add_argument("--" + flag, type=nonempty)

    p = sub("list-memories", "数据面：按用户/智能体/运行列举记忆")
    scope(p)
    p.add_argument("--page", type=integer_in_range(1), default=1)
    p = sub("add-memories", "数据面：添加记忆或提交异步提取任务")
    scope(p)
    content = p.add_mutually_exclusive_group(required=True)
    content.add_argument("--text", type=nonempty, help="简写为一条 user 消息")
    content.add_argument("--messages", help="消息 JSON 数组或 @文件（@- 表示 stdin）")
    p.add_argument("--metadata", help="JSON 对象或 @文件")
    p.add_argument("--memory-type", type=nonempty)
    p.add_argument("--infer", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--async-mode", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--prev-job-id", type=nonempty)
    p = sub("search-memories", "数据面：语义搜索记忆")
    scope(p)
    p.add_argument("--query", required=True, type=nonempty)
    p.add_argument("--top-k", type=integer_in_range(1, 1000))
    p.add_argument("--filters", help="JSON 过滤对象或 @文件；语义见官方文档")
    p.add_argument("--enable-graph", action=argparse.BooleanOptionalAction, default=None)
    for action, help_text in (
        ("get-memory", "读取单条记忆"), ("update-memory", "更新单条记忆"),
        ("delete-memory", "删除单条记忆"),
    ):
        p = sub(action, "数据面：" + help_text)
        p.add_argument("--memory-id", required=True, type=nonempty)
        if action == "update-memory":
            p.add_argument("--text", required=True, type=nonempty)
    p = sub("delete-memories", "数据面：删除指定用户/智能体/运行的全部记忆")
    scope(p)
    p.add_argument("--confirm", action="store_true", help="确认删除已指定范围的全部记忆")
    p = sub("get-event", "数据面：查询异步事件详情")
    p.add_argument("--event-id", required=True, type=nonempty)
    return parser


def data_request(args):
    """Validate and construct a documented data-plane request before any network I/O."""
    action = args.action
    project = {"project_id": args.project_id} if args.project_id else {}
    scope = {k: getattr(args, k) for k in ("user_id", "agent_id", "run_id") if getattr(args, k, None)}
    if action in ("list-memories", "add-memories", "delete-memories") and not scope:
        raise ValueError("至少指定 --user-id、--agent-id、--run-id 中一个")
    if action == "ping":
        query = dict(project)
        if args.org_id:
            query["org_id"] = args.org_id
        return "GET", "/v1/ping/", query, None
    if action == "list-memories":
        return "GET", "/v1/memories/", dict(project, **scope, page=args.page), None
    if action == "delete-memories":
        if not args.confirm:
            raise ValueError("批量删除必须指定 --confirm，并限定 user/agent/run 范围")
        return "DELETE", "/v1/memories/", dict(project, **scope), None
    if action == "add-memories":
        if not args.infer and args.async_mode:
            raise ValueError("--no-infer 必须同时使用 --no-async-mode")
        messages = [{"role": "user", "content": args.text}] if args.text else json_argument(args.messages, list)
        if not messages or any(not isinstance(m, dict) or not isinstance(m.get("role"), str)
                               or not m["role"].strip() or not isinstance(m.get("content"), str)
                               or not m["content"].strip() for m in messages):
            raise ValueError("messages 必须是非空数组，每条包含非空字符串 role 和 content")
        payload = dict(project, **scope, messages=messages, infer=args.infer, async_mode=args.async_mode)
        if args.metadata is not None:
            metadata = json_argument(args.metadata, dict)
            if any(k.startswith("__") or k.endswith("__") for k in metadata):
                raise ValueError("metadata 的键不能以 __ 开头或结尾")
            payload["metadata"] = metadata
        for key in ("memory_type", "prev_job_id"):
            if getattr(args, key) is not None:
                payload[key] = getattr(args, key)
        return "POST", "/v1/memories/", None, payload
    if action == "search-memories":
        payload = dict(project, **scope, query=args.query)
        if args.top_k is not None:
            payload["top_k"] = args.top_k
        if args.enable_graph is not None:
            payload["enable_graph"] = args.enable_graph
        if args.filters is not None:
            payload["filters"] = json_argument(args.filters, dict)
        return "POST", "/v1/search/", None, payload
    if action == "get-event":
        return "GET", f"/v1/event/{path_id(args.event_id)}/", None, None
    path = f"/v1/memories/{path_id(args.memory_id)}/"
    if action == "update-memory":
        return "PUT", path, project, {"text": args.text}
    return ("DELETE" if action == "delete-memory" else "GET"), path, project, None


def path_id(value):
    # Do not allow dot segments or slashes to change which resource is addressed.
    if value in (".", "..") or "/" in value or "\\" in value:
        raise ValueError("memory-id/event-id 不能包含路径分隔符或点路径")
    return urllib.parse.quote(value, safe="")


def parse_args(argv=None):
    args = build_parser().parse_args(argv)
    defaults = {
        "region": os.getenv("VOLCENGINE_REGION", "cn-beijing"), "env_file": None,
        "project_id": os.getenv("MEM0_PROJECT_ID"), "api_key_id": os.getenv("MEM0_API_KEY_ID"),
        "data_endpoint": os.getenv("MEM0_ENDPOINT"), "timeout": 60, "output": None,
    }
    for name, default in defaults.items():
        if not hasattr(args, name):
            setattr(args, name, default)
    return args


def run(args, client):
    if args.action == "list-projects":
        filters = json_argument(args.tag_filters, list) if args.tag_filters is not None else None
        if filters is not None and (len(filters) > 10 or any(
            not isinstance(f, dict) or not isinstance(f.get("Key"), str) or not f["Key"]
            or ("Values" in f and (not isinstance(f["Values"], list)
                                  or any(not isinstance(v, str) for v in f["Values"]))) for f in filters
        )):
            raise ValueError("tag-filters 最多 10 组，每组含 Key 字符串及可选 Values 字符串数组")
        return client.list_projects(args.page_number, args.page_size, args.resource_project,
                                    filters, args.all_pages, args.memory_project_name)
    if args.action in ("describe-project", "describe-api-key"):
        if not args.project_id:
            raise ValueError("该操作需要 --project-id")
        if args.action == "describe-project":
            return client.describe_project(args.project_id)
        if not args.api_key_id:
            raise ValueError("describe-api-key 需要 --api-key-id")
        return client.describe_api_key(args.project_id, args.api_key_id)
    method, path, query, payload = data_request(args)
    endpoint, key = client.data_connection(args.project_id, args.data_endpoint, args.api_key_id)
    return client.data(endpoint, key, method, path, query, payload)


def main(argv=None):
    args = parse_args(argv)
    client = None
    try:
        client = Mem0Client(args.region, load_credentials(args.env_file), args.timeout)
        print(f"[操作] {args.action}  [地域] {args.region}", file=sys.stderr)
        result = client.redact(run(args, client))
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        print(rendered, end="")
        if args.output:
            # Prevent symlink following and keep potentially private memories owner-only.
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
            try:
                with os.fdopen(os.open(args.output, flags, 0o600), "w", encoding="utf-8") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(rendered)
            except OSError as exc:
                raise Mem0Error(f"API 调用已完成，结果已输出到 stdout；保存文件失败，请勿重复写操作: {exc}") from None
        return 0
    except (Mem0Error, ValueError, OSError) as exc:
        message = str(exc)
        print(f"错误: {client.redact(message) if client else message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
