"""
Tests for journal.py — SQLite persistence and realized-P&L on close.

Each test points journal.DB_PATH at a throwaway temp database.
"""

import journal
from executor import PaperFill


def _make_fill(**overrides) -> PaperFill:
    base = dict(
        original_tx="0xabc",
        copied_wallet="0x" + "44" * 20,
        dex="uniswap_v3",
        token_in_address="0x" + "11" * 20,
        token_out_address="0x" + "22" * 20,
        token_in_symbol="WETH",
        token_out_symbol="TOSHI",
        token_in_decimals=18,
        token_out_decimals=18,
        their_amount_usd=1000.0,
        our_amount_usd=10.0,
        our_amount_raw=10 ** 16,
        token_in_price_usd=1000.0,
        token_out_price_usd=2.0,
        simulated_price=500.0,
        estimated_gas_usd=0.01,
        gas_price_gwei=0.05,
    )
    base.update(overrides)
    return PaperFill(**base)


def test_record_open_and_close_pnl(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "trades.db")
    journal.init_db()

    trade_id = journal.record_fill(_make_fill())
    assert trade_id == 1

    open_trades = journal.get_open_trades()
    assert len(open_trades) == 1
    assert open_trades[0]["status"] == "open"

    # Entry token_out price $2 → 5 tokens bought with $10.
    # Exit at $4 → position worth $20 → P&L = 20 - 10 - 0.01 gas = $9.99.
    journal.close_trade(trade_id, exit_price_usd=4.0, exit_simulated_price=250.0)

    closed = journal.get_trade(trade_id)
    assert closed["status"] == "closed"
    assert round(closed["pnl_usd"], 2) == 9.99
    assert closed["pnl_pct"] > 0
    assert journal.get_open_trades() == []


def test_record_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "trades.db")
    journal.init_db()

    journal.record_skip(
        copied_wallet="0x" + "44" * 20,
        original_tx="0xabc",
        token_in="0x" + "11" * 20,
        token_out="0x" + "22" * 20,
        reason="below_min",
        their_amount_usd=1.0,
    )

    with journal._connect() as conn:
        rows = conn.execute("SELECT * FROM skipped").fetchall()

    assert len(rows) == 1
    assert rows[0]["reason"] == "below_min"
