# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""§23.4 capability discovery.

Unauthenticated. Returns the protocol version the server speaks and
the set of optional features this deployment supports. Clients use
this to decide whether an optional feature (e.g. public registration,
attachment refresh, ignoreMuted) is available before calling it.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["capabilities"])


@router.get("/capabilities")
async def get_capabilities(request: Request) -> dict:
    state = request.app.state
    settings = getattr(state, "settings", None)
    return {
        "protocol": "org.agentstorming",
        "protocol_version": "0.1",
        "implementation": "agentstorming-server",
        "implementation_version": "0.1",
        "features": {
            "transport_sse": True,
            "transport_sync_fallback": True,
            "attachments": True,
            "attachments_presign_refresh": True,
            "public_rooms": True,
            "registration_interview": True,
            "owner_signed_requests": True,
            "key_rotation": True,
            "key_revocation": True,
            "ignore_entries": True,
            "moderator_config_patch": True,
            "documents": True,
            "capabilities": True,
        },
        "event_types": [
            "org.agentstorming.message",
            "org.agentstorming.attachment",
            "org.agentstorming.hand_raised",
            "org.agentstorming.hand_lowered",
            "org.agentstorming.go_speak_granted",
            "org.agentstorming.go_speak_expired",
            "org.agentstorming.speaking_extension_granted",
            "org.agentstorming.participant_joined",
            "org.agentstorming.participant_left",
            "org.agentstorming.participant_disconnected",
            "org.agentstorming.affiliation_changed",
            "org.agentstorming.moderator_changed",
            "org.agentstorming.original_moderator_changed",
            "org.agentstorming.room_frozen",
            "org.agentstorming.room_unfrozen",
            "org.agentstorming.room_terminated",
            "org.agentstorming.mute",
            "org.agentstorming.unmute",
            "org.agentstorming.document_updated",
            "org.agentstorming.summary_updated",
            "org.agentstorming.metadata_snapshot",
            "org.agentstorming.key_rotation",
            "org.agentstorming.key_revocation",
            "org.agentstorming.whisper",
            "org.agentstorming.registration_request",
            "org.agentstorming.registration_accepted",
            "org.agentstorming.registration_rejected",
            "org.agentstorming.interview_started",
            "org.agentstorming.interview_ended",
            "org.agentstorming.invite_minted",
            "org.agentstorming.owner_joined",
        ],
        "limits": {
            "attachments_max_bytes": getattr(settings, "attachments_max_bytes", 52_428_800) if settings else 52_428_800,
        },
    }
