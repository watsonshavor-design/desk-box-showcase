# Quantitative Trading Systems — Architecture Samples

Sanitized code samples from **Desk Box**, a live multi-agent automated
trading desk for U.S. micro-cap equities, designed, built, and operated
by **Shavor Watson** (Edibudds LLC, Taylors, SC).

These are architecture and risk-engineering samples only — no live
order-execution path, no broker credentials, no account information, no
production configuration. The production repository is private.

## Contents

**Safety & risk package — `scout/`**

| File | What it shows |
| --- | --- |
| `scout/backtester_cli.py` | Bar-based backtesting harness with exact Decimal accounting, T+1 execution, and slippage/tick modeling |
| `scout/breaker.py` | Latched, disk-persisted daily-loss circuit breaker — fails closed on missing or corrupt state |
| `scout/gate.py` | Choke-point pre-trade execution gate (entries and exits take different paths) |
| `scout/pdt.py` | Pattern-day-trader rule tracking |
| `scout/risk_math.py` | Position-sizing and risk calculations |
| `scout/premarket_scanner.py` | Pre-market momentum scanner (float, volume, catalyst filters) |
| `scout/ticks.py` | Tick-size and rounding primitives |
| `scout/safety.py` | One import surface for the safety core |

**Console & identity samples**

| File | What it shows |
| --- | --- |
| `sender_identity.py` | Per-seat identity resolution for the agent command channel: constant-time token comparison, fail-closed duplicates, body-supplied identity never trusted |
| `wake_filter_and_mentions.py` | Production FastAPI excerpt: cursor-paginated wake filter and per-seat mention queue from the multi-agent command room |

**Backtesting — `backtesting/`**

| File | What it shows |
| --- | --- |
| `backtesting/replay_glnd_excerpt.py` | Counterfactual replay harness that drives the real production executor over a recorded 1-minute tape with a stubbed broker |
| `backtesting/scenario-model.py` | An early Monte Carlo scenario model — preserved as built, flaw and all (see the validation notes) |

**Tests** — `test_*.py` at the repo root: **130 checks, all passing**
(`python -m pytest .`). Every module ships with its tests; behavior is
verified, not assumed.

## Documents

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — seats and roles, data
  flow, the layered risk architecture, the identity & security model,
  and the desk's staged verification discipline.
- [`docs/BACKTEST-VALIDATION.md`](docs/BACKTEST-VALIDATION.md) — how
  the desk validates simulations against live fill data, including the
  day its own simulator was caught lying: assumed inputs said +1.40%
  expectancy; live tape said −2.00%.

## Design principles

- Risk controls are hard-coded and fail-closed: an uncertain state
  pauses entries, never exits.
- Identity comes from credentials, never from claims — a request body
  that says who it is from proves nothing.
- Simulators must earn their inputs: backtest assumptions are replaced
  with measured live-tape statistics (win rate, realized losses,
  gap-throughs, non-fills) before any number is quoted.
- A change is reported by stage — built, drill-tested, installed,
  live-verified — and the stages are never blurred.

## Context

Desk Box is a multi-agent trading console (Python / FastAPI /
WebSockets, containerized, deployed on Railway) that coordinates
specialized research, development, strategy, and execution roles, with
broker-API execution and staged live verification of every money-path
change.

**Contact:** Shavor Watson — watsonshavor@gmail.com
