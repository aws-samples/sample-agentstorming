// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Owner-key primitives: init-key (in-memory), request-invite (signed body),
 * and the signed reads listRooms / getRoom / listKeys.
 *
 * Every owner signature covers `op`, the method and API path being called
 * ("GET /v1/owner/rooms"), so the server accepts it on that endpoint only.
 * `op` is signed but never sent: the server rebuilds it from the request.
 * A body request carries nonce/ts/pubkey/sig in the body; a GET carries them
 * in the X-AgentStorming-Owner-* headers. The nonce request is signed too,
 * over `op` and `ts` alone, since it is how the nonce is obtained.
 *
 * In Node.js the caller usually persists the keypair to a file
 * themselves (the TS SDK doesn't prescribe a vault backend, matching
 * the Python SDK's env-driven choice).
 */

import {
  b64url,
  b64urlDecode,
  canonicalise,
  generateKeypair,
  signBlob,
} from "./signing.js";

const ENC = new TextEncoder();

export interface OwnerKeypair {
  priv: Uint8Array;
  pub: Uint8Array;
}

export async function initOwnerKey(): Promise<OwnerKeypair> {
  return generateKeypair();
}

export function serializeOwnerKey(kp: OwnerKeypair): { private_key_b64: string; public_key_b64: string } {
  return { private_key_b64: b64url(kp.priv), public_key_b64: b64url(kp.pub) };
}

export function loadOwnerKey(serialized: { private_key_b64: string; public_key_b64: string }): OwnerKeypair {
  return {
    priv: b64urlDecode(serialized.private_key_b64),
    pub: b64urlDecode(serialized.public_key_b64),
  };
}

export interface OwnerInvite {
  invite_token: string;
  invite_link: string;
  kind: string;
  room_id: string;
  expires_at: string;
}

/** Headers that carry the owner signature on a GET, which has no body. */
export const OWNER_SIG_HEADERS = {
  nonce: "X-AgentStorming-Owner-Nonce",
  ts: "X-AgentStorming-Owner-Ts",
  pubkey: "X-AgentStorming-Owner-Pubkey",
  sig: "X-AgentStorming-Owner-Sig",
} as const;

/**
 * The path as it appears in `op`: "%" becomes "%25", then "?" becomes "%3F".
 * Same rule as the server. Without it a room id containing "?" signed the
 * same `op` as a shorter id followed by a query string.
 */
export function opPath(path: string): string {
  return path.replace(/%/g, "%25").replace(/\?/g, "%3F");
}

let lastTsMs = 0;

/**
 * A timestamp later than the last one this process signed with.
 *
 * Ed25519 is deterministic and toISOString() has millisecond resolution, so
 * two nonce requests started in the same millisecond were byte-identical, and
 * the server, which accepts each signed nonce request once, refused the second
 * with 409. Only a tie or a step back of under a second is bumped: after a
 * larger step back the clock wins again, or every request would stay ahead of
 * the server's ±5 minute window until real time caught up.
 */
export function uniqueTimestamp(): string {
  const now = Date.now();
  lastTsMs = now > lastTsMs || lastTsMs - now > 1000 ? now : lastTsMs + 1;
  return new Date(lastTsMs).toISOString();
}

/**
 * Sign an owner request. `path` is the decoded API path, e.g.
 * "/v1/owner/rooms". Returns the timestamp and signature to send; `op` is
 * signed, not sent. `nonce` is null only for GET /v1/owner/nonce itself.
 */
export async function signOwnerRequest(
  method: string,
  path: string,
  fields: Record<string, any>,
  nonce: string | null,
  priv: Uint8Array,
  ts: string = uniqueTimestamp(),
): Promise<{ ts: string; sig: string }> {
  const payload: Record<string, any> = { ...fields, ts, op: `${method.toUpperCase()} ${opPath(path)}` };
  if (nonce !== null) payload.nonce = nonce;
  const sig = await signBlob(payload, priv);
  return { ts, sig };
}

async function ownerNonce(base: string, kp: OwnerKeypair): Promise<string> {
  const path = "/v1/owner/nonce";
  const { ts, sig } = await signOwnerRequest("GET", path, {}, null, kp.priv);
  const nonceResp = await fetch(`${base}${path}`, {
    headers: {
      [OWNER_SIG_HEADERS.ts]: ts,
      [OWNER_SIG_HEADERS.pubkey]: b64url(kp.pub),
      [OWNER_SIG_HEADERS.sig]: sig,
    },
  });
  if (!nonceResp.ok) throw new Error(`nonce ${nonceResp.status}: ${await nonceResp.text()}`);
  const { nonce } = (await nonceResp.json()) as { nonce: string };
  return nonce;
}

async function signedGet<T>(baseUrl: string, path: string, kp: OwnerKeypair): Promise<T> {
  const base = baseUrl.replace(/\/$/, "");
  const nonce = await ownerNonce(base, kp);
  const { ts, sig } = await signOwnerRequest("GET", path, {}, nonce, kp.priv);
  // Percent-encode the URL; the server decodes it back to the signed path.
  const url = `${base}${path.split("/").map(encodeURIComponent).join("/")}`;
  const resp = await fetch(url, {
    headers: {
      [OWNER_SIG_HEADERS.nonce]: nonce,
      [OWNER_SIG_HEADERS.ts]: ts,
      [OWNER_SIG_HEADERS.pubkey]: b64url(kp.pub),
      [OWNER_SIG_HEADERS.sig]: sig,
    },
  });
  if (!resp.ok) throw new Error(`GET ${path} ${resp.status}: ${await resp.text()}`);
  return (await resp.json()) as T;
}

export async function requestInvite(
  baseUrl: string,
  roomId: string,
  kind: "participant" | "moderator" | "owner",
  kp: OwnerKeypair,
): Promise<OwnerInvite> {
  const base = baseUrl.replace(/\/$/, "");
  const path = "/v1/owner/request-invite";
  const nonce = await ownerNonce(base, kp);
  const fields = { room_id: roomId, kind };
  const { ts, sig } = await signOwnerRequest("POST", path, fields, nonce, kp.priv);
  const resp = await fetch(`${base}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...fields, nonce, ts, pubkey: b64url(kp.pub), sig }),
  });
  if (!resp.ok) throw new Error(`request-invite ${resp.status}: ${await resp.text()}`);
  return (await resp.json()) as OwnerInvite;
}

/** Every active room on the server, private ones included. Owner only. */
export async function listRooms(baseUrl: string, kp: OwnerKeypair): Promise<{ rooms: any[] }> {
  return signedGet(baseUrl, "/v1/owner/rooms", kp);
}

/** One room's state, full config and moderator. Owner only. */
export async function getRoom(baseUrl: string, roomId: string, kp: OwnerKeypair): Promise<any> {
  return signedGet(baseUrl, `/v1/owner/rooms/${roomId}`, kp);
}

/** The server's active owner keys. Owner only. */
export async function listKeys(baseUrl: string, kp: OwnerKeypair): Promise<{ keys: any[] }> {
  return signedGet(baseUrl, "/v1/owner/keys", kp);
}
