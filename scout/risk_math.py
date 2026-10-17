"""Risk math: buying-power Kelly sizing, timestamp-aligned correlation gate,
dynamic liquidity-vacuum exit. Stdlib only. Every guard fails closed:
bad or missing data means "don't size", "don't enter", or "exit".
"""
import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


def _finite(*xs) -> bool:
    return all(isinstance(x, (int, float)) and not isinstance(x, bool)
               and math.isfinite(x) for x in xs)


# ---------------------------------------------------------------- Kelly
def _wilson_lower(p: float, n: int, z: float) -> float:
    if z <= 0:
        return p
    denom = 1.0 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / denom)


def calculate_half_kelly_sizing(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    buying_power: float,
    *,
    n_trades: int,
    min_trades: int = 30,
    max_fraction_cap: float = 0.25,
    session_scale: float = 1.0,
    confidence_z: float = 1.0,
) -> float:
    """Dollar allocation = buying_power * min(half-Kelly, cap) * session_scale.

    avg_win / avg_loss are positive per-trade magnitudes, net of fees and
    slippage. The win rate is replaced by its Wilson lower bound (confidence_z)
    so small samples size down. Returns 0.0 on any invalid input.
    """
    if not _finite(win_rate, avg_win, avg_loss, buying_power,
                   max_fraction_cap, session_scale, confidence_z):
        return 0.0
    if isinstance(n_trades, bool) or not isinstance(n_trades, int):
        return 0.0
    if not (0.0 < win_rate < 1.0) or avg_win <= 0 or avg_loss <= 0 or buying_power <= 0:
        return 0.0
    if not (0.0 < max_fraction_cap <= 1.0) or not (0.0 < session_scale <= 1.0):
        return 0.0
    if n_trades < max(1, min_trades):
        return 0.0

    p = _wilson_lower(win_rate, n_trades, confidence_z)
    q = 1.0 - p
    b = avg_win / avg_loss
    full = (p * b - q) / b
    if full <= 0.0:
        return 0.0
    frac = min(full * 0.5, max_fraction_cap)
    return math.floor(buying_power * frac * session_scale * 100 + 1e-9) / 100


def kelly_shares(price: float, *args, **kwargs) -> int:
    """Whole shares for the Kelly dollar allocation; never exceeds buying power."""
    if not _finite(price) or price <= 0:
        return 0
    dollars = calculate_half_kelly_sizing(*args, **kwargs)
    shares = int(dollars // price)
    return shares if shares * price <= dollars else 0


# ---------------------------------------------------------- correlation
@dataclass(frozen=True)
class Decision:
    ok: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok


def _pearson(xs, ys) -> Optional[float]:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 1e-18 or syy <= 1e-18:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy)))


def check_correlation_exposure(
    proposed_symbol: str,
    proposed_side: str,
    current_positions: Mapping[str, float],
    returns: Mapping[str, Mapping[Any, float]],
    max_correlation_limit: float = 0.70,
    min_samples: int = 30,
) -> Decision:
    """returns[symbol] maps bar timestamp -> return. Series are aligned on
    common timestamps only. Direction-aware: a short against a correlated
    long is a hedge and passes. Missing, short, or constant data rejects.
    """
    if proposed_side not in ("BUY", "SELL"):
        return Decision(False, "bad_side")
    open_syms = [s for s, q in current_positions.items()
                 if _finite(q) and q != 0 and s != proposed_symbol]
    if any(not _finite(q) for q in current_positions.values()):
        return Decision(False, "bad_position_qty")
    if not open_syms:
        return Decision(True)
    prop = returns.get(proposed_symbol)
    if not prop:
        return Decision(False, f"no_returns:{proposed_symbol}")
    prop_sign = 1.0 if proposed_side == "BUY" else -1.0

    for sym in open_syms:
        other = returns.get(sym)
        if not other:
            return Decision(False, f"no_returns:{sym}")
        common = sorted(set(prop) & set(other))
        xs, ys = [], []
        for t in common:
            a, b = prop[t], other[t]
            if _finite(a, b):
                xs.append(a)
                ys.append(b)
        if len(xs) < min_samples:
            return Decision(False, f"insufficient_overlap:{sym}:{len(xs)}")
        corr = _pearson(xs, ys)
        if corr is None:
            return Decision(False, f"zero_variance:{sym}")
        pos_sign = 1.0 if current_positions[sym] > 0 else -1.0
        effective = corr * prop_sign * pos_sign
        if effective >= max_correlation_limit:
            return Decision(False, f"correlated:{sym}:{effective:.2f}")
    return Decision(True)


def evaluate_correlation_exposure_cap(*args, **kwargs) -> bool:
    return bool(check_correlation_exposure(*args, **kwargs))


# ------------------------------------------------------ liquidity vacuum
def detect_dynamic_liquidity_vacuum_exit(
    bid_depth_vol: float,
    ask_depth_vol: float,
    avg_minute_volume: float,
    spread_pct: float,      # FRACTION (0.02 = 2%), not percent
    atr_14: Optional[float],
    current_price: float,
    base_vacuum_ratio: float = 0.15,
    min_spread_cap: float = 0.02,
    max_spread_cap: float = 0.05,
) -> bool:
    """True = exit. Bad data, crossed book, no volume baseline, a spread above
    the ATR-scaled cap, or thin depth all say exit."""
    if not _finite(bid_depth_vol, ask_depth_vol, avg_minute_volume,
                   spread_pct, current_price):
        return True
    if current_price <= 0 or spread_pct < 0 or bid_depth_vol < 0 or ask_depth_vol < 0:
        return True
    if avg_minute_volume <= 0:
        return True

    if _finite(atr_14) and atr_14 > 0:
        vol_pct = atr_14 / current_price
    else:
        vol_pct = 0.01
    cap = min(max(min_spread_cap, vol_pct * 1.5), max_spread_cap)
    if spread_pct > cap:
        return True
    return (bid_depth_vol + ask_depth_vol) / avg_minute_volume < base_vacuum_ratio
