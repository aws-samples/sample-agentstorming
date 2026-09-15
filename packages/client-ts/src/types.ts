// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

export type InviteKind = "participant" | "moderator" | "owner";

export interface Envelope {
  id: string;
  seq?: number;
  type: string;
  room_id: string;
  sender: string;
  ts_sender: string;
  ts_server?: string;
  iat: string;
  nonce: string;
  reply_to?: number | null;
  mentions: string[];
  payload: any;
  sig?: { alg: string; kid: string; val: string };
}

export interface PublicRoom {
  room_id: string;
  title: string;
  description: string;
  visibility: string;
  state: string;
  participant_count: number | null;
  raise_hand_required: boolean;
  max_participants: number;
  created_at: string | null;
}

export interface ClaimResult {
  pid: string;
  kind: string;
  affiliation: string;
  access_token: string;
  refresh_token: string;
  access_expires_at: string;
  refresh_expires_at: string;
  snapshot: any;
}
