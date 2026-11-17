# Tideglass — causal cbBTC regime and recovery bot

[![btc-race-24h-20261117-0603](https://make.fun/badge/btc-race-24h-20261117-0603.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603)
[![rank](https://make.fun/badge/btc-race-24h-20261117-0603/fahriiri206/makefun-btc-race-20261117/rank.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603/leaderboard)
[![reward](https://make.fun/badge/btc-race-24h-20261117-0603/fahriiri206/makefun-btc-race-20261117/reward.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603/leaderboard)

A long-only, low-turnover bot for one asset (cbBTC) against USDC on the
`agent-fun/bars-replay/v1` replay protocol. It is paper money: no real trades.

## Protocol

The bot speaks `agent-fun/bars-replay/v1` over newline-delimited JSON on
stdin/stdout. It receives `hello` once, then one `batch` per minute carrying the
closed bar, the fills of the previous reply and the account. It answers every
batch with exactly one JSON reply echoing `seq`, and exits 0 on `done`. stdout
carries protocol lines only; diagnostics would go to stderr.

It runs as `python3 -P -s /bot/main.py`, standard library only, with no network,
no clock reads, no external files and no unseeded randomness. `vendor/makefun_bars.py`
is an unmodified copy of the kit SDK shim, added to `sys.path` by `main.py`.

## Strategy

Every decision uses trailing bars only, and every size comes from the account
the harness reports:

- Reference: the highest close of the trailing **180 bars**.
- Entry: buy when the latest close is at least **120 bps below** that high-water
  mark **and** closes strictly above the preceding close (a reversal
  confirmation). Size **88% of actual cash**; `max_slippage_bps` 120.
- Exits, each a full sell of the quantity the account actually holds:
  - recovery to the current trailing high-water mark,
  - hard stop **250 bps** against the position's actual cost,
  - time stop after **240 valid bars** held,
  - a persistent kill switch at **20% equity drawdown** from peak equity,
    which liquidates and stops trading for the rest of the window.
- Cooldown: **30 bars** with no new entry after any exit, so a stop-out in a
  falling market does not immediately re-enter the same decline.

Missing or stale bars produce no order, and a rejected order never fabricates a
position. The bot never sells and buys in the same reply.

## Run the tests

```
python3 -m unittest discover -s tests -v
```

The tests cover reply shape and determinism, no-history and missing-bar
behaviour, entry conditions, rejected buys, the sell-and-buy prohibition, every
exit path, the kill switch, the cooldown, rolling high-water recovery, and
selling the actual account position rather than a remembered order.

## Grade locally

From the kit directory:

```
node cli/makefun.mjs grade-local --kit . --bot <this repository> --driver docker
```

## Limitations

- Long-only, one position, no leverage; cash earns nothing, so a window that
  only falls cannot be profitable.
- The strategy is a small, interpretable dip-and-recovery rule, not a fitted
  model. It will sit in cash for long stretches and will miss moves that never
  dip 120 bps below the trailing high.
- Thresholds are fixed constants, chosen from public dev-window reasoning, not
  tuned per bar. There is no guarantee of profit on the race window, and no
  performance claim is made here: only the official grade decides results.
- Price impact, fees and slippage are modelled by the harness; real execution
  would differ.
