#!/usr/bin/env python3
"""创建 Endpoint、Cloud Environment、Harness 和 RuntimeTemplate。"""

import re

from _clients import TopClient
from _common import emit, new_state, parser, required_setting, run, state_path
from _workflow import create_resources


def main() -> None:
    cli = parser(__doc__)
    cli.add_argument(
        "--prefix",
        default="harness-chat",
        help="本轮资源名称前缀，默认 harness-chat",
    )
    cli.add_argument(
        "--endpoint-id",
        default="",
        help="账号存在多个 Endpoint 时显式选择",
    )
    cli.add_argument(
        "--model-id",
        help="模型 ID；默认读取 HARNESS_INFRA_MODEL_ID",
    )
    cli.add_argument(
        "--model-base-url",
        help="模型 BaseUrl；默认读取 HARNESS_INFRA_MODEL_BASE_URL 或 ARK_BASE_URL",
    )
    cli.add_argument(
        "--runtime-role-name",
        help="Runtime 执行角色；默认读取 HARNESS_INFRA_RUNTIME_ROLE_NAME",
    )
    cli.add_argument(
        "--agent-loop",
        choices=("adk", "codex", "piagent"),
        default="adk",
        help="Harness AgentLoop，默认 adk",
    )
    args = cli.parse_args()

    prefix = args.prefix.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", prefix):
        cli.error("--prefix must match [a-z0-9][a-z0-9-]{0,39}")
    model_id = args.model_id or required_setting(
        "HARNESS_INFRA_MODEL_ID", dry_run=args.dry_run
    )
    model_base_url = args.model_base_url or required_setting(
        "HARNESS_INFRA_MODEL_BASE_URL",
        "ARK_BASE_URL",
        dry_run=args.dry_run,
    )
    role_name = args.runtime_role_name or required_setting(
        "HARNESS_INFRA_RUNTIME_ROLE_NAME",
        "AGENTKIT_RUNTIME_ROLE_NAME",
        dry_run=args.dry_run,
    )
    if args.dry_run:
        emit(
            "dry_run",
            "控制面创建计划",
            json_output=args.json_output,
            actions=[
                "ListEndpoints/CreateEndpoint",
                "CreateEnvironment/GetEnvironment",
                "ListEnvironmentResources/GetEnvironmentResource",
                "CreateHarness",
                "CreateRuntimeTemplate/GetRuntimeTemplate",
            ],
            model_id=model_id,
            model_base_url=model_base_url,
            role_name=role_name,
            agent_loop=args.agent_loop,
            status="dry_run（未创建资源，名称和 ID 尚未生成）",
        )
        return

    client = TopClient()
    state = new_state(
        client,
        prefix=prefix,
        model_id=model_id,
        model_base_url=model_base_url,
        role_name=role_name,
        agent_loop=args.agent_loop,
    )
    emit(
        "state",
        "已初始化独立状态",
        json_output=args.json_output,
        step=1,
        id=state["run_id"],
        parameters={"state_file": str(state_path()), **state["config"]},
        status=state["status"],
    )
    create_resources(
        client,
        state,
        endpoint_id=args.endpoint_id.strip(),
        json_output=args.json_output,
    )


if __name__ == "__main__":
    run(main)
