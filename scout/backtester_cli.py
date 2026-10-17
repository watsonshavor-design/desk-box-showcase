#!/usr/bin/env python3
"""Realistic bar-based backtester (long-only, exact Decimal accounting).

Bars CSV : time,symbol,open,high,low,close[,volume]   (ISO-8601 times; naive = UTC)
Signals  : JSON list of {time, symbol, direction, price[, qty][, order_type]}

Rules
  * T+1 execution: a signal fills on the first bar of its symbol with
    time STRICTLY AFTER the signal time. No later bar -> rejected.
  * Market orders: slippage (bps, adverse) on the target price, then adverse
    tick rounding, then clamp to the bar's [low, high].
  * Limit orders (default): fill only if the bar trades THROUGH the limit
    (buy: low < limit, sell: high > limit). Fill at the limit, or at the open
    if the bar gapped through it. No slippage (a limit can't fill worse).
    Limits are tick-rounded passively first (buys down, sells up), as the gate does.
  * Cash, fees and equity are Decimal, in exact cents.
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal, InvalidOperation
from typing import Dict, List, Optional

CENT = Decimal("0.01")
BPS = Decimal("10000")
ONE = Decimal(1)
ZERO = Decimal(0)


class BacktestError(ValueError):
    pass


# ------------------------------------------------------------ helpers
def _dec(x, name) -> Decimal:
    try:
        d = Decimal(str(x).strip())
    except (InvalidOperation, AttributeError):
        raise BacktestError(f"bad number for {name}: {x!r}")
    if not d.is_finite():
        raise BacktestError(f"non-finite number for {name}: {x!r}")
    return d


def _ts(s) -> datetime:
    try:
        dt = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    except ValueError:
        raise BacktestError(f"bad timestamp: {s!r}")
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def cents(x: Decimal) -> Decimal:
    return x.quantize(CENT, ROUND_HALF_UP)


def round_tick(p: Decimal, rounding) -> Decimal:
    t = Decimal("0.01") if p >= 1 else Decimal("0.0001")
    return (p / t).to_integral_value(rounding) * t


# ------------------------------------------------------------ data
@dataclass(frozen=True)
class Bar:
    time: datetime
    symbol: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True)
class Signal:
    time: datetime
    symbol: str
    direction: str            # BUY | SELL
    price: Optional[Decimal]
    qty: Optional[int]
    order_type: str           # limit | market


@dataclass
class Config:
    cash: Decimal
    slippage_bps: Decimal = ZERO
    fee_per_trade: Decimal = ZERO
    fee_bps: Decimal = ZERO
    default_qty: int = 100
    limit_ttl_bars: int = 1


def load_bars(path) -> List[Bar]:
    bars, seen = [], set()
    with open(path, newline="") as f:
        rd = csv.DictReader(f)
        need = {"time", "symbol", "open", "high", "low", "close"}
        if not rd.fieldnames or not need <= {c.strip().lower() for c in rd.fieldnames}:
            raise BacktestError(f"bars CSV needs columns {sorted(need)}")
        for i, row in enumerate(rd, start=2):
            row = {k.strip().lower(): v for k, v in row.items()}
            b = Bar(_ts(row["time"]), row["symbol"].strip(),
                    *(_dec(row[k], f"{k} (line {i})") for k in ("open", "high", "low", "close")))
            if min(b.open, b.high, b.low, b.close) <= 0 or b.low > b.high \
                    or b.low > min(b.open, b.close) or b.high < max(b.open, b.close):
                raise BacktestError(f"inconsistent OHLC at line {i}")
            if (b.symbol, b.time) in seen:
                raise BacktestError(f"duplicate bar {b.symbol} {b.time} (line {i})")
            seen.add((b.symbol, b.time))
            bars.append(b)
    if not bars:
        raise BacktestError("no bars")
    return bars


def load_signals(path, default_type="limit") -> List[Signal]:
    with open(path) as f:
        raw = json.loads(f.read(), parse_float=Decimal)
    if not isinstance(raw, list):
        raise BacktestError("signals JSON must be a list")
    out = []
    for i, r in enumerate(raw):
        try:
            d = str(r["direction"]).upper()
            if d not in ("BUY", "SELL"):
                raise BacktestError(f"signal {i}: bad direction")
            ot = str(r.get("order_type", default_type)).lower()
            if ot not in ("limit", "market"):
                raise BacktestError(f"signal {i}: bad order_type")
            price = _dec(r["price"], f"signal {i} price") if r.get("price") is not None else None
            if price is not None and price <= 0:
                raise BacktestError(f"signal {i}: price must be > 0")
            if ot == "limit" and price is None:
                raise BacktestError(f"signal {i}: limit order needs a price")
            qty = r.get("qty")
            if qty is not None and (isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0):
                raise BacktestError(f"signal {i}: qty must be a positive integer")
            out.append(Signal(_ts(r["time"]), str(r["symbol"]).strip(), d, price, qty, ot))
        except KeyError as e:
            raise BacktestError(f"signal {i}: missing {e}")
    return out


# ------------------------------------------------------------ engine
@dataclass
class _Active:
    sig: Signal
    seen: int = 0


def _try_fill(sig: Signal, bar: Bar, cfg: Config) -> Optional[Decimal]:
    buy = sig.direction == "BUY"
    if sig.order_type == "limit":
        lim = round_tick(sig.price, ROUND_DOWN if buy else ROUND_UP)
        if buy:
            return min(lim, bar.open) if bar.low < lim else None
        return max(lim, bar.open) if bar.high > lim else None
    target = sig.price if sig.price is not None else bar.open
    mult = ONE + cfg.slippage_bps / BPS if buy else ONE - cfg.slippage_bps / BPS
    px = round_tick(target * mult, ROUND_UP if buy else ROUND_DOWN)
    px = min(px, bar.high) if buy else max(px, bar.low)       # slippage, THEN clamp
    return min(max(px, bar.low), bar.high)                    # never outside the bar


def run_backtest(bars: List[Bar], signals: List[Signal], cfg: Config) -> Dict:
    bars = sorted(bars, key=lambda b: (b.time, b.symbol))
    by_sym = defaultdict(list)
    for s in sorted(signals, key=lambda s: s.time):
        by_sym[s.symbol].append(s)
    ptr = defaultdict(int)
    active: Dict[str, List[_Active]] = defaultdict(list)
    cash = cfg.cash
    pos: Dict[str, int] = {}
    basis: Dict[str, Decimal] = {}
    last: Dict[str, Decimal] = {}
    fills, rejects, curve = [], [], [(None, cfg.cash)]
    fees_total, wins, losses = ZERO, 0, 0

    def reject(sig, reason, t=None):
        rejects.append({"signal_time": sig.time.isoformat(), "symbol": sig.symbol,
                        "direction": sig.direction, "reason": reason})

    def fee_for(notional):
        return cents(cfg.fee_per_trade + notional * cfg.fee_bps / BPS)

    for bar in bars:
        s = bar.symbol
        lst = by_sym[s]
        while ptr[s] < len(lst) and lst[ptr[s]].time < bar.time:
            active[s].append(_Active(lst[ptr[s]]))
            ptr[s] += 1
        keep = []
        for a in active[s]:
            sig = a.sig
            a.seen += 1
            px = _try_fill(sig, bar, cfg)
            if px is not None and px.as_tuple().exponent > -2:
                px = px.quantize(CENT)  # display only; value unchanged
            if px is None:
                if a.seen < cfg.limit_ttl_bars:
                    keep.append(a)
                else:
                    reject(sig, "limit_not_traded_through")
                continue
            if sig.direction == "BUY":
                qty = sig.qty or cfg.default_qty
                notional = cents(px * qty)
                fee = fee_for(notional)
                if notional + fee > cash:
                    reject(sig, "insufficient_cash")
                    continue
                cash -= notional + fee
                pos[s] = pos.get(s, 0) + qty
                basis[s] = basis.get(s, ZERO) + notional + fee
                realized = None
            else:
                held = pos.get(s, 0)
                qty = sig.qty or held
                if held == 0:
                    reject(sig, "no_position")
                    continue
                if qty > held:
                    reject(sig, "oversell")
                    continue
                notional = cents(px * qty)
                fee = fee_for(notional)
                part = basis[s] if qty == held else cents(basis[s] * qty / held)
                realized = notional - fee - part
                cash += notional - fee
                basis[s] -= part
                pos[s] = held - qty
                if pos[s] == 0:
                    del pos[s], basis[s]
                wins, losses = (wins + 1, losses) if realized > 0 else (wins, losses + 1)
            fees_total += fee
            fills.append({"time": bar.time.isoformat(), "signal_time": sig.time.isoformat(),
                          "symbol": s, "side": sig.direction, "qty": qty, "price": str(px),
                          "fee": str(fee), "order_type": sig.order_type,
                          "realized": None if realized is None else str(realized)})
        active[s] = keep
        last[s] = bar.close
        equity = cash + sum(cents(q * last[x]) for x, q in pos.items())
        curve.append((bar.time, equity))

    for s, lst in by_sym.items():
        for sig in lst[ptr[s]:]:
            reject(sig, "no_subsequent_bar")
        for a in active[s]:
            reject(a.sig, "expired_end_of_data")

    peak, mdd = curve[0][1], ZERO
    for _, eq in curve:
        peak = max(peak, eq)
        if peak > 0:
            mdd = max(mdd, (peak - eq) / peak)
    end_eq = curve[-1][1]
    reasons = defaultdict(int)
    for r in rejects:
        reasons[r["reason"]] += 1
    closed = wins + losses
    q4 = Decimal("0.0001")
    return {
        "starting_cash": str(cfg.cash),
        "ending_cash": str(cash),
        "ending_equity": str(end_eq),
        "net_pnl": str(end_eq - cfg.cash),
        "return_pct": str(((end_eq - cfg.cash) / cfg.cash * 100).quantize(q4, ROUND_HALF_UP)),
        "max_drawdown_pct": str((mdd * 100).quantize(q4, ROUND_HALF_UP)),
        "fees_total": str(fees_total),
        "fills": len(fills),
        "closed_trades": closed,
        "win_rate": str((Decimal(wins) / closed).quantize(q4, ROUND_HALF_UP)) if closed else None,
        "rejected": dict(sorted(reasons.items())),
        "open_positions": dict(pos),
        "fill_log": fills,
        "reject_log": rejects,
    }


# ------------------------------------------------------------ CLI
def build_parser():
    p = argparse.ArgumentParser(description="Bar-based backtester with T+1 fills and trade-through limits.")
    p.add_argument("--bars", required=True)
    p.add_argument("--signals", required=True)
    p.add_argument("--cash", required=True, help="starting cash, e.g. 4431.00")
    p.add_argument("--slippage-bps", default="0", help="adverse slippage for market orders")
    p.add_argument("--fee-per-trade", default="0")
    p.add_argument("--fee-bps", default="0")
    p.add_argument("--default-qty", type=int, default=100)
    p.add_argument("--default-order-type", choices=["limit", "market"], default="limit")
    p.add_argument("--limit-ttl-bars", type=int, default=1,
                   help="bars a limit order stays live after T+1 begins (default 1)")
    p.add_argument("--out", help="write full JSON here (default: stdout)")
    p.add_argument("--summary-only", action="store_true", help="omit fill/reject logs")
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        cfg = Config(cash=_dec(a.cash, "cash"), slippage_bps=_dec(a.slippage_bps, "slippage"),
                     fee_per_trade=_dec(a.fee_per_trade, "fee"), fee_bps=_dec(a.fee_bps, "fee-bps"),
                     default_qty=a.default_qty, limit_ttl_bars=a.limit_ttl_bars)
        if cfg.cash <= 0 or cfg.default_qty <= 0 or cfg.limit_ttl_bars < 1:
            raise BacktestError("cash, default-qty must be > 0 and limit-ttl-bars >= 1")
        if not (ZERO <= cfg.slippage_bps < BPS) or cfg.fee_per_trade < 0 or cfg.fee_bps < 0:
            raise BacktestError("slippage must be in [0, 10000) bps; fees must be >= 0")
        res = run_backtest(load_bars(a.bars), load_signals(a.signals, a.default_order_type), cfg)
    except (BacktestError, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.summary_only:
        res.pop("fill_log"), res.pop("reject_log")
    text = json.dumps(res, indent=2)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
