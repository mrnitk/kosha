"""Phase-1 command line for Kosha.

Enough of an interface to set up the master password, unlock the vault, and
inspect its state before the GUI exists. Run with:

    python -m kosha.cli status
    python -m kosha.cli init
    python -m kosha.cli unlock
    python -m kosha.cli change-password
"""

from __future__ import annotations

import argparse
import getpass
import sys

from . import config
from .db import Database, WrongPasswordError

MIN_PASSWORD_LEN = 8
MAX_UNLOCK_ATTEMPTS = 3


def _prompt_new_password() -> str:
    while True:
        pw = getpass.getpass("Choose a master password: ")
        if len(pw) < MIN_PASSWORD_LEN:
            print(f"  Too short — need at least {MIN_PASSWORD_LEN} characters.")
            continue
        confirm = getpass.getpass("Confirm master password: ")
        if pw != confirm:
            print("  Passwords did not match. Try again.")
            continue
        return pw


def cmd_status(_args: argparse.Namespace) -> int:
    db = Database()
    print(f"Data directory: {config.data_dir()}")
    print(f"Database:       {config.db_path()}")
    print(f"Initialised:    {'yes' if db.exists else 'no'}")
    return 0


def cmd_init(_args: argparse.Namespace) -> int:
    db = Database()
    if db.exists:
        print("A Kosha database already exists. Use 'unlock' or 'change-password'.")
        return 1
    print("Setting up a new encrypted Kosha vault.")
    print("This password cannot be recovered — there is no cloud, no reset.\n")
    pw = _prompt_new_password()
    print("Deriving key (Argon2id, this takes a moment)...")
    db.create(pw)
    db.lock()
    print(f"\nVault created at {config.db_path()}")
    return 0


def _unlock_interactive(db: Database) -> bool:
    for attempt in range(1, MAX_UNLOCK_ATTEMPTS + 1):
        pw = getpass.getpass("Master password: ")
        try:
            db.unlock(pw)
            return True
        except WrongPasswordError:
            left = MAX_UNLOCK_ATTEMPTS - attempt
            msg = f"  Incorrect password. {left} attempt(s) left." if left else "  Incorrect password."
            print(msg)
    return False


def cmd_unlock(_args: argparse.Namespace) -> int:
    db = Database()
    if not db.exists:
        print("No vault yet. Run 'init' first.")
        return 1
    if not _unlock_interactive(db):
        print("Unlock failed.")
        return 1
    row = db.connection.execute("SELECT count(*) FROM transactions").fetchone()
    print(f"Unlocked. {row[0]} transaction(s) stored.")
    db.lock()
    return 0


def cmd_change_password(_args: argparse.Namespace) -> int:
    db = Database()
    if not db.exists:
        print("No vault yet. Run 'init' first.")
        return 1
    print("Unlock with your current password first.")
    if not _unlock_interactive(db):
        print("Unlock failed.")
        return 1
    old = getpass.getpass("Re-enter current password: ")
    print("Now choose a new password.")
    new = _prompt_new_password()
    try:
        db.change_password(old, new)
    except WrongPasswordError:
        print("Current password did not match. No change made.")
        db.lock()
        return 1
    db.lock()
    print("Master password changed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kosha", description="Kosha encrypted vault (Phase 1 CLI)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show vault location and state").set_defaults(func=cmd_status)
    sub.add_parser("init", help="create the encrypted vault").set_defaults(func=cmd_init)
    sub.add_parser("unlock", help="verify the master password").set_defaults(func=cmd_unlock)
    sub.add_parser("change-password", help="change the master password").set_defaults(func=cmd_change_password)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
