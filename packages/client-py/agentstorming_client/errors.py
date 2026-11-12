# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Typed exceptions mapped from server error codes."""

from __future__ import annotations


class StormError(Exception):
    code: str = "org.agentstorming.err.internal"
    #: The HTTP status, when the error came from a server response.
    status: int | None = None

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


class ForbiddenError(StormError):
    """The credential is valid but may not do this (ejected, penned, revoked)."""
    code = "org.agentstorming.err.pen_active"


class NotFoundError(StormError):
    code = "org.agentstorming.err.not_found"


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
    "org.agentstorming.err.pen_active": ForbiddenError,
    "org.agentstorming.err.key_revoked": ForbiddenError,
    "org.agentstorming.err.not_found": NotFoundError,
}


def error_from_body(body: dict) -> StormError:
    # The server raises FastAPI HTTPExceptions, which arrive as
    # {"detail": {"code": ...}}; a validation failure arrives as
    # {"detail": [ ... ]}. Reading only a top-level "code" turned every server
    # error, 401 and 403 included, into a generic internal error.
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        body = detail
    elif isinstance(detail, list):
        body = {"code": "org.agentstorming.err.validation", "message": str(detail)[:500]}
    elif isinstance(detail, str):
        body = {"code": "org.agentstorming.err.http", "message": detail}
    code = body.get("code") or "org.agentstorming.err.internal"
    msg = body.get("message") or code
    details = body.get("details") or {}
    if not isinstance(details, dict):
        details = {}
    cls = _CODE_MAP.get(code, StormError)
    err = cls(msg, **details)
    # The instance carries the server's code. The class only groups codes, so
    # .code read from the class reported "internal" for every unmapped code
    # (nonce_invalid, room_full, bad_kind, ...) and pen_active for key_revoked.
    err.code = code
    return err
