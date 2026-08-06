from __future__ import annotations

import argparse
import getpass
import json
import sys

from .auth_store import (
    delete_user,
    ensure_auth_schema,
    list_users,
    revoke_tokens,
    set_table_patterns,
    set_user_enabled,
    upsert_user,
)
from .security import ensure_secret_file


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="SO Data API account administration")
    sub = p.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="initialize auth database and JWT secret")

    add = sub.add_parser("user-add", help="create or update an API user")
    add.add_argument("username")
    add.add_argument("--full-name")
    add.add_argument("--max-rows", type=int, default=5000)
    add.add_argument("--password-stdin", action="store_true")

    grant = sub.add_parser("grant", help="replace user's table permissions")
    grant.add_argument("username")
    grant.add_argument(
        "--tables",
        required=True,
        help='comma-separated glob patterns, e.g. "*" or "provider_*,install_redpack_v"',
    )

    for name in ("enable", "disable", "revoke"):
        cmd = sub.add_parser(name)
        cmd.add_argument("username")
    delete = sub.add_parser("user-delete")
    delete.add_argument("username")

    passwd = sub.add_parser("passwd")
    passwd.add_argument("username")
    passwd.add_argument("--password-stdin", action="store_true")

    sub.add_parser("user-list")
    return p


def read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("Password (minimum 12 characters): ")
    second = getpass.getpass("Confirm password: ")
    if first != second:
        raise ValueError("passwords do not match")
    return first


def main() -> int:
    args = parser().parse_args()
    ensure_auth_schema()
    if args.command == "init":
        path = ensure_secret_file()
        print(f"initialized; secret={path}")
        return 0
    if args.command == "user-add":
        upsert_user(
            args.username,
            read_password(args.password_stdin),
            full_name=args.full_name,
            max_rows=args.max_rows,
        )
        print(f"user saved: {args.username}")
        return 0
    if args.command == "grant":
        patterns = [p.strip() for p in args.tables.split(",")]
        count = set_table_patterns(args.username, patterns)
        print(f"permissions replaced: {args.username} ({count} patterns)")
        return 0
    if args.command == "enable":
        if not set_user_enabled(args.username, True):
            raise ValueError("unknown user")
        print(f"enabled: {args.username}")
        return 0
    if args.command == "disable":
        if not set_user_enabled(args.username, False):
            raise ValueError("unknown user")
        print(f"disabled and tokens revoked: {args.username}")
        return 0
    if args.command == "revoke":
        if not revoke_tokens(args.username):
            raise ValueError("unknown user")
        print(f"tokens revoked: {args.username}")
        return 0
    if args.command == "user-delete":
        if not delete_user(args.username):
            raise ValueError("unknown user")
        print(f"user deleted: {args.username}")
        return 0
    if args.command == "passwd":
        user = next((u for u in list_users() if u["username"] == args.username), None)
        if not user:
            raise ValueError("unknown user")
        upsert_user(
            args.username,
            read_password(args.password_stdin),
            full_name=user["full_name"],
            max_rows=user["max_rows"],
            enabled=bool(user["enabled"]),
        )
        print(f"password updated and tokens revoked: {args.username}")
        return 0
    if args.command == "user-list":
        print(json.dumps(list_users(), ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
