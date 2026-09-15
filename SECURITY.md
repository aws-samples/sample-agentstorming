# Security policy

## Supported versions

This project is pre-1.0. Only the current `main` branch receives
security fixes.

## Reporting a vulnerability

Please **do not** open a public issue for a security report.

Use [GitHub's private vulnerability reporting][gh-private] on this
repository. That is the only reporting channel: it is bound to the
repository itself, so a report cannot be intercepted by anyone who does
not already have access here.

Earlier revisions of this file listed `security@agentstorming.dev`. That
domain is **not registered to this project**, so mail to it was never
received — and anyone could have registered it and collected unpatched
vulnerability reports. Do not use that address, and do not reintroduce a
contact domain here unless the project verifiably controls it.

[gh-private]: https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability

Include:

- A reproduction (minimal script or request).
- What you believe the impact is.
- Your contact info for acknowledgement.

## What qualifies

- Authentication / authorisation bypass.
- Signature-verification bypass.
- Invite-token, refresh-token, or owner-key exposure.
- Remote code execution via message handling.
- Replay or impersonation attacks.

## What doesn't (without reproduction)

- Reports of missing headers that have no exploitable consequence.
- Generic "could be more hardened" without a concrete attack.
- Issues in third-party dependencies unless Agent Storming uses them in
  a vulnerable configuration.

## Thanks

Reporters will be acknowledged in `CHANGELOG.md` when a fix lands
(unless you request anonymity).
