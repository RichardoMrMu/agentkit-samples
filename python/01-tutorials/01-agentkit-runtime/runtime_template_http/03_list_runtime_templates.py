"""调用 ListRuntimeTemplates，并演示 MaxResults/NextToken 分页。"""

from __future__ import annotations

from typing import Any

from _common import event, parser, positive_int, print_json, run
from _config import activate_control, load_config
from _http_client import RuntimeTemplateHttpClient


def _validate_page(value: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    items = value.get("Items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise RuntimeError("ListRuntimeTemplates Result.Items must be an array")
    token = value.get("NextToken") or ""
    if not isinstance(token, str):
        raise RuntimeError("ListRuntimeTemplates Result.NextToken must be a string")
    return items, token


def main() -> None:
    config = load_config()
    args_parser = parser("列出 RuntimeTemplate")
    args_parser.add_argument("--max-results", type=positive_int, default=20)
    args_parser.add_argument("--next-token", default="")
    args_parser.add_argument("--all", action="store_true", help="自动获取后续全部分页")
    args = args_parser.parse_args()
    activate_control(config, require_credentials=not args.dry_run)
    body: dict[str, Any] = {"MaxResults": args.max_results}
    if args.next_token:
        body["NextToken"] = args.next_token
    action = "ListRuntimeTemplates"
    event(
        action,
        "dry-run" if args.dry_run else "request",
        body,
        json_output=args.json_output,
    )
    if args.dry_run:
        return

    client = RuntimeTemplateHttpClient()
    collected: list[dict[str, Any]] = []
    seen_tokens = set()
    page_number = 0
    while True:
        page_number += 1
        response = client.call(action, body)
        items, next_token = _validate_page(response)
        collected.extend(items)
        if not args.json_output:
            print(f"[第 {page_number} 页] {len(items)} 条，累计 {len(collected)} 条")
            for item in items:
                print(
                    f"  {item.get('RuntimeTemplateId', '-')} | "
                    f"{item.get('Status', '-')} | {item.get('Name', '-')}"
                )
        if not args.all or not next_token:
            break
        if next_token in seen_tokens:
            raise RuntimeError("server returned a repeated NextToken")
        seen_tokens.add(next_token)
        body = {"MaxResults": args.max_results, "NextToken": next_token}

    result = {"Items": collected, "NextToken": next_token}
    if args.json_output:
        print_json(result)
    elif next_token:
        print("还有下一页；添加 --all，或把 NextToken 传给 --next-token。")


if __name__ == "__main__":
    run(main)
