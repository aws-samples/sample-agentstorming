# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The per-test TRUNCATE covers every table the schema creates.

Six tables were once missing from it, owner_keys among them, and no test failed:
rows leaked between cases instead. A leaked owner key is worse than untidy, because
the app seeds its configured owner key only into an empty owner_keys table, so the
next test's signed requests are refused for a reason that has nothing to do with
what that test checks.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..conftest import _TRUNCATE_ALL, _TRUNCATE_TABLES

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema"


def _schema_tables() -> set[str]:
    sql = "\n".join(p.read_text() for p in sorted(SCHEMA_DIR.glob("*.sql")))
    return set(re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?([a-z_]+)", sql, re.I))


def test_truncate_covers_schema():
    assert _schema_tables() == set(_TRUNCATE_TABLES)


def test_truncate_statement_matches_the_list():
    named = set(re.findall(r"[a-z_]+", _TRUNCATE_ALL.removeprefix("TRUNCATE TABLE ").removesuffix("CASCADE")))
    assert named == set(_TRUNCATE_TABLES)
