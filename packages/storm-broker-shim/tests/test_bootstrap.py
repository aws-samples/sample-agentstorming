# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Bootstrap shim tests.

We don't test the deeper hooks (boto3 / anthropic / openai patching)
in unit tests because they need real broker integration; those are
covered in test_e2e_broker.py against a running broker.
"""

import os

import pytest

from storm_broker_shim import bootstrap


def test_disabled_when_no_socket(monkeypatch):
    monkeypatch.delenv("STORM_BROKER_SOCKET", raising=False)
    monkeypatch.setenv("STORM_BROKER", "")
    bootstrap.uninstall()
    bootstrap.install()
    # _INSTALLED stays False because no socket env var
    assert bootstrap._INSTALLED is False


def test_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("STORM_BROKER", "disabled")
    monkeypatch.setenv("STORM_BROKER_SOCKET", "/tmp/nope.sock")  # nosec B108 - mkdtemp creates 0700; /tmp is deliberate because macOS caps AF_UNIX paths at 104 chars and pytest's tmp_path exceeds it
    bootstrap.uninstall()
    bootstrap.install()
    assert bootstrap._INSTALLED is False
