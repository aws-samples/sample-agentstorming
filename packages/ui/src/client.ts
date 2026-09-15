// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Browser-side Agent Storming client: Ed25519 keypair + SSE + sign. */

import * as ed from "@noble/ed25519";
import { sha256 } from "@noble/hashes/sha256";
import { fetchEventSource } from "@microsoft/fetch-event-source";

const ENC = new TextEncoder();

function b64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlDecode(s: string): Uint8Array {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const binary = atob((s + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}

function canonicalise(v: any): string {
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

function stripForSigning(env: any): any {
  const out: any = {};
  for (const k of Object.keys(env)) {
    if (k === "seq" || k === "ts_server" || k === "sig") continue;
    out[k] = env[k];
  }
  return out;
}

export interface Session {
  baseUrl: string;
  roomId: string;
  pid: string | null;
  kind: string | null;
  affiliation: string | null;
  accessToken: string | null;
  refreshToken: string | null;
  privKey: Uint8Array | null;
  pubKey: Uint8Array | null;
  since: number;
}

const STORAGE_KEY = "agentstorming.session";

export function loadSession(baseUrl: string, roomId: string): Session {
  const raw = localStorage.getItem(STORAGE_KEY + ":" + roomId);
  if (raw) {
    try {
      const j = JSON.parse(raw);
      return {
        baseUrl,
        roomId,
        pid: j.pid ?? null,
        kind: j.kind ?? null,
        affiliation: j.affiliation ?? null,
        accessToken: j.accessToken ?? null,
        refreshToken: j.refreshToken ?? null,
        privKey: j.privKey ? b64urlDecode(j.privKey) : null,
        pubKey: j.pubKey ? b64urlDecode(j.pubKey) : null,
        since: j.since ?? -1,
      };
    } catch {
      /* fallthrough to fresh session */
    }
  }
  return {
    baseUrl,
    roomId,
    pid: null,
    kind: null,
    affiliation: null,
    accessToken: null,
    refreshToken: null,
    privKey: null,
    pubKey: null,
    since: -1,
  };
}

export function saveSession(s: Session): void {
  localStorage.setItem(
    STORAGE_KEY + ":" + s.roomId,
    JSON.stringify({
      pid: s.pid,
      kind: s.kind,
      affiliation: s.affiliation,
      accessToken: s.accessToken,
      refreshToken: s.refreshToken,
      privKey: s.privKey ? b64url(s.privKey) : null,
      pubKey: s.pubKey ? b64url(s.pubKey) : null,
      since: s.since,
    }),
  );
}

export async function ensureKeypair(s: Session): Promise<void> {
  if (s.privKey && s.pubKey) return;
  s.privKey = ed.utils.randomPrivateKey();
  s.pubKey = await ed.getPublicKeyAsync(s.privKey);
  saveSession(s);
}

/**
 * Redeem an invite. The server infers the kind from the invite record;
 * no dropdown needed. Returns the snapshot event.
 */
export async function claim(s: Session, inviteToken: string): Promise<any> {
  await ensureKeypair(s);
  const body = { invite_token: inviteToken, pubkey: b64url(s.pubKey!) };
  const resp = await fetch(`${s.baseUrl}/v1/rooms/${encodeURIComponent(s.roomId)}/claim`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`claim failed ${resp.status}: ${await resp.text()}`);
  const j = await resp.json();
  s.pid = j.pid;
  s.kind = j.kind ?? null;
  s.affiliation = j.affiliation ?? null;
  s.accessToken = j.access_token;
  s.refreshToken = j.refresh_token;
  saveSession(s);
  return j.snapshot;
}

async function kid(pub: Uint8Array): Promise<string> {
  const d = sha256(pub);
  return Array.from(d.slice(0, 8)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function postEvent(
  s: Session,
  type_: string,
  payload: any,
  opts: { mentions?: string[]; reply_to?: number | null } = {},
): Promise<any> {
  if (!s.privKey || !s.pubKey || !s.pid || !s.accessToken) {
    throw new Error("not authenticated");
  }
  const ts = new Date().toISOString();
  const nonce = b64url(crypto.getRandomValues(new Uint8Array(16)));
  const env: any = {
    id: crypto.randomUUID(),
    type: type_,
    room_id: s.roomId,
    sender: s.pid,
    ts_sender: ts,
    iat: ts,
    nonce,
    reply_to: opts.reply_to ?? null,
    mentions: opts.mentions ?? [],
    payload,
  };
  const canonical = ENC.encode(canonicalise(stripForSigning(env)));
  const sig = await ed.signAsync(canonical, s.privKey);
  env.sig = { alg: "ed25519", kid: await kid(s.pubKey), val: b64url(sig) };
  const resp = await fetch(`${s.baseUrl}/v1/rooms/${encodeURIComponent(s.roomId)}/events`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${s.accessToken}`,
      "Idempotency-Key": crypto.randomUUID(),
    },
    body: JSON.stringify(env),
  });
  if (!resp.ok) throw new Error(`post failed ${resp.status}: ${await resp.text()}`);
  return resp.json();
}

export async function raiseHand(s: Session, hint = ""): Promise<any> {
  return postEvent(s, "org.agentstorming.hand_raised", { hint });
}

export async function postMessage(s: Session, text: string, mentions: string[] = []): Promise<any> {
  return postEvent(s, "org.agentstorming.message", { text }, { mentions });
}

/**
 * Claim the moderator role for this session (requires owner or original-
 * moderator affiliation). Thin wrapper around POST /v1/rooms/{id}/moderation/reclaim.
 */
/**
 * List the public rooms on a server (unauthenticated).
 */
/** Fetch the latest metadata_snapshot for the room (REST). */
export async function fetchSnapshot(s: Session): Promise<any> {
  if (!s.accessToken) throw new Error("not authenticated");
  const resp = await fetch(
    `${s.baseUrl}/v1/rooms/${encodeURIComponent(s.roomId)}/snapshot`,
    { headers: { Authorization: `Bearer ${s.accessToken}` } },
  );
  if (!resp.ok) throw new Error(`snapshot failed ${resp.status}`);
  return resp.json();
}

export async function listPublicRooms(baseUrl: string): Promise<any[]> {
  const resp = await fetch(`${baseUrl}/v1/rooms`);
  if (!resp.ok) return [];
  const j = await resp.json();
  return j.rooms || [];
}

export async function claimModerator(s: Session): Promise<any> {
  if (!s.accessToken) throw new Error("not authenticated");
  const resp = await fetch(
    `${s.baseUrl}/v1/rooms/${encodeURIComponent(s.roomId)}/moderation/reclaim`,
    {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${s.accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({}),
    },
  );
  if (!resp.ok) throw new Error(`claim moderator failed ${resp.status}: ${await resp.text()}`);
  return resp.json();
}

type StreamHandlers = {
  onEvent(raw: any): void;
  onError(err: unknown): void;
  onOpen?(): void;
};

/**
 * Open the SSE stream. Returns a function that closes it.
 *
 * Uses @microsoft/fetch-event-source so we can set Authorization headers
 * (native EventSource can't). Auto-reconnects with Last-Event-ID.
 */
export function openStream(s: Session, handlers: StreamHandlers): () => void {
  if (!s.accessToken) throw new Error("not authenticated");
  const ctrl = new AbortController();
  const url = `${s.baseUrl}/v1/rooms/${encodeURIComponent(s.roomId)}/stream?since=${s.since}`;

  fetchEventSource(url, {
    signal: ctrl.signal,
    headers: {
      "Authorization": `Bearer ${s.accessToken}`,
      "Accept": "text/event-stream",
    },
    openWhenHidden: true,
    async onopen(resp) {
      if (resp.status === 401 && s.refreshToken) {
        const r = await fetch(`${s.baseUrl}/v1/tokens/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: s.refreshToken }),
        });
        if (r.ok) {
          const j = await r.json();
          s.accessToken = j.access_token;
          s.refreshToken = j.refresh_token;
          saveSession(s);
        }
        throw new Error("auth refreshed; retrying");
      }
      if (!resp.ok) {
        throw new Error(`stream open ${resp.status}`);
      }
      handlers.onOpen?.();
    },
    onmessage(msg) {
      if (!msg.data) return;
      try {
        const raw = JSON.parse(msg.data);
        handlers.onEvent(raw);
        if (msg.id) {
          const n = parseInt(msg.id, 10);
          if (!Number.isNaN(n)) {
            s.since = n;
            saveSession(s);
          }
        } else if (typeof raw.seq === "number") {
          s.since = raw.seq;
          saveSession(s);
        }
      } catch (e) {
        handlers.onError(e);
      }
    },
    onerror(err) {
      handlers.onError(err);
      // Fall through to default reconnect.
    },
  }).catch((err) => {
    if ((err as Error)?.name !== "AbortError") handlers.onError(err);
  });

  return () => ctrl.abort();
}
