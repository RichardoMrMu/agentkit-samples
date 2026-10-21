# Sandbox Session 生命周期 MCP 示例

五个独立 Python 脚本，通过标准 MCP Client / Streamable HTTP 调用已有 Sandbox 服务。
每个脚本只调用对应的业务 tool；MCP 初始化、工具发现和 schema 校验由公共客户端完成。

[English](README_en.md)

| 脚本 | MCP tool | 行为 |
| --- | --- | --- |
| `01_create_session.py` | `createSession` | 创建默认 8 小时 Session，保存 SessionId，输出已受理。 |
| `02_list_sessions.py` | `listSessions` | 分页列出 Session，不写状态。 |
| `03_get_session.py` | `getSession` | 查询实例；支持 `--wait ready` / `--wait deleted`，同步当前跟踪实例的本地状态。 |
| `04_invoke_tool.py` | `invokeTool` | 在已有 Session 中执行 RunCode，检查代码执行失败。 |
| `05_delete_session.py` | `deleteSession` | 删除当前或指定实例，输出已受理；用脚本 03 确认完成。 |

## 安装与配置

需要 Python 3.11+。在仓库根目录执行：

```bash
cd python/01-tutorials/04-agentkit-tools/sandbox_lifecycle_mcp
uv venv --python 3.12
uv pip install -r requirements.txt
# 尚无本机配置时执行；已有 config.yaml 则直接使用。
cp config.yaml.example config.yaml
chmod 600 config.yaml
```

模板按鉴权方式分别保存 MCP 地址和参数：

```yaml
mcp:
  mode: keyauth # keyauth / oauth
  keyauth:
    url: "https://your-keyauth-gateway.example.com/mcp"
    api_key: "<your-api-key>"
  oauth:
    volcengine_access_key: "<your-access-key>"
    volcengine_secret_key: "<your-secret-key>"
    url: "https://your-oauth-gateway.example.com/mcp"
    user_pool_uid: "<your-user-pool-uid>"
    client_uid: "" # 可选；多个 M2M Client 时指定
    host: open.volcengineapi.com
    api_version: "2025-10-30"
```

URL 必须包含网关提供的完整 Streamable HTTP 路径。
**`mcp.mode` 必填，选择默认鉴权方式；`--auth` 可覆盖本次选择，同时切换对应的地址和凭据。**
某一组缺少 URL 时会明确报错，不会借用另一组地址或凭据。

### Key Auth

填写 `mcp.keyauth.api_key`，使用不含 Bearer 前缀的原始 Key；也可用 `MCP_API_KEY` 覆盖。
发送 `Authorization: Bearer <API Key>`，不需要 AK/SK。可先通过只读列表脚本检查连接：

```bash
uv run --active python 02_list_sessions.py --auth keyauth
```

### OAuth

填写 `mcp.oauth.user_pool_uid`。`client_uid` 可选：留空时通过 `ListUserPoolClients` 查询并使用
唯一的 `MACHINE_TO_MACHINE` Client；没有 M2M Client 时提示创建，有多个时要求显式指定。
显式填写 `client_uid` 时直接查询详情，无需列表查询权限。自动选择需要 `id:ListUserPoolClients` 权限。
账号凭据填写到 `mcp.oauth.volcengine_access_key` 和 `mcp.oauth.volcengine_secret_key`。
`VOLCENGINE_ACCESS_KEY`、`VOLCENGINE_SECRET_KEY` 环境变量仍可覆盖配置；临时凭据还需
`VOLCENGINE_SESSION_TOKEN` 或 `mcp.oauth.volcengine_session_token`。
签名地域使用 `VOLCENGINE_REGION`，未设置时默认 `cn-beijing`；也可配置 `mcp.oauth.region`。

OAuth 鉴权流程：
Identity `GetUserPool` / `GetUserPoolClient`（签名 service=id）→
GET TokenUrl（`grant_type=client_credentials`、HTTP Basic）→ Bearer access token。
网关需要接受该用户池签发的 JWT。ClientSecret 和 token 查询后仅保存在进程内存中。

```bash
uv run --active python 02_list_sessions.py --auth oauth --state-file .sandbox_mcp_oauth_state.json
```

高级场景可以在 `mcp.oauth` 下填写 `token_url/client_id/client_secret`，直接换 token，无需 AK/SK。
已有 token 可用 `--auth bearer` + `MCP_TOKEN`，连接 `mcp.oauth.url`。

## 运行顺序

以下示例设置 `mcp.mode: keyauth`，使用默认状态文件：

```bash
uv run --active python 01_create_session.py
uv run --active python 02_list_sessions.py
uv run --active python 03_get_session.py --wait ready
uv run --active python 04_invoke_tool.py --code 'print(sum([1, 2, 3]))'
uv run --active python 05_delete_session.py
uv run --active python 03_get_session.py --wait deleted
```

OAuth 使用独立状态文件，以保留另一套网关的实例记录：

```bash
uv run --active python 01_create_session.py --auth oauth --state-file .sandbox_mcp_oauth_state.json
uv run --active python 03_get_session.py --auth oauth --state-file .sandbox_mcp_oauth_state.json --wait ready
uv run --active python 04_invoke_tool.py --auth oauth --state-file .sandbox_mcp_oauth_state.json
uv run --active python 05_delete_session.py --auth oauth --state-file .sandbox_mcp_oauth_state.json
uv run --active python 03_get_session.py --auth oauth --state-file .sandbox_mcp_oauth_state.json --wait deleted
```

创建和删除不隐式调用其他业务 tool，API 已受理不代表异步操作完成。
脚本 03 仅把明确的 Deleted 状态或资源不存在业务错误视为删除完成；鉴权错误不算。
脚本 04 使用 JSON 字符串形式的 OperationPayload，需要支持 Jupyter 的镜像和默认 `python3` 内核。
模板省略 `sandbox` 时沿用内置默认值；需要时可增加 `sandbox.ttl_seconds`、`code`、
`kernel_name`、`invoke_timeout_seconds`，或使用脚本 04 的 `--code`。

## 状态、环境变量与兼容性

每次业务工具调用分别输出青色 **请求 REQUEST** 和绿色 **返回 RESPONSE** 面板，错误返回用红色标记。
JSON 缩进并高亮，内嵌 JSON 字符串仅在展示时展开；实际请求类型不变。凭据和签名链接脱敏。
`--json` 输出无颜色的 NDJSON，包含 `request` / `response` 事件，保留内嵌 JSON 的字符串类型。

所有脚本支持 `--help`、`--json`、`--dry-run`、`--config`、`--state-file` 和 `--auth`。
Dry-run 不访问网络、不获取凭据、不写状态。脚本 03/04/05 的 `--session-id` 默认读取状态；
脚本 01 可用 `--user-session-id` 指定逻辑会话 ID。

- `--config` 或 `SANDBOX_MCP_CONFIG` 选择 YAML，默认是脚本目录的 `config.yaml`。
- `--state-file` 优先于环境变量和 YAML；相对路径以配置文件目录为基准。
- `MCP_URL` 是显式的全局 URL 覆盖，会覆盖当前选中的组。同时使用两套 YAML 地址时请不要设置它。
- `MCP_API_KEY`、`MCP_TOKEN` 和上述 AK/SK/地域变量分别覆盖对应配置。
- 其余字段可通过 `SANDBOX_MCP_<大写字段路径，点替换为下划线>` 覆盖。
- 新模板按已绑定 Tool 的网关使用，不继承其他项目的 `AGENTKIT_TOOL_ID`。
  若某个服务要求 ToolId，可在相应组增加 `mcp.keyauth.tool_id` 或 `mcp.oauth.tool_id`。
- 旧的 `mcp.url`、`auth`、`identity`、`volcengine` 布局仍兼容；其中 `auth.mode: m2m` 等价于 OAuth。
  旧布局仍遵循原有 `AGENTKIT_TOOL_ID` 环境变量规则。

状态原子写入，权限 `0600`，保存作用域、实例 ID 和时间，不保存凭据或完整响应。
切换网关或 Tool 使用独立状态文件；存在未清理记录时脚本 01 拒绝覆盖。
查询其他实例不覆盖已有记录；修改其他实例需配合独立状态文件。
默认配置和状态文件已忽略提交，自定义路径需自行加入忽略规则。

请求发送前记录 `*_requested`，失败或超时不自动重试 mutation、不自动删除实例。
创建响应丢失时，用脚本 02 的 UserSessionId 对照状态找回实例，再查询并清理。

## 本地验证

```bash
uv pip install pytest ruff
uv run --active python -m pytest -q tests
uv run --active ruff check .
uv run --active ruff format --check .
```

测试覆盖新旧配置、鉴权和地址的关联选择、跨网关状态隔离、五个脚本的协议调用及脱敏。
2026-10-21 已使用两套网关分别验证 Key Auth 和 OAuth 的创建、列表、就绪查询、代码执行、删除及删除确认。
测试实例均已清理；另已验证 `client_uid` 留空时的 OAuth 自动发现及列表调用。
