"""Tests for config.redacted() — keeping API keys out of logs."""

import config


def test_redacted_masks_the_key():
    url = "wss://base-mainnet.g.alchemy.com/v2/SuperSecretKey123"
    out = config.redacted(url)
    assert "SuperSecretKey123" not in out
    assert out.startswith("wss://base-mainnet.g.alchemy.com/v2/")
    assert out.endswith("…")


def test_redacted_handles_empty_and_short():
    assert config.redacted("") == ""
    # Nothing after the last slash long enough to mask -> unchanged.
    assert config.redacted("wss://host/ab") == "wss://host/ab"
