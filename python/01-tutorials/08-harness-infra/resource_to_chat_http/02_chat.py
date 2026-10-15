#!/usr/bin/env python3
"""创建一次性环境 Key，发起真实模型对话并回收 Session 与 Key。"""

from _clients import TopClient
from _common import emit, load_state, parser, required_setting, resource_info, run
from _workflow import create_key_and_chat

DEFAULT_PROMPT = "计算 17 × 23，只回复结果。"


def main() -> None:
    cli = parser(__doc__)
    cli.add_argument(
        "--ark-api-key-name",
        help="方舟 API Key 的 Name；默认读取 HARNESS_INFRA_MODEL_API_KEY_NAME",
    )
    cli.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="发送给 Harness 的真实用户消息",
    )
    cli.add_argument(
        "--expected-substring",
        default="391",
        help="最终回复必须包含的文本；传空字符串可关闭校验",
    )
    args = cli.parse_args()
    ark_api_key_name = args.ark_api_key_name or required_setting(
        "HARNESS_INFRA_MODEL_API_KEY_NAME",
        dry_run=args.dry_run,
    )
    if args.dry_run:
        emit(
            "dry_run",
            "真实对话计划",
            json_output=args.json_output,
            actions=[
                "CreateAPIKey",
                "POST /v1/sessions",
                "POST /v1/sessions/{id}/events",
                "GET /v1/sessions/{id}/events",
                "archive/delete Session",
                "DeleteAPIKey",
            ],
            ark_api_key_name=ark_api_key_name,
            prompt=args.prompt,
            status="dry_run（未查询或创建资源，名称和 ID 未获取）",
        )
        return

    client = TopClient()
    state = load_state(client)
    answer = create_key_and_chat(
        client,
        state,
        ark_api_key_name=ark_api_key_name,
        prompt=args.prompt,
        json_output=args.json_output,
    )
    if args.expected_substring and args.expected_substring not in answer:
        raise RuntimeError(
            "real conversation completed, but the final answer did not contain "
            f"{args.expected_substring!r}"
        )
    emit(
        "verified",
        "真实模型回复校验通过"
        if args.expected_substring
        else "真实对话完成（未启用回复内容校验）",
        json_output=args.json_output,
        step=6,
        **{
            **resource_info(state, "session"),
            "parameters": {"expected_substring": args.expected_substring or "未启用"},
            "result": "回复校验通过"
            if args.expected_substring
            else "对话完成，未启用内容校验",
        },
    )


if __name__ == "__main__":
    run(main)
