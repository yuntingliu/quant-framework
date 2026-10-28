# Two-Agent example — reserved

**This directory is intentionally empty of code.** It reserves the location and
the contract for a two-Agent example that the later multi-Agent work will
build. Nothing here is a working example, and nothing here should be read as
evidence that multi-Agent support is finished.

What exists today is recorded in [INTERFACE.md](INTERFACE.md): which parts of
delegation already work on the bundled local Host, which parts are missing, and
one verified defect that blocks the obvious implementation. That document is the
handover, and it is written from probes against the pinned core at
`build/conexus/UPSTREAM.json` revision `9cd8c69`, not from the core's own docs.

## Why reserve instead of ship

Issue #6 covers framework documentation, examples and documentation validation.
Its scope boundary excludes introducing a second Agent loop. Delegation is also
not purely additive: the Agent-facing contract for a child run exists in Runtime
Core, but the local Host's user-input routing does not reach a child run at all
(see [INTERFACE.md](INTERFACE.md#verified-defect-nested-user-input-deadlocks-the-run)),
and that fix belongs to a reviewed core re-import rather than to a documentation
change.

`scripts/check_conexus_docs.py` asserts this directory stays documentation-only,
so the boundary is enforced mechanically rather than by convention.

## When this is implemented

Build it as a sibling of the existing examples, with the same shape:

```text
two-agent/
  README.md        replaces this file
  INTERFACE.md     keep, updated to describe what shipped
  harness.mjs      lead Agent, worker Agent, shared Tool node
  host.mjs         bootstrap, mirroring ../minimal-agent/host.mjs
  main.mjs         runnable end-to-end demo
  test/            delegation, child failure, child cancellation, child input
```

Reuse `../minimal-agent/doubles/scripted-model.mjs`; a two-Agent script needs a
way to tell the two Agents apart, and the per-Agent system prompt is the only
reliable discriminator — see
[INTERFACE.md](INTERFACE.md#telling-the-two-agents-apart-in-a-test-double).

Remove the documentation-only assertion in `scripts/check_conexus_docs.py` in
the same change, and record the acceptance evidence in
`docs/08_LOCAL_DEPLOYMENT_VALIDATION.md`.
