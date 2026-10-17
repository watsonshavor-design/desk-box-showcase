"""BR-31 — per-seat sender identity for the Desk room post path.

Implemented by Ace (Oct 17 2026) to Flint's BR-31 package spec:
  - The room's post path historically validated ONE shared token
    (DESK_TOKEN) and hardcoded from="ace", so Flint's and Shavor's own
    posts all arrived stamped "ace".
  - This module maps a presented token to a seat by CONSTANT-TIME,
    EXACT match against per-seat token values read from the environment:
        DESK_TOKEN   -> "ace"     (legacy shared token, unchanged)
        FLINT_TOKEN  -> "flint"
        SHAVOR_TOKEN -> "shavor"
  - FAIL CLOSED: if two seat token VALUES are ever equal (misconfig),
    that value resolves to NO seat. A token that matches nothing
    resolves to None. Empty/missing/non-string tokens resolve to None.
  - Identity comes ONLY from the token. A "from"/"as" field supplied in
    a request body is never consulted here (and must never be, by any
    caller): body-supplied identity is how impersonation happens.
  - AceBot command authorization keys on from in {"shavor", "ace"}.
    The flint seat must therefore never resolve to "shavor" or "ace";
    the exact per-value mapping above is what guarantees that.

Token values are read from the environment at import time by default;
resolve_seat() also accepts an explicit mapping for tests.
"""

import hmac
import os

SEAT_TOKENS = {
    "ace": os.environ.get("DESK_TOKEN", ""),
    "flint": os.environ.get("FLINT_TOKEN", ""),
    "shavor": os.environ.get("SHAVOR_TOKEN", ""),
}


def _matches(got, want):
    if not isinstance(got, str) or not isinstance(want, str):
        return False
    if not got or not want:
        return False
    try:
        return hmac.compare_digest(got.encode(), want.encode())
    except Exception:
        return False


def resolve_seat(token, mapping=None):
    """Return the seat id for a presented token, or None.

    mapping: optional {seat: token_value} override (tests). Defaults to
    the environment-derived SEAT_TOKENS. Duplicate non-empty values in
    the mapping fail closed for the duplicated value.
    """
    if not isinstance(token, str) or not token:
        return None
    m = mapping if mapping is not None else SEAT_TOKENS
    values = [v for v in m.values() if isinstance(v, str) and v]
    if len(values) != len(set(values)):
        # Duplicated token value somewhere in the config: any token
        # equal to a duplicated value is ambiguous -> no seat.
        dupes = {v for v in values if values.count(v) > 1}
        if token in dupes:
            return None
    for seat, want in m.items():
        if _matches(token, want):
            return seat
    return None
