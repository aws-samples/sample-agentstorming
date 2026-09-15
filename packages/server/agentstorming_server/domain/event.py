# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Event envelope and typed payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ---- signature ---------------------------------------------------------

class Signature(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    alg: Literal["ed25519"]
    kid: str
    val: str


# ---- base envelope -----------------------------------------------------

class Envelope(BaseModel):
    """Storm event envelope, as on the wire.

    ts_sender / ts_server / iat are stored as strings (RFC 3339) to
    guarantee byte-for-byte canonicalisation across the sign/verify
    roundtrip. Pydantic's datetime round-trip can reformat offsets and
    microseconds, which would break the signature.
    """

    model_config = ConfigDict(extra="allow")

    seq: int | None = None
    id: UUID
    type: str
    room_id: str
    sender: str  # PID or "system"
    ts_sender: str
    ts_server: str | None = None
    iat: str
    nonce: str
    reply_to: int | None = None
    mentions: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    sig: Signature


# ---- standard payload schemas ------------------------------------------

class MessagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    grant_id: str | None = None


class AttachmentDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    url: str
    s3_key: str | None = None
    content_type: str
    size_bytes: int
    filename: str
    sha256: str


class AttachmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = ""
    attachments: list[AttachmentDescriptor]
    grant_id: str | None = None


class HandRaisedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hint: str = ""


class HandLoweredPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hand_id: str


class KeyRotationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    old_pubkey: str
    new_pubkey: str
    old_sig: str
    new_sig: str


class KeyRevocationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revoked_pubkey: str


# ---- type registry -----------------------------------------------------

TYPE_MESSAGE = "org.agentstorming.message"
TYPE_ATTACHMENT = "org.agentstorming.attachment"
TYPE_HAND_RAISED = "org.agentstorming.hand_raised"
TYPE_HAND_LOWERED = "org.agentstorming.hand_lowered"
TYPE_GO_SPEAK_GRANTED = "org.agentstorming.go_speak_granted"
TYPE_GO_SPEAK_EXPIRED = "org.agentstorming.go_speak_expired"
TYPE_SPEAKING_EXTENSION_GRANTED = "org.agentstorming.speaking_extension_granted"
TYPE_PARTICIPANT_JOINED = "org.agentstorming.participant_joined"
TYPE_PARTICIPANT_LEFT = "org.agentstorming.participant_left"
TYPE_PARTICIPANT_DISCONNECTED = "org.agentstorming.participant_disconnected"
TYPE_AFFILIATION_CHANGED = "org.agentstorming.affiliation_changed"
TYPE_MODERATOR_CHANGED = "org.agentstorming.moderator_changed"
TYPE_ORIGINAL_MODERATOR_CHANGED = "org.agentstorming.original_moderator_changed"
TYPE_ROOM_FROZEN = "org.agentstorming.room_frozen"
TYPE_ROOM_UNFROZEN = "org.agentstorming.room_unfrozen"
TYPE_ROOM_TERMINATED = "org.agentstorming.room_terminated"
TYPE_MUTE = "org.agentstorming.mute"
TYPE_UNMUTE = "org.agentstorming.unmute"
TYPE_DOCUMENT_UPDATED = "org.agentstorming.document_updated"
TYPE_SUMMARY_UPDATED = "org.agentstorming.summary_updated"
TYPE_METADATA_SNAPSHOT = "org.agentstorming.metadata_snapshot"
TYPE_KEY_ROTATION = "org.agentstorming.key_rotation"
TYPE_KEY_REVOCATION = "org.agentstorming.key_revocation"
TYPE_REGISTRATION_REQUEST = "org.agentstorming.registration_request"
TYPE_REGISTRATION_ACCEPTED = "org.agentstorming.registration_accepted"
TYPE_REGISTRATION_REJECTED = "org.agentstorming.registration_rejected"
TYPE_INTERVIEW_STARTED = "org.agentstorming.interview_started"
TYPE_INTERVIEW_ENDED = "org.agentstorming.interview_ended"
TYPE_WHISPER = "org.agentstorming.whisper"

# Stage 13 addendum: credential vault + tool-execution authorization.
TYPE_SECURITY_VIOLATION = "org.agentstorming.security_violation"
TYPE_TOOL_DESCRIPTION_CHANGED = "org.agentstorming.tool_description_changed"
TYPE_CAPABILITY_CHANGED = "org.agentstorming.capability_changed"
TYPE_ATTESTATION_FAILED = "org.agentstorming.attestation_failed"
TYPE_TOOL_EXECUTED = "org.agentstorming.tool_executed"
TYPE_APPROVAL_REQUEST = "org.agentstorming.approval_request"
TYPE_APPROVAL_GRANTED = "org.agentstorming.approval_granted"
TYPE_APPROVAL_DENIED = "org.agentstorming.approval_denied"
TYPE_APPROVAL_REVOKED = "org.agentstorming.approval_revoked"
TYPE_APPROVAL_EXPIRED = "org.agentstorming.approval_expired"
TYPE_MODE_PROMOTED = "org.agentstorming.mode_promoted"

PARTICIPANT_POSTED_TYPES = frozenset({
    TYPE_MESSAGE,
    TYPE_ATTACHMENT,
    TYPE_HAND_RAISED,
    TYPE_HAND_LOWERED,
    TYPE_KEY_ROTATION,
    TYPE_KEY_REVOCATION,
    TYPE_WHISPER,
})

SYSTEM_POSTED_TYPES = frozenset({
    TYPE_GO_SPEAK_GRANTED,
    TYPE_GO_SPEAK_EXPIRED,
    TYPE_SPEAKING_EXTENSION_GRANTED,
    TYPE_PARTICIPANT_JOINED,
    TYPE_PARTICIPANT_LEFT,
    TYPE_PARTICIPANT_DISCONNECTED,
    TYPE_AFFILIATION_CHANGED,
    TYPE_MODERATOR_CHANGED,
    TYPE_ORIGINAL_MODERATOR_CHANGED,
    TYPE_ROOM_FROZEN,
    TYPE_ROOM_UNFROZEN,
    TYPE_ROOM_TERMINATED,
    TYPE_MUTE,
    TYPE_UNMUTE,
    TYPE_DOCUMENT_UPDATED,
    TYPE_SUMMARY_UPDATED,
    TYPE_METADATA_SNAPSHOT,
    TYPE_REGISTRATION_ACCEPTED,
    TYPE_REGISTRATION_REJECTED,
    TYPE_INTERVIEW_STARTED,
    TYPE_INTERVIEW_ENDED,
    # Stage 13 addendum
    TYPE_SECURITY_VIOLATION,
    TYPE_TOOL_DESCRIPTION_CHANGED,
    TYPE_CAPABILITY_CHANGED,
    TYPE_ATTESTATION_FAILED,
    TYPE_TOOL_EXECUTED,
    TYPE_APPROVAL_REQUEST,
    TYPE_APPROVAL_GRANTED,
    TYPE_APPROVAL_DENIED,
    TYPE_APPROVAL_REVOKED,
    TYPE_APPROVAL_EXPIRED,
    TYPE_MODE_PROMOTED,
})
