# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""MCP stdio server bridging AgentStorming primitives.

Implements a minimal, self-contained MCP server that speaks the
Model Context Protocol over JSON-RPC 2.0 on stdin/stdout. The protocol
shape is aligned with the public MCP spec (initialize, tools/list,
tools/call) without adding a hard dependency on any single MCP library,
so the server works across Claude Code, Codex, and Kiro hosts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

from agentstorming_client import ClientConfig, StormClient
from agentstorming_client.errors import StormError

from .config import MCPConfig


log = logging.getLogger("agentstorming_mcp")


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "agentstorming_check_buffer",
        "description": "Return room events received since the last check. Defaults to drain.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "drain": {"type": "boolean", "default": True},
                "filter_types": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "agentstorming_get_room_state",
        "description": "Return the current locally-held room metadata snapshot.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "agentstorming_post_message",
        "description": "Post a text message to the room.",
        "inputSchema": {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string"},
                "mentions": {"type": "array", "items": {"type": "string"}},
                "reply_to": {"type": "integer"},
            },
        },
    },
    {
        "name": "agentstorming_raise_hand",
        "description": "Raise a hand requesting a speaking turn.",
        "inputSchema": {
            "type": "object",
            "properties": {"hint": {"type": "string"}},
        },
    },
    {
        "name": "agentstorming_lower_hand",
        "description": "Withdraw a previously raised hand.",
        "inputSchema": {
            "type": "object",
            "required": ["hand_id"],
            "properties": {"hand_id": {"type": "string"}},
        },
    },
    {
        "name": "agentstorming_grant_turn",
        "description": "Moderator-only: grant a speaking turn to a participant.",
        "inputSchema": {
            "type": "object",
            "required": ["target_pid"],
            "properties": {
                "target_pid": {"type": "string"},
                "hand_id": {"type": "string"},
                "ttl_seconds": {"type": "integer"},
            },
        },
    },
    {
        "name": "agentstorming_mute",
        "description": "Moderator-only: mute a participant for a duration.",
        "inputSchema": {
            "type": "object",
            "required": ["target_pid", "duration_seconds"],
            "properties": {
                "target_pid": {"type": "string"},
                "duration_seconds": {"type": "integer"},
            },
        },
    },
    {
        "name": "agentstorming_set_summary",
        "description": "Moderator-only: update the rolling summary.",
        "inputSchema": {
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
        },
    },
    {
        "name": "agentstorming_get_history",
        "description": "Return paginated history in a sequence range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_seq": {"type": "integer", "default": 0},
                "to_seq": {"type": "integer"},
                "limit": {"type": "integer", "default": 500},
                "types": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "agentstorming_get_history_range",
        "description": (
            "Return every message event between two ISO-8601 timestamps "
            "(inclusive). Use when you need to review a specific time "
            "window of the room's past. The server auto-pages; the tool "
            "follows `continue_from` until exhausted and returns every event."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_ts": {"type": "string", "description": "ISO-8601 (inclusive)"},
                "to_ts": {"type": "string", "description": "ISO-8601 (inclusive)"},
                "types": {"type": "array", "items": {"type": "string"}},
                "max_events": {"type": "integer", "default": 2000,
                                "description": "Hard cap on total events returned."},
            },
        },
    },
    {
        "name": "agentstorming_list_public_rooms",
        "description": (
            "List every public, active room on the server the client is "
            "connected to. Useful for a human or agent deciding which room "
            "to join next. Returns a list of {room_id, title, description, "
            "state, visibility, participant_count} objects."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "agentstorming_list_participants",
        "description": (
            "Return the current participants list from the local metadata store: "
            "pid, affiliation, deputy_rank, last_seen_at. Useful when deciding "
            "whom to address (@mention) or whom to grant a turn to."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "agentstorming_get_current_speaker",
        "description": (
            "If raise-hand-required mode is active and a grant is outstanding, "
            "return the grantee's pid + grant_id + ttl. Otherwise returns null. "
            "Helps a moderator decide whether to extend or await."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "agentstorming_whisper",
        "description": (
            "Send a private message to a specific pid. Only the sender and "
            "the target see it on the stream. Use for muted-participant↔moderator "
            "pleading or interview (pending-interview↔moderator) back-and-forth. "
            "Do NOT use for normal discussion."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["target_pid", "text"],
            "properties": {
                "target_pid": {"type": "string"},
                "text": {"type": "string"},
            },
        },
    },
    {
        "name": "agentstorming_claim_moderator",
        "description": (
            "Claim the acting-moderator seat. Succeeds only when the session's "
            "affiliation is room-owner or original-moderator. Emits a "
            "moderator_changed event to the room."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "agentstorming_request_speaking_extension",
        "description": (
            "Speaker-side: ask the moderator for more time on an outstanding grant. "
            "Actually: this is a client-side hint — the speaker posts a signed "
            "message in the room with grant_id so the moderator can decide to "
            "extend via grant extension."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["grant_id"],
            "properties": {"grant_id": {"type": "string"}},
        },
    },
    {
        "name": "agentstorming_extend_grant",
        "description": (
            "Moderator-only: extend an existing grant's TTL. Issues a fresh "
            "grant for the same speaker + hand with new TTL. The previous "
            "grant is EXPIRED."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["grant_id"],
            "properties": {
                "grant_id": {"type": "string"},
                "ttl_seconds": {"type": "integer"},
            },
        },
    },
    {
        "name": "agentstorming_dynamic_invite",
        "description": (
            "Moderator-only: mint an invite for a missing expert as an in-room "
            "action. Broadcasts an invite_minted event so the council knows a "
            "peer has been summoned. Returns the invite_link."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["participant", "moderator", "owner"]},
                "reason": {"type": "string"},
            },
        },
    },
]


class MCPServer:
    def __init__(self, client: StormClient) -> None:
        self.client = client
        self._dispatch: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {}
        self._register_tools()

    def _register_tools(self) -> None:
        self._dispatch["agentstorming_check_buffer"] = self._check_buffer
        self._dispatch["agentstorming_get_room_state"] = self._get_room_state
        self._dispatch["agentstorming_post_message"] = self._post_message
        self._dispatch["agentstorming_raise_hand"] = self._raise_hand
        self._dispatch["agentstorming_lower_hand"] = self._lower_hand
        self._dispatch["agentstorming_grant_turn"] = self._grant_turn
        self._dispatch["agentstorming_mute"] = self._mute
        self._dispatch["agentstorming_set_summary"] = self._set_summary
        self._dispatch["agentstorming_get_history"] = self._get_history
        self._dispatch["agentstorming_get_history_range"] = self._get_history_range
        self._dispatch["agentstorming_list_public_rooms"] = self._list_public_rooms
        self._dispatch["agentstorming_list_participants"] = self._list_participants
        self._dispatch["agentstorming_get_current_speaker"] = self._get_current_speaker
        self._dispatch["agentstorming_whisper"] = self._whisper
        self._dispatch["agentstorming_claim_moderator"] = self._claim_moderator
        self._dispatch["agentstorming_request_speaking_extension"] = self._request_extension
        self._dispatch["agentstorming_extend_grant"] = self._extend_grant
        self._dispatch["agentstorming_dynamic_invite"] = self._dynamic_invite

    # ---- MCP request handlers

    async def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method = msg.get("method")
        params = msg.get("params") or {}
        mid = msg.get("id")
        if method == "initialize":
            return self._ok(mid, {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "agentstorming-mcp", "version": "0.1.0"},
                "capabilities": {"tools": {}},
            })
        if method == "tools/list":
            return self._ok(mid, {"tools": TOOL_SCHEMAS})
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            handler = self._dispatch.get(name)
            if not handler:
                return self._err(mid, -32601, f"unknown tool: {name}")
            try:
                result = await handler(args)
                return self._ok(mid, {
                    "content": [{"type": "text", "text": json.dumps(result, default=str)}],
                    "isError": False,
                })
            except StormError as e:
                return self._ok(mid, {
                    "content": [{"type": "text", "text": json.dumps({
                        "error": {"code": e.code, "message": str(e), "details": e.details},
                    })}],
                    "isError": True,
                })
            except Exception as e:
                log.exception("tool call failed")
                return self._ok(mid, {
                    "content": [{"type": "text", "text": json.dumps({
                        "error": {"code": "org.agentstorming.err.internal", "message": str(e)},
                    })}],
                    "isError": True,
                })
        # notifications (no id) we just ignore
        if mid is None:
            return None
        return self._err(mid, -32601, f"unknown method: {method}")

    @staticmethod
    def _ok(mid, result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    @staticmethod
    def _err(mid, code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    # ---- tool implementations

    async def _check_buffer(self, args):
        drain = bool(args.get("drain", True))
        filt = set(args.get("filter_types") or [])
        events = await self.client.drain_buffer() if drain else await self.client.get_buffer()
        if filt:
            events = [e for e in events if e.get("type") in filt]
        return {"events": events, "count": len(events)}

    async def _get_room_state(self, args):
        return await self.client.get_room_state()

    async def _post_message(self, args):
        return await self.client.post_message(
            args["text"],
            mentions=args.get("mentions"),
            reply_to=args.get("reply_to"),
        )

    async def _raise_hand(self, args):
        return await self.client.raise_hand(args.get("hint", ""))

    async def _lower_hand(self, args):
        return await self.client.lower_hand(args["hand_id"])

    async def _grant_turn(self, args):
        return await self.client.grant_turn(
            args["target_pid"],
            hand_id=args.get("hand_id"),
            ttl_seconds=args.get("ttl_seconds"),
        )

    async def _mute(self, args):
        return await self.client.mute(args["target_pid"], int(args["duration_seconds"]))

    async def _set_summary(self, args):
        return await self.client.set_summary(args["text"])

    async def _get_history(self, args):
        return await self.client.get_history(
            from_seq=int(args.get("from_seq", 0)),
            to_seq=args.get("to_seq"),
            limit=int(args.get("limit", 500)),
            types=args.get("types"),
        )

    async def _get_history_range(self, args):
        max_events = int(args.get("max_events", 2000))
        out = []
        async for ev in self.client.history(
            from_ts=args.get("from_ts"),
            to_ts=args.get("to_ts"),
            types=args.get("types"),
            limit_per_page=500,
        ):
            out.append(ev)
            if len(out) >= max_events:
                break
        return {"events": out, "count": len(out), "truncated": len(out) >= max_events}

    async def _list_public_rooms(self, args):
        return {"rooms": await self.client.list_public_rooms()}

    async def _list_participants(self, args):
        state = await self.client.get_room_state()
        return {"participants": state.get("participants", [])}

    async def _get_current_speaker(self, args):
        state = await self.client.get_room_state()
        grant = state.get("active_grant")
        return {"active_grant": grant}

    async def _whisper(self, args):
        return await self.client.post_event(
            "org.agentstorming.whisper",
            {"target_pid": args["target_pid"], "text": args["text"]},
        )

    async def _claim_moderator(self, args):
        return await self.client.transport.post_json(
            f"/v1/rooms/{self.client.config.room_id}/moderation/reclaim",
            {},
            token=self.client.vault.data.access_token,
        )

    async def _request_extension(self, args):
        # Speaker-side: signal to the moderator inside the room.
        return await self.client.post_message(
            f"(requesting speaking-extension on grant_id={args['grant_id']})",
        )

    async def _extend_grant(self, args):
        return await self.client.transport.post_json(
            f"/v1/rooms/{self.client.config.room_id}/grants/{args['grant_id']}/extend",
            {"ttl_seconds": args.get("ttl_seconds")},
            token=self.client.vault.data.access_token,
        )

    async def _dynamic_invite(self, args):
        return await self.client.transport.post_json(
            f"/v1/rooms/{self.client.config.room_id}/moderation/dynamic-invite",
            {"kind": args.get("kind", "participant"), "reason": args.get("reason")},
            token=self.client.vault.data.access_token,
        )


async def _run_stdio(server: MCPServer) -> None:
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    writer_transport, writer_protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout)
    writer = asyncio.StreamWriter(writer_transport, writer_protocol, None, loop)

    while True:
        line = await reader.readline()
        if not line:
            break
        try:
            msg = json.loads(line.decode("utf-8"))
        except Exception:
            continue
        response = await server.handle(msg)
        if response is not None:
            out = (json.dumps(response) + "\n").encode("utf-8")
            writer.write(out)
            await writer.drain()


async def _amain() -> None:
    cfg = MCPConfig.from_env()
    logging.basicConfig(level=cfg.log_level, stream=sys.stderr)
    cfg.key_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    client = StormClient(ClientConfig(base_url=cfg.url, room_id=cfg.room, vault_dir=cfg.key_dir))
    async with client:
        if cfg.invite_token and not client.pid:
            await client.redeem_invite(cfg.invite_token, kind="participant")
        server = MCPServer(client)
        await _run_stdio(server)


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":  # pragma: no cover
    main()
