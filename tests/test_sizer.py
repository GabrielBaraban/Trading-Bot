"""
Tests for sizer.py — copy-size calculation.

The DexScreener price lookup is replaced with a stub so the sizing logic
(copy ratio, min/max caps, skip reasons) is tested deterministically with
no network access.
"""

import asyncio

import sizer

TOKEN = "0x" + "11" * 20


def _run(coro):
    return asyncio.run(coro)


def _patch_price(monkeypatch, price):
    async def fake_price(session, token):
        return price
    monkeypatch.setattr(sizer, "get_token_price_usd", fake_price)


def _patch_bounds(monkeypatch, ratio=0.01, min_usd=5.0, max_usd=100.0):
    monkeypatch.setattr(sizer.config, "COPY_RATIO", ratio)
    monkeypatch.setattr(sizer.config, "MIN_TRADE_USD", min_usd)
    monkeypatch.setattr(sizer.config, "MAX_TRADE_USD", max_usd)


def test_basic_copy_ratio(monkeypatch):
    _patch_price(monkeypatch, 1000.0)
    _patch_bounds(monkeypatch)

    # 1 token @ $1000 = $1000 original; copy 1% = $10
    res = _run(sizer.calculate_size(None, TOKEN, 10 ** 18, 18))

    assert not res.skip
    assert res.their_amount_usd == 1000.0
    assert res.our_amount_usd == 10.0
    assert res.token_in_price_usd == 1000.0
    assert res.our_amount_raw == 10 ** 16   # (10 / 1000) * 1e18


def test_skips_below_min(monkeypatch):
    _patch_price(monkeypatch, 100.0)
    _patch_bounds(monkeypatch)

    # 1 token @ $100 = $100 original; copy 1% = $1 < $5 min
    res = _run(sizer.calculate_size(None, TOKEN, 10 ** 18, 18))

    assert res.skip
    assert res.skip_reason.startswith("below_min")
    assert res.their_amount_usd == 100.0


def test_caps_at_max(monkeypatch):
    _patch_price(monkeypatch, 1_000_000.0)
    _patch_bounds(monkeypatch)

    # 1 token @ $1M = $1M original; copy 1% = $10k, capped to $100
    res = _run(sizer.calculate_size(None, TOKEN, 10 ** 18, 18))

    assert not res.skip
    assert res.our_amount_usd == 100.0


def test_skips_when_price_unavailable(monkeypatch):
    _patch_price(monkeypatch, None)
    _patch_bounds(monkeypatch)

    res = _run(sizer.calculate_size(None, TOKEN, 10 ** 18, 18))

    assert res.skip
    assert res.skip_reason == "price_unavailable"
