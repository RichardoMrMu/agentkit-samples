#!/usr/bin/env python3
"""清理 RuntimeTemplate、EnvironmentResource、Environment 并归档 Harness。"""

from _clients import TopClient
from _common import emit, load_state, parser, run
from _workflow import cleanup_resources


def main() -> None:
    cli = parser(__doc__)
    args = cli.parse_args()
    if args.dry_run:
        emit(
            "dry_run",
            "控制面清理计划",
            json_output=args.json_output,
            actions=[
                "检查 Session / 清理残留 API Key",
                "DeleteRuntimeTemplate / 等待删除完成",
                "DeleteEnvironmentResource / 等待 Deleted",
                "DeleteEnvironment / 等待删除完成",
                "ArchiveHarness",
                "retain Endpoint",
            ],
            status="dry_run（未查询资源，名称和 ID 未获取）",
        )
        return
    client = TopClient()
    state = load_state(client)
    cleanup_resources(client, state, json_output=args.json_output)


if __name__ == "__main__":
    run(main)
