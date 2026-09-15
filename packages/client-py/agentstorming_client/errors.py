# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Typed exceptions mapped from server error codes."""

from __future__ import annotations


class StormError(Exception):
    code: str = "org.agentstorming.err.internal"

    def __init__(self, message: str = "", **details):
        super().__init__(message)
        self.message = message
        self.details = details


class AuthError(StormError):
    code = "org.agentstorming.err.auth"


class SignatureError(StormError):
    code = "org.agentstorming.err.signature_invalid"


class MutedError(StormError):
    code = "org.agentstorming.err.muted"


class RoomFrozenError(StormError):
    code = "org.agentstorming.err.room_frozen"


class NoGrantError(StormError):
    code = "org.agentstorming.err.no_grant"


class HandAlreadyRaisedError(StormError):
    code = "org.agentstorming.err.hand_already_raised"

    def __init__(self, message: str = "", *, hand_id: str | None = None, **rest):
        super().__init__(message, hand_id=hand_id, **rest)
        self.hand_id = hand_id


class GrantConflictError(StormError):
    code = "org.agentstorming.err.grant_conflict"


class RateLimitError(StormError):
    code = "org.agentstorming.err.rate_limited"

    def __init__(self, message: str = "", *, retry_after_seconds: float | None = None, **rest):
        super().__init__(message, retry_after_seconds=retry_after_seconds, **rest)
        self.retry_after_seconds = retry_after_seconds


class RoomTerminatedError(StormError):
    code = "org.agentstorming.err.room_terminated"


class InviteError(StormError):
    code = "org.agentstorming.err.invite_invalid"


_CODE_MAP: dict[str, type[StormError]] = {
    "org.agentstorming.err.auth": AuthError,
    "org.agentstorming.err.signature_invalid": SignatureError,
    "org.agentstorming.err.muted": MutedError,
    "org.agentstorming.err.room_frozen": RoomFrozenError,
    "org.agentstorming.err.no_grant": NoGrantError,
    "org.agentstorming.err.hand_already_raised": HandAlreadyRaisedError,
    "org.agentstorming.err.grant_conflict": GrantConflictError,
    "org.agentstorming.err.rate_limited": RateLimitError,
    "org.agentstorming.err.room_terminated": RoomTerminatedError,
    "org.agentstorming.err.invite_invalid": InviteError,
    "org.agentstorming.err.invite_consumed": InviteError,
    "org.agentstorming.err.invite_expired": InviteError,
}


def error_from_body(body: dict) -> StormError:
    code = body.get("code") or "org.agentstorming.err.internal"
    msg = body.get("message") or code
    details = body.get("details") or {}
    cls = _CODE_MAP.get(code, StormError)
    return cls(msg, **details)
