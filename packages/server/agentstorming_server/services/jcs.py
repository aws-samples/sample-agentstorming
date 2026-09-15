# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""JSON Canonicalization Scheme (RFC 8785) — minimal, local implementation.

We implement the subset we need: objects with sorted string keys, arrays
preserving order, strings as JSON strings, numbers as shortest round-
trippable form for the values we use (integers and ISO-8601 strings;
we never serialise floats with non-integer representations).
"""

from __future__ import annotations

import json
from typing import Any


def canonicalise(value: Any) -> bytes:
    """Return RFC-8785-compatible bytes for JSON-representable ``value``."""
    return _canon(value).encode("utf-8")


def _canon(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        # Our protocol does not use non-integer floats; emit a plain
        # representation. If needed later, implement RFC 8785 §3.2.2.2.
        if v.is_integer():
            return str(int(v))
        return json.dumps(v, allow_nan=False)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ",".join(_canon(x) for x in v) + "]"
    if isinstance(v, dict):
        items = sorted(v.items(), key=lambda kv: kv[0])
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + _canon(val) for k, val in items) + "}"
    raise TypeError(f"Non-JSON-serialisable type: {type(v).__name__}")
