"""
reports.py — Terminal reports generated from the journal DB.

Run standalone:  python reports.py
Or call print_summary() from main.py on demand.
"""

from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich import box
from rich.text import Text

import journal

console = Console()


def _fmt_ts(ts: Optional[float]) -> str:
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _pnl_color(val: Optional[float]) -> str:
    if val is None:
        return "white"
    return "green" if val >= 0 else "red"


# ── Summary ───────────────────────────────────────────────────

def print_summary() -> None:
    trades = journal.get_all_trades()
    closed = [t for t in trades if t["status"] == "closed"]
    open_  = [t for t in trades if t["status"] == "open"]

    if not trades:
        console.print("[yellow]No trades in journal yet.[/yellow]")
        return

    total_pnl      = sum(t["pnl_usd"] or 0 for t in closed)
    total_gas      = sum(t["estimated_gas_usd"] for t in trades)
    winners        = [t for t in closed if (t["pnl_usd"] or 0) > 0]
    losers         = [t for t in closed if (t["pnl_usd"] or 0) <= 0]
    win_rate       = len(winners) / len(closed) * 100 if closed else 0
    avg_pnl        = total_pnl / len(closed) if closed else 0
    best_trade     = max(closed, key=lambda t: t["pnl_usd"] or 0, default=None)
    worst_trade    = min(closed, key=lambda t: t["pnl_usd"] or 0, default=None)
    avg_hold       = (sum(t["hold_seconds"] or 0 for t in closed) / len(closed)) if closed else 0

    console.rule("[bold cyan]Trading Bot — Summary Report[/bold cyan]")

    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    t.add_column("Metric", style="bold")
    t.add_column("Value")

    t.add_row("Total trades",    str(len(trades)))
    t.add_row("  Closed",        str(len(closed)))
    t.add_row("  Open",          str(len(open_)))
    t.add_row("Win rate",        f"{win_rate:.1f}%  ({len(winners)}W / {len(losers)}L)")
    t.add_row("Total P&L",       Text(f"${total_pnl:+.2f}", style=_pnl_color(total_pnl)))
    t.add_row("Avg P&L / trade", Text(f"${avg_pnl:+.2f}", style=_pnl_color(avg_pnl)))
    t.add_row("Total gas paid",  f"${total_gas:.4f}")
    t.add_row("Avg hold time",   f"{avg_hold/60:.1f} min")

    if best_trade:
        t.add_row(
            "Best trade",
            f"#{best_trade['id']}  {best_trade['token_in_symbol']}→"
            f"{best_trade['token_out_symbol']}  "
            + Text(f"${best_trade['pnl_usd']:+.2f}", style="green").plain,
        )
    if worst_trade:
        t.add_row(
            "Worst trade",
            f"#{worst_trade['id']}  {worst_trade['token_in_symbol']}→"
            f"{worst_trade['token_out_symbol']}  "
            + Text(f"${worst_trade['pnl_usd']:+.2f}", style="red").plain,
        )

    console.print(t)


# ── Per-wallet breakdown ───────────────────────────────────────

def print_wallet_breakdown() -> None:
    trades = journal.get_all_trades()
    closed = [t for t in trades if t["status"] == "closed"]
    if not closed:
        console.print("[yellow]No closed trades yet.[/yellow]")
        return

    wallets: dict[str, list] = {}
    for t in closed:
        wallets.setdefault(t["copied_wallet"], []).append(t)

    console.rule("[bold cyan]P&L per Copied Wallet[/bold cyan]")
    tbl = Table(box=box.SIMPLE, padding=(0, 2))
    tbl.add_column("Wallet",   style="dim")
    tbl.add_column("Trades",   justify="right")
    tbl.add_column("Win %",    justify="right")
    tbl.add_column("Total P&L", justify="right")
    tbl.add_column("Avg P&L",  justify="right")
    tbl.add_column("Best",     justify="right")
    tbl.add_column("Worst",    justify="right")

    rows = []
    for wallet, wt in wallets.items():
        pnls    = [t["pnl_usd"] or 0 for t in wt]
        total   = sum(pnls)
        wins    = sum(1 for p in pnls if p > 0)
        wr      = wins / len(wt) * 100
        rows.append((wallet, wt, total, wr, pnls))

    rows.sort(key=lambda r: r[2], reverse=True)

    for wallet, wt, total, wr, pnls in rows:
        avg   = total / len(wt)
        best  = max(pnls)
        worst = min(pnls)
        tbl.add_row(
            wallet[:20] + "…",
            str(len(wt)),
            f"{wr:.0f}%",
            Text(f"${total:+.2f}", style=_pnl_color(total)),
            Text(f"${avg:+.2f}",   style=_pnl_color(avg)),
            Text(f"${best:+.2f}",  style="green"),
            Text(f"${worst:+.2f}", style="red"),
        )

    console.print(tbl)


# ── Full trade log ────────────────────────────────────────────

def print_trade_log(limit: int = 50) -> None:
    trades = journal.get_all_trades()
    trades = trades[-limit:]

    console.rule(f"[bold cyan]Last {len(trades)} Trades[/bold cyan]")
    tbl = Table(box=box.SIMPLE, padding=(0, 1))
    tbl.add_column("ID",      justify="right", style="dim")
    tbl.add_column("Time",    style="dim")
    tbl.add_column("Wallet",  style="dim")
    tbl.add_column("Pair")
    tbl.add_column("Their $",  justify="right")
    tbl.add_column("Our $",    justify="right")
    tbl.add_column("Gas $",    justify="right")
    tbl.add_column("P&L $",    justify="right")
    tbl.add_column("P&L %",    justify="right")
    tbl.add_column("Hold",     justify="right")
    tbl.add_column("Status")

    for t in trades:
        pnl    = t["pnl_usd"]
        pnl_p  = t["pnl_pct"]
        hold_s = t["hold_seconds"]
        hold   = f"{hold_s/60:.0f}m" if hold_s else "—"
        status = t["status"]
        status_style = {"open": "yellow", "closed": "green", "skipped": "dim"}.get(status, "white")

        tbl.add_row(
            str(t["id"]),
            _fmt_ts(t["entry_ts"]),
            t["copied_wallet"][:10] + "…",
            f"{t['token_in_symbol']}→{t['token_out_symbol']}",
            f"${t['their_amount_usd']:.2f}",
            f"${t['our_amount_usd']:.2f}",
            f"${t['estimated_gas_usd']:.4f}",
            Text(f"${pnl:+.2f}", style=_pnl_color(pnl)) if pnl is not None else Text("—"),
            Text(f"{pnl_p:+.1f}%", style=_pnl_color(pnl_p)) if pnl_p is not None else Text("—"),
            hold,
            Text(status, style=status_style),
        )

    console.print(tbl)


# ── Skipped trades ────────────────────────────────────────────

def print_skipped() -> None:
    with journal._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM skipped ORDER BY ts DESC LIMIT 50"
        ).fetchall()

    if not rows:
        console.print("[yellow]No skipped trades.[/yellow]")
        return

    console.rule("[bold cyan]Skipped Trades (last 50)[/bold cyan]")
    tbl = Table(box=box.SIMPLE, padding=(0, 1))
    tbl.add_column("Time",   style="dim")
    tbl.add_column("Wallet", style="dim")
    tbl.add_column("Pair")
    tbl.add_column("Their $",  justify="right")
    tbl.add_column("Reason")

    for r in rows:
        tbl.add_row(
            _fmt_ts(r["ts"]),
            r["copied_wallet"][:10] + "…",
            f"{r['token_in'][:8]}→{r['token_out'][:8]}",
            f"${r['their_amount_usd']:.2f}" if r["their_amount_usd"] else "—",
            r["reason"],
        )
    console.print(tbl)


# ── Cumulative P&L curve (ASCII) ──────────────────────────────

def print_pnl_curve() -> None:
    trades = [t for t in journal.get_all_trades() if t["status"] == "closed"]
    if not trades:
        console.print("[yellow]No closed trades yet.[/yellow]")
        return

    trades.sort(key=lambda t: t["exit_ts"] or 0)
    cumulative = []
    running = 0.0
    for t in trades:
        running += t["pnl_usd"] or 0
        cumulative.append(running)

    console.rule("[bold cyan]Cumulative P&L[/bold cyan]")
    # Simple sparkline using block characters
    lo, hi = min(cumulative), max(cumulative)
    span   = hi - lo or 1
    width  = 60
    blocks = " ▁▂▃▄▅▆▇█"

    line = ""
    step = max(1, len(cumulative) // width)
    for i in range(0, len(cumulative), step):
        val   = cumulative[i]
        idx   = int((val - lo) / span * (len(blocks) - 1))
        color = "green" if val >= 0 else "red"
        line += f"[{color}]{blocks[idx]}[/{color}]"

    final_color = _pnl_color(running)
    console.print(line)
    console.print(f"  Final: [bold {final_color}]${running:+.2f}[/bold {final_color}]  "
                  f"over {len(trades)} closed trades")


# ── CLI entry point ───────────────────────────────────────────

if __name__ == "__main__":
    journal.init_db()
    print_summary()
    print_wallet_breakdown()
    print_trade_log()
    print_skipped()
    print_pnl_curve()
