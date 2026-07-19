"""Key derivation for Kosha's encrypted database.

The master password never touches disk. On first setup we generate a random
salt (stored beside the DB — a salt is not secret) and derive a 256-bit key
with Argon2id. That key is handed to SQLCipher as a *raw* key, so SQLCipher
performs no additional KDF pass and the Argon2 work factor is the only barrier.

The salt file also records the Argon2 parameters used, so the key can still be
reproduced if we tune the defaults for future databases.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

from argon2.low_level import Type, hash_secret_raw

KEY_BYTES = 32  # 256-bit key for AES-256
SALT_BYTES = 16


@dataclass(frozen=True)
class Argon2Params:
    """Argon2id cost parameters. Defaults target ~roughly a desktop unlock."""

    time_cost: int = 3
    memory_cost: int = 262144  # KiB == 256 MiB
    parallelism: int = 4

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(text: str) -> "Argon2Params":
        return Argon2Params(**json.loads(text))


def derive_key(password: str, salt: bytes, params: Argon2Params) -> bytes:
    """Derive a 32-byte key from ``password`` and ``salt`` using Argon2id."""
    if len(salt) != SALT_BYTES:
        raise ValueError(f"salt must be {SALT_BYTES} bytes, got {len(salt)}")
    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=params.time_cost,
        memory_cost=params.memory_cost,
        parallelism=params.parallelism,
        hash_len=KEY_BYTES,
        type=Type.ID,
    )


def key_to_sqlcipher_pragma(key: bytes) -> str:
    """Format a raw key as a SQLCipher ``PRAGMA key`` blob literal.

    A 64-char hex string in ``x'...'`` form tells SQLCipher to use the bytes
    directly as the encryption key with no further derivation.
    """
    return f"\"x'{key.hex()}'\""


# --- salt file persistence ---------------------------------------------------

def new_salt() -> bytes:
    return secrets.token_bytes(SALT_BYTES)


def write_salt_file(path: Path, salt: bytes, params: Argon2Params) -> None:
    """Persist salt + Argon2 params atomically."""
    payload = {"salt": salt.hex(), "params": json.loads(params.to_json())}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def read_salt_file(path: Path) -> tuple[bytes, Argon2Params]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    salt = bytes.fromhex(payload["salt"])
    params = Argon2Params(**payload["params"])
    return salt, params
