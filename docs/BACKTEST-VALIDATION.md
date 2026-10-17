# Backtest Validation — Simulators Lie in Their Inputs, Not Their Arithmetic

The desk's standing rule: **no simulation is tuned or quoted until its
inputs come from live fills** — measured win rate, realized loss
distribution (not the configured stop), gap-through tail, and non-fill
rate. This note documents why the rule exists, with the desk's own
numbers.

## The catch

An early Monte Carlo scenario model (`backtesting/scenario-model.py`,
preserved in this repo exactly as built) assumed 40–55% win rates and
capped every loss at 2–4%. Its headline profile showed **+1.40%
expectancy per trade**.

The desk's live tape told a different story. Across 91 round trips
(Sep 25 – Oct 9, 2026), measured from broker fills:

| Input | Simulator assumed | Live tape measured |
| --- | --- | --- |
| Win rate | 40–55% | **28.6%** |
| Average win | — | +4.19% |
| Average loss | capped at −2% to −4% | **−4.48%** |
| Losses past the −5% hard line | 0 (impossible in the model) | **21 of 91** |

Plugging the live inputs into the same arithmetic flipped expectancy to
**−2.00% per trade**. The model was not badly coded. Its inputs were
fiction — and no amount of Monte Carlo polish fixes fiction.

## What the harness does differently now

- **Counterfactual replay over real tape.** The production executor
  itself is driven over recorded 1-minute bars with a stubbed broker
  (`backtesting/replay_glnd_excerpt.py`). The code that is tested is
  the code that trades, and runs are deterministic — byte-for-byte
  reproducible ledgers.
- **Non-fills are data.** Sessions where the leaders were close-only at
  the broker, or limits simply never filled, are scored as zero-trade
  outcomes — never silently dropped from the sample.
- **Fill realism in the backtester.** The bar-based backtester
  (`scout/backtester_cli.py`) executes signals T+1 (a signal fills on
  the first bar *strictly after* it), applies adverse slippage and tick
  rounding to market orders, fills limits only when the bar trades
  through, and keeps cash, fees, and equity in exact Decimal cents.
- **Trigger classes are validated before they ship.** Candidate entry
  classes are measured against a historical event census — gated
  events per day, stop-breach rate, gated mean — and ship OFF until
  they pass. Then paper, then probation, then live.
- **Slippage is measured, not assumed.** Backtested P&L is
  cross-checked against live fill logs so the gap between simulated
  and real execution is a number the desk knows, not a surprise it
  discovers.
