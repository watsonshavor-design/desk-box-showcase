"""Safety-core aggregator: the desk's pre-trade gates, loss breakers, and
rule guards behind one import surface.

Every component here fails closed — an uncertain state (missing equity,
corrupt persisted state, unknown order) resolves to *no entry*, never to
a guess. Exits are never blocked by these guards.
"""
from .ticks import round_to_tick, tick_size
from .breaker import PersistentBreaker, session_key
from .pdt import PDTGuard
from .gate import GateResult, Order, TradeGate

__all__ = [
    "round_to_tick", "tick_size",
    "PersistentBreaker", "session_key",
    "PDTGuard",
    "GateResult", "Order", "TradeGate",
]
