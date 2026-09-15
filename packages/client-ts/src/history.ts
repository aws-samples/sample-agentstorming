// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import type { StormClient } from "./client.js";

export interface HistoryOptions {
  fromTs?: string;
  toTs?: string;
  types?: string[];
  limitPerPage?: number;
}

/**
 * Time-range paginated history. Async iterable. Auto-follows
 * `continue_from` until the server says there's nothing more.
 */
export async function* history(client: StormClient, opts: HistoryOptions = {}): AsyncGenerator<any> {
  const base = (client as any).baseUrl as string;
  const room = encodeURIComponent((client as any).roomId as string);
  const token = (client as any).accessToken as string | null;
  if (!token) throw new Error("not authenticated");
  let cursor: number | null = null;
  const limit = opts.limitPerPage ?? 500;

  while (true) {
    const url = new URL(`${base}/v1/rooms/${room}/messages`);
    url.searchParams.set("limit", String(limit));
    if (opts.fromTs) url.searchParams.set("from_ts", opts.fromTs);
    if (opts.toTs) url.searchParams.set("to_ts", opts.toTs);
    if (opts.types?.length) url.searchParams.set("types", opts.types.join(","));
    if (cursor != null) url.searchParams.set("cursor", String(cursor));

    const resp = await fetch(url, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!resp.ok) throw new Error(`history ${resp.status}`);
    const j = (await resp.json()) as {
      events: any[];
      has_more: boolean;
      continue_from: number | null;
    };
    for (const e of j.events) yield e;
    if (!j.has_more || j.continue_from == null) return;
    cursor = j.continue_from;
  }
}
