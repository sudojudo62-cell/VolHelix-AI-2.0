"""Quantitative strategy toolkit: trend/volatility signals, ATR levels, and a cost-aware backtester.

Pure functions only (no network, no globals) so everything is unit-testable and safe to call from
the live scan path. Nothing here can increase position size beyond what callers already allow.
"""
