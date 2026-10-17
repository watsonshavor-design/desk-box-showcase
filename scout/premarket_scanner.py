"""Pre-market gap scanner. Fail-closed: a row with bad, stale, mock or
missing data is rejected (with a counted reason), never guessed at.

Snapshot row keys: code, last_price, prev_close, premarket_volume, plus
bid, ask, quote_ts (timezone-aware datetime) for the quality filters.
"""
import logging
import math
import os
import random
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)  # no basicConfig here: the app configures logging


class MockFeedForbidden(RuntimeError):
    pass


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _aware(x) -> bool:
    return isinstance(x, datetime) and x.tzinfo is not None


class PremarketScanner:
    def __init__(
        self,
        min_gap_pct: float = 5.0,
        min_volume: int = 5000,
        min_price: float = 0.50,
        max_price: float = 20.00,
        *,
        max_gap_pct: float = 100.0,        # bigger = likely split/bad print
        min_dollar_volume: float = 25_000.0,
        max_spread_pct: float = 3.0,       # percent of mid
        max_quote_age_s: Optional[float] = 10.0,  # None disables the check
        require_book: bool = True,
        allow_mock: bool = False,
        top_n: Optional[int] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        if not (0 < min_price <= max_price):
            raise ValueError("need 0 < min_price <= max_price")
        if not (0 <= min_gap_pct <= max_gap_pct):
            raise ValueError("need 0 <= min_gap_pct <= max_gap_pct")
        if min_volume < 0 or min_dollar_volume < 0 or max_spread_pct <= 0:
            raise ValueError("bad volume or spread limits")
        if top_n is not None and top_n < 1:
            raise ValueError("top_n must be >= 1")
        self.min_gap_pct, self.max_gap_pct = min_gap_pct, max_gap_pct
        self.min_volume, self.min_dollar_volume = min_volume, min_dollar_volume
        self.min_price, self.max_price = min_price, max_price
        self.max_spread_pct, self.max_quote_age_s = max_spread_pct, max_quote_age_s
        self.require_book, self.allow_mock, self.top_n = require_book, allow_mock, top_n
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    def _evaluate(self, item) -> Tuple[Optional[Dict[str, Any]], str]:
        if not isinstance(item, dict):
            return None, "bad_item"
        sym = item.get("code")
        if not isinstance(sym, str) or not sym.strip():
            return None, "bad_symbol"
        if item.get("mock") and not self.allow_mock:
            return None, "mock_not_allowed"
        last, prev, vol = item.get("last_price"), item.get("prev_close"), item.get("premarket_volume")
        if not (_num(last) and _num(prev) and _num(vol)):
            return None, "bad_number"
        if last <= 0 or prev <= 0 or vol < 0:
            return None, "bad_value"
        if not (self.min_price <= last <= self.max_price):
            return None, "price_out_of_range"
        if vol < self.min_volume:
            return None, "low_volume"
        dollar_vol = last * vol
        if dollar_vol < self.min_dollar_volume:
            return None, "low_dollar_volume"
        gap = (last - prev) / prev * 100.0
        if gap < self.min_gap_pct:
            return None, "gap_too_small"
        if gap > self.max_gap_pct:
            return None, "gap_implausible"

        if self.max_quote_age_s is not None:
            ts = item.get("quote_ts")
            if not _aware(ts):
                return None, "no_quote_ts"
            age = (self.clock() - ts).total_seconds()
            if age > self.max_quote_age_s or age < -5.0:
                return None, "stale_quote"

        spread = None
        bid, ask = item.get("bid"), item.get("ask")
        if self.require_book or bid is not None or ask is not None:
            if not (_num(bid) and _num(ask)) or not (0 < bid <= ask):
                return None, "bad_book"
            spread = (ask - bid) / ((ask + bid) / 2.0) * 100.0
            if spread > self.max_spread_pct:
                return None, "wide_spread"

        return {
            "symbol": sym.strip().upper(),
            "last_price": last,
            "prev_close": prev,
            "gap_pct": round(gap, 2),
            "volume": vol,
            "dollar_volume": round(dollar_vol, 2),
            "spread_pct": None if spread is None else round(spread, 3),
        }, ""

    def scan_with_reasons(self, snapshot: Sequence[Dict[str, Any]]):
        cands: List[Dict[str, Any]] = []
        rejected: Counter = Counter()
        seen = set()
        for item in snapshot:
            c, why = self._evaluate(item)
            if c is not None:
                if c["symbol"] in seen:
                    c, why = None, "duplicate_symbol"
                else:
                    seen.add(c["symbol"])
            if c is None:
                rejected[why] += 1
            else:
                cands.append(c)
        cands.sort(key=lambda c: (-c["gap_pct"], -c["dollar_volume"], c["symbol"]))
        if self.top_n:
            cands = cands[: self.top_n]
        if rejected:
            log.debug("scanner rejected: %s", dict(rejected))
        return cands, rejected

    def scan_candidates(self, market_snapshot: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self.scan_with_reasons(market_snapshot)[0]


# ----------------------------------------------------------------------
def generate_mock_premarket_feed(
    symbols: Sequence[str],
    *,
    seed: Optional[int] = None,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Synthetic rows for paper-pipeline tests. Refuses to run unless
    PAPER_TRADING=true, and every row is tagged mock=True so a scanner
    only accepts it when built with allow_mock=True."""
    if os.environ.get("PAPER_TRADING", "").strip().lower() != "true":
        raise MockFeedForbidden("mock feed requires PAPER_TRADING=true")
    rng = random.Random(seed)
    now = now or datetime.now(timezone.utc)
    rows = []
    for sym in symbols:
        prev = round(rng.uniform(1.0, 10.0), 2)
        last = round(prev * rng.choice([0.95, 1.01, 1.06, 1.12]), 2)
        half = max(0.01, round(last * rng.uniform(0.002, 0.01), 2))
        rows.append({
            "code": sym,
            "last_price": last,
            "prev_close": prev,
            "premarket_volume": rng.randint(2000, 150000),
            "bid": round(last - half, 2),
            "ask": round(last + half, 2),
            "quote_ts": now,
            "mock": True,
        })
    return rows
