// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Ed25519 + JCS canonicalisation — byte-identical with the Python SDK.
 */

import * as ed from "@noble/ed25519";
import { sha256 } from "@noble/hashes/sha256";

const ENC = new TextEncoder();

export function b64url(bytes: Uint8Array): string {
  // Works in both browsers (btoa) and Node 20+ (btoa exists).
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64urlDecode(s: string): Uint8Array {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const binary = atob((s + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}

export function canonicalise(v: any): string {
  if (v === null || v === undefined) return "null";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") {
    if (Number.isInteger(v)) return String(v);
    return JSON.stringify(v);
  }
  if (typeof v === "string") return JSON.stringify(v);
  if (Array.isArray(v)) return "[" + v.map(canonicalise).join(",") + "]";
  if (typeof v === "object") {
    const keys = Object.keys(v).sort();
    return "{" + keys.map((k) => JSON.stringify(k) + ":" + canonicalise(v[k])).join(",") + "}";
  }
  throw new Error("unsupported type: " + typeof v);
}

const _BLACKLIST = new Set(["seq", "ts_server", "sig"]);

export function stripForSigning(env: any): any {
  const out: any = {};
  for (const k of Object.keys(env)) {
    if (!_BLACKLIST.has(k)) out[k] = env[k];
  }
  return out;
}

export async function kidOf(pub: Uint8Array): Promise<string> {
  const d = sha256(pub);
  return Array.from(d.slice(0, 8))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export async function signEnvelope(env: any, priv: Uint8Array, pub: Uint8Array): Promise<any> {
  const canonical = ENC.encode(canonicalise(stripForSigning(env)));
  const sig = await ed.signAsync(canonical, priv);
  return { ...env, sig: { alg: "ed25519", kid: await kidOf(pub), val: b64url(sig) } };
}

export async function signBlob(body: Record<string, any>, priv: Uint8Array): Promise<string> {
  const canonical = ENC.encode(canonicalise(body));
  const sig = await ed.signAsync(canonical, priv);
  return b64url(sig);
}

export async function generateKeypair(): Promise<{ priv: Uint8Array; pub: Uint8Array }> {
  const priv = ed.utils.randomPrivateKey();
  const pub = await ed.getPublicKeyAsync(priv);
  return { priv, pub };
}
