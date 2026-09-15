# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Outward-facing links come from configuration, not from the Host header.

``request.base_url`` is reconstructed by Starlette from the request's Host
header. Invite links carry a live single-use invite token in the query string,
so a link built from the Host header points wherever the requester asked rather
than where the deployment actually lives. These tests pin the precedence.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agentstorming_server.routers.deps import public_base_url

HOSTILE = "evil.example"
CONFIGURED = "https://storm.example.com"


def _client(configured: str) -> TestClient:
    app = FastAPI()
    app.state.settings = SimpleNamespace(public_base_url=configured)

    @app.get("/probe")
    def probe(request: Request) -> dict[str, str]:
        return {"base": public_base_url(request)}

    return TestClient(app)


def test_falls_back_to_request_base_url_when_unset() -> None:
    resp = _client("").get("/probe")
    assert resp.status_code == 200
    assert resp.json()["base"] == "http://testserver"


def test_configured_value_wins_over_host_header() -> None:
    resp = _client(CONFIGURED).get("/probe", headers={"Host": HOSTILE})
    assert resp.json()["base"] == CONFIGURED


def test_hostile_host_header_reaches_the_link_only_when_unconfigured() -> None:
    """The regression this guards: a Host the operator does not control ending
    up inside a URL that carries a credential."""
    unconfigured = _client("").get("/probe", headers={"Host": HOSTILE}).json()["base"]
    assert HOSTILE in unconfigured, "sanity: base_url really does follow Host"

    configured = _client(CONFIGURED).get("/probe", headers={"Host": HOSTILE}).json()["base"]
    assert HOSTILE not in configured


@pytest.mark.parametrize(
    "configured",
    ["https://storm.example.com/", "https://storm.example.com///", "  https://storm.example.com  "],
)
def test_trailing_slashes_and_whitespace_are_normalised(configured: str) -> None:
    """Callers append "/r/{room}/join?t=..." directly, so a trailing slash would
    produce a double slash in a link people paste into a browser."""
    assert _client(configured).get("/probe").json()["base"] == CONFIGURED


def test_whitespace_only_is_treated_as_unset() -> None:
    assert _client("   ").get("/probe").json()["base"] == "http://testserver"


def test_missing_setting_does_not_raise() -> None:
    """Older config objects predate the setting; the helper must not explode."""
    app = FastAPI()
    app.state.settings = SimpleNamespace()

    @app.get("/probe")
    def probe(request: Request) -> dict[str, str]:
        return {"base": public_base_url(request)}

    assert TestClient(app).get("/probe").json()["base"] == "http://testserver"
