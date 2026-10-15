#!/usr/bin/env python3
"""Delete an EnvironmentResource, then optionally wait for its tombstone."""

from _common import parser, resource_id, run, submit


def main() -> None:
    cli = parser(__doc__, resource=True, mutation=True)
    args = cli.parse_args()
    body = {
        "resource_id": resource_id(args),
        "client_token": args.client_token,
    }
    submit("DeleteEnvironmentResource", body, args, wanted="deleted")


if __name__ == "__main__":
    run(main)
