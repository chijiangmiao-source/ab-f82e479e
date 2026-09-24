"""Shared validation constants for the gauge-block API.

Requests are validated from the raw JSON dict in ``main`` (rather than via
Pydantic coercion) so that malformed rows such as non-integer lengths can be
reported with a field-level reason while the exact user input is echoed back.
"""
from __future__ import annotations

import re

# Any non-whitespace characters are allowed (Chinese identifiers included),
# but whitespace would break the solver's line protocol and control characters
# are illegal unescaped in JSON, so both are rejected.  Length 1-32.
ID_PATTERN = re.compile(r"^[^\s\x00-\x1f\x7f]{1,32}$", re.UNICODE)

MIN_BLOCKS, MAX_BLOCKS = 8, 16
MIN_TARGETS, MAX_TARGETS = 2, 4
LENGTH_MIN, LENGTH_MAX = 1, 1_000_000_000
TARGET_MIN, TARGET_MAX = 1, 1_000_000_000
TOL_MIN, TOL_MAX = 0, 1_000_000_000
MAX_BLOCKS_PER_GAP = 6
