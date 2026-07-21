"""Feature engineering derived from a statement narration.

Two derivations run on every imported transaction:

    * **txn_type**        — UPI / NEFT / IMPS / CARD / ATM / SI / OTHER
    * **merchant_keyword** — the payee/merchant name pulled out of the narration

The heuristics target Indian retail-bank narration conventions (HDFC-style, but
the UPI/NEFT/IMPS prefixes are near-universal across Indian banks). A parser may
override these if an institution needs special handling.

Everything here is pure and re-derivable: given the immutable raw_description we
can always recompute these, so enrichment never has to be trusted as canonical.
"""

from __future__ import annotations

import hashlib
import re

# Canonical set, matching the transactions.txn_type column.
UPI = "UPI"
NEFT = "NEFT"
IMPS = "IMPS"
CARD = "CARD"
ATM = "ATM"
SI = "SI"
OTHER = "OTHER"

# Which hyphen-separated segment carries the payee name, per type. The narration
# is split on '-'; segment 0 is the type/prefix token.
_MERCHANT_SEGMENT = {
    UPI: 1,
    NEFT: 2,
    IMPS: 2,
    SI: 1,
    OTHER: 1,
}

_ATM_PREFIXES = ("ATW", "NWD", "EAW", "CWDR", "ATM")
_CARD_PREFIXES = ("POS", "VPS", "ECOM", "CARD")

# Trailing UPI remark noise we don't want as a merchant name.
_NOISE_SEGMENTS = {
    "PAID VIA CRED", "PAID VIA CRED AND", "UPI", "PAYMENT ON CRED",
    "PAYMENT FROM PHONE", "COLLECT", "NA",
}


def derive_txn_type(narration: str) -> str:
    """Classify a narration into one of the canonical transaction types."""
    n = narration.strip().upper()
    if n.startswith("UPILITE") or n.startswith("UPI"):
        return UPI
    if n.startswith("NEFT"):
        return NEFT
    if n.startswith("IMPS"):
        return IMPS
    if n.startswith("ACH"):
        return SI                       # ACH mandate / auto-debit
    if any(re.match(rf"^{p}\b", n) for p in _ATM_PREFIXES):
        return ATM
    if any(re.match(rf"^{p}\b", n) for p in _CARD_PREFIXES):
        return CARD
    return OTHER


def normalize_keyword(text: str) -> str:
    """Uppercase, collapse whitespace, trim edge punctuation.

    The canonical form for both extracted merchant keywords and user-entered
    rule keywords, so a rule matches the transactions the user saw it on.
    """
    text = re.sub(r"\s+", " ", text).strip().upper()
    return text.strip(" .,-/@")


# Internal alias kept for the existing call sites in this module.
_normalize = normalize_keyword


def derive_merchant_keyword(narration: str, txn_type: str) -> str:
    """Extract a normalized merchant/payee keyword.

    Always returns a non-empty keyword so every transaction is groupable — the
    descriptive part of the narration where possible, else a sensible fallback
    (first real word, or the transaction type). Trailing transaction-specific
    reference/account tokens (e.g. ``DP0160 92120635RK8ND (REF# ST26…)``) are
    dropped so recurring payees like ``BBPY CC PAYMENT`` collapse to one keyword
    instead of a unique keyword per transaction.
    """
    # CARD / ATM narrations are space-delimited (e.g. "POS <card#> AMAZON"),
    # not hyphen-delimited like the transfer types.
    if txn_type in (CARD, ATM) and "-" not in narration:
        return _keyword_from_tokens(narration) or _fallback_keyword(narration, txn_type)

    parts = [p.strip() for p in narration.split("-")]
    idx = _MERCHANT_SEGMENT.get(txn_type, 1)

    candidate = ""
    if idx < len(parts):
        candidate = _normalize(parts[idx])

    # If the chosen segment is empty or pure noise, walk forward for a better one.
    if not candidate or candidate in _NOISE_SEGMENTS:
        for seg in parts[1:]:
            norm = _normalize(seg)
            if norm and norm not in _NOISE_SEGMENTS and not _looks_like_ref(norm):
                candidate = norm
                break

    # Last resort: fall back to whitespace tokens (single-segment narrations).
    if not candidate:
        candidate = _keyword_from_tokens(narration) or ""

    candidate = _trim_ref_tail(candidate)
    return candidate or _fallback_keyword(narration, txn_type)


# Noise tokens common in card descriptions that aren't part of the merchant.
_TOKEN_NOISE = {"WWW"}


def _keyword_from_tokens(narration: str) -> str | None:
    """Merchant name from a space-delimited narration, dropping prefixes/refs.

    Handles card descriptions too: payment-gateway prefixes like ``PYU*Swiggy``
    or ``RAZ*Tijori`` are stripped to the merchant after the ``*``. Collection
    stops at the first reference-like token *once a name has started*, so a
    trailing ref tail is dropped while a leading card-number (``POS <num> NAME``)
    is skipped over.
    """
    skip_prefixes = _ATM_PREFIXES + _CARD_PREFIXES
    kept: list[str] = []
    for tok in _normalize(narration).split():
        if "*" in tok:                       # gateway prefix, e.g. PYU*SWIGGY
            tok = tok.split("*", 1)[1]
        if not tok or tok in skip_prefixes or tok in _TOKEN_NOISE:
            continue
        if _is_ref_token(tok) or "XXXX" in tok:
            if kept:
                break                        # ref after the name → tail, stop
            continue                         # ref before the name → skip it
        kept.append(tok)
    return " ".join(kept) or None


def _trim_ref_tail(text: str) -> str:
    """Drop a trailing run of reference/account tokens from an extracted name."""
    kept: list[str] = []
    for tok in text.split():
        if _is_ref_token(tok) or "XXXX" in tok:
            if kept:
                break
            continue
        kept.append(tok)
    return " ".join(kept)


def _fallback_keyword(narration: str, txn_type: str) -> str:
    """Guaranteed non-empty keyword: first real word, else the txn type."""
    for tok in _normalize(narration).split():
        if "*" in tok:
            tok = tok.split("*", 1)[1]
        if tok.isalpha() and len(tok) >= 2 and tok not in _TOKEN_NOISE:
            return tok
    return txn_type or "MISC"


def _is_ref_token(tok: str) -> bool:
    """True for a transaction-specific reference/account/id token (not a name)."""
    if _looks_like_ref(tok):                    # @, IFSC, long pure-numeric
        return True
    if "#" in tok:                              # '(REF#...' etc.
        return True
    has_digit = any(c.isdigit() for c in tok)
    if has_digit and any(c.isalpha() for c in tok) and len(tok) >= 5:
        return True                             # coded alphanumeric, e.g. DP0160
    if tok.isdigit() and len(tok) >= 4:         # account/ref number (keep short years)
        return True
    return False


def _looks_like_ref(segment: str) -> bool:
    """True for reference-number / VPA / IFSC-ish segments (not a merchant)."""
    if "@" in segment:                          # UPI VPA, e.g. FOO@YBL
        return True
    if re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6,}", segment):   # IFSC
        return True
    if re.fullmatch(r"[0-9]{6,}", segment):     # long numeric ref
        return True
    return False


def normalize_for_dedup(raw_description: str) -> str:
    """Stable, whitespace-insensitive form of a description for dedup hashing."""
    return re.sub(r"\s+", " ", raw_description).strip().upper()


def dedup_hash(txn_date, amount: float, direction: str, raw_description: str, account_id: int) -> str:
    """hash(date + amount + normalized description + account) per the README."""
    payload = "|".join([
        txn_date.isoformat(),
        f"{amount:.2f}",
        direction,
        normalize_for_dedup(raw_description),
        str(account_id),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
