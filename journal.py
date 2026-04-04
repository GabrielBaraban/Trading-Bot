"""
journal.py — Persists every trade decision to a SQLite database.

Schema covers both the entry (when we copy a trade) and the exit
(when the token is sold / position closed). Exit tracking is done
by a background task in main.py that periodically re-prices open
positions and marks them closed once the copied wallet sells.

Database file: trades.db  (created automatically next to this file)
"""

from __future__ import annotations
import logging
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from executor import PaperFill

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "trades.db"

# ── DDL ───────────────────────────────────────────────────────

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS trades (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Timing
    entry_ts            REAL    NOT NULL,   -- unix timestamp of copy
    exit_ts             REAL,               -- unix timestamp of close

    -- Origin
    copied_wallet       TEXT    NOT NULL,
    original_tx         TEXT    NOT NULL,
    dex                 TEXT    NOT NULL,
    is_paper            INTEGER NOT NULL DEFAULT 1,

    -- Tokens
    token_in_address    TEXT    NOT NULL,
    token_out_address   TEXT    NOT NULL,
    token_in_symbol     TEXT    NOT NULL,
    token_out_symbol    TEXT    NOT NULL,
    token_in_decimals   INTEGER NOT NULL,
    token_out_decimals  INTEGER NOT NULL,

    -- Sizing
    their_amount_usd    REAL    NOT NULL,
    our_amount_usd      REAL    NOT NULL,
    our_amount_raw      INTEGER NOT NULL,

    -- Entry prices
    token_in_price_usd  REAL    NOT NULL,
    token_out_price_usd REAL    NOT NULL,
    simulated_price     REAL    NOT NULL,   -- token_in per token_out at entry

    -- Gas
    gas_price_gwei      REAL    NOT NULL,
    estimated_gas_usd   REAL    NOT NULL,

    -- Exit / P&L (filled when position closes)
    exit_price_usd      REAL,               -- token_out USD price at exit
    exit_simulated_price REAL,              -- token_in per token_out at exit
    pnl_usd             REAL,               -- realised P&L after gas
    pnl_pct             REAL,               -- P&L as % of our_amount_usd
    hold_seconds        REAL,               -- how long we held

    -- Status
    status              TEXT    NOT NULL DEFAULT 'open',
    -- 'open' | 'closed' | 'skipped'
    skip_reason         TEXT                -- populated when status='skipped'
)
"""

_CREATE_SKIPPED_TABLE = """
CREATE TABLE IF NOT EXISTS skipped (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              REAL    NOT NULL,
    copied_wallet   TEXT    NOT NULL,
    original_tx     TEXT    NOT NULL,
    token_in        TEXT    NOT NULL,
    token_out       TEXT    NOT NULL,
    their_amount_usd REAL,
    reason          TEXT    NOT NULL
)
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(_CREATE_TABLE)
        conn.execute(_CREATE_SKIPPED_TABLE)
    log.info("Journal DB ready: %s", DB_PATH)


# ── Write ─────────────────────────────────────────────────────

def record_fill(fill: PaperFill) -> int:
    """Insert a new open trade. Returns the row id."""
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO trades (
                entry_ts, copied_wallet, original_tx, dex, is_paper,
                token_in_address, token_out_address,
                token_in_symbol, token_out_symbol,
                token_in_decimals, token_out_decimals,
                their_amount_usd, our_amount_usd, our_amount_raw,
                token_in_price_usd, token_out_price_usd, simulated_price,
                gas_price_gwei, estimated_gas_usd,
                status
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                'open'
            )
            """,
            (
                fill.timestamp,
                fill.copied_wallet,
                fill.original_tx,
                fill.dex,
                int(fill.is_paper),
                fill.token_in_address,
                fill.token_out_address,
                fill.token_in_symbol,
                fill.token_out_symbol,
                fill.token_in_decimals,
                fill.token_out_decimals,
                fill.their_amount_usd,
                fill.our_amount_usd,
                fill.our_amount_raw,
                fill.token_in_price_usd,
                fill.token_out_price_usd,
                fill.simulated_price,
                fill.gas_price_gwei,
                fill.estimated_gas_usd,
            ),
        )
        row_id = cur.lastrowid
    log.info("Journaled trade id=%d  %s→%s  $%.2f",
             row_id, fill.token_in_symbol, fill.token_out_symbol, fill.our_amount_usd)
    return row_id


def record_skip(
    copied_wallet: str,
    original_tx: str,
    token_in: str,
    token_out: str,
    reason: str,
    their_amount_usd: float = 0.0,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO skipped (ts, copied_wallet, original_tx, token_in, token_out,
                                 their_amount_usd, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (time.time(), copied_wallet, original_tx, token_in, token_out,
             their_amount_usd, reason),
        )
    log.debug("Skipped trade  reason=%s  tx=%s", reason, original_tx[:12])


def close_trade(
    trade_id: int,
    exit_price_usd: float,
    exit_simulated_price: float,
) -> None:
    """Mark a trade as closed and calculate P&L."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT our_amount_usd, token_out_price_usd, estimated_gas_usd, entry_ts "
            "FROM trades WHERE id = ?",
            (trade_id,),
        ).fetchone()
        if row is None:
            log.warning("close_trade: id %d not found", trade_id)
            return

        entry_value_usd = row["our_amount_usd"]
        # What our token_out is worth now
        # our_amount_out_usd = our_amount_usd / entry token_out_price * exit_price_usd
        entry_out_price = row["token_out_price_usd"] or exit_price_usd
        token_out_qty   = entry_value_usd / entry_out_price if entry_out_price else 0
        exit_value_usd  = token_out_qty * exit_price_usd
        gas             = row["estimated_gas_usd"]
        pnl_usd         = exit_value_usd - entry_value_usd - gas
        pnl_pct         = (pnl_usd / entry_value_usd * 100) if entry_value_usd else 0
        hold_seconds    = time.time() - row["entry_ts"]

        conn.execute(
            """
            UPDATE trades SET
                exit_ts               = ?,
                exit_price_usd        = ?,
                exit_simulated_price  = ?,
                pnl_usd               = ?,
                pnl_pct               = ?,
                hold_seconds          = ?,
                status                = 'closed'
            WHERE id = ?
            """,
            (time.time(), exit_price_usd, exit_simulated_price,
             pnl_usd, pnl_pct, hold_seconds, trade_id),
        )
    log.info("Closed trade id=%d  pnl=$%.2f (%.1f%%)", trade_id, pnl_usd, pnl_pct)


# ── Read ──────────────────────────────────────────────────────

def get_open_trades() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM trades WHERE status = 'open' ORDER BY entry_ts"
        ).fetchall()


def get_all_trades() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM trades ORDER BY entry_ts"
        ).fetchall()


def get_trade(trade_id: int) -> Optional[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM trades WHERE id = ?", (trade_id,)
        ).fetchone()
