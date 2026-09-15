# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Event visibility / whisper filtering.

Single source of truth for "can viewer_pid see this event?" Called by
every endpoint that returns events to a requester: the SSE stream,
the one-shot /sync history, and the /messages time-range history.

Whisper-class events carry a ``payload.target_pid`` (or ``payload.pid``
for system-emitted targeted notifications like ``mute``). Only the
sender and the target are allowed to see them; every other viewer in
the room gets the event dropped.

Moderators MAY additionally maintain a per-viewer *ignore map*
(§12.1a) that suppresses whisper / registration_request traffic from
specific peers in that moderator's own view. The ignore map is passed
by callers as ``ignore_map={ignored_pid: kind}`` where ``kind`` is
``"muted"`` or ``"candidate"``. Other viewers are unaffected.
"""

from __future__ import annotations

from typing import Iterable, Mapping


WHISPER_TYPES = frozenset(
    {
        "org.agentstorming.whisper",
        "org.agentstorming.registration_request",
        "org.agentstorming.go_speak_expired",
        "org.agentstorming.muted",
        "org.agentstorming.mute",
        "org.agentstorming.unmute",
        "org.agentstorming.interview_started",
        "org.agentstorming.interview_ended",
        # Stage 13 addendum — sender + target moderator only.
        "org.agentstorming.security_violation",
        "org.agentstorming.tool_description_changed",
        "org.agentstorming.approval_request",
        "org.agentstorming.approval_granted",
        "org.agentstorming.approval_denied",
        "org.agentstorming.approval_revoked",
        "org.agentstorming.approval_expired",
    }
)

# Types gated by the view-local ignore map (§12.1a). Everything else
# bypasses the ignore filter even if the sender is ignored.
_IGNORE_TYPES = frozenset(
    {
        "org.agentstorming.whisper",
        "org.agentstorming.registration_request",
    }
)


def event_dlp_clean(ev: dict) -> bool:
    """True iff the event has no DLP-pattern matches in its payload.

    The server uses this as a backstop before delivering an event to
    any viewer (Stage 13 addendum). When False, the server SHOULD
    drop the event and emit `org.agentstorming.security_violation`.
    """
    from .dlp import event_dlp_violation
    return event_dlp_violation(ev) is None


def event_visible_to(
    ev: dict,
    viewer_pid: str,
    *,
    ignore_map: Mapping[str, str] | None = None,
    apply_dlp: bool = False,
) -> bool:
    """True iff viewer_pid should receive this event.

    Whisper-class events are visible to:
      - the sender,
      - the ``payload.target_pid`` (the explicit recipient), and
      - the ``payload.pid`` (the subject — e.g. the registration
        candidate for a ``registration_request`` event).

    If ``ignore_map`` is provided (only for a moderator viewer), then
    ``org.agentstorming.whisper`` from any ignored PID is suppressed,
    and ``org.agentstorming.registration_request`` whose
    ``candidate_pid`` is ignored with ``kind=candidate`` is suppressed.
    """
    t = ev.get("type", "")
    sender = ev.get("sender") or ""
    payload = ev.get("payload") or {}

    # DLP backstop (Stage 13 addendum). When apply_dlp is True the
    # caller wants us to drop events that contain credential-shape
    # material. The whisper filter still runs so the offender's
    # whisper isn't broadcast.
    if apply_dlp:
        from .dlp import event_dlp_violation
        if event_dlp_violation(ev) is not None:
            return False

    # Whisper-class gating.
    whisper_visible = True
    if t in WHISPER_TYPES:
        whisper_visible = (
            sender == viewer_pid
            or payload.get("target_pid") == viewer_pid
            or payload.get("pid") == viewer_pid
        )
    if not whisper_visible:
        return False

    # Ignore-map gating — always applied to registration_request /
    # whisper, only filters OUT; never filters IN.
    if ignore_map and t in _IGNORE_TYPES:
        if t == "org.agentstorming.whisper":
            # Suppress any whisper from an ignored peer, regardless of
            # whether the entry is "muted" or "candidate". The whisper
            # is still visible to the sender (handled above).
            if sender != viewer_pid and sender in ignore_map:
                return False
        elif t == "org.agentstorming.registration_request":
            candidate_pid = payload.get("candidate_pid")
            # Viewer who IS the candidate still sees their own request;
            # we only suppress for third-party moderators who ignored
            # the candidate.
            if (
                candidate_pid
                and ignore_map.get(candidate_pid) == "candidate"
                and viewer_pid != candidate_pid
                and sender != viewer_pid
            ):
                return False

    return True


def filter_visible(
    events: Iterable[dict],
    viewer_pid: str,
    *,
    ignore_map: Mapping[str, str] | None = None,
) -> list[dict]:
    return [e for e in events if event_visible_to(e, viewer_pid, ignore_map=ignore_map)]
