from __future__ import annotations

import unittest

from turtle_invest.config import StrategyConfig
from turtle_invest.risk_controls import RiskControlContext, apply_portfolio_risk_limits
from turtle_invest.strategy import Position, SignalAction, SignalReason, StrategySignal


def config() -> StrategyConfig:
    return StrategyConfig(
        risk_per_trade=0.01,
        atr_period=20,
        entry_breakout_days=20,
        exit_breakout_days=10,
        stop_loss_atr_multiple=2.0,
        pyramid_atr_step=0.5,
        max_units_per_symbol=4,
        universe_size=10,
        universe_refresh="yearly",
    )


def buy_signal(symbol: str = "AAPL", quantity: int = 100, price: float = 100, atr: float = 2) -> StrategySignal:
    return StrategySignal(
        symbol=symbol,
        action=SignalAction.BUY,
        reason=SignalReason.ENTRY_BREAKOUT,
        exchange="NASD",
        quantity=quantity,
        reference_price=price,
        atr=atr,
        threshold=99,
        units_after=1,
        message="buy",
    )


class RiskControlTests(unittest.TestCase):
    def test_caps_new_position_to_15_percent(self) -> None:
        adjusted = apply_portfolio_risk_limits(
            [buy_signal(quantity=100, price=100, atr=2)],
            RiskControlContext(total_equity=10000, positions={}, latest_prices={"AAPL": 100}),
            config(),
        )

        self.assertEqual(adjusted[0].quantity, 15)
        self.assertIn("portfolio risk limits", adjusted[0].message)

    def test_caps_symbol_position_to_25_percent(self) -> None:
        adjusted = apply_portfolio_risk_limits(
            [buy_signal(quantity=100, price=100, atr=2)],
            RiskControlContext(
                total_equity=10000,
                positions={"AAPL": Position("AAPL", quantity=20, units=1, last_entry_price=90)},
                latest_prices={"AAPL": 100},
            ),
            config(),
        )

        self.assertEqual(adjusted[0].quantity, 5)

    def test_blocks_when_total_exposure_cap_is_reached(self) -> None:
        adjusted = apply_portfolio_risk_limits(
            [buy_signal(quantity=10, price=100, atr=2)],
            RiskControlContext(
                total_equity=10000,
                positions={"MSFT": Position("MSFT", quantity=95, units=1, last_entry_price=100)},
                latest_prices={"MSFT": 100, "AAPL": 100},
            ),
            config(),
        )

        self.assertEqual(adjusted[0].action, SignalAction.HOLD)
        self.assertEqual(adjusted[0].quantity, 0)

    def test_sell_reduces_projected_exposure_before_buy(self) -> None:
        sell = StrategySignal(
            symbol="MSFT",
            action=SignalAction.SELL,
            reason=SignalReason.EXIT_BREAKDOWN,
            exchange="NASD",
            quantity=10,
            reference_price=100,
            atr=2,
            threshold=99,
            units_after=0,
            message="sell",
        )

        adjusted = apply_portfolio_risk_limits(
            [sell, buy_signal(quantity=20, price=100, atr=2)],
            RiskControlContext(
                total_equity=10000,
                positions={"MSFT": Position("MSFT", quantity=95, units=1, last_entry_price=100)},
                latest_prices={"MSFT": 100, "AAPL": 100},
            ),
            config(),
        )

        self.assertEqual(adjusted[0].action, SignalAction.SELL)
        self.assertEqual(adjusted[1].quantity, 10)


if __name__ == "__main__":
    unittest.main()
