"""Tick-size rounding (SEC Rule 612): $0.01 at/above $1.00, $0.0001 below."""
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP

_MODES = {"down": ROUND_DOWN, "up": ROUND_UP, "nearest": ROUND_HALF_UP}


def tick_size(price) -> Decimal:
    p = Decimal(str(price))
    return Decimal("0.01") if p >= Decimal("1") else Decimal("0.0001")


def round_to_tick(price, mode: str = "nearest") -> float:
    if mode not in _MODES:
        raise ValueError(f"bad rounding mode: {mode}")
    if isinstance(price, bool) or price is None:
        raise ValueError("invalid price")
    p = Decimal(str(price))
    if not p.is_finite() or p <= 0:
        raise ValueError(f"invalid price: {price}")
    t = tick_size(p)
    q = (p / t).to_integral_value(rounding=_MODES[mode]) * t
    if q <= 0:
        raise ValueError(f"price rounds to zero: {price}")
    return float(q)
