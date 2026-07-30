"""Tests for password strength, failed-attempt backoff, and the privacy mask."""

from __future__ import annotations

import pytest

from kosha import format as fmt
from kosha import security


# --- password strength -------------------------------------------------------

def test_empty_and_common_passwords_score_zero():
    assert security.password_strength("")[0] == 0
    assert security.password_strength("password")[0] == 0
    assert security.password_strength("Password")[0] == 0        # case-insensitive
    assert "commonly used" in security.password_strength("qwerty")[2]


def test_length_dominates_scoring():
    short = security.password_strength("Ab1!x")[0]
    passphrase = security.password_strength("correct horse battery staple")[0]
    assert passphrase > short
    assert passphrase == 4                       # long passphrase, no symbols needed


def test_short_password_cannot_reach_top_score():
    assert security.password_strength("Ab1!")[0] < 4


def test_repeated_characters_are_penalised():
    assert security.password_strength("aaaaaaaaaaaaaaaa")[0] <= 1


def test_strength_returns_label_and_hint():
    score, label, hint = security.password_strength("short")
    assert 0 <= score <= 4
    assert label and isinstance(label, str)
    assert str(security.MIN_PASSWORD_LEN) in hint         # tells you the minimum


# --- backoff ladder ----------------------------------------------------------

def test_first_few_attempts_are_free():
    assert security.delay_for_attempts(1) == 0
    assert security.delay_for_attempts(3) == 0


def test_delay_escalates_then_caps():
    d4 = security.delay_for_attempts(4)
    d5 = security.delay_for_attempts(5)
    assert 0 < d4 < d5
    assert security.delay_for_attempts(999) == security.delay_for_attempts(50)   # capped


# --- attempt tracker ---------------------------------------------------------

def test_tracker_counts_and_clears(tmp_path):
    t = security.AttemptTracker(tmp_path / "attempts.json")
    assert t.failures == 0 and t.seconds_remaining() == 0
    for _ in range(3):
        assert t.record_failure() == 0            # still free
    assert t.failures == 3
    assert t.record_failure() > 0                 # 4th triggers a delay
    assert t.seconds_remaining() > 0
    t.record_success()
    assert t.failures == 0 and t.seconds_remaining() == 0


def test_tracker_persists_across_instances(tmp_path):
    path = tmp_path / "attempts.json"
    t1 = security.AttemptTracker(path)
    for _ in range(5):
        t1.record_failure()
    # A fresh instance (i.e. app restarted) still sees the penalty.
    t2 = security.AttemptTracker(path)
    assert t2.failures == 5
    assert t2.seconds_remaining() > 0


def test_tracker_delay_expires_with_time(tmp_path):
    t = security.AttemptTracker(tmp_path / "attempts.json")
    for _ in range(4):
        t.record_failure(now=1000.0)
    required = security.delay_for_attempts(4)
    assert t.seconds_remaining(now=1000.0) == required
    assert t.seconds_remaining(now=1000.0 + required + 1) == 0     # waited it out


def test_tracker_tolerates_corrupt_file(tmp_path):
    path = tmp_path / "attempts.json"
    path.write_text("not json", encoding="utf-8")
    t = security.AttemptTracker(path)              # must not raise
    assert t.failures == 0


# --- privacy mask ------------------------------------------------------------

def test_mask_hides_all_amounts():
    try:
        assert fmt.format_inr(300000) == "3,00,000.00"
        fmt.set_masked(True)
        assert fmt.is_masked()
        assert fmt.format_inr(300000) == fmt.MASK
        assert fmt.format_inr_short(300000) == fmt.MASK
    finally:
        fmt.set_masked(False)
    assert fmt.format_inr(300000) == "3,00,000.00"   # restored


def test_mask_off_by_default():
    assert fmt.is_masked() is False
