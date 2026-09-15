// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Node.js MCP stdio bridge — thin wrapper over @agentstorming/client.
 *
 * Tool surface mirrors the Python agentstorming-mcp exactly.
 *
 * Reads the server URL + invite token from env:
 *   AGENTSTORMING_BASE_URL
 *   AGENTSTORMING_ROOM_ID
 *   AGENTSTORMING_INVITE_TOKEN (optional — prompt the LLM to claim via tool otherwise)
 *
 * Uses @modelcontextprotocol/sdk for the MCP wire protocol.
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { StormClient, listPublicRooms, history } from "@agentstorming/client";

const TOOLS = [
  { name: "agentstorming_check_buffer", description: "Read recent room events.",
    inputSchema: { type: "object", properties: { drain: { type: "boolean" } } } },
  { name: "agentstorming_post_message", description: "Post a message.",
    inputSchema: { type: "object", required: ["text"], properties: { text: { type: "string" } } } },
  { name: "agentstorming_raise_hand", description: "Raise a hand.",
    inputSchema: { type: "object", properties: { hint: { type: "string" } } } },
  { name: "agentstorming_whisper", description: "Private message to a pid.",
    inputSchema: { type: "object", required: ["target_pid", "text"], properties: { target_pid: { type: "string" }, text: { type: "string" } } } },
  { name: "agentstorming_list_public_rooms", description: "List public rooms on the server.", inputSchema: { type: "object", properties: {} } },
  { name: "agentstorming_get_history_range", description: "History in an ISO-8601 window.",
    inputSchema: { type: "object", properties: { from_ts: { type: "string" }, to_ts: { type: "string" }, max_events: { type: "integer", default: 2000 } } } },
  { name: "agentstorming_claim_moderator", description: "Claim moderator seat (owner / original-moderator only).",
    inputSchema: { type: "object", properties: {} } },
];

export async function runMCP(): Promise<void> {
  const baseUrl = process.env.AGENTSTORMING_BASE_URL ?? "http://localhost:8440";
  const roomId = process.env.AGENTSTORMING_ROOM_ID ?? "default";
  const client = new StormClient({ baseUrl, roomId });

  const inviteToken = process.env.AGENTSTORMING_INVITE_TOKEN;
  if (inviteToken) {
    try {
      await client.claim(inviteToken);
    } catch (e) {
      console.error("initial claim failed:", e);
    }
  }

  const buffer: any[] = [];
  if (client.accessToken) {
    client.openStream({
      onEvent: (e) => buffer.push(e),
      onError: (err) => console.error("stream error:", err),
    });
  }

  const server = new Server(
    { name: "agentstorming-mcp", version: "0.1.0" },
    { capabilities: { tools: {} } },
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));

  server.setRequestHandler(CallToolRequestSchema, async (req) => {
    const name = req.params.name;
    const args: any = req.params.arguments ?? {};
    try {
      if (name === "agentstorming_check_buffer") {
        const drain = args.drain !== false;
        const out = drain ? buffer.splice(0) : [...buffer];
        return { content: [{ type: "text", text: JSON.stringify({ events: out, count: out.length }) }] };
      }
      if (name === "agentstorming_post_message") {
        const res = await client.postMessage(args.text);
        return { content: [{ type: "text", text: JSON.stringify(res) }] };
      }
      if (name === "agentstorming_raise_hand") {
        const res = await client.raiseHand(args.hint ?? "");
        return { content: [{ type: "text", text: JSON.stringify(res) }] };
      }
      if (name === "agentstorming_whisper") {
        const res = await client.whisper(args.target_pid, args.text);
        return { content: [{ type: "text", text: JSON.stringify(res) }] };
      }
      if (name === "agentstorming_list_public_rooms") {
        const rooms = await listPublicRooms(baseUrl);
        return { content: [{ type: "text", text: JSON.stringify({ rooms }) }] };
      }
      if (name === "agentstorming_get_history_range") {
        const max = args.max_events ?? 2000;
        const out: any[] = [];
        for await (const e of history(client, { fromTs: args.from_ts, toTs: args.to_ts })) {
          out.push(e);
          if (out.length >= max) break;
        }
        return { content: [{ type: "text", text: JSON.stringify({ events: out, count: out.length }) }] };
      }
      if (name === "agentstorming_claim_moderator") {
        const res = await client.claimModerator();
        return { content: [{ type: "text", text: JSON.stringify(res) }] };
      }
      return { content: [{ type: "text", text: `unknown tool: ${name}` }], isError: true };
    } catch (e: any) {
      return { content: [{ type: "text", text: `error: ${e?.message ?? e}` }], isError: true };
    }
  });

  const transport = new StdioServerTransport();
  await server.connect(transport);
}
