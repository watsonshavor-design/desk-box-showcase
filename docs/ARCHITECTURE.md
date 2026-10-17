# Desk Box — Architecture

Desk Box is a live, multi-agent automated trading desk for U.S. micro-cap
equities, designed, built, and operated by Shavor Watson (Edibudds LLC).
Specialized AI agents ("seats") coordinate through a real-time command
console; a supervised executor trades a live brokerage account; every
money-path change is staged and verified before it is believed.

## Seats and roles

| Seat | Role |
| --- | --- |
| Operator (human) | Owns the account and the reserved decisions — final authority on size, exits on manual positions, and every go-live |
| Coordinating seat | Briefs the builder, audits deliveries line by line, verifies live state, directs execution |
| Trading seat | A second, equal trading lane on the same claim list and rule book |
| Builder | Writes all code that ships or touches money — over-builds past the brief on validation, failure paths, and drills |
| Understudy seat | Trains on checklists and supervised drills before any capital activation |
| Support seats | Research, drafting, strategy and memory — graded input, never the ship candidate |

Role-gated permissions: a seat acts only inside its lane. Executor
commands are accepted only from authorized senders, and the identity
behind a command is resolved from a per-seat token — never from anything
the request body claims (see `sender_identity.py`).

## Data flow

1. The broker gateway streams quotes and Level 2 depth to the scanner.
2. The scanner covers the whole market with no price floor, ranking
   pre-market and intraday movers by gain, float turnover, and bid/ask
   depth. Broker tradability is proven per name (a restriction map marks
   close-only names before capital is ever committed).
3. Candidates are claimed in the room under the claim protocol — claim
   before entry, no lane enters another lane's name, exits only by the
   owning lane.
4. The executor enters only on a pullback-and-hold trigger (never on a
   spike), with the full exit stack armed at entry.
5. Fills are reconciled from the broker — not from internal state — and
   posted to the room the same minute. Account snapshots flow back to
   the console continuously.

## The room (command console)

A FastAPI + WebSocket application (deployed on Railway, GitHub CI/CD)
that every seat — human and AI — shares: posts, commands, fills, and
alerts in one auditable stream. Agents poll cursor feeds and wake only
when addressed (`wake_filter_and_mentions.py`): a scanner feed or an
unaddressed post never wakes a seat, so attention and compute are spent
only where they're needed. Every seat also has a mention queue scanned
over the full server-side history.

## Risk architecture — layered, hard-coded, fail-closed

1. **Entry gates** — tradability proven per name; pullback-and-hold
   trigger required; position sizing from measured risk, not conviction.
2. **Per-position exits** — stop below real support; a −2% drift rule
   closes slow bleeders; a −3.5% broker-side backup stop is placed with
   every entry so the book is protected even if the software dies; a
   −5% absolute hard line sits behind everything.
3. **Per-lane breakers** — each lane pauses, then stops, at its own
   daily loss limits (`scout/breaker.py` — latched, disk-persisted,
   and it reads corrupt or missing state as *tripped*).
4. **Desk-wide circuit breaker** — a daily net-loss limit halts all new
   entries across every lane. Exits always continue.
5. **Uncertainty pauses entries, never exits** — a stale feed, an
   unknown account state, or an unverifiable claim stops new risk, not
   risk reduction.

## Identity & security model

Per-seat tokens compared in constant time; duplicated token values fail
closed to *no identity*; empty or unknown tokens resolve to nothing;
identity is derived exclusively from the presented token. Secrets live
in environment and secret stores — never in code, chat, or this repo.

## Verification discipline

Every change is reported by stage, and the stages are never blurred:
**built → drill-tested → installed → live-verified**. Test suites run
in the hundreds (this showcase's samples alone carry 130 passing
checks). Live state is proven at the process level — enumerate the
actual running instances — and against broker fills, never from a pid
file, a banner, or a builder's say-so. A deployment that cannot be
verified live is reported as exactly that.

## Deployment

GitHub → Railway auto-deploy for the console; a supervised local stack
(executor, supervisor, scanner, snapshot pusher) with single-instance
locks and watchdogs on the operator's machine.
