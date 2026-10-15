#!/usr/bin/env python3
"""List resources with filters and optional traversal of NextToken cursors."""

from _common import (
    EnvironmentResourceHttpClient,
    emit,
    parser,
    positive_int,
    preview,
    run,
)


def main() -> None:
    cli = parser(__doc__)
    cli.add_argument("--target-type", choices=("ark", "agentkit"))
    cli.add_argument("--environment-id")
    cli.add_argument(
        "--status",
        choices=(
            "creating",
            "ready",
            "updating",
            "deleting",
            "failed",
            "delete_failed",
            "deleted",
        ),
    )
    cli.add_argument("--include-deleted", action="store_true")
    cli.add_argument(
        "--max-results",
        type=positive_int,
        help="Page size, 1-100 (server default: 100)",
    )
    cli.add_argument(
        "--next-token", help="Opaque NextToken value from the previous response"
    )
    cli.add_argument(
        "--all", action="store_true", help="Follow NextToken until exhausted"
    )
    args = cli.parse_args()
    if args.max_results is not None and args.max_results > 100:
        cli.error("--max-results must be between 1 and 100")
    body = {
        key: getattr(args, key)
        for key in (
            "target_type",
            "environment_id",
            "status",
            "max_results",
            "next_token",
        )
        if getattr(args, key) is not None
    }
    if args.include_deleted:
        body["include_deleted"] = True
    preview(
        "ListEnvironmentResources", body, args.dry_run, json_output=args.json_output
    )
    if args.dry_run:
        return
    client = EnvironmentResourceHttpClient()
    seen = {args.next_token} if args.next_token else set()
    page_number = 0
    total = 0
    while True:
        response = client.list_environment_resources(body)
        if not isinstance(response.get("items"), list):
            raise RuntimeError("ListEnvironmentResources response is missing Items[]")
        page_number += 1
        total += len(response["items"])
        emit(
            {
                "phase": "page",
                "response": response,
                "page_number": page_number,
                "total": total,
                "all_pages": args.all,
            },
            json_output=args.json_output,
        )
        token = response.get("next_token")
        if not args.all or not token:
            return
        if not isinstance(token, str) or token in seen:
            raise RuntimeError(
                "ListEnvironmentResources returned an invalid or repeated NextToken"
            )
        seen.add(token)
        body["next_token"] = token


if __name__ == "__main__":
    run(main)
