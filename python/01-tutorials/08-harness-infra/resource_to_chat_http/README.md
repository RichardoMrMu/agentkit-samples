# Harness Infra：从资源创建到真实对话

本案例使用纯 HTTP 完成 Harness Infra 的完整接入流程：

1. 使用账号 AK/SK 调用控制面 TOP。
2. 创建或复用 Endpoint。
3. 创建 Cloud Environment，并等待自动 Sandbox 资源就绪。
4. 创建 Harness 和 RuntimeTemplate。
5. 创建一次性 Environment API Key。
6. 使用 Endpoint `BaseUrl` 和 Environment Key 创建 Session。
7. 发送用户消息，读取真实模型回复。
8. 删除 Session、Environment Key、RuntimeTemplate、EnvironmentResource 和
   Environment，归档 Harness。

Endpoint 是账号级共享入口，清理脚本默认保留。

## 文件

| 文件 | 用途 |
| --- | --- |
| `01_create_resources.py` | 创建控制面资源并等待就绪 |
| `02_chat.py` | 创建一次性环境 Key，完成真实对话，并立即删除 Session 与 Key |
| `03_cleanup.py` | 清理其余控制面资源，保留 Endpoint |
| `_clients.py` | TOP HMAC-SHA256 和数据面 REST Client |
| `_common.py` | 脱敏输出、状态文件和轮询 |
| `_workflow.py` | 确定性生命周期编排 |
| `tests/test_flow.py` | 无云资源的离线协议测试 |

## 控制面与数据面

| 分面 | 地址 | 认证 | 字段 |
| --- | --- | --- | --- |
| 控制面 | `https://open.volcengineapi.com/` | 账号 AK/SK，按目标 Service 和 Region 签名 | 固定字段 PascalCase；列表为 `Items/NextToken` |
| 数据面 | `ListEndpoints/CreateEndpoint` 返回的 `BaseUrl` | 首次 `CreateAPIKey` 返回的 Environment Key，通过 `x-api-key` 发送 | Session/Event REST 使用 snake_case |
| 模型 Key | 不由客户端传明文 | 创建 Session 时通过 `x-ark-key-name` 传方舟 API Key 的 **Name** | Runtime 按账号和 Name 查询唯一 Key |

账号 AK/SK、Environment Key 和方舟模型 Key 是三类不同凭据，不能互换。

## 前置条件

- 控制面账号已开通目标 Service。
- 账号拥有创建 Endpoint、Environment、Harness、RuntimeTemplate 和 API Key 的权限。
- 已存在一个方舟 API Key，并知道它的 **Name**。这里不是 Key 明文、SID 或内部 ID。
- 已存在 Runtime 执行角色。角色需要信任 Runtime 服务，并至少拥有运行时所需权限，
  包括 `ark:ListApiKeys` 和 `ark:GetRawApiKey`。
- 模型 ID 对该方舟 API Key 可用。

## 安装

使用 `uv`：

```bash
uv venv
uv pip install -r requirements.txt
source .venv/bin/activate
```

脚本不自动读取 `.env`。需要时由 shell 注入：

```bash
set -a
source /path/to/.env
set +a
```

## 配置

推荐使用以下专用变量：

```bash
export HARNESS_INFRA_ACCESS_KEY='<account-ak>'
export HARNESS_INFRA_SECRET_KEY='<account-sk>'
export HARNESS_INFRA_REGION='cn-beijing'
export HARNESS_INFRA_SERVICE='agentkit'
export HARNESS_INFRA_CONTROL_HOST='open.volcengineapi.com'

export HARNESS_INFRA_MODEL_ID='<available-model-id>'
export HARNESS_INFRA_MODEL_BASE_URL='https://ark.cn-beijing.volces.com/api/v3'
export HARNESS_INFRA_MODEL_API_KEY_NAME='<ark-api-key-name>'
export HARNESS_INFRA_RUNTIME_ROLE_NAME='<runtime-role-name>'
```

`HARNESS_INFRA_SERVICE` 必须使用目标环境正式发布的 ServiceCode，不能从响应中的
Service 标签推断。预发布环境的内部 ServiceCode 不写入开源案例。

控制面凭据也兼容 `VOLCENGINE_ACCESS_KEY`、`VOLCENGINE_SECRET_KEY`、
`VOLCENGINE_REGION` 和 `VOLCENGINE_AGENTKIT_SERVICE`。模型地址兼容
`ARK_BASE_URL`，Runtime Role 兼容 `AGENTKIT_RUNTIME_ROLE_NAME`。

使用独立状态文件可以避免覆盖其他测试：

```bash
export HARNESS_INFRA_STATE="/tmp/harness-infra-chat-$(date +%s).json"
```

状态文件权限为 `0600`，只保存资源 ID、名称、状态和幂等 token，不保存任何 Key
明文。

## 1. 创建资源

```bash
python 01_create_resources.py \
  --prefix harness-chat \
  --agent-loop adk
```

脚本依次执行：

- `ListEndpoints`，账号没有 Endpoint 时调用 `CreateEndpoint`。
- `CreateEnvironment` 创建 2000 milli CPU / 4096 MiB 的 Cloud Environment。
- `ListEnvironmentResources/GetEnvironmentResource` 等待自动资源达到
  `Ready`，并确认 `DesiredGeneration == ObservedGeneration`。
- `CreateHarness`，保存模型 ID、BaseUrl 和 AgentLoop。
- `CreateRuntimeTemplate`，等待状态从 `Building` 变为 `Ready`。

账号存在多个 Endpoint 时必须显式选择：

```bash
python 01_create_resources.py --endpoint-id ep_example
```

`--dry-run` 只显示计划，不访问网络或写状态。

## 2. 发起真实对话

默认问题是“计算 17 × 23，只回复结果。”，最终回复必须包含 `391`：

```bash
python 02_chat.py
```

自定义问题和验收文本：

```bash
python 02_chat.py \
  --prompt '只回复：HARNESS_CHAT_OK' \
  --expected-substring 'HARNESS_CHAT_OK'
```

脚本执行：

1. `CreateAPIKey` 创建当前 Environment 与 Endpoint 绑定的一次性访问 Key。
2. Key 明文只保存在当前 Python 进程内。
3. `POST /v1/sessions` 创建 Session，并通过 `x-ark-key-name` 绑定方舟 Key Name。
4. `POST /v1/sessions/{id}/events` 发送带固定事件 ID 的 `user.message`。
5. `GET /v1/sessions/{id}/events` 按 sequence 增量读取事件。
6. 收到本轮 `agent.message`，随后收到
   `session.status_idle` 且 `stop_reason.type=end_turn` 才判定成功。
7. 在退出前归档并删除 Session，再调用 `DeleteAPIKey` 撤销一次性 Key。

任意 `idle`、HTTP 200 或 Event 发送成功都不等于模型回复完成。

如果对话进程在创建 Key 后被强制终止，Key 明文无法恢复。应通过状态中的
`ApiKeyId` 删除旧 Key，重新运行一轮；不要用原 ClientToken 期待恢复明文。

## 3. 清理控制面资源

```bash
python 03_cleanup.py
```

清理顺序：

1. 删除 RuntimeTemplate，并等待 Get 返回 404。
2. 删除 Cloud Environment 自动创建的 EnvironmentResource，等待 `Deleted`。
3. 删除 Environment，并等待 Get 返回 404。
4. 归档 Harness。
5. 保留账号级 Endpoint。

脚本拒绝在 Session 仍活动时继续清理，避免遗留 Work 或物理资源。

## 常见停点

| 现象 | 处理 |
| --- | --- |
| `CreateEndpoint` 返回 409 | 运行 `ListEndpoints` 并复用已有 Endpoint；本案例已自动处理单 Endpoint 场景 |
| `CreateAPIKey` 报 `The number of the APIKey has exceeded the quota` | 提升 APIG 的 `api_key-per-consumer` 配额，或由资源所有者删除确认不再使用的 Key；不得删除其他环境的未知 Key |
| RuntimeTemplate 长时间 `Building` | 用状态文件中的原 ID 继续调用 `GetRuntimeTemplate`，不要换 token 重建 |
| `x-ark-key-name` 对应零条或多条 Ark Key | 使用当前账号、地域和项目内唯一的完整 Name，不使用明文、SID 或前缀 |
| Session 返回 `requires_action` | 当前基础案例不处理客户端工具确认；改用无工具 Harness，或实现对应确认/工具结果协议 |
| Environment 删除冲突 | 先删除 Session 和 EnvironmentResource，再删除 Environment |

## 输出与超时

- 默认输出中文摘要；`--json` 输出脱敏 JSON 事件。
- `HARNESS_INFRA_HTTP_TIMEOUT_SECONDS`：控制面单请求超时，默认 60 秒。
- `HARNESS_INFRA_DATA_TIMEOUT_SECONDS`：数据面单请求超时，默认 60 秒。
- `HARNESS_INFRA_WAIT_TIMEOUT_SECONDS`：异步资源轮询超时，默认 900 秒。
- `HARNESS_INFRA_POLL_INTERVAL_SECONDS`：控制面轮询间隔，默认 5 秒。
- `HARNESS_INFRA_HTTP_RETRIES`：429/503 自动重试次数，默认 2。

自动重试复用完全相同的请求体和幂等 token。手动重试时也必须保持原 token 和参数。

## 离线测试

```bash
uv run --with 'requests>=2.31,<3' \
  python -m unittest discover -s tests -v

uv run --with 'ruff==0.11.12' ruff check .
uv run --with 'ruff==0.11.12' ruff format --check .
```

离线测试使用本机 HTTP Server，不访问云资源，覆盖：

- TOP PascalCase 请求、签名和 `Result` 解包。
- Cloud 自动资源与 RuntimeTemplate 轮询。
- Endpoint 路径前缀、`x-api-key` 和 `x-ark-key-name`。
- `agent.message + end_turn` 完成判定。
- Session/Key 即时清理和控制面资源清理顺序。
- Environment Key 不进入 stdout、stderr 或状态文件。

## 契约依据

- Harness Infra API 使用指南：从创建资源到对话
- akx `docs/harness-infra/api/`
- akx `docs/harness-infra/control-data-plane.md`
- API Version：`2025-10-30`

文档契约、本地测试和真实发布是三个独立证据。目标入口是否发布对应 Action、默认镜像、
Runtime Role 和模型权限，必须通过实际环境验证。
