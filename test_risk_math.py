import math
import random

import pytest

from scout.risk_math import (calculate_half_kelly_sizing as kelly,
                             check_correlation_exposure as corr_check,
                             detect_dynamic_liquidity_vacuum_exit as vac,
                             evaluate_correlation_exposure_cap, kelly_shares)

# ---------------- Kelly ----------------
def k(**o):
    a = dict(win_rate=0.6, avg_win=2.0, avg_loss=1.0, buying_power=1000.0,
             n_trades=100, confidence_z=0.0)
    a.update(o)
    return kelly(**a)


def test_half_kelly_basic():
    assert k() == 200.0  # full=.4, half=.2


def test_cap_applies():
    assert k(win_rate=0.9, avg_win=3.0) == 250.0


def test_no_edge_is_zero():
    assert k(win_rate=0.3) == 0.0


def test_min_sample_enforced():
    assert k(n_trades=29) == 0.0
    assert k(n_trades=30) > 0.0


def test_lower_bound_sizes_down_small_samples():
    assert k(n_trades=30, confidence_z=1.0) < k(n_trades=30, confidence_z=0.0)
    assert k(n_trades=3000, confidence_z=1.0) > k(n_trades=30, confidence_z=1.0)


def test_session_scale():
    assert k(session_scale=0.5) == 100.0


@pytest.mark.parametrize("bad", [dict(win_rate=1.0), dict(win_rate=0.0), dict(win_rate=1.5),
                                 dict(avg_loss=0), dict(avg_win=-1), dict(buying_power=0),
                                 dict(buying_power=float("nan")), dict(win_rate=float("inf")),
                                 dict(session_scale=0), dict(session_scale=2.0),
                                 dict(max_fraction_cap=0), dict(n_trades=True)])
def test_kelly_bad_inputs(bad):
    assert k(**bad) == 0.0


def test_uses_buying_power_and_whole_shares():
    s = kelly_shares(2.0, 0.6, 2.0, 1.0, 145.31, n_trades=100, confidence_z=0.0)
    assert s == 14
    assert kelly_shares(0, 0.6, 2.0, 1.0, 145.31, n_trades=100) == 0
    assert kelly_shares(500.0, 0.6, 2.0, 1.0, 145.31, n_trades=100, confidence_z=0.0) == 0


# ---------------- correlation ----------------
def series(vals, start=0):
    return {start + i: v for i, v in enumerate(vals)}


R = random.Random(1)
A = [R.gauss(0, 0.01) for _ in range(60)]
B = [R.gauss(0, 0.01) for _ in range(60)]
POS = {"AAA": 100}


def test_no_positions_allows():
    assert corr_check("BBB", "BUY", {}, {}).ok


def test_correlated_long_blocked():
    d = corr_check("BBB", "BUY", POS, {"AAA": series(A), "BBB": series([2 * x for x in A])})
    assert not d.ok and d.reason.startswith("correlated")


def test_uncorrelated_allowed():
    assert corr_check("BBB", "BUY", POS, {"AAA": series(A), "BBB": series(B)}).ok


def test_hedge_short_allowed():
    r = {"AAA": series(A), "BBB": series([2 * x for x in A])}
    assert corr_check("BBB", "SELL", POS, r).ok
    assert not corr_check("BBB", "SELL", {"AAA": -100}, r).ok


def test_timestamp_alignment_not_tail_alignment():
    # same values but shifted timestamps: only 20 overlap -> fail closed
    r = {"AAA": series(A[:50], 0), "BBB": series(A[:50], 30)}
    d = corr_check("BBB", "BUY", POS, r)
    assert not d.ok and d.reason.startswith("insufficient_overlap")


def test_insufficient_samples_fail_closed():
    r = {"AAA": series(A[:10]), "BBB": series(A[:10])}
    assert not corr_check("BBB", "BUY", POS, r).ok


def test_constant_series_fail_closed():
    r = {"AAA": series([0.0] * 60), "BBB": series(B)}
    assert corr_check("BBB", "BUY", POS, r).reason.startswith("zero_variance")


def test_missing_data_fail_closed():
    assert not corr_check("BBB", "BUY", POS, {"AAA": series(A)}).ok
    assert not corr_check("BBB", "BUY", POS, {"BBB": series(B)}).ok


def test_nan_samples_dropped():
    b = list(B)
    for i in range(0, 60, 2):
        b[i] = float("nan")
    d = corr_check("BBB", "BUY", POS, {"AAA": series(A), "BBB": series(b)})
    assert d.ok  # exactly 30 valid pairs remain, uncorrelated


def test_zero_qty_position_ignored_and_bool_wrapper():
    assert evaluate_correlation_exposure_cap("BBB", "BUY", {"AAA": 0}, {})
    assert not evaluate_correlation_exposure_cap(
        "BBB", "BUY", POS, {"AAA": series(A), "BBB": series(A)})


# ---------------- liquidity vacuum ----------------
def v(**o):
    a = dict(bid_depth_vol=800, ask_depth_vol=800, avg_minute_volume=5000,
             spread_pct=0.01, atr_14=0.02, current_price=2.0)
    a.update(o)
    return vac(**a)


def test_healthy_book_holds():
    assert v() is False


def test_wide_spread_exits():
    assert v(spread_pct=0.03) is True


def test_volatility_expands_cap_but_is_bounded():
    assert v(spread_pct=0.04, atr_14=0.06) is False   # cap = 4.5%
    assert v(spread_pct=0.06, atr_14=0.5) is True     # capped at 5%


def test_thin_depth_exits():
    assert v(bid_depth_vol=100, ask_depth_vol=100) is True


def test_no_volume_baseline_exits():
    assert v(avg_minute_volume=0) is True


@pytest.mark.parametrize("bad", [dict(current_price=0), dict(spread_pct=-0.01),
                                 dict(spread_pct=float("nan")), dict(bid_depth_vol=-1),
                                 dict(avg_minute_volume=float("inf"))])
def test_bad_data_exits(bad):
    assert v(**bad) is True


def test_missing_atr_uses_default():
    assert v(atr_14=None) is False
    assert v(atr_14=float("nan"), spread_pct=0.03) is True
