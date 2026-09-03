"""Tests for :mod:`duplicacy_scripts.duration`."""

from __future__ import annotations

import pytest

from duplicacy_scripts.duration import DurationError, parse_duration


class TestParseDuration:
    @pytest.mark.parametrize(
        ("text", "expected_seconds"),
        [
            ("30s", 30.0),
            ("5m", 5 * 60),
            ("1h", 3600.0),
            ("1d", 86400.0),
            ("1w", 7 * 86400),
            ("1y", 365 * 86400),
            ("1h30m", 5400.0),
            ("1 month", 30 * 86400),
        ],
    )
    def test_parses_durations(self, text: str, expected_seconds: float) -> None:
        assert parse_duration(text) == expected_seconds

    @pytest.mark.parametrize("text", ["", "abc", "1x", "1M5", "7 ducks"])
    def test_raises_on_invalid_duration(self, text: str) -> None:
        with pytest.raises(DurationError):
            parse_duration(text)
