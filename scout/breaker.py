"""Latched, disk-persisted daily loss breaker.

Fails closed: missing session start equity, corrupt state file, or NaN P&L
all read as TRIPPED. Session boundary is 4:00 PM America/New_York.
"""
import json
import math
import os
import tempfile
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
CUTOFF = time(16, 0)


def session_key(now: datetime) -> str:
    """Session id = ET date of the 4 PM close that ends the session."""
    if now.tzinfo is None:
        raise ValueError("clock must return timezone-aware datetimes")
    et = now.astimezone(ET)
    d = et.date()
    if et.time() >= CUTOFF:
        d += timedelta(days=1)
    return d.isoformat()


def _fresh(session):
    return {"session": session, "start_equity": None, "tripped": False,
            "reason": "", "tripped_at": None}


class PersistentBreaker:
    def __init__(self, path, clock, max_loss_abs=75.0, max_loss_pct=0.03):
        self.path = str(path)
        self.clock = clock
        self.max_loss_abs = float(max_loss_abs)
        self.max_loss_pct = float(max_loss_pct)
        self._corrupt = False
        self._state = self._load()

    # --- persistence -------------------------------------------------
    def _load(self):
        if not os.path.exists(self.path):
            return _fresh(session_key(self.clock()))
        try:
            with open(self.path) as f:
                st = json.load(f)
            for k in ("session", "start_equity", "tripped"):
                if k not in st:
                    raise ValueError(k)
            return st
        except Exception:
            self._corrupt = True
            return _fresh(session_key(self.clock()))

    def _save(self):
        d = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._state, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def _roll(self):
        if self._corrupt:
            return
        key = session_key(self.clock())
        if self._state["session"] != key:
            self._state = _fresh(key)  # new session: needs start_session()
            self._save()

    # --- API ---------------------------------------------------------
    def start_session(self, equity: float) -> float:
        """Set baseline once per session; a restart never moves it."""
        self._roll()
        if self._corrupt:
            return float("nan")
        if self._state["start_equity"] is None:
            if not isinstance(equity, (int, float)) or not math.isfinite(equity) or equity <= 0:
                raise ValueError("invalid equity")
            self._state["start_equity"] = float(equity)
            self._save()
        return self._state["start_equity"]

    def loss_limit(self):
        self._roll()
        se = self._state["start_equity"]
        if se is None:
            return None
        return min(self.max_loss_abs, self.max_loss_pct * se)

    def trip(self, reason: str):
        self._roll()
        if self._corrupt:
            return
        self._state.update(tripped=True, reason=reason,
                           tripped_at=self.clock().isoformat())
        self._save()

    def record_pnl(self, session_pnl: float) -> bool:
        """Feed realized+unrealized session P&L. Returns True if tripped."""
        limit = self.loss_limit()
        if limit is None:
            return self.is_tripped()
        if not isinstance(session_pnl, (int, float)) or not math.isfinite(session_pnl):
            self.trip("non-finite pnl")
        elif session_pnl <= -limit:
            self.trip(f"loss {session_pnl:.2f} hit limit -{limit:.2f}")
        return self.is_tripped()

    def is_tripped(self) -> bool:
        self._roll()
        if self._corrupt:
            return True
        if self._state["start_equity"] is None:
            return True  # session not initialised -> fail closed
        return bool(self._state["tripped"])

    def reason(self) -> str:
        if self._corrupt:
            return "state file corrupt"
        if self._state["start_equity"] is None:
            return "session not initialised"
        return self._state.get("reason", "")

    def manual_reset(self):
        """Explicit operator reset. Keeps the session baseline; will re-trip
        on the next record_pnl if P&L is still beyond the limit."""
        if self._corrupt:
            self._state = _fresh(session_key(self.clock()))
            self._corrupt = False
        else:
            self._roll()
            self._state.update(tripped=False, reason="", tripped_at=None)
        self._save()
