"""调用 DeleteRuntimeTemplate，并可用 Get 404 验证删除结果。"""

from __future__ import annotations

from _common import (
    clear_state,
    event,
    parser,
    poll_template,
    resolve_template_id,
    run,
)
from _config import activate_control, load_config
from _http_client import RuntimeTemplateHttpClient


def main() -> None:
    config = load_config()
    args_parser = parser("删除 RuntimeTemplate", resource=True, mutation=True)
    args_parser.add_argument("--reason", required=True, help="删除原因")
    args = args_parser.parse_args()
    activate_control(config, require_credentials=not args.dry_run)
    template_id = resolve_template_id(args.runtime_template_id, dry_run=args.dry_run)
    if not args.reason.strip():
        raise RuntimeError("--reason must not be empty")
    body = {
        "RuntimeTemplateId": template_id,
        "Reason": args.reason,
        "ClientToken": args.client_token,
    }
    action = "DeleteRuntimeTemplate"
    event(
        action,
        "dry-run" if args.dry_run else "request",
        body,
        json_output=args.json_output,
    )
    if args.dry_run:
        return

    client = RuntimeTemplateHttpClient()
    response = client.call(action, body)
    if response != {"RuntimeTemplateId": template_id}:
        raise RuntimeError(
            "DeleteRuntimeTemplate Result must contain only RuntimeTemplateId"
        )
    if args.wait:
        poll_template(client, template_id, wanted="Deleted", deleting=True)
    clear_state(template_id)
    event(
        action,
        "response",
        body,
        response=response,
        json_output=args.json_output,
    )


if __name__ == "__main__":
    run(main)
