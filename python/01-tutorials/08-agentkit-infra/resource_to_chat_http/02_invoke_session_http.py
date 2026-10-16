#!/usr/bin/env python3
"""从 YAML 读取 Session，通过 HTTP 调用 Event 数据面。"""

from _common import emit, parser, run
from _config import validated_config
from _workflow import invoke_session


def main() -> None:
    cli = parser(__doc__)
    cli.add_argument(
        "--prompt",
        help="发送给 Harness 的用户消息；默认读取 config.yaml",
    )
    cli.add_argument(
        "--expected-substring",
        help="最终回复必须包含的文本；默认读取 config.yaml",
    )
    args = cli.parse_args()
    config = validated_config(
        require_model_key_name=False,
        require_credentials=False,
    )
    prompt = args.prompt if args.prompt is not None else config.session.prompt
    expected_substring = (
        args.expected_substring
        if args.expected_substring is not None
        else config.session.expected_substring
    )
    if args.dry_run:
        emit(
            "dry_run",
            "真实对话计划",
            json_output=args.json_output,
            actions=[
                "读取 config.yaml 中的 Session 和 Environment API Key",
                "POST /v1/sessions/{id}/events",
                "GET /v1/sessions/{id}/events",
            ],
            session_id=config.session.id or "<config.yaml:session.id>",
            prompt=prompt,
            status="dry_run（未访问数据面）",
        )
        return

    invoke_session(
        endpoint_base_url=config.session.endpoint_base_url,
        environment_api_key=config.environment.env_api_key_value,
        session_id=config.session.id,
        prompt=prompt,
        expected_substring=expected_substring,
        verify_sandbox_tools=(
            config.session.verify_sandbox_tools and prompt == config.session.prompt
        ),
        json_output=args.json_output,
    )


if __name__ == "__main__":
    run(main)
