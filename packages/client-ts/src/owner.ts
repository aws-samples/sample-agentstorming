// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Owner-key primitives: init-key (in-memory), request-invite (signed).
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

export async function requestInvite(
  baseUrl: string,
  roomId: string,
  kind: "participant" | "moderator" | "owner",
  kp: OwnerKeypair,
): Promise<OwnerInvite> {
  const base = baseUrl.replace(/\/$/, "");
  const nonceResp = await fetch(`${base}/v1/owner/nonce`);
  if (!nonceResp.ok) throw new Error(`nonce ${nonceResp.status}`);
  const { nonce } = (await nonceResp.json()) as { nonce: string };
  const body = {
    room_id: roomId,
    kind,
    nonce,
    ts: new Date().toISOString(),
  };
  const sig = await signBlob(body, kp.priv);
  const resp = await fetch(`${base}/v1/owner/request-invite`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, pubkey: b64url(kp.pub), sig }),
  });
  if (!resp.ok) throw new Error(`request-invite ${resp.status}: ${await resp.text()}`);
  return (await resp.json()) as OwnerInvite;
}
