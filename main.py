"""
main.py — Entry point for the copy-trading bot.

Pipeline per block:
  watcher  →  decoder (inside watcher)  →  sizer  →  executor  →  journal

Background task: every 60 s, re-price all open positions and close
any where the copied wallet has already sold (future: detect exit tx).
For now the background task simply logs current unrealised P&L so
you can monitor positions in real-time.

Run:
    python main.py

Stop with Ctrl+C.
"""

from __future__ import annotations
import asyncio
import logging
import os
import signal
import time

import aiohttp
from rich.console import Console
from rich.logging import RichHandler
from web3 import AsyncWeb3, WebSocketProvider

import config
import journal
from executor import execute_paper
from sizer import calculate_size, get_token_price_usd
from watcher import watch_swaps
import reports

# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True, markup=True)],
)
log = logging.getLogger("bot")
console = Console()

# How often the background monitor re-prices open positions (seconds)
MONITOR_INTERVAL = int(os.getenv("MONITOR_INTERVAL", "60"))


# ── Main loop ─────────────────────────────────────────────────

async def main() -> None:
    journal.init_db()
    console.rule("[bold green]Copy-Trading Bot — PAPER MODE[/bold green]")
    log.info("Watching wallets: %s", config.WATCHED_WALLETS)

    async with aiohttp.ClientSession() as session:
        async with AsyncWeb3(WebSocketProvider(config.RPC_WSS)) as w3:
            # Start background monitor
            monitor_task = asyncio.create_task(
                _monitor_open_positions(session, w3)
            )

            try:
                async for swap in watch_swaps():
                    await _handle_swap(swap, session, w3)
            except asyncio.CancelledError:
                pass
            finally:
                monitor_task.cancel()
                await asyncio.gather(monitor_task, return_exceptions=True)

    # Final report on shutdown
    console.rule("[bold cyan]Session Report[/bold cyan]")
    reports.print_summary()
    reports.print_trade_log(limit=20)


async def _handle_swap(swap, session: aiohttp.ClientSession, w3: AsyncWeb3) -> None:
    """Process one decoded swap from a watched wallet."""
    # ── Get token decimals for sizing ────────────────────────
    from executor import _token_meta
    meta = await _token_meta(w3, swap.token_in)
    if meta is None:
        journal.record_skip(
            swap.trader, swap.tx_hash,
            swap.token_in, swap.token_out,
            reason="token_meta_unavailable",
        )
        return

    _, in_decimals = meta

    # ── Size the trade ────────────────────────────────────────
    size = await calculate_size(session, swap.token_in, swap.amount_in, in_decimals)

    if size.skip:
        journal.record_skip(
            swap.trader, swap.tx_hash,
            swap.token_in, swap.token_out,
            reason=size.skip_reason,
            their_amount_usd=size.their_amount_usd,
        )
        log.info("[dim]Skipped  %s  reason=%s[/dim]", swap.tx_hash[:12], size.skip_reason)
        return

    # ── Simulate execution ────────────────────────────────────
    fill = await execute_paper(w3, session, swap, size)
    if fill is None:
        journal.record_skip(
            swap.trader, swap.tx_hash,
            swap.token_in, swap.token_out,
            reason="executor_failed",
            their_amount_usd=size.their_amount_usd,
        )
        return

    trade_id = journal.record_fill(fill)

    console.print(
        f"[green]✓ PAPER TRADE #{trade_id}[/green]  "
        f"[bold]{fill.token_in_symbol}→{fill.token_out_symbol}[/bold]  "
        f"our=${fill.our_amount_usd:.2f}  "
        f"their=${fill.their_amount_usd:.2f}  "
        f"gas≈${fill.estimated_gas_usd:.4f}  "
        f"wallet={fill.copied_wallet[:10]}…"
    )


# ── Background: monitor open positions ────────────────────────

async def _monitor_open_positions(
    session: aiohttp.ClientSession,
    w3: AsyncWeb3,
) -> None:
    """
    Every MONITOR_INTERVAL seconds, re-price all open positions
    and log unrealised P&L. Auto-closes positions older than
    MAX_HOLD_HOURS (default 24 h) to keep the journal tidy.
    """
    max_hold = float(os.getenv("MAX_HOLD_HOURS", "24")) * 3600

    while True:
        await asyncio.sleep(MONITOR_INTERVAL)
        open_trades = journal.get_open_trades()
        if not open_trades:
            continue

        log.info("[cyan]Monitoring %d open position(s)…[/cyan]", len(open_trades))
        now = time.time()

        for trade in open_trades:
            trade_id    = trade["id"]
            token_out   = trade["token_out_address"]
            entry_price = trade["token_out_price_usd"] or 0
            our_usd     = trade["our_amount_usd"]
            entry_ts    = trade["entry_ts"]
            sym_in      = trade["token_in_symbol"]
            sym_out     = trade["token_out_symbol"]

            current_price = await get_token_price_usd(session, token_out)
            if current_price is None:
                log.warning("Cannot re-price trade #%d (%s→%s)", trade_id, sym_in, sym_out)
                continue

            # Unrealised P&L
            token_out_qty  = our_usd / entry_price if entry_price else 0
            current_value  = token_out_qty * current_price
            gas            = trade["estimated_gas_usd"]
            unrealised_pnl = current_value - our_usd - gas
            pnl_pct        = (unrealised_pnl / our_usd * 100) if our_usd else 0
            color          = "green" if unrealised_pnl >= 0 else "red"

            log.info(
                "  #%d [bold]%s→%s[/bold]  entry=$%.4f  now=$%.4f  "
                "unrealised=[%s]$%+.2f (%.1f%%)[/%s]",
                trade_id, sym_in, sym_out,
                entry_price, current_price,
                color, unrealised_pnl, pnl_pct, color,
            )

            # Auto-close stale positions
            if now - entry_ts > max_hold:
                log.info("Auto-closing trade #%d (held > %.0f h)", trade_id, max_hold / 3600)
                # For exit price, treat token_out as the output — use token_in price now
                token_in_now = await get_token_price_usd(session, trade["token_in_address"])
                exit_sim     = (token_in_now / current_price) if (token_in_now and current_price) else 0
                journal.close_trade(trade_id, current_price, exit_sim)


# ── Shutdown handling ──────────────────────────────────────────

def _handle_signal(sig, frame) -> None:
    log.info("Caught signal %s — shutting down…", sig)
    for task in asyncio.all_tasks():
        task.cancel()


if __name__ == "__main__":
    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    asyncio.run(main())
