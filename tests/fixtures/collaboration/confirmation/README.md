# Member 3 confirmation corpus

Version 1 is a frozen, eight-case holdout intended for the Issue #24 three-arm
benchmark. It is separate from `../dev/` and must not be used to tune prompts,
model settings or scoring during development.

Only one `inputs/{fixture_id}.json` file may be passed to an Agent for a run.
Do not expose `manifest.json`, `oracle.json`, expected categories, rationales or
scores to the researcher/reviewer. Store run records and human adjudications
under ignored `build/member3/`, not in this directory.

Verify the input and oracle hashes, evidence allowlists, citation pointers and
independent arithmetic/timing facts with:

```bash
python scripts/verify_member3_confirmation.py
```

The verifier does not decide qualitative model quality. Human/independent
adjudication remains required for support, false-alarm and final qualification.
