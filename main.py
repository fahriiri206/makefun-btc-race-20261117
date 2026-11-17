"""Tideglass: a causal, low-turnover cbBTC dip-and-recovery bot.

The bot trades one long-only asset against USDC on agent-fun/bars-replay/v1. It
measures how far the latest close sits below the highest close of the trailing
window, buys a deep, confirmed displacement, and sells on recovery to that
reference, on a hard stop, on a time stop, or when equity itself is badly hurt.
Every decision uses only bars already delivered; sizes come from the account
the harness reports, never from an assumption that an order filled.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars


class Strategy:
    """Dip entry below a trailing high-water mark, with explicit risk exits."""

    LOOKBACK = 180
    ENTRY_DISP_BPS = 120
    STOP_BPS = 250
    MAX_HOLD_BARS = 240
    COOLDOWN_BARS = 30
    SIZE_PCT = 88
    ENTRY_SLIPPAGE_BPS = 120
    EXIT_SLIPPAGE_BPS = 250
    KILL_DD_BPS = 2000

    def __init__(self, state):
        self.symbol = next(iter(state.symbols))
        self.closes = []
        self.peak_equity = state.equity
        self.halted = False
        self.held_bars = 0
        self.pending_buy = False
        self.exiting = False
        self.cooldown = 0

    def _bar(self, batch):
        for bar in batch.get("bars", ()):
            if bar.get("symbol") == self.symbol:
                return bar
        return None

    def on_batch(self, state, batch):
        bar = self._bar(batch)
        if bar is None:
            return []
        close = int(bar["c"])
        self.closes.append(close)
        if len(self.closes) > self.LOOKBACK:
            del self.closes[0]

        if state.equity > self.peak_equity:
            self.peak_equity = state.equity

        held = state.positions.get(self.symbol)
        qty = held.qty if held else 0

        if qty > 0:
            self.pending_buy = False
            self.held_bars += 1
            return self._exit_orders(state, held, close)

        if self.exiting:
            self.held_bars = 0
            self.exiting = False
        self.pending_buy = False

        if self.halted:
            return []
        if self.cooldown > 0:
            self.cooldown -= 1
            return []
        if state.equity * 10000 <= self.peak_equity * (10000 - self.KILL_DD_BPS):
            self.halted = True
            return []
        if len(self.closes) < self.LOOKBACK:
            return []

        reference = max(self.closes)
        previous = self.closes[-2]
        displaced = close * 10000 <= reference * (10000 - self.ENTRY_DISP_BPS)
        if not (displaced and close > previous):
            return []

        notional = state.cash * self.SIZE_PCT // 100
        if notional < int(state.hello["fees"]["min_notional_quote_units"]):
            return []

        self.held_bars = 0
        self.pending_buy = True
        return [{"side": "buy", "symbol": self.symbol,
                 "notional_quote_units": notional,
                 "max_slippage_bps": self.ENTRY_SLIPPAGE_BPS}]

    def _exit_orders(self, state, held, close):
        if state.equity * 10000 <= self.peak_equity * (10000 - self.KILL_DD_BPS):
            self.halted = True
            return self._sell_all(held.qty)
        if held.cost > 0:
            value = state.value(self.symbol, held.qty)
            if value * 10000 <= held.cost * (10000 - self.STOP_BPS):
                return self._sell_all(held.qty)
        if close >= max(self.closes):
            return self._sell_all(held.qty)
        if self.held_bars >= self.MAX_HOLD_BARS:
            return self._sell_all(held.qty)
        return []

    def _sell_all(self, qty):
        if qty <= 0:
            return []
        self.exiting = True
        self.cooldown = self.COOLDOWN_BARS
        return [{"side": "sell", "symbol": self.symbol, "qty": qty,
                 "max_slippage_bps": self.EXIT_SLIPPAGE_BPS}]


_STRATEGY = None


def on_batch(state, batch):
    global _STRATEGY
    if _STRATEGY is None:
        _STRATEGY = Strategy(state)
    return _STRATEGY.on_batch(state, batch)


if __name__ == "__main__":
    makefun_bars.run(on_batch)
