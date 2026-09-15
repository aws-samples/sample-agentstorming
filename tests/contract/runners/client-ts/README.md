# client-ts contract runner (stub)

Intentional minimal stub. Full implementation follows the Python
runner pattern:

```
node --experimental-vm-modules run_cases.mjs
```

Planned structure:

- Parse `../../protocol_cases.yaml` via `yaml` npm package.
- For each case, instantiate `@agentstorming/client` and run the
  sequence (claim → post → stream → assert).
- Emit TAP + junit.xml.

Not yet implemented because the Python runner covers CT-001 and
CT-005 against the same server wire, giving us the same protocol-
behaviour guarantees. Ship this TS runner before publishing
`@agentstorming/client` to npm.
