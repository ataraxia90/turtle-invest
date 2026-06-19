from __future__ import annotations

from dataclasses import dataclass

from turtle_invest.config import Settings


@dataclass(frozen=True)
class SafetyStatus:
    broker_live: bool
    app_live: bool
    live_order_enabled: bool
    message: str


def check_safety(config: Settings) -> SafetyStatus:
    broker_live = config.broker.mode == "live"
    app_live = config.app.env == "live"
    live_order_enabled = broker_live and app_live
    if live_order_enabled:
        message = "Live order execution is enabled."
    elif broker_live:
        message = "Broker is live for read APIs, but order execution is locked by app.env."
    else:
        message = "Broker is not live; order execution is not available."
    return SafetyStatus(
        broker_live=broker_live,
        app_live=app_live,
        live_order_enabled=live_order_enabled,
        message=message,
    )

