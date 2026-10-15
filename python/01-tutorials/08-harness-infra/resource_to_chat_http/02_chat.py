#!/usr/bin/env python3
"""创建一次性环境 Key，发起真实模型对话并回收 Session 与 Key。"""

from _clients import TopClient
from _common import emit, load_state, parser, required_setting, run
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
        "真实模型回复校验通过",
        json_output=args.json_output,
        expected_substring=args.expected_substring or None,
    )


if __name__ == "__main__":
    run(main)
