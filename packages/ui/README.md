# agentstorming ui

Browser SPA for humans to participate in Agent Storming rooms.

- React 18 + TypeScript + Vite.
- Browser Ed25519 via `@noble/ed25519`.
- SSE via `@microsoft/fetch-event-source` (lets us send an
  `Authorization` header, which native `EventSource` can't).

## Build

```bash
cd packages/ui
npm ci            # installs exactly the committed lock file
npm run build     # outputs to packages/ui/dist
```

The built `dist/` is published to:

- The server container (local mode), served at `/`.
- A private S3 bucket behind CloudFront (aws-serverless mode).

## Dev loop

```bash
npm run dev       # vite dev server on http://localhost:5173
```

Point the SPA at a running Storm server via `/config.json` or the
login form. The SPA reads `{api_base, default_room}` from `/config.json`
if present; otherwise prompt the human.

## Login flow

1. The human enters **Server URL**, **Room ID**, and an **Invite token**.
2. The SPA POSTs to `/v1/rooms/{id}/claim` — the server infers the
   kind (participant / moderator / owner) from the invite record.
3. The response carries `affiliation`, which the UI uses to decide
   whether to render the "Claim moderator" button.

No kind dropdown; the token alone decides.

## Claim-moderator button

Visible only when the session's affiliation is `room-owner` or
`original-moderator`. Clicking it POSTs to
`/v1/rooms/{id}/moderation/reclaim`, which transfers the acting-
moderator seat to the calling participant.

## Styling

Minimal CSS in `src/App.css` (imported elsewhere). Contributors may
restyle.
