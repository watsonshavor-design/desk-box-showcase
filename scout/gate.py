"""Choke-point TradeGate. Wire it inside RestBroker.place_order so nothing
(including subagents) can reach the REST client without passing through it.

Entries: serialized under one asyncio.Lock; need breaker clear, fresh quote
(<= max_quote_age, fail closed on unknown/error), and PDT headroom.
Exits (reduce_only): never blocked by breaker/feed/PDT, but verified against
the live position so the flag cannot be used to open or flip exposure.
"""
import asyncio
import math
from dataclasses import dataclass, replace
from typing import Any, Optional

from .ticks import round_to_tick


@dataclass(frozen=True)
class Order:
    symbol: str
    side: str            # "BUY" | "SELL"
    qty: int
    limit_price: float
    client_order_id: str
    reduce_only: bool = False


@dataclass
class GateResult:
    ok: bool
    reason: str = ""
    order: Optional[Order] = None
    response: Any = None


class TradeGate:
    def __init__(self, submit, breaker, pdt, get_quote_ts, get_equity,
                 get_position, clock, max_quote_age=3.0, submit_timeout=10.0):
        self._submit = submit              # async (Order) -> response
        self.breaker, self.pdt = breaker, pdt
        self._quote_ts = get_quote_ts      # (symbol) -> aware datetime | None
        self._equity = get_equity          # () -> float
        self._position = get_position      # (symbol) -> signed qty
        self.clock = clock
        self.max_quote_age = float(max_quote_age)
        self.submit_timeout = float(submit_timeout)
        self._lock = asyncio.Lock()
        self._seen = set()
        self._exit_inflight = {}

    @staticmethod
    def _no(reason):
        return GateResult(False, reason)

    def _validate(self, o: Order):
        if o.side not in ("BUY", "SELL"):
            return "bad_side"
        if not isinstance(o.qty, int) or isinstance(o.qty, bool) or o.qty <= 0:
            return "bad_qty"
        if not o.client_order_id:
            return "no_client_order_id"
        if o.client_order_id in self._seen:
            return "duplicate_client_order_id"
        return ""

    async def place(self, order: Order) -> GateResult:
        bad = self._validate(order)
        if bad:
            return self._no(bad)
        try:
            if order.reduce_only:
                px = round_to_tick(order.limit_price, "nearest")
            else:
                px = round_to_tick(order.limit_price,
                                   "down" if order.side == "BUY" else "up")
        except ValueError:
            return self._no("bad_price")
        order = replace(order, limit_price=px)

        if order.reduce_only:
            return await self._place_exit(order)
        async with self._lock:
            return await self._place_entry(order)

    # --- exits -------------------------------------------------------
    async def _place_exit(self, order: Order) -> GateResult:
        try:
            pos = float(self._position(order.symbol))
        except Exception:
            return self._no("reduce_only_unverified")
        if not math.isfinite(pos):
            return self._no("reduce_only_unverified")
        closes_long = pos > 0 and order.side == "SELL"
        closes_short = pos < 0 and order.side == "BUY"
        inflight = self._exit_inflight.get(order.symbol, 0)
        if not (closes_long or closes_short) or order.qty + inflight > abs(pos):
            return self._no("reduce_only_invalid")
        # no awaits between the check above and the reservation below
        self._exit_inflight[order.symbol] = inflight + order.qty
        self._seen.add(order.client_order_id)
        try:
            return await self._send(order)
        finally:
            self._exit_inflight[order.symbol] -= order.qty

    # --- entries -----------------------------------------------------
    async def _place_entry(self, order: Order) -> GateResult:
        if self.breaker.is_tripped():
            return self._no(f"breaker_tripped: {self.breaker.reason()}")
        try:
            ts = self._quote_ts(order.symbol)
        except Exception:
            return self._no("quote_error")
        if ts is None or getattr(ts, "tzinfo", None) is None:
            return self._no("quote_unknown")
        age = (self.clock() - ts).total_seconds()
        if age > self.max_quote_age or age < -1.0:
            return self._no(f"quote_stale: {age:.1f}s")
        try:
            equity = self._equity()
        except Exception:
            return self._no("equity_error")
        if not self.pdt.can_open_entry(equity):
            return self._no("pdt_limit")
        self._seen.add(order.client_order_id)
        return await self._send(order)

    # --- submit ------------------------------------------------------
    async def _send(self, order: Order) -> GateResult:
        try:
            resp = await asyncio.wait_for(self._submit(order), self.submit_timeout)
        except asyncio.TimeoutError:
            return GateResult(False, "submit_timeout_state_unknown", order)
        except Exception as e:
            return GateResult(False, f"submit_failed: {type(e).__name__}", order)
        return GateResult(True, "", order, resp)
