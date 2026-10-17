import json
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from scout.backtester_cli import (BacktestError, Bar, Config, Signal, load_bars,
                                  load_signals, main, run_backtest)

T0 = datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc)


def t(i):
    return T0 + timedelta(minutes=i)


def bar(i, o, h, l, c, sym="AAA"):
    return Bar(t(i), sym, D(str(o)), D(str(h)), D(str(l)), D(str(c)))


def sig(i, side="BUY", price=None, qty=None, ot="market", sym="AAA"):
    return Signal(t(i), sym, side, None if price is None else D(str(price)), qty, ot)


def cfg(**kw):
    kw.setdefault("cash", D("10000"))
    return Config(**kw)


def run(bars, sigs, **kw):
    return run_backtest(bars, sigs, cfg(**kw))


# ---------- slippage then clamp ----------
def test_buy_slippage_clamped_to_high():
    r = run([bar(0, 10, 10.1, 9.9, 10), bar(1, 10, 10.02, 9.95, 10)],
            [sig(0, price=10.00, qty=10)], slippage_bps=D("50"))
    assert r["fill_log"][0]["price"] == "10.02"


def test_sell_slippage_clamped_to_low():
    bars = [bar(0, 10, 10, 10, 10), bar(1, 10, 10.05, 9.98, 10), bar(2, 10, 10.05, 9.98, 10)]
    r = run(bars, [sig(0, qty=10, price=10), sig(1, "SELL", price=10.00)], slippage_bps=D("50"))
    assert r["fill_log"][1]["price"] == "9.98"


def test_slippage_inside_range_applies_and_rounds_adverse():
    r = run([bar(0, 10, 10.5, 9.5, 10), bar(1, 10, 10.5, 9.5, 10)],
            [sig(0, price=10.00, qty=1)], slippage_bps=D("10"))
    assert r["fill_log"][0]["price"] == "10.01"


def test_target_below_low_clamps_up_to_low():
    r = run([bar(0, 10, 10, 10, 10), bar(1, 10, 10.2, 9.9, 10)], [sig(0, price=5.00, qty=1)])
    assert r["fill_log"][0]["price"] == "9.90"


def test_adverse_tick_rounding_for_subpenny_target():
    r = run([bar(0, 2, 2, 2, 2), bar(1, 2, 2.05, 1.95, 2)], [sig(0, price=2.001, qty=1)])
    assert r["fill_log"][0]["price"] == "2.01"


def test_fill_never_outside_bar_property():
    rnd = random.Random(7)
    for _ in range(300):
        lo = D(str(round(rnd.uniform(0.2, 20), 2)))
        hi = lo + D(str(round(rnd.uniform(0, 1), 2)))
        b = Bar(t(1), "AAA", lo, hi, lo, hi)
        for side in ("BUY", "SELL"):
            base = [bar(0, 5, 5, 5, 5)]
            tgt = D(str(round(rnd.uniform(0.1, 25), 4)))
            bps = D(str(rnd.randint(0, 900)))
            s = [sig(0, "BUY", price=5, qty=1)] if side == "SELL" else []
            s.append(Signal(t(0) if side == "BUY" else t(1) - timedelta(seconds=1), "AAA", side, tgt, 1, "market"))
            r = run(base + [b, Bar(t(2), "AAA", lo, hi, lo, hi)], s, slippage_bps=bps)
            for f in r["fill_log"]:
                if f["time"] == t(1).isoformat():
                    assert lo <= D(f["price"]) <= hi


# ---------- T+1 ----------
def test_signal_fills_on_next_bar_not_same_bar():
    r = run([bar(0, 10, 11, 9, 10), bar(1, 10, 11, 9, 10)], [sig(0, price=10, qty=1)])
    assert r["fill_log"][0]["time"] == t(1).isoformat()


def test_no_subsequent_bar_rejected():
    r = run([bar(0, 10, 11, 9, 10)], [sig(0, price=10, qty=1)])
    assert r["fills"] == 0 and r["rejected"] == {"no_subsequent_bar": 1}


def test_signal_between_bars_uses_first_strictly_after():
    s = Signal(t(0) + timedelta(seconds=30), "AAA", "BUY", D("10"), 1, "market")
    r = run([bar(0, 10, 11, 9, 10), bar(1, 10, 11, 9, 10)], [s])
    assert r["fill_log"][0]["time"] == t(1).isoformat()


def test_unknown_symbol_rejected():
    r = run([bar(0, 10, 11, 9, 10), bar(1, 10, 11, 9, 10)], [sig(0, price=10, sym="ZZZ")])
    assert r["rejected"] == {"no_subsequent_bar": 1}


# ---------- trade-through ----------
def test_limit_buy_touch_does_not_fill():
    r = run([bar(0, 10, 10, 10, 10), bar(1, 10.2, 10.3, 10.00, 10.1)],
            [sig(0, price=10.00, qty=1, ot="limit")])
    assert r["fills"] == 0 and r["rejected"] == {"limit_not_traded_through": 1}


def test_limit_buy_trade_through_fills_at_limit():
    r = run([bar(0, 10, 10, 10, 10), bar(1, 10.2, 10.3, 9.99, 10.1)],
            [sig(0, price=10.00, qty=1, ot="limit")], slippage_bps=D("500"))
    assert r["fill_log"][0]["price"] == "10.00"  # no slippage on limits


def test_limit_buy_gap_below_fills_at_open():
    r = run([bar(0, 10, 10, 10, 10), bar(1, 9.80, 9.9, 9.7, 9.8)],
            [sig(0, price=10.00, qty=1, ot="limit")])
    assert r["fill_log"][0]["price"] == "9.80"


def test_limit_sell_needs_high_above():
    bars = [bar(0, 10, 10, 10, 10), bar(1, 10, 10, 10, 10), bar(2, 10, 10.5, 10, 10), bar(3, 10, 10.5, 10, 10)]
    sigs = [sig(0, qty=1, price=10), sig(1, "SELL", price=10.50, ot="limit")]
    r = run(bars, sigs, limit_ttl_bars=1)
    assert [f["side"] for f in r["fill_log"]] == ["BUY"]
    bars[2] = bar(2, 10, 10.51, 10, 10)
    r = run(bars, sigs, limit_ttl_bars=1)
    assert r["fill_log"][1]["price"] == "10.50"


def test_limit_ttl_allows_second_bar():
    bars = [bar(0, 10, 10, 10, 10), bar(1, 10.2, 10.3, 10.1, 10.2), bar(2, 10.1, 10.2, 9.9, 10)]
    s = [sig(0, price=10.00, qty=1, ot="limit")]
    assert run(bars, s, limit_ttl_bars=1)["fills"] == 0
    assert run(bars, s, limit_ttl_bars=2)["fills"] == 1


def test_subpenny_limit_rounded_passively_down():
    r = run([bar(0, 2, 2, 2, 2), bar(1, 2.05, 2.06, 1.99, 2)],
            [sig(0, price=2.019, qty=1, ot="limit")])
    assert r["fill_log"][0]["price"] == "2.01"


# ---------- exact decimal accounting ----------
def test_no_float_drift_over_1000_fills():
    n = 500
    bars = [bar(i, "0.10", "0.10", "0.10", "0.10") for i in range(2 * n + 1)]
    sigs = []
    for k in range(n):
        sigs += [sig(2 * k, qty=10), sig(2 * k + 1, "SELL")]
    r = run(bars, sigs, cash=D("100"), fee_per_trade=D("0.01"))
    assert r["fills"] == 2 * n
    assert r["ending_cash"] == "90.00" and r["ending_equity"] == "90.00"
    assert r["fees_total"] == "10.00"


def test_fee_bps_and_realized_pnl():
    bars = [bar(0, 10, 10, 10, 10), bar(1, 10, 10, 10, 10), bar(2, 11, 11, 11, 11)]
    r = run(bars, [sig(0, qty=100), sig(1, "SELL")], fee_bps=D("10"))
    # buy 1000 + 1.00 fee; sell 1100 - 1.10 fee => realized 1100-1.10-1001 = 97.90
    assert r["fill_log"][1]["realized"] == "97.90"
    assert r["net_pnl"] == "97.90" and r["win_rate"] == "1.0000"


def test_insufficient_cash_no_position_and_oversell():
    bars = [bar(i, 10, 10, 10, 10) for i in range(4)]
    r = run(bars, [sig(0, qty=1000)], cash=D("500"))
    assert r["rejected"] == {"insufficient_cash": 1}
    r = run(bars, [sig(0, "SELL")])
    assert r["rejected"] == {"no_position": 1}
    r = run(bars, [sig(0, qty=5), sig(1, "SELL", qty=9)])
    assert r["rejected"] == {"oversell": 1}


def test_partial_sell_prorates_basis():
    bars = [bar(i, 10, 10, 10, 10) for i in range(4)] + [bar(4, 12, 12, 12, 12)]
    r = run(bars, [sig(0, qty=10), sig(3, "SELL", qty=4)])
    assert r["fill_log"][1]["realized"] == "8.00"
    assert r["open_positions"] == {"AAA": 6}


def test_max_drawdown_and_multi_symbol_isolation():
    bars = [bar(0, 10, 10, 10, 10), bar(1, 10, 10, 10, 10), bar(2, 8, 8, 8, 8)]
    r = run(bars, [sig(0, qty=100)], cash=D("1000"))
    assert D(r["max_drawdown_pct"]) == D("20.0000")
    r = run(bars + [bar(0, 5, 5, 5, 5, "BBB"), bar(1, 5, 5, 5, 5, "BBB")],
            [sig(0, qty=1, sym="BBB")])
    assert r["fill_log"][0]["symbol"] == "BBB" and r["fill_log"][0]["price"] == "5.00"


# ---------- loading / CLI ----------
def test_load_validation(tmp_path):
    p = tmp_path / "b.csv"
    p.write_text("time,symbol,open,high,low,close\n2026-10-09T14:00:00Z,A,10,9,11,10\n")
    with pytest.raises(BacktestError):
        load_bars(p)
    s = tmp_path / "s.json"
    s.write_text(json.dumps([{"time": "2026-10-09T14:00:00Z", "symbol": "A", "direction": "BUY"}]))
    with pytest.raises(BacktestError):
        load_signals(s)  # limit order without a price


def test_cli_end_to_end(tmp_path):
    b = tmp_path / "b.csv"
    b.write_text("time,symbol,open,high,low,close,volume\n"
                 "2026-10-09T14:00:00Z,A,10,10,10,10,1\n"
                 "2026-10-09T14:01:00Z,A,10,10.1,9.9,10,1\n")
    s = tmp_path / "s.json"
    s.write_text(json.dumps([{"time": "2026-10-09T14:00:00Z", "symbol": "A",
                              "direction": "BUY", "price": 10.00, "qty": 10}]))
    out = tmp_path / "o.json"
    assert main(["--bars", str(b), "--signals", str(s), "--cash", "1000", "--out", str(out)]) == 0
    res = json.loads(out.read_text())
    assert res["fills"] == 1 and res["fill_log"][0]["price"] == "10.00"
    assert main(["--bars", str(b), "--signals", str(s), "--cash", "-5"]) == 2
    assert main(["--bars", str(tmp_path / "nope.csv"), "--signals", str(s), "--cash", "5"]) == 2
