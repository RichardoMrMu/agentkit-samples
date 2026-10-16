"""调用 GetRuntimeTemplate 查询模板并可等待稳定状态。"""

from __future__ import annotations

from _common import (
    event,
    parser,
    poll_template,
    resolve_template_id,
    run,
    save_state,
    validate_template,
)
from _config import activate_control, load_config
from _http_client import RuntimeTemplateHttpClient


def main() -> None:
    config = load_config()
    args_parser = parser("查询 RuntimeTemplate", resource=True)
    args_parser.add_argument(
        "--wait",
        choices=("Ready", "Failed"),
        help="持续轮询到指定状态",
    )
    args = args_parser.parse_args()
    activate_control(config, require_credentials=not args.dry_run)
    template_id = resolve_template_id(args.runtime_template_id, dry_run=args.dry_run)
    body = {"RuntimeTemplateId": template_id}
    action = "GetRuntimeTemplate"
    event(
        action,
        "dry-run" if args.dry_run else "request",
        body,
        json_output=args.json_output,
    )
    if args.dry_run:
        return
    client = RuntimeTemplateHttpClient()
    response = validate_template(client.call(action, body))
    if args.wait and response["Status"] != args.wait:
        response = poll_template(client, template_id, wanted=args.wait)
    save_state(response)
    event(
        action,
        "response",
        body,
        response=response,
        json_output=args.json_output,
    )


if __name__ == "__main__":
    run(main)
