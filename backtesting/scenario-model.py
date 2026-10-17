#!/usr/bin/env python3
"""
Daily profit scenario model for Shavor's desk.
Simulates different strategy profiles to find the best path to $300/day.
Starting capital: $992. Hard stop: -4%. KALA/GLND excluded from sim.
"""
import random

CAPITAL = 992
TARGET = 300
SIMS = 10000

def simulate(profile, days=1):
    """Run Monte Carlo sims for a strategy profile. Returns avg daily P&L and hit rate."""
    results = []
    for _ in range(SIMS):
        capital = CAPITAL
        start = capital
        for _ in range(profile['trades_per_day']):
            if capital <= 0:
                break
            # Position size
            size = min(profile['position_pct'] * capital, capital)
            # Win or loss?
            if random.random() < profile['win_rate']:
                gain_pct = random.uniform(*profile['win_range'])
                # Intraday compounding: profits available for next trade
                if profile.get('compound'):
                    capital += size * gain_pct
                else:
                    capital += size * gain_pct
            else:
                loss_pct = random.uniform(*profile['loss_range'])
                capital -= size * loss_pct
                capital = max(capital, 0)
        results.append(capital - start)
    avg = sum(results) / len(results)
    hit_rate = sum(1 for r in results if r >= TARGET) / len(results)
    p10 = sorted(results)[int(len(results) * 0.1)]
    p90 = sorted(results)[int(len(results) * 0.9)]
    return {'avg_pnl': avg, 'hit_rate': hit_rate, 'p10': p10, 'p90': p90}

profiles = {
    'Scalper (take +5%, 20 trades)': {
        'trades_per_day': 20, 'position_pct': 0.25, 'win_rate': 0.55,
        'win_range': (0.03, 0.07), 'loss_range': (0.02, 0.04), 'compound': False,
    },
    'Runner (let ride to +15%, 8 trades)': {
        'trades_per_day': 8, 'position_pct': 0.30, 'win_rate': 0.40,
        'win_range': (0.08, 0.20), 'loss_range': (0.02, 0.04), 'compound': False,
    },
    'Hybrid (partial +5%, trail rest, 12 trades)': {
        'trades_per_day': 12, 'position_pct': 0.30, 'win_rate': 0.50,
        'win_range': (0.05, 0.15), 'loss_range': (0.02, 0.04), 'compound': False,
    },
    'AGGRESSIVE (compound+pyramid+concentrated, 15 trades)': {
        'trades_per_day': 15, 'position_pct': 0.50, 'win_rate': 0.50,
        'win_range': (0.05, 0.20), 'loss_range': (0.02, 0.04), 'compound': True,
    },
    'ULTRA (all-in 2 names, compound, 10 trades)': {
        'trades_per_day': 10, 'position_pct': 0.80, 'win_rate': 0.45,
        'win_range': (0.05, 0.25), 'loss_range': (0.02, 0.04), 'compound': True,
    },
}

print(f"{'Profile':<50} {'Avg P&L':>10} {'Hit $300':>10} {'Worst 10%':>10} {'Best 10%':>10}")
print("=" * 95)
for name, p in profiles.items():
    r = simulate(p)
    print(f"{name:<50} ${r['avg_pnl']:>9.0f} {r['hit_rate']:>9.0%} ${r['p10']:>9.0f} ${r['p90']:>9.0f}")

print("\nKey: Avg P&L = expected daily profit. Hit $300 = % of days hitting target.")
print("Worst 10% / Best 10% = range of outcomes. All sims use -4% max loss per trade.")
