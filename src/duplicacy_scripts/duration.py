"""Duration parsing for retention policy ages and frequencies.

Uses :mod:`pytimeparse2` (see https://pypi.org/project/pytimeparse2/), which
accepts duration strings such as ``1h``, ``5m``, ``1d``, ``1w``, ``30s`` and
compounds like ``1h30m``. Suffixes are case-insensitive (``M`` parses as
minutes, not months; spell months as ``1month`` instead).
"""

from __future__ import annotations

from pytimeparse2 import parse as _pytimeparse2_parse


class DurationError(ValueError):
    """Raised when a duration string cannot be parsed."""


def parse_duration(text: str) -> float:
    """Return the duration in seconds for a duration string such as ``1h``.

    Accepts the suffixes understood by :mod:`pytimeparse2` (``s``, ``m``,
    ``h``, ``d``, ``w``, ``y``, spelled-out units, and compounds such as
    ``1h30m``). Raises :class:`DurationError` for anything else.
    """
    seconds = _pytimeparse2_parse(text)
    if seconds is None:
        raise DurationError(f"invalid duration: {text!r}")
    return seconds
