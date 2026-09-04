# -*- coding: utf-8 -*-
"""
Alias / Module entry point for Vwap EMA BUY strategy.
"""
from __future__ import annotations
import importlib

vwap_ema_buy = importlib.import_module("Vwap EMA BUY")

BaseBrokerClient = vwap_ema_buy.BaseBrokerClient
FyersBrokerAdapter = vwap_ema_buy.FyersBrokerAdapter
SourceAddressAdapter = vwap_ema_buy.SourceAddressAdapter

if __name__ == "__main__":
    vwap_ema_buy.main()
