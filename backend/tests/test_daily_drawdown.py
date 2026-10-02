from backend.models.market import Regime
from backend.store.portfolio_store import PortfolioStore


def _acct(equity):
    return {"equity": equity, "buying_power": equity, "balances": {}}


def test_daily_pnl_tracks_equity_change_from_day_start():
    store = PortfolioStore()
    store.update_from_exchange(_acct(10000.0), [], Regime.NORMAL)
    store.update_from_exchange(_acct(9650.0), [], Regime.NORMAL)
    snap = store.get_snapshot()
    assert snap.daily_pnl == -350.0
    assert snap.daily_pnl_pct == -3.5


def test_daily_pnl_resets_on_new_utc_day():
    store = PortfolioStore()
    store.update_from_exchange(_acct(9000.0), [], Regime.NORMAL)
    store._day_key = "1999-01-01"
    store.update_from_exchange(_acct(9100.0), [], Regime.NORMAL)
    assert store.get_snapshot().daily_pnl == 0.0
