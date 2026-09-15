// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import type { PublicRoom } from "./types.js";

export async function listPublicRooms(baseUrl: string): Promise<PublicRoom[]> {
  const resp = await fetch(`${baseUrl.replace(/\/$/, "")}/v1/rooms`);
  if (!resp.ok) return [];
  const j = (await resp.json()) as { rooms: PublicRoom[] };
  return j.rooms ?? [];
}
