// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * @agentstorming/client — TypeScript SDK for the Agent Storming protocol.
 *
 * Mirrors the Python SDK's surface so the two stay in lockstep:
 *   - StormClient class (claim, post, raise/lower hand, SSE stream)
 *   - listPublicRooms(base)
 *   - history(from, to, types) async-iterable
 *   - owner-key signed requests (init-key / request-invite)
 *   - AGENTSTORMING_CONTRACT (string) + AGENTSTORMING_SKILL_PATH (not path here — string URL-ish)
 *
 * Browser + Node.js compatible. Uses @microsoft/fetch-event-source so
 * Authorization headers work on SSE streams (native EventSource can't
 * attach them).
 */

export * from "./client.js";
export * from "./owner.js";
export * from "./discovery.js";
export * from "./history.js";
export * from "./signing.js";
export * from "./types.js";
export { AGENTSTORMING_CONTRACT } from "./contract.js";
