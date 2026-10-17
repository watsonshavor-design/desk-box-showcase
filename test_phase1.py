import asyncio
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from scout.safety import (GateResult, Order, PDTGuard, PersistentBreaker,
                          TradeGate, round_to_tick, session_key)

ET = ZoneInfo("America/New_York")
UTC = timezone.utc


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def et(y, m, d, h=10, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=ET)


# ---------------- ticks ----------------
def test_tick_rounding():
    assert round_to_tick(1.004, "down") == 1.00
    assert round_to_tick(1.004, "up") == 1.01
    assert round_to_tick(5.505, "nearest") == 5.51
    assert round_to_tick(0.12345, "nearest") == 0.1235
    assert round_to_tick(0.99996, "down") == 0.9999
    assert round_to_tick(2.001, "down") == 2.00  # the bid+0.001 case


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), None, True])
def test_tick_rejects_bad(bad):
    with pytest.raises(ValueError):
        round_to_tick(bad)


# ---------------- session key / DST ----------------
def test_session_key_boundary_summer_and_winter():
    assert session_key(datetime(2026, 7, 1, 19, 59, tzinfo=UTC)) == "2026-07-01"
    assert session_key(datetime(2026, 7, 1, 20, 0, tzinfo=UTC)) == "2026-07-02"
    assert session_key(datetime(2026, 12, 1, 20, 59, tzinfo=UTC)) == "2026-12-01"
    assert session_key(datetime(2026, 12, 1, 21, 0, tzinfo=UTC)) == "2026-12-02"


def test_session_key_requires_aware():
    with pytest.raises(ValueError):
        session_key(datetime(2026, 10, 9, 10, 0))


# ---------------- breaker ----------------
def mk_breaker(tmp_path, clock):
    return PersistentBreaker(tmp_path / "breaker.json", clock)


def test_limit_is_stricter_of_abs_and_pct(tmp_path):
    c = Clock(et(2026, 10, 9))
    b = mk_breaker(tmp_path, c)
    b.start_session(4431)
    assert b.loss_limit() == 75.0
    b2 = PersistentBreaker(tmp_path / "b2.json", c)
    b2.start_session(1000)
    assert b2.loss_limit() == pytest.approx(30.0)


def test_fail_closed_until_session_started(tmp_path):
    b = mk_breaker(tmp_path, Clock(et(2026, 10, 9)))
    assert b.is_tripped()
    b.start_session(4431)
    assert not b.is_tripped()


def test_trips_latches_and_survives_restart(tmp_path):
    c = Clock(et(2026, 10, 9))
    b = mk_breaker(tmp_path, c)
    b.start_session(4431)
    assert not b.record_pnl(-74.99)
    assert b.record_pnl(-75.0)
    b.record_pnl(+500)  # recovery does not unlatch
    assert b.is_tripped()
    b2 = mk_breaker(tmp_path, c)  # simulated Railway restart
    assert b2.is_tripped()


def test_restart_does_not_move_baseline(tmp_path):
    c = Clock(et(2026, 10, 9))
    mk_breaker(tmp_path, c).start_session(4431)
    b2 = mk_breaker(tmp_path, c)
    assert b2.start_session(3000) == 4431


def test_nan_pnl_trips(tmp_path):
    b = mk_breaker(tmp_path, Clock(et(2026, 10, 9)))
    b.start_session(4431)
    assert b.record_pnl(float("nan"))


def test_corrupt_state_fails_closed(tmp_path):
    p = tmp_path / "breaker.json"
    p.write_text("{not json")
    b = PersistentBreaker(p, Clock(et(2026, 10, 9)))
    assert b.is_tripped()
    b.start_session(4431)
    assert b.is_tripped()
    b.manual_reset()
    b.start_session(4431)
    assert not b.is_tripped()


def test_clears_only_at_4pm_boundary_and_needs_new_baseline(tmp_path):
    c = Clock(et(2026, 10, 9, 10))
    b = mk_breaker(tmp_path, c)
    b.start_session(4431)
    b.record_pnl(-80)
    c.now = et(2026, 10, 9, 15, 59)
    assert b.is_tripped()
    c.now = et(2026, 10, 9, 16, 0)
    assert b.is_tripped()          # new session, baseline not set yet
    b.start_session(4350)
    assert not b.is_tripped()


def test_manual_reset_retrips_if_still_beyond_limit(tmp_path):
    b = mk_breaker(tmp_path, Clock(et(2026, 10, 9)))
    b.start_session(4431)
    b.record_pnl(-90)
    b.manual_reset()
    assert not b.is_tripped()
    assert b.record_pnl(-90)


# ---------------- PDT ----------------
def test_pdt_blocks_at_three_under_25k(tmp_path):
    c = Clock(et(2026, 10, 9))
    g = PDTGuard(tmp_path / "pdt.json", c)
    for s in ("A", "B"):
        g.record_day_trade(s)
    assert g.can_open_entry(4431)
    g.record_day_trade("C")
    assert g.count() == 3
    assert not g.can_open_entry(4431)
    assert g.can_open_entry(25_000)
    assert not g.can_open_entry(float("nan"))


def test_pdt_rolling_window_and_persistence(tmp_path):
    c = Clock(et(2026, 10, 5))  # Monday
    g = PDTGuard(tmp_path / "pdt.json", c)
    g.record_day_trade("A")
    c.now = et(2026, 10, 6)
    g.record_day_trade("B")
    g.record_day_trade("C")
    c.now = et(2026, 10, 9)  # Fri: window Oct 5-9 -> all three
    g2 = PDTGuard(tmp_path / "pdt.json", c)
    assert g2.count() == 3
    c.now = et(2026, 10, 12)  # Mon: window Oct 6-12 -> Oct 5 drops out
    assert g2.count() == 2
    assert g2.can_open_entry(4431)


def test_pdt_corrupt_fails_closed(tmp_path):
    p = tmp_path / "pdt.json"
    p.write_text("garbage")
    assert not PDTGuard(p, Clock(et(2026, 10, 9))).can_open_entry(4431)


# ---------------- gate ----------------
class Env:
    def __init__(self, tmp_path, quote_age=1.0, pos=0, equity=4431, delay=0.0, exit_delay=None):
        self.clock = Clock(et(2026, 10, 9, 10))
        self.breaker = PersistentBreaker(tmp_path / "b.json", self.clock)
        self.breaker.start_session(4431)
        self.pdt = PDTGuard(tmp_path / "p.json", self.clock)
        self.quote_age, self.pos, self.equity, self.delay = quote_age, pos, equity, delay
        self.exit_delay = exit_delay
        self.sent = []
        self.active = 0
        self.max_active = 0
        self.gate = TradeGate(self.submit, self.breaker, self.pdt, self.quote_ts,
                              lambda: self.equity, lambda s: self.pos, self.clock)

    def quote_ts(self, sym):
        if self.quote_age is None:
            return None
        if isinstance(self.quote_age, Exception):
            raise self.quote_age
        return self.clock() - timedelta(seconds=self.quote_age)

    async def submit(self, order):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        d = self.exit_delay if (order.reduce_only and self.exit_delay is not None) else self.delay
        await asyncio.sleep(d)
        self.sent.append(order)
        self.active -= 1
        return {"id": order.client_order_id}


def buy(i="e1", px=2.001, q=10, ro=False):
    return Order("AAA", "BUY", q, px, i, ro)


def sell(i="x1", px=2.0, q=10, ro=True):
    return Order("AAA", "SELL", q, px, i, ro)


def run(c):
    return asyncio.run(c)


def test_entry_ok_and_price_rounded_down(tmp_path):
    e = Env(tmp_path)
    r = run(e.gate.place(buy()))
    assert r.ok and e.sent[0].limit_price == 2.00


def test_entry_blocked_by_breaker_but_exit_passes(tmp_path):
    e = Env(tmp_path, pos=100)
    e.breaker.record_pnl(-100)
    assert run(e.gate.place(buy())).reason.startswith("breaker_tripped")
    assert run(e.gate.place(sell())).ok


@pytest.mark.parametrize("age", [3.5, None, RuntimeError("-12006")])
def test_entry_fails_closed_on_bad_quote_but_exit_passes(tmp_path, age):
    e = Env(tmp_path, quote_age=age, pos=100)
    assert not run(e.gate.place(buy())).ok
    assert run(e.gate.place(sell())).ok


def test_naive_quote_timestamp_rejected(tmp_path):
    e = Env(tmp_path)
    e.quote_ts = lambda s: datetime(2026, 10, 9, 9, 59, 59)
    e.gate._quote_ts = e.quote_ts
    assert run(e.gate.place(buy())).reason == "quote_unknown"


def test_entry_blocked_by_pdt(tmp_path):
    e = Env(tmp_path)
    for s in "ABC":
        e.pdt.record_day_trade(s)
    assert run(e.gate.place(buy())).reason == "pdt_limit"


def test_reduce_only_cannot_open_or_flip(tmp_path):
    e = Env(tmp_path, pos=0)
    assert run(e.gate.place(sell())).reason == "reduce_only_invalid"
    assert run(e.gate.place(buy("b2", ro=True))).reason == "reduce_only_invalid"
    e.pos = 5
    assert run(e.gate.place(sell("x3", q=10))).reason == "reduce_only_invalid"
    assert run(e.gate.place(sell("x4", q=5))).ok


def test_reduce_only_unverified_when_position_lookup_fails(tmp_path):
    e = Env(tmp_path, pos=100)
    def boom(s):
        raise RuntimeError("down")
    e.gate._position = boom
    assert run(e.gate.place(sell())).reason == "reduce_only_unverified"


def test_duplicate_client_order_id(tmp_path):
    e = Env(tmp_path)
    assert run(e.gate.place(buy("dup"))).ok
    assert run(e.gate.place(buy("dup"))).reason == "duplicate_client_order_id"


def test_bad_inputs(tmp_path):
    e = Env(tmp_path)
    assert run(e.gate.place(buy(q=0))).reason == "bad_qty"
    assert run(e.gate.place(buy(i=""))).reason == "no_client_order_id"
    assert run(e.gate.place(buy(px=float("nan")))).reason == "bad_price"


def test_submit_exception_returns_result_not_raise(tmp_path):
    e = Env(tmp_path)
    async def bad(o):
        raise ConnectionError("x")
    e.gate._submit = bad
    r = run(e.gate.place(buy()))
    assert not r.ok and r.reason.startswith("submit_failed")


def test_submit_timeout_reports_unknown_state(tmp_path):
    e = Env(tmp_path, delay=0.5)
    e.gate.submit_timeout = 0.05
    assert run(e.gate.place(buy())).reason == "submit_timeout_state_unknown"


def test_entries_are_serialized_and_breaker_rechecked(tmp_path):
    e = Env(tmp_path, delay=0.05)

    async def go():
        async def trip_during_first():
            await asyncio.sleep(0.01)
            e.breaker.record_pnl(-100)
        return await asyncio.gather(e.gate.place(buy("a")), e.gate.place(buy("b")),
                                    trip_during_first())
    r1, r2, _ = run(go())
    assert e.max_active == 1
    assert r1.ok and not r2.ok and r2.reason.startswith("breaker_tripped")


def test_exit_not_blocked_by_hung_entry(tmp_path):
    e = Env(tmp_path, delay=0.2, pos=100, exit_delay=0.0)

    async def go():
        t = asyncio.create_task(e.gate.place(buy("a")))
        await asyncio.sleep(0.02)
        return await asyncio.wait_for(e.gate.place(sell("x")), 0.15), t
    r, t = run(go())
    assert r.ok


def test_concurrent_exits_cannot_oversell(tmp_path):
    e = Env(tmp_path, delay=0.05, pos=100)

    async def go():
        return await asyncio.gather(e.gate.place(sell("x1", q=60)),
                                    e.gate.place(sell("x2", q=60)))
    a, b = run(go())
    assert [a.ok, b.ok].count(True) == 1
