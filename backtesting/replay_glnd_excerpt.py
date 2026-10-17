#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Excerpt — counterfactual replay harness (replay_glnd.py, Oct 14 2026).

The harness drives the REAL shipped production executor (loaded through
a test rig: temp copy, stubbed broker, dummy configs — no room, no
laptop, no credentials) over a recorded 1-minute tape, so the code that
is tested is the code that trades. Outputs are deterministic: re-running
reproduces them byte-for-byte. Only the setup, tape loading, and exit
labeling are excerpted here; the tick loop and reporting are omitted.
"""

# --- Original module docstring ----------------------------------------------
# replay_glnd.py — COUNTERFACTUAL replay: "Shavor's plan, running all
# day." The REAL shipped v9.6.1 PlanExecutor (ace_agent_v961.py,
# loaded through the harness_v961 rig: temp copy, stubbed futu, dummy
# configs — no room, no laptop, no credentials) is driven over the
# actual GLND 1-minute tape of 2026-10-14 (Yahoo 1m incl. premarket,
# 04:00-13:43 ET, 584 bars).
#
# Setup (per task): single candidate US.GLND; scanner signal
# READY/IGNITION with the day's published gates score 3/5, split
# UNKNOWN, held-name (GLND is in AUTO_HELD_EVAL); autonomy ON in the
# rig (enabled_fn True, as in the harness). One bar per tick:
# effective price = bar close, cumulative session volume = running
# sum of bar volumes. Fills at the sampled price under the v961
# harness's virtual-broker semantics (buy limit = sampled px x 1.003,
# so entries fill at the sampled price; sells fill at the sampled
# price). Signals are refreshed every 10 minutes to model the
# scanner's continuous published state (candidate episodes go stale
# after 900 s without one); setup label is IGNITION before 09:30 ET,
# READY after — the executor treats the label as advisory and
# requires its own pullback-and-hold confirmation.
#
# Outputs (this directory): route.png + ledger.md. Deterministic:
# re-running reproduces both byte-for-byte except PNG metadata.
#
# Run:  python3 replay_glnd.py
# -----------------------------------------------------------------------------

import csv
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
HERE = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(HERE)
sys.path.insert(0, AUTO_DIR)

import harness_v961 as H  # noqa: E402  (rig: load_agent/Tape/SimBroker/hunt_msg)

CODE = "US.GLND"
TAPE_CSV = os.path.join(AUTO_DIR, "glnd_tape_2026-10-14.csv")
SIGNAL_EVERY_S = 600
FLOAT_M = 33.39


class ReplayClock:
    def __init__(self, t):
        self.t = float(t)

    def now(self):
        return self.t

    def et(self):
        return datetime.fromtimestamp(self.t, ET)

    def set(self, t):
        self.t = float(t)


def load_bars():
    bars = []
    cum = 0
    with open(TAPE_CSV) as f:
        for row in csv.DictReader(f):
            ts = datetime.strptime(row["ts_et"], "%Y-%m-%d %H:%M:%S")
            ts = ts.replace(tzinfo=ET)
            vol = int(row["volume"])
            cum += vol
            bars.append({"t": ts.timestamp(), "et": ts,
                         "px": float(row["close"]), "vol": vol,
                         "cum": cum})
    return bars


def sell_label(why):
    if why.startswith("Harvest bank +2%"):
        return "BANK+2"
    if why.startswith("Harvest bank +4%"):
        return "BANK+4"
    if why.startswith("AGE EXIT"):
        return "AGE"
    if why.startswith("fast cut"):
        return "CUT"
    if why.startswith("drift sale"):
        return "DRIFT"
    if why.startswith("dead-money"):
        return "DEAD-MONEY"
    if "giveback" in why:
        return "GIVEBACK"
    if why.startswith("flat by close"):
        return "FLAT"
    if why.startswith("lane day-stop"):
        return "LANE-STOP"
    return why
