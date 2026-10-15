# EnvironmentResource 契约与 PPE 验证记录

本文记录 2026-10-14 按 akx 最新公开契约完成的样例改造与验证结果。

## 契约依据

| 项目 | 值 |
| --- | --- |
| akx 仓库 HEAD | `82d149c` |
| API 文档 | `docs/harness-infra/api/environment-resource-api.md` |
| Action Version | `2025-10-30` |
| 样例入口 | Volcengine/BytePlus TOP 或账号级 API Key 直连 |

本次以公开 API 文档中的 TOP 请求和响应定义为准。主要变化如下：

| 领域 | 当前契约 |
| --- | --- |
| 请求字段 | 递归 PascalCase；UpdateMask 每段 PascalCase |
| 响应 | 解包 `Result`；资源字段归一化为 snake_case |
| Create | `Name` 必填；移除 ResourceMode 和 Sandbox.Profile |
| Resource | 只公开 ID、名称、状态、Revision、代次、Tool/Runtime ID、时间、Config、ErrorCode |
| List | `MaxResults/NextToken`；响应 `Items/NextToken` |
| Update | 支持 Sandbox 字段、`Runtime.Resources` 和 `RoleName` |
| Delete | 仅 `ResourceId/ClientToken`；结果仅 `ResourceId` |
| 完成判定 | 状态终态、无 ErrorCode；ready 还要求期望/观察代次一致 |

`EnvVars` 和未定义结构的 `Sandbox.Networking` 属于调用方字典，键名在 wire
映射中保持不变。已定义对象，例如 Resources 和 AgentKit.Networking，按公开字段名
转换。

## 实现范围

- `_http_client.py`
  - 所有传输统一构建公开 PascalCase 请求。
  - 支持 TOP `ResponseMetadata + Result` 和成功结果校验。
  - List 严格校验 `Items`、资源 ID/状态和 `NextToken`。
  - Delete 将 ID-only 结果映射成本地 `deleting`，随后通过 Get 轮询。
- `_common.py`
  - 状态文件不再保存 operation 信息。
  - 失败以 `failed/delete_failed/ErrorCode` 判断。
  - `ready` 要求代次收敛，`deleted` 直接视为删除完成。
  - Update 与 Delete 的资源解析分离，Delete 不依赖 Revision。
- 五个脚本
  - Create 增加名称默认值、Sandbox Resources/Networking 和 Runtime Resources。
  - List 改为 `--max-results/--next-token`。
  - Update 增加 Runtime Resources 和 RoleName。
  - Delete 移除 `--expected-revision`。

## 离线验证

执行命令：

```bash
uv run --with 'requests>=2.31,<3' \
  python -m unittest discover -s tests -v
```

结果：16 项测试全部通过。覆盖：

- Create/Get/List/Update/Delete 最新 wire 字段。
- PascalCase 枚举和 UpdateMask。
- `Items/NextToken` 分页及重复游标保护。
- EnvVars 和任意 Networking 字典键名保留。
- ID-only Delete 后继续 Get 轮询。
- generation/ErrorCode 完成判定。
- Volcengine/BytePlus 签名、STS、请求摘要。
- IAM 显式角色校验。
- 503 原请求体重试、错误诊断、状态 endpoint 隔离。
- dry-run、输入校验、敏感值脱敏。

## PPE 完整生命周期

| 项目 | 值 |
| --- | --- |
| 日期 | 2026-10-14 |
| Host | `open.volcengineapi.com` |
| Service | `agentkit_ppe` |
| Region | `cn-beijing` |
| Target | Ark |
| 资源 ID | `er_db7fgdc1snms5d2gmte0` |
| 初始名称 | `sandbox-http-latest-1014113403` |
| 最终状态 | `deleted` |
| 证据目录 | `/tmp/environment-resource-sample-ppe-20261014-113403` |

使用独立状态文件和每步独立 ClientToken 完成以下流程：

| 步骤 | 结果 |
| --- | --- |
| Create | `creating` → `ready`，Revision 1 |
| Get | ready，Tool/Runtime ID 均存在 |
| List | Ark + EnvironmentId + ready + MaxResults=1 返回本轮资源 |
| 元数据 Update | Name/Description 成功，Revision 2，组件 ID 不变 |
| Sandbox EnvVars Update | `updating` → `ready`，Revision 3，新 Tool/Runtime ID |
| Delete | ID-only 响应被接受，`deleting` → `deleted`，Revision 4 |
| 清理检查 | 活动 List 为 0；deleted + IncludeDeleted 可查询本轮墓碑 |

创建复用了已有 Runtime IAM Role。PPE 实测没有发送 `Runtime.Resources`，因为当前
部署可能仍返回 `InvalidRuntimeProfile`；该字段的最新 wire 映射由离线测试覆盖。

本轮使用默认 Tool/Runtime `0.0.2` 镜像。Environment Key、AK/SK、请求体和
EnvVars 值均未写入状态文件。最终状态文件只保留资源 ID、Revision 4、deleted、
代次和 endpoint。

## 验证边界

本轮在线验证覆盖 Volcengine PPE、Ark 目标、已有 IAM Role 复用和默认镜像。以下
能力未在本轮制造真实云端场景：

- Runtime Resources 在线调整。
- IAM 新角色创建和策略绑定。
- AgentKit 目标、BytePlus 和直连部署。
- ResourceInUse、RevisionConflict、失败回滚及并发更新。
- Session/Work、Tool 实际调用和模型推理。
