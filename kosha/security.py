"""Security helpers: password strength scoring and failed-attempt backoff.

Kept free of Qt so it can be unit-tested directly.

* :func:`password_strength` — a 0-4 score plus a human hint, weighted towards
  *length* (which is what actually resists cracking) rather than character-class
  gimmicks.
* :class:`AttemptTracker` — escalating delay after wrong master passwords. The
  state is persisted next to the vault, so quitting and relaunching doesn't reset
  the penalty for someone guessing.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

MIN_PASSWORD_LEN = 10

#: Wrong attempts before a delay kicks in, and the delay ladder (seconds).
_FREE_ATTEMPTS = 3
_DELAY_LADDER = (5, 15, 30, 60, 120, 300)

_COMMON = {
    "password", "passw0rd", "123456", "12345678", "qwerty", "abc123", "iloveyou",
    "admin", "welcome", "letmein", "monkey", "dragon", "football", "kosha",
}


def password_strength(password: str) -> tuple[int, str, str]:
    """Return ``(score 0-4, label, hint)`` for ``password``.

    Length dominates: a long passphrase scores well without symbols, while a
    short password can't reach the top score however exotic its characters.
    """
    pw = password or ""
    if not pw:
        return 0, "Empty", f"use at least {MIN_PASSWORD_LEN} characters"
    if pw.lower() in _COMMON:
        return 0, "Very weak", "this is a commonly used password"

    length = len(pw)
    classes = sum([
        any(c.islower() for c in pw),
        any(c.isupper() for c in pw),
        any(c.isdigit() for c in pw),
        any(not c.isalnum() for c in pw),
    ])

    score = 0
    if length >= 8:
        score += 1
    if length >= 12:
        score += 1
    if length >= 16:
        score += 1
    # The 4th point: either a genuinely long passphrase, or good length plus a
    # mix of character types. Length alone is enough — a 20+ character phrase
    # resists cracking better than a short password with symbols sprinkled in.
    if length >= 20 or (classes >= 3 and length >= 12):
        score += 1
    if len(set(pw)) <= 3:                 # 'aaaaaaaaaa' — long but trivial
        score = min(score, 1)
    score = max(0, min(4, score))

    labels = {0: "Very weak", 1: "Weak", 2: "Fair", 3: "Good", 4: "Strong"}
    hint = ""
    if length < MIN_PASSWORD_LEN:
        hint = f"use at least {MIN_PASSWORD_LEN} characters"
    elif score < 3:
        hint = "longer is stronger — try a passphrase of a few words"
    elif score < 4:
        hint = "add length or another character type for full strength"
    return score, labels[score], hint


def delay_for_attempts(failures: int) -> int:
    """Seconds to wait before the next attempt after ``failures`` wrong tries."""
    if failures <= _FREE_ATTEMPTS:
        return 0
    index = min(failures - _FREE_ATTEMPTS - 1, len(_DELAY_LADDER) - 1)
    return _DELAY_LADDER[index]


class AttemptTracker:
    """Persisted count of failed unlock attempts, with an escalating delay.

    The state file sits beside the vault. It holds no secrets — only a counter and
    the timestamp of the last failure — so it's safe in plain JSON.
    """

    def __init__(self, path):
        self._path = Path(path)
        self._failures = 0
        self._last_failure = 0.0
        self._load()

    # --- state ---------------------------------------------------------------

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._failures = int(data.get("failures", 0))
            self._last_failure = float(data.get("last_failure", 0.0))
        except (OSError, ValueError):
            self._failures, self._last_failure = 0, 0.0

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"failures": self._failures, "last_failure": self._last_failure}),
                encoding="utf-8")
        except OSError:
            pass          # never block unlocking because the counter can't be written

    # --- api -----------------------------------------------------------------

    @property
    def failures(self) -> int:
        return self._failures

    def record_failure(self, now: Optional[float] = None) -> int:
        """Count a wrong password. Returns the delay (seconds) now required."""
        self._failures += 1
        self._last_failure = time.time() if now is None else now
        self._save()
        return delay_for_attempts(self._failures)

    def record_success(self) -> None:
        """Clear the penalty after a correct password."""
        self._failures, self._last_failure = 0, 0.0
        self._save()

    def seconds_remaining(self, now: Optional[float] = None) -> int:
        """How long the user must still wait before another attempt is allowed."""
        required = delay_for_attempts(self._failures)
        if not required:
            return 0
        now = time.time() if now is None else now
        elapsed = now - self._last_failure
        return max(0, int(round(required - elapsed)))
