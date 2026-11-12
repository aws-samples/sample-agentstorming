-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
-- SPDX-License-Identifier: MIT-0

-- A nonce is usable only by the kind of request it was issued for. The owner
-- route issues nonces only to a signed owner request, but the candidate
-- registration route issues them to anyone, and both shared one unscoped
-- store, so an anonymous caller could obtain a nonce every owner route would
-- accept. Existing rows predate the column and are owner nonces.
ALTER TABLE owner_nonces ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'owner';
