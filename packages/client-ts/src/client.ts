// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * StormClient — TypeScript SDK.
 */

import { fetchEventSource } from "@microsoft/fetch-event-source";
import {
  b64url,
  b64urlDecode,
  canonicalise,
  generateKeypair,
  signEnvelope,
} from "./signing.js";
import type { ClaimResult, Envelope, PublicRoom } from "./types.js";

export interface ClientOptions {
  baseUrl: string;
  roomId: string;
  accessToken?: string;
  refreshToken?: string;
  privKey?: Uint8Array;
  pubKey?: Uint8Array;
  pid?: string;
  verifyIncoming?: boolean;
}

export class StormClient {
  baseUrl: string;
  roomId: string;
  pid: string | null;
  accessToken: string | null;
  refreshToken: string | null;
  privKey: Uint8Array | null;
  pubKey: Uint8Array | null;
  affiliation: string | null = null;
  kind: string | null = null;
  since = -1;
  private _abort: AbortController | null = null;

  constructor(o: ClientOptions) {
    this.baseUrl = o.baseUrl.replace(/\/$/, "");
    this.roomId = o.roomId;
    this.accessToken = o.accessToken ?? null;
    this.refreshToken = o.refreshToken ?? null;
    this.privKey = o.privKey ?? null;
    this.pubKey = o.pubKey ?? null;
    this.pid = o.pid ?? null;
  }

  async ensureKeypair(): Promise<void> {
    if (this.privKey && this.pubKey) return;
    const kp = await generateKeypair();
    this.privKey = kp.priv;
    this.pubKey = kp.pub;
  }

  async claim(inviteToken: string, runsAs?: "agent" | "human"): Promise<ClaimResult> {
    await this.ensureKeypair();
    const body: any = { invite_token: inviteToken, pubkey: b64url(this.pubKey!) };
    if (runsAs) body.runs_as = runsAs;
    const resp = await fetch(`${this.baseUrl}/v1/rooms/${encodeURIComponent(this.roomId)}/claim`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`claim failed ${resp.status}: ${await resp.text()}`);
    const j = (await resp.json()) as ClaimResult;
    this.pid = j.pid;
    this.kind = j.kind;
    this.affiliation = j.affiliation;
    this.accessToken = j.access_token;
    this.refreshToken = j.refresh_token;
    return j;
  }

  async postEvent(type_: string, payload: any, opts: { mentions?: string[]; reply_to?: number | null } = {}): Promise<any> {
    if (!this.privKey || !this.pubKey || !this.pid || !this.accessToken) {
      throw new Error("not authenticated");
    }
    const ts = new Date().toISOString();
    const nonce = b64url(crypto.getRandomValues(new Uint8Array(16)));
    const env: Envelope = {
      id: crypto.randomUUID(),
      type: type_,
      room_id: this.roomId,
      sender: this.pid,
      ts_sender: ts,
      iat: ts,
      nonce,
      reply_to: opts.reply_to ?? null,
      mentions: opts.mentions ?? [],
      payload,
    } as Envelope;
    const signed = await signEnvelope(env, this.privKey, this.pubKey);
    const resp = await fetch(`${this.baseUrl}/v1/rooms/${encodeURIComponent(this.roomId)}/events`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${this.accessToken}`,
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: JSON.stringify(signed),
    });
    if (!resp.ok) throw new Error(`post failed ${resp.status}: ${await resp.text()}`);
    return resp.json();
  }

  postMessage(text: string, mentions: string[] = []): Promise<any> {
    return this.postEvent("org.agentstorming.message", { text }, { mentions });
  }

  raiseHand(hint = ""): Promise<any> {
    return this.postEvent("org.agentstorming.hand_raised", { hint });
  }

  lowerHand(handId: string): Promise<any> {
    return this.postEvent("org.agentstorming.hand_lowered", { hand_id: handId });
  }

  whisper(targetPid: string, text: string): Promise<any> {
    return this.postEvent("org.agentstorming.whisper", { target_pid: targetPid, text });
  }

  async claimModerator(): Promise<any> {
    if (!this.accessToken) throw new Error("not authenticated");
    const resp = await fetch(
      `${this.baseUrl}/v1/rooms/${encodeURIComponent(this.roomId)}/moderation/reclaim`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${this.accessToken}` },
        body: JSON.stringify({}),
      },
    );
    if (!resp.ok) throw new Error(`claim moderator failed ${resp.status}`);
    return resp.json();
  }

  openStream(handlers: {
    onEvent: (e: any) => void;
    onError: (err: unknown) => void;
    onOpen?: () => void;
  }): () => void {
    if (!this.accessToken) throw new Error("not authenticated");
    this._abort?.abort();
    const ctrl = new AbortController();
    this._abort = ctrl;
    const url = `${this.baseUrl}/v1/rooms/${encodeURIComponent(this.roomId)}/stream?since=${this.since}`;

    fetchEventSource(url, {
      signal: ctrl.signal,
      headers: {
        Authorization: `Bearer ${this.accessToken}`,
        Accept: "text/event-stream",
      },
      openWhenHidden: true,
      onopen: async (resp) => {
        if (!resp.ok) throw new Error(`stream open ${resp.status}`);
        handlers.onOpen?.();
      },
      onmessage: (msg) => {
        if (!msg.data) return;
        try {
          const raw = JSON.parse(msg.data);
          handlers.onEvent(raw);
          if (msg.id) {
            const n = parseInt(msg.id, 10);
            if (!Number.isNaN(n)) this.since = n;
          } else if (typeof raw.seq === "number") {
            this.since = raw.seq;
          }
        } catch (e) {
          handlers.onError(e);
        }
      },
      onerror: (err) => {
        handlers.onError(err);
      },
    }).catch((err) => {
      if ((err as Error)?.name !== "AbortError") handlers.onError(err);
    });

    return () => {
      ctrl.abort();
      this._abort = null;
    };
  }

  stop(): void {
    this._abort?.abort();
    this._abort = null;
  }
}
