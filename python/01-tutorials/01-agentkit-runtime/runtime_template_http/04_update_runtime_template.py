"""调用 UpdateRuntimeTemplate 更新受管模板的描述或元数据。"""

from __future__ import annotations

from _common import (
    event,
    json_object,
    parser,
    poll_template,
    resolve_template_id,
    run,
    save_state,
    string_map,
    validate_template,
)
from _config import activate_control, load_config
from _http_client import RuntimeTemplateHttpClient


def main() -> None:
    config = load_config()
    args_parser = parser(
        "更新 RuntimeTemplate 的 Description/Meta",
        resource=True,
        mutation=True,
    )
    description = args_parser.add_mutually_exclusive_group()
    description.add_argument("--description", help="替换描述")
    description.add_argument(
        "--clear-description", action="store_true", help="清空描述"
    )
    meta = args_parser.add_mutually_exclusive_group()
    meta.add_argument("--meta", type=json_object, help="整体替换字符串元数据 JSON 对象")
    meta.add_argument("--clear-meta", action="store_true", help="清空元数据")
    args = args_parser.parse_args()
    activate_control(config, require_credentials=not args.dry_run)

    template_id = resolve_template_id(args.runtime_template_id, dry_run=args.dry_run)
    body = {
        "RuntimeTemplateId": template_id,
        "UpdateMask": [],
        "ClientToken": args.client_token,
    }
    if args.description is not None or args.clear_description:
        body["UpdateMask"].append("Description")
        body["Description"] = None if args.clear_description else args.description
    if args.meta is not None or args.clear_meta:
        body["UpdateMask"].append("Meta")
        body["Meta"] = None if args.clear_meta else string_map(args.meta, "Meta")
    if not body["UpdateMask"]:
        raise RuntimeError(
            "pass --description, --clear-description, --meta, or --clear-meta"
        )

    action = "UpdateRuntimeTemplate"
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
    save_state(response)
    if args.wait and response["Status"] != "Ready":
        response = poll_template(client, template_id, wanted="Ready")
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
