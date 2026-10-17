"""Rolling 5-business-day day-trade counter (FINRA PDT, equity < $25k)."""
import json
import math
import os
import tempfile
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def business_window(today: date, n: int = 5, holidays=frozenset()):
    days, d = [], today
    while len(days) < n:
        if d.weekday() < 5 and d.isoformat() not in holidays:
            days.append(d.isoformat())
        d -= timedelta(days=1)
    return set(days)


class PDTGuard:
    def __init__(self, path, clock, equity_threshold=25_000.0,
                 max_day_trades=3, holidays=()):
        self.path = str(path)
        self.clock = clock
        self.threshold = float(equity_threshold)
        self.max = int(max_day_trades)
        self.holidays = frozenset(holidays)  # ISO dates of market holidays
        self._corrupt = False
        self._trades = self._load()

    def _today(self) -> date:
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("clock must be timezone-aware")
        return now.astimezone(ET).date()

    def _load(self):
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path) as f:
                data = json.load(f)
            return [dict(date=str(t["date"]), symbol=str(t.get("symbol", ""))) for t in data]
        except Exception:
            self._corrupt = True
            return []

    def _save(self):
        d = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._trades, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def record_day_trade(self, symbol: str):
        """Call when a position opened and closed on the same ET date."""
        if self._corrupt:
            return
        cutoff = (self._today() - timedelta(days=21)).isoformat()
        self._trades = [t for t in self._trades if t["date"] >= cutoff]
        self._trades.append({"date": self._today().isoformat(), "symbol": symbol})
        self._save()

    def count(self) -> int:
        win = business_window(self._today(), 5, self.holidays)
        return sum(1 for t in self._trades if t["date"] in win)

    def can_open_entry(self, equity) -> bool:
        if self._corrupt:
            return False
        if not isinstance(equity, (int, float)) or not math.isfinite(equity):
            return False
        if equity >= self.threshold:
            return True
        return self.count() < self.max

    def reset_corrupt(self):
        self._trades, self._corrupt = [], False
        self._save()
