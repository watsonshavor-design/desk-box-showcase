"""Excerpt — wake filter & Mention Radar endpoints from the Desk Box console.

Taken from the production FastAPI application (a single-file monolith,
app.py) that hosts the desk's multi-agent command room, October 2026.
Framework wiring and shared state (`app`, `history`, `history_lock`,
`_desk_token_from`, `JSONResponse`, `Request`) are defined elsewhere in
the monolith and are omitted here; what is shown is the endpoint logic
exactly as it runs in production.

Design notes:
  * Agents poll cursor feeds instead of holding sockets open. A seat
    wakes only for posts explicitly addressed to it — scanner feeds and
    general traffic never wake anyone. Attention is a budget.
  * The mention queue scans the full server-side history, not the
    room's recent-message window, and paginates by timestamp cursor.
  * Both endpoints are token-gated and resolve identity from the
    presented token only (see sender_identity.py).
"""

# --- Wake filter + Mention Radar (production excerpt) -----------------------
# Wake filter semantics (drill-reconciled): Beacon's forwarder polls
# /api/for-beacon and wakes her ONLY for posts explicitly addressed to
# her (text begins with her name or carries an @beacon mention), from any
# sender except herself. Scanner/board feeds never address her, so they
# never wake her; general unaddressed posts never wake her either.
_BEACON_ADDR = re.compile(r"^\s*@?beacon\b", re.IGNORECASE)


def _addressed_to_beacon(text):
    t = str(text or "")
    return bool(_BEACON_ADDR.search(t)) or "@beacon" in t.lower()


@app.get("/api/for-beacon")
async def for_beacon(request: Request, since: str = ""):
    """Cursor feed of wake-worthy posts for Beacon (see note above)."""
    if not _desk_token_from(request, None):
        return JSONResponse({"error": "bad token"}, status_code=403)
    async with history_lock:
        hist = list(history)
    out = [e for e in hist
           if (not since or str(e.get("ts", "")) > since)
           and e.get("from") != "beacon"
           and _addressed_to_beacon(e.get("text"))]
    cursor = out[-1]["ts"] if out else (hist[-1]["ts"] if hist else since)
    return {"wake": out, "cursor": cursor}


_MENTION_SEATS = ("ace", "ember", "flint", "beacon", "shavor",
                  "rail", "anchor", "scout", "wick")


def _mentions_seat(text, seat):
    t = str(text or "")
    if ("@" + seat) in t.lower():
        return True
    return bool(re.search(r"^\s*@?" + seat + r"\b\s*[:,]", t, re.IGNORECASE))


@app.get("/api/mentions")
async def mentions(request: Request, seat: str = "", cursor: str = "",
                   limit: int = 20):
    """Mention Radar: one seat's mention queue over the full server-side
    history (not the room's 50-message window), cursor-paginated by ts."""
    if not _desk_token_from(request, None):
        return JSONResponse({"error": "bad token"}, status_code=403)
    seat = str(seat or "").lower()
    if seat not in _MENTION_SEATS:
        return JSONResponse({"error": "unknown seat"}, status_code=400)
    limit = max(1, min(int(limit or 20), 100))
    async with history_lock:
        hist = list(history)
    hits = [e for e in hist
            if (not cursor or str(e.get("ts", "")) > cursor)
            and e.get("from") != seat
            and _mentions_seat(e.get("text"), seat)]
    page = hits[:limit]
    next_cursor = page[-1]["ts"] if len(hits) > limit else ""
    return {"seat": seat, "messages": page, "next_cursor": next_cursor,
            "more": len(hits) > limit}
