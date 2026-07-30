"""Indian-style number formatting.

The Indian numbering system groups the integer part as 2-3 digits: the last
three digits, then every two digits before that (thousand, lakh, crore). Python's
``{:,}`` only does 3-digit (Western) grouping, so we do it by hand.

    format_inr(300000)     -> '3,00,000.00'
    format_inr(12345678)   -> '1,23,45,678.00'
    format_inr_short(...)  -> compact '₹3.0L' / '₹1.2Cr' for chart axes.
"""

from __future__ import annotations

LAKH = 100_000
CRORE = 10_000_000

#: Placeholder shown for every amount while privacy mask is on.
MASK = "••••••"

# Privacy mask: a single app-wide switch so one toggle hides every figure on
# screen (shoulder-surfing protection). Charts read the same flag, so masked
# amounts never leak through tooltips either.
_masked = False


def set_masked(on: bool) -> None:
    """Turn the app-wide privacy mask on/off."""
    global _masked
    _masked = bool(on)


def is_masked() -> bool:
    return _masked


def group_indian(int_digits: str) -> str:
    """Insert Indian-style commas into a string of integer digits (no sign)."""
    if len(int_digits) <= 3:
        return int_digits
    last3 = int_digits[-3:]
    rest = int_digits[:-3]
    parts = []
    while len(rest) > 2:
        parts.insert(0, rest[-2:])
        rest = rest[:-2]
    if rest:
        parts.insert(0, rest)
    return ",".join(parts) + "," + last3


def format_inr(value: float, decimals: int = 2) -> str:
    """Format ``value`` with Indian digit grouping, e.g. '3,00,000.00'.

    Returns :data:`MASK` while the privacy mask is on.
    """
    if _masked:
        return MASK
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    neg = num < 0
    s = f"{abs(num):.{decimals}f}"
    int_part, _, frac = s.partition(".")
    grouped = group_indian(int_part)
    out = grouped + (f".{frac}" if decimals else "")
    return ("-" if neg else "") + out


def format_inr_short(value: float, symbol: str = "₹") -> str:
    """Compact form for chart ticks: ₹3.0L, ₹1.2Cr, ₹12.3K, ₹850."""
    if _masked:
        return MASK
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    neg = num < 0
    n = abs(num)
    if n >= CRORE:
        body = f"{n / CRORE:.1f}Cr"
    elif n >= LAKH:
        body = f"{n / LAKH:.1f}L"
    elif n >= 1_000:
        body = f"{n / 1_000:.1f}K"
    else:
        body = f"{n:.0f}"
    return ("-" if neg else "") + symbol + body
