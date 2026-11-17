"""Tests for the Tideglass strategy and its bars-replay protocol behaviour."""

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "vendor"))

import makefun_bars  # noqa: E402
import main  # noqa: E402

SYMBOL = "CBBTC"
QUOTE_SCALE = 6
PRICE_SCALE = 5
QTY_SCALE = 8
P0 = 10_000_000  # 100.00000
DEPTH = "800000000000"
START = 1_000_000_000


def hello():
    return {
        "type": "hello",
        "protocol": "agent-fun/bars-replay/v1",
        "seed": "7",
        "symbols": [{"symbol": SYMBOL, "mint": "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij",
                     "price_scale": PRICE_SCALE, "qty_scale": QTY_SCALE}],
        "capital": {"quote": "USDC", "quote_scale": QUOTE_SCALE, "quote_units": str(START)},
        "bar_interval": "1m",
        "first_close_ms": 1_000_000,
        "caps": {"max_open_positions": 1, "max_orders_per_bar": 16,
                 "max_weight_bps": 10000, "shorts": False, "leverage": False},
        "fees": {"fee_bps": 4, "slippage_bps": 2, "min_notional_quote_units": "1000000"},
        "latency_bars": 1,
    }


def bar(t, close, prev_close=None):
    o = prev_close if prev_close is not None else close
    return {"symbol": SYMBOL, "t": t, "o": str(o), "h": str(max(o, close)),
            "l": str(min(o, close)), "c": str(close), "v": None,
            "o_depth": [DEPTH], "c_depth": [DEPTH]}


def account(cash, positions):
    equity = cash
    for pos in positions:
        equity += pos["qty"] * int(pos["close"]) * 10 ** QUOTE_SCALE // 10 ** (QTY_SCALE + PRICE_SCALE)
    return {"cash_quote_units": str(cash), "positions": positions,
            "equity_quote_units": str(equity)}


def holding(qty, cost, close):
    return {"symbol": SYMBOL, "qty": str(qty), "cost_quote_units": str(cost), "close": str(close)}


class Rig:
    """Drives one Strategy over synthetic batches exactly as run() would."""

    def __init__(self):
        self.state = makefun_bars.BarsState(hello())
        self.strategy = main.Strategy(self.state)
        self.seq = -1
        self.t = 0
        self.cash = START
        self.positions = []
        self.last_close = P0

    def step(self, close, cash=None, positions=None, bars=True):
        self.seq += 1
        self.t += 60_000
        if cash is not None:
            self.cash = cash
        if positions is not None:
            self.positions = positions
        self.last_close = close
        wire_positions = []
        for p in self.positions:
            wire_positions.append({"symbol": SYMBOL, "qty": str(p["qty"]),
                                   "cost_quote_units": str(p["cost"])})
        batch = {"type": "batch", "seq": self.seq, "t": self.t,
                 "bars": [bar(self.t, close)] if bars else [],
                 "fills": [], "account": account(self.cash, [
                     {"symbol": SYMBOL, "qty": p["qty"], "cost_quote_units": p["cost"],
                      "close": str(close)} for p in self.positions])}
        self.state.apply_batch(batch)
        return self.strategy.on_batch(self.state, batch)


def warm(rig, n, price=P0):
    orders = []
    for _ in range(n):
        orders = rig.step(price)
    return orders


class TestProtocolShape(unittest.TestCase):
    def test_no_orders_before_full_history(self):
        rig = Rig()
        for _ in range(main.Strategy.LOOKBACK - 1):
            self.assertEqual(rig.step(P0), [])
        self.assertEqual(rig.strategy.held_bars, 0)

    def test_missing_bar_sends_no_order(self):
        rig = Rig()
        warm(rig, main.Strategy.LOOKBACK)
        self.assertEqual(rig.step(P0 * 9850 // 10000, bars=False), [])

    def test_reply_encodes_one_line_per_batch(self):
        line = makefun_bars.encode_reply(3, [])
        self.assertEqual(line, '{"type":"reply","seq":3,"orders":[]}')
        self.assertNotIn("\n", line)

    def test_deterministic_replies(self):
        def run_once():
            rig = Rig()
            warm(rig, main.Strategy.LOOKBACK)
            rig.step(P0 * 9850 // 10000)
            return rig.step(P0 * 9870 // 10000)
        first, second = run_once(), run_once()
        self.assertEqual(makefun_bars.encode_reply(0, first),
                         makefun_bars.encode_reply(0, second))


class TestEntry(unittest.TestCase):
    def dip_then_up(self, rig):
        warm(rig, main.Strategy.LOOKBACK)
        rig.step(P0 * 9850 // 10000)
        return rig.step(P0 * 9870 // 10000)

    def test_entry_on_deep_dip_with_up_close(self):
        rig = Rig()
        orders = self.dip_then_up(rig)
        self.assertEqual(len(orders), 1)
        order = orders[0]
        self.assertEqual(order["side"], "buy")
        self.assertEqual(order["symbol"], SYMBOL)
        self.assertEqual(order["notional_quote_units"], START * main.Strategy.SIZE_PCT // 100)
        self.assertLessEqual(order["max_slippage_bps"], 120)

    def test_no_entry_without_up_close(self):
        rig = Rig()
        warm(rig, main.Strategy.LOOKBACK)
        self.assertEqual(rig.step(P0 * 9870 // 10000), [])
        self.assertEqual(rig.step(P0 * 9850 // 10000), [])

    def test_no_entry_when_dip_too_shallow(self):
        rig = Rig()
        warm(rig, main.Strategy.LOOKBACK)
        rig.step(P0 * 9950 // 10000)
        self.assertEqual(rig.step(P0 * 9960 // 10000), [])

    def test_rejected_buy_does_not_create_position(self):
        rig = Rig()
        self.dip_then_up(rig)
        orders = rig.step(P0 * 9880 // 10000, cash=START, positions=[])
        for order in orders:
            self.assertNotEqual(order["side"], "sell")

    def test_never_sells_and_buys_in_one_reply(self):
        rig = Rig()
        self.dip_then_up(rig)
        qty = 880_000_000
        orders = rig.step(P0, cash=120_000_000, positions=[{"qty": qty, "cost": 870_000_000}])
        sides = [o["side"] for o in orders]
        self.assertNotIn("buy", sides)


class TestExits(unittest.TestCase):
    def entered(self):
        rig = Rig()
        warm(rig, main.Strategy.LOOKBACK)
        rig.step(P0 * 9850 // 10000)
        rig.step(P0 * 9870 // 10000)
        qty = 880_000_000
        rig.step(P0 * 9870 // 10000, cash=120_000_000,
                 positions=[{"qty": qty, "cost": 880_000_000}])
        return rig, qty

    def test_recovery_exit_sells_everything(self):
        rig, qty = self.entered()
        orders = rig.step(P0, cash=120_000_000,
                          positions=[{"qty": qty, "cost": 880_000_000}])
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["side"], "sell")
        self.assertEqual(orders[0]["qty"], qty)

    def test_stop_loss_exit(self):
        rig, qty = self.entered()
        orders = rig.step(P0 * 9600 // 10000, cash=120_000_000,
                          positions=[{"qty": qty, "cost": 880_000_000}])
        self.assertEqual([o["side"] for o in orders], ["sell"])

    def test_time_stop_exit(self):
        rig, qty = self.entered()
        orders = []
        for _ in range(main.Strategy.MAX_HOLD_BARS + 2):
            orders = rig.step(P0 * 9870 // 10000, cash=120_000_000,
                              positions=[{"qty": qty, "cost": 880_000_000}])
            if orders:
                break
        self.assertEqual([o["side"] for o in orders], ["sell"])

    def test_kill_switch_liquidates_and_halts(self):
        rig = Rig()
        warm(rig, main.Strategy.LOOKBACK)
        qty = 880_000_000
        orders = rig.step(P0 * 7000 // 10000, cash=120_000_000,
                          positions=[{"qty": qty, "cost": 880_000_000}])
        self.assertEqual([o["side"] for o in orders], ["sell"])
        self.assertTrue(rig.strategy.halted)
        rig.step(P0 * 9870 // 10000, cash=120_000_000, positions=[])
        rig.step(P0 * 9870 // 10000, cash=120_000_000, positions=[])
        self.assertEqual(rig.step(P0 * 9870 // 10000, cash=120_000_000, positions=[]), [])

    def test_recovery_uses_the_rolling_high_water(self):
        rig, qty = self.entered()
        # The old peak rolls out of the window, so the trailing mark is the
        # new, lower plateau and reaching it is the recovery this bot trades.
        orders = []
        for _ in range(main.Strategy.LOOKBACK + 5):
            orders = rig.step(P0 * 9500 // 10000, cash=120_000_000,
                              positions=[{"qty": qty, "cost": 880_000_000}])
            if orders:
                break
        self.assertEqual([o["side"] for o in orders], ["sell"])

    def test_sell_uses_the_account_position_not_the_order(self):
        rig, _ = self.entered()
        partial = 123_456_789
        orders = rig.step(P0, cash=120_000_000,
                          positions=[{"qty": partial, "cost": 880_000_000}])
        self.assertEqual([o["side"] for o in orders], ["sell"])
        self.assertEqual(orders[0]["qty"], partial)


class TestChurn(unittest.TestCase):
    def test_cooldown_after_stop_out(self):
        rig = Rig()
        warm(rig, main.Strategy.LOOKBACK)
        rig.step(P0 * 9850 // 10000)
        rig.step(P0 * 9870 // 10000)
        qty = 880_000_000
        rig.step(P0 * 9870 // 10000, cash=120_000_000,
                 positions=[{"qty": qty, "cost": 880_000_000}])
        orders = rig.step(P0 * 9600 // 10000, cash=120_000_000,
                          positions=[{"qty": qty, "cost": 880_000_000}])
        self.assertEqual([o["side"] for o in orders], ["sell"])
        # The sell fills; the market is still deep below the mark and ticks up,
        # which is exactly the shape that re-enters straight into another stop.
        for _ in range(main.Strategy.COOLDOWN_BARS):
            self.assertEqual(rig.step(P0 * 9620 // 10000, cash=1_000_000_000, positions=[]), [])
        orders = rig.step(P0 * 9630 // 10000, cash=1_000_000_000, positions=[])
        self.assertEqual([o["side"] for o in orders], ["buy"])


if __name__ == "__main__":
    unittest.main()
