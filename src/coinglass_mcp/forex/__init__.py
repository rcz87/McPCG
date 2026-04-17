"""Forex module — retail sentiment (MyFXBook) + institutional (CFTC COT).

Additive module: registers MCP tools alongside existing CoinGlass/Binance/Nansen/Arkham.
Does not modify existing behavior. Safe to load even if MYFXBOOK credentials missing
(tools return clean error envelopes).
"""

from .tools import register_forex_tools, start_forex_pollers, stop_forex_pollers

__all__ = ["register_forex_tools", "start_forex_pollers", "stop_forex_pollers"]
