import importlib
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

import scout.premarket_scanner as mod
from scout.premarket_scanner import (MockFeedForbidden, PremarketScanner,
                                     generate_mock_premarket_feed)

NOW = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)


def row(**o):
    r = dict(code="AAA", last_price=2.10, prev_close=2.00, premarket_volume=50_000,
             bid=2.09, ask=2.11, quote_ts=NOW - timedelta(seconds=2))
    r.update(o)
    return r


def scanner(**kw):
    kw.setdefault("clock", lambda: NOW)
    return PremarketScanner(**kw)


@contextmanager
def env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_basic_candidate_fields():
    (c,) = scanner().scan_candidates([row()])
    assert c["symbol"] == "AAA" and c["gap_pct"] == 5.0
    assert c["dollar_volume"] == 105000.0 and c["spread_pct"] == pytest.approx(0.952, rel=1e-3)


@pytest.mark.parametrize("over,reason", [
    (dict(last_price=0.40, prev_close=0.30), "price_out_of_range"),
    (dict(last_price=25.0, prev_close=20.0), "price_out_of_range"),
    (dict(premarket_volume=4999), "low_volume"),
    (dict(premarket_volume=5000, last_price=2.10), "low_dollar_volume"),
    (dict(last_price=2.05), "gap_too_small"),
    (dict(last_price=5.0, prev_close=2.0), "gap_implausible"),
    (dict(prev_close=0), "bad_value"),
    (dict(last_price=float("nan")), "bad_number"),
    (dict(premarket_volume=float("inf")), "bad_number"),
    (dict(premarket_volume=True), "bad_number"),
    (dict(last_price="2.1"), "bad_number"),
    (dict(code=None), "bad_symbol"),
    (dict(code="  "), "bad_symbol"),
    (dict(quote_ts=NOW - timedelta(seconds=11)), "stale_quote"),
    (dict(quote_ts=NOW + timedelta(seconds=30)), "stale_quote"),
    (dict(quote_ts=None), "no_quote_ts"),
    (dict(quote_ts=datetime(2026, 10, 9, 8, 0)), "no_quote_ts"),
    (dict(bid=None), "bad_book"),
    (dict(bid=2.12, ask=2.11), "bad_book"),
    (dict(bid=2.00, ask=2.20), "wide_spread"),
    (dict(mock=True), "mock_not_allowed"),
])
def test_rejection_reasons(over, reason):
    s = scanner(min_volume=5000 if reason != "low_dollar_volume" else 5000)
    cands, rej = s.scan_with_reasons([row(**over)])
    assert cands == [] and dict(rej) == {reason: 1}


def test_non_dict_row_rejected():
    assert dict(scanner().scan_with_reasons(["junk", None])[1]) == {"bad_item": 2}


def test_sorting_tiebreak_and_top_n():
    rows = [row(code="B", last_price=2.20), row(code="A", last_price=2.20),
            row(code="C", last_price=2.40, bid=2.39, ask=2.41),
            row(code="D", last_price=2.20, premarket_volume=90_000)]
    assert [c["symbol"] for c in scanner().scan_candidates(rows)] == ["C", "D", "A", "B"]
    assert [c["symbol"] for c in scanner(top_n=2).scan_candidates(rows)] == ["C", "D"]


def test_duplicate_symbols_and_case_normalised():
    c, rej = scanner().scan_with_reasons([row(code="aaa"), row(code="AAA ")])
    assert len(c) == 1 and c[0]["symbol"] == "AAA" and dict(rej) == {"duplicate_symbol": 1}


def test_book_optional_but_validated_when_present():
    r = row()
    del r["bid"], r["ask"]
    s = scanner(require_book=False)
    assert len(s.scan_candidates([r])) == 1 and s.scan_candidates([r])[0]["spread_pct"] is None
    assert scanner(require_book=False).scan_candidates([row(bid=2.2, ask=2.1)]) == []


def test_quote_age_check_can_be_disabled():
    r = row(quote_ts=None)
    assert len(scanner(max_quote_age_s=None).scan_candidates([r])) == 1


def test_config_validation():
    for kw in (dict(min_price=0), dict(min_price=5, max_price=1), dict(min_gap_pct=-1),
               dict(max_spread_pct=0), dict(top_n=0)):
        with pytest.raises(ValueError):
            PremarketScanner(**kw)


# ---------- mock feed ----------
def test_mock_feed_refused_outside_paper_mode():
    for v in (None, "false", ""):
        with env(PAPER_TRADING=v):
            with pytest.raises(mod.MockFeedForbidden):  # mod: survives the reload test
                mod.generate_mock_premarket_feed(["A"])


def test_mock_feed_seeded_tagged_and_gated_in_scanner():
    syms = [f"S{i}" for i in range(40)]
    with env(PAPER_TRADING="true"):
        a = generate_mock_premarket_feed(syms, seed=3, now=NOW)
        b = generate_mock_premarket_feed(syms, seed=3, now=NOW)
    assert a == b and all(r["mock"] for r in a)
    assert scanner().scan_candidates(a) == []                    # live scanner refuses
    got = scanner(allow_mock=True).scan_candidates(a)
    assert all(c["gap_pct"] >= 5.0 for c in got)
    assert {c["symbol"] for c in got} <= set(syms)


def test_import_does_not_configure_logging():
    root = logging.getLogger()
    before = list(root.handlers)
    importlib.reload(mod)
    assert root.handlers == before
