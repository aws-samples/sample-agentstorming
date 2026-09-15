# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Tests for the server-side DLP backstop (Stage 13 addendum).

Fixtures come from `dlp_shapes`, which assembles credential-shaped strings from
fragments at import time. The DLP backstop must be fed inputs that genuinely
match its patterns, and building them at runtime means no source file here
contains a literal for a secret scanner to flag.
"""

from .dlp_shapes import AWS_ACCESS_KEY, GITHUB_PAT

from agentstorming_server.services.dlp import DLPScanner, event_dlp_violation
from agentstorming_server.services.visibility import event_dlp_clean, event_visible_to


def test_clean_text_passes():
    s = DLPScanner()
    assert s.is_clean("normal message with no secrets")


def test_aws_key_caught():
    s = DLPScanner()
    matches = s.scan(f"here is the key {AWS_ACCESS_KEY}")
    assert any(m.pattern_name == "aws_access_key" for m in matches)


def test_event_with_aws_key_in_text_caught():
    ev = {
        "type": "org.agentstorming.message",
        "sender": "alice@demo",
        "payload": {"text": f"the key is {AWS_ACCESS_KEY} thanks"},
    }
    m = event_dlp_violation(ev)
    assert m is not None and m.pattern_name == "aws_access_key"
    assert event_dlp_clean(ev) is False


def test_event_with_clean_text_passes():
    ev = {
        "type": "org.agentstorming.message",
        "sender": "alice@demo",
        "payload": {"text": "just a normal sentence"},
    }
    assert event_dlp_clean(ev) is True


def test_event_with_secret_in_other_payload_field_caught():
    ev = {
        "type": "org.agentstorming.attachment",
        "sender": "alice",
        "payload": {"text": "ok", "filename": f"{GITHUB_PAT}.tmp"},
    }
    m = event_dlp_violation(ev)
    assert m is not None and m.pattern_name == "github_pat"


def test_apply_dlp_blocks_in_visible_to():
    ev = {
        "type": "org.agentstorming.message",
        "sender": "alice@demo",
        "payload": {"text": AWS_ACCESS_KEY},
    }
    # Without DLP applied, the visibility filter passes (it's not a whisper).
    assert event_visible_to(ev, "bob@demo", apply_dlp=False) is True
    # With DLP applied, the event is blocked from delivery.
    assert event_visible_to(ev, "bob@demo", apply_dlp=True) is False


def test_apply_dlp_lets_clean_messages_through():
    ev = {
        "type": "org.agentstorming.message",
        "sender": "alice@demo",
        "payload": {"text": "this is a normal message"},
    }
    assert event_visible_to(ev, "bob@demo", apply_dlp=True) is True
