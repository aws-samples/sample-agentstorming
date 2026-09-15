# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Event visibility (whisper filter) — same filter used by /stream, /sync, /messages."""

from agentstorming_server.services.visibility import (
    WHISPER_TYPES,
    event_visible_to,
    filter_visible,
)


def _whisper(sender: str, target: str, text: str = "hi") -> dict:
    return {
        "type": "org.agentstorming.whisper",
        "sender": sender,
        "payload": {"target_pid": target, "text": text},
    }


def _message(sender: str, text: str = "hi") -> dict:
    return {
        "type": "org.agentstorming.message",
        "sender": sender,
        "payload": {"text": text},
    }


def test_normal_messages_always_visible():
    ev = _message("alice@demo")
    assert event_visible_to(ev, "alice@demo")
    assert event_visible_to(ev, "bob@demo")
    assert event_visible_to(ev, "charlie@demo")


def test_whisper_visible_to_sender():
    ev = _whisper("alice@demo", "bob@demo")
    assert event_visible_to(ev, "alice@demo")


def test_whisper_visible_to_target():
    ev = _whisper("alice@demo", "bob@demo")
    assert event_visible_to(ev, "bob@demo")


def test_whisper_hidden_from_third_party():
    ev = _whisper("alice@demo", "bob@demo")
    assert not event_visible_to(ev, "charlie@demo"), (
        "third party must NOT see whisper events"
    )


def test_mute_event_hidden_from_non_target():
    ev = {
        "type": "org.agentstorming.mute",
        "sender": "system",
        "payload": {"target_pid": "bob@demo", "duration_seconds": 60},
    }
    assert event_visible_to(ev, "bob@demo")
    assert not event_visible_to(ev, "charlie@demo")


def test_registration_request_hidden_from_non_target():
    ev = {
        "type": "org.agentstorming.registration_request",
        "sender": "system",
        "payload": {"pid": "candidate@demo", "target_pid": "mod@demo"},
    }
    assert event_visible_to(ev, "mod@demo")
    assert event_visible_to(ev, "candidate@demo")
    assert not event_visible_to(ev, "bystander@demo")


def test_filter_visible_bulk():
    events = [
        _message("alice@demo", "public1"),
        _whisper("alice@demo", "bob@demo", "secret"),
        _message("bob@demo", "public2"),
    ]
    out = filter_visible(events, "charlie@demo")
    assert len(out) == 2, "charlie must only see the two public messages"
    assert all(e["type"] == "org.agentstorming.message" for e in out)


def test_ignore_map_suppresses_whisper_from_ignored_sender():
    """§12.1a — moderator's own whisper stays visible; whispers FROM an
    ignored peer disappear from that moderator's view only."""
    ev_from_alice = _whisper("alice@demo", "mod@demo", "please grant me turn")
    # No ignore map → visible to the moderator.
    assert event_visible_to(ev_from_alice, "mod@demo")
    # With alice ignored → hidden from the moderator.
    assert not event_visible_to(
        ev_from_alice, "mod@demo", ignore_map={"alice@demo": "muted"}
    )
    # Sender always sees their own whispers, even when ignored by the
    # recipient (sender's own stream is unaffected).
    assert event_visible_to(
        ev_from_alice, "alice@demo", ignore_map={"alice@demo": "muted"}
    )


def test_ignore_map_candidate_kind_hides_registration_request():
    req = {
        "type": "org.agentstorming.registration_request",
        "sender": "system",
        "payload": {"candidate_pid": "cand@demo", "pid": "cand@demo", "target_pid": "mod@demo"},
    }
    # Default: moderator sees it.
    assert event_visible_to(req, "mod@demo")
    # kind=muted does NOT suppress registration_request (only whispers).
    assert event_visible_to(req, "mod@demo", ignore_map={"cand@demo": "muted"})
    # kind=candidate suppresses registration_request.
    assert not event_visible_to(req, "mod@demo", ignore_map={"cand@demo": "candidate"})
    # Candidate still sees their own registration request in their buffer.
    assert event_visible_to(req, "cand@demo", ignore_map={"cand@demo": "candidate"})


def test_ignore_map_does_not_affect_non_ignore_types():
    msg = _message("alice@demo", "public")
    # Regular messages pass through even when alice is ignored.
    assert event_visible_to(msg, "mod@demo", ignore_map={"alice@demo": "muted"})


def test_whisper_types_coverage():
    # Regression: every type we consider whisper-class must be in the set.
    must_include = {
        "org.agentstorming.whisper",
        "org.agentstorming.mute",
        "org.agentstorming.unmute",
        "org.agentstorming.registration_request",
        "org.agentstorming.go_speak_expired",
    }
    assert must_include.issubset(WHISPER_TYPES)
