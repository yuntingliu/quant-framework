# Historical migration fixture

`legacy_app.db` is the frozen database formerly shipped at
`data/app/alphalab.db` (SHA-256
`44a9aed4dbfd1d897cf5e71d173b9e43d858ce521cca8543c413d0446fced52e`).
Only migration tests use it, after copying it into a temporary directory.
Never open it as a writable application store or regenerate it during tests.

New installations initialize an empty, ignored `data/app/alphalab.db` from the
schema and built-in source templates on first use.

### Member 3 collaboration confirmation set

`collaboration/confirmation/` is a separate frozen eight-case holdout for the
Issue #24 paired benchmark. Only individual files in its `inputs/` directory
are Agent inputs. Its manifest and oracle are scoring-only. Verify the corpus
with `python scripts/verify_member3_confirmation.py`; do not copy oracle data
into Agent requests or development prompts.
