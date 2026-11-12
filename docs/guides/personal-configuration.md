# Personal configuration

Open **Personal configuration** in the workspace sidebar. The existing dialog now
has Models, Web Search, Data Sources and Import / Export tabs. English and Chinese
use the application's language preference; controls support keyboard navigation.
Closing the dialog or leaving a tab discards its private input. No configuration
or credential is stored in browser storage. The language preference remains there.

## Profiles and explicit tests

Choose a saved profile or New profile, give it a name, edit its fields, then choose
**Save profile** or **Save and use**. Saving alone does not select or test it.
SQLite also supports **Use saved profile** and **Disable / select none**. None
does not allow environment fallback. Existing JSON fallback is displayed as such;
the dialog cannot create an unsupported explicit JSON none/environment selection.
Durable SQLite does not offer environment selection. It is an existing runtime
restriction, not an invitation to copy environment secrets into a profile.
Selection-only changes do not save edited fields. Unsaved edits stay marked and
cannot enable an RQData test against a different saved account; save or reload
them explicitly. A successful selection change is not a profile-save receipt.
JSON model reads report selection `source` separately from `active_id`: a saved
profile whose ID is literally `environment` is not environment fallback. This
public provenance field contains no environment-derived value or credential.

- **Models:** configure an OpenRouter-compatible API URL/model ID or a local
  endpoint such as `http://127.0.0.1:11434/v1`. Local models may need no key.
- **Web Search:** configure Brave, OpenRouter or Off. OpenRouter references a
  model profile (or the selected model under the existing authorized reference
  contract). A missing reference remains visible; it is never silently replaced.
  Invalid destinations or incomplete referenced models are rejected by the API.
  JSON environment fallback has a separate **Test current environment** action.
  It uses the legacy search-test contract without creating a profile or saving
  environment credentials. Its result is separate from the new-profile editor.
  This action is unavailable for SQLite; it never falls back to JSON testing.
- **Data Sources:** configure an RQData username, host and private password.
  RQData Test connection uses the existing `rq.a_share_research` connection-test
  contract and tests the **saved default**, never unsaved fields. Save and use
  before testing an edited profile. Supported JSON environment defaults can also
  be explicitly tested without importing their values into the form.

Saved/configured and connected are different states. Only explicit **Test
connection** or **Test current environment** actions make provider/SDK requests.
They may incur charges, and passing does
not guarantee future availability. Opening, editing, saving, importing or
restarting does not run tests. Off verification sends no search request.
Remote/unavailable Agent configuration is disabled or rejected by the API; the
dialog does not bypass managed ownership or authentication.

Password fields are always empty on load. Blank retains a credential **only**
under the current backend's same-profile identity rules. A new destination may
require a new credential. Changing destination fields discards freshly typed
private input. Explicit clear takes precedence over retention and fallback.
The clear decision remains checked for the same profile when its destination
or search provider changes, including Off. Choosing another profile or reloading
discards that draft decision; changing providers alone does not.
SQLite supports model-key clearing; the JSON model API does not, so this control
is unavailable there. Brave and RQData expose their supported clear controls.
Saving incomplete profiles can require reconfiguration before use.

SQLite edits send the loaded revision. On conflict, reload and review edits;
there is no automatic retry. Each installation has its own private store and
identity, not a multi-user account. Existing native private-file permissions and
installation-root checks apply. The UI introduces no activation mechanism.

Within the installation's runtime root, JSON stores are
`conexus/secrets/model-providers.json`, `conexus/secrets/search-providers.json`
and `app/secrets/rqdata-profiles.json`. The committed SQLite store is
`app/secrets/configuration-store.sqlite3`; private receipt records are in
`app/secrets/configuration-transfers`. These are private credential-bearing
locations, not portable exports or files to attach to diagnostics. Existing
POSIX policy uses private directories/files (0700/0600); Windows retains its
existing inheritance policy without ACL changes. The UI does not relocate them.

New runs/jobs use new settings according to their existing capture lifecycle:
model capture at first completion, independent search capture at first search,
and RQData capture per admitted job. Already captured operations keep their
configuration; changing the default does not rewrite an admitted job.

## Export and import

Export downloads the server's canonical `alphalab-configuration.json`. It contains
approved portable profile metadata and selection intent, **not** keys, passwords,
credential-presence flags, environment values, paths, reports, research datasets
or private history. A failed export creates no download.

Choose a UTF-8 JSON file (maximum 256 KiB), services, merge/replace mode and whether
to apply selections. **Preview import** submits the raw file text so server
duplicate-key and legacy-schema validation remain authoritative. The file stays
in component memory. The preview shows additions, changes, unchanged/removals,
metadata, selections, migrations, missing credentials and limitations.

For **every imported profile**, explicitly choose clear, fresh private input or
retain. Retain appears only when the server confirms the current same-ID and
destination identity is eligible; eligibility does not assert a key is present.
It never borrows a different profile's, installation's or deleted profile's key.
Refresh the preview after choosing policies. Enter fresh values only in the
private confirmation fields; these values are not sent in preview envelopes or
stored in receipts. Accept each required consent and review the final diff before
Confirm import. Private input is cleared on success, cancellation, closure and
any rejected application that requires restarting the flow.
An in-flight apply clears fresh inputs immediately. Closing the dialog or
switching tabs keeps only the non-secret operation reference and outcome in the
mounted settings entry. Reopen Import / Export to inspect it. Requests have a
bounded timeout; an uncertain outcome is never automatically submitted again.
This in-memory tracking does not survive a complete page/application reload.
Confirmed import, rollback and subsequent inspection of a committed operation
refresh settings and Agent readiness. Preview and rejected operations do not.

JSON installations can export and preview but cannot atomically import or
logically roll back. The UI reports this limitation and provides no cutover UI.
Transfer additionally requires the existing local-origin consent header and
Basic authentication where configured. The client uses the application's API
base, credentials policy, no-store requests and scoped consent header. It does
not redirect requests or log request/error bodies. Transfer refuses non-loopback,
credential-bearing or query-bearing API origins before any request. An Electron `file:`/null
origin rejected by the server must use an authenticated loopback web session;
server authorization is not weakened to support that origin.

Receipts are single-use, revision-bound and expire. Stale, expired or used receipts
require inspection or a new preview, never an automatic preview-and-apply retry.
Requests have a bounded client timeout; a failed response is not proof that an
apply failed. **Inspect operation outcome** checks the recorded operation ID.
Uncertain outcomes must not be replayed. History capacity requires reviewed
offline maintenance, not automatic deletion of private history.

## Logical rollback and limitations

After a confirmed import, the client asks the backend for operation eligibility.
Only an eligible exact current import offers **Preview logical rollback**. This
has its own credential policy, preview, consents and confirmation. It publishes
a new revision of sanitized metadata; it does not restore an old database or old
secrets. Re-enter missing credentials. Intervening edits invalidate eligibility.
Recursive, JSON and uncertain rollback are unsupported. Recovery, database
restoration, backend activation and credential-history deletion are not exposed.

See [transfer contracts](configuration-transfer.md) and
[recovery security](configuration-recovery-security.md) for existing guarantees.
Real macOS deployment and physical power-loss certification remain separate.

## Acceptance evidence and developer checks

`npm --prefix apps/desktop run test:configuration` uses node:test, React's real
DOM renderer and jsdom. It drives the actual dialog, fields, buttons, tabs and
confirmation flow with synthetic API fixtures. It verifies no automatic probes,
private-input cleanup, safe downloads, credential decisions, conflicts and
uncertain operation handling. jsdom adds no browser test framework. These tests
are interaction evidence, not screenshots or full native Electron certification.

The production API tests in `tests/api/test_configuration_transfer.py` use real
SQLite/leases and the actual application lifespan/routes with fake SDK/provider
entry points and test-owned managed children. They verify backend retention and
rollback eligibility as well as the existing authorization/apply contracts.

| Issue #16 criterion | Executable evidence |
| --- | --- |
| Unified entry, named profiles, save/select/test | `configuration-ui.test.mjs`: dialog, three profile workflows, explicit none and test assertions |
| No browser secrets, no automatic probes | UI test teardown/storage checks; save/open/tab/cancel assertions; only explicit test dispatch |
| No exported secrets/data/reports | Transfer canonical export/canary tests; UI exact-server download test |
| Blank/retain/update/delete and missing keys | Profile-service credential identity tests; UI clear, empty retention, missing reference and import policy tests |
| Older schemas, invalid no mutation | Phase 4A schema suite; transfer legacy/invalid-input and read-only preview tests |
| Two personal installations, no borrowing | Transfer independent-installation tests; profile/runtime isolation tests |
| Restart persistence, running/new jobs | Python storage/profile/runtime and Node configuration-runtime capture tests |
| Atomic import, stale receipts, safe rollback | Transfer real-store/API suite; UI consent, double-submit, conflict, operation and rollback tests |
| Local model/OpenRouter/RQData test doubles | Existing model/search API tests, runtime data tests and real-bundle local model fixture |

Run the declared frontend tests, `lint`, `build:desktop`, configuration Python
suites, Node Agent tests and facade/hygiene/documentation checks together for
release acceptance. Local Windows ARM64 Application Control blocks PyArrow's
dataset DLL; blocked Python collection is **not** passing integration evidence.
Hosted Windows/Linux results must identify the exact tested source commit.
Substituted-core tests never certify the real Conexus bundle; its separate
supported build/test procedure is `scripts/build_conexus_runtime.py` followed by
`scripts/check_conexus_runtime.py` and the real local-Agent integration test.
The cross-language concurrency fixture holds a real shared authority scope, as
production does, while eight concurrent reads race 30 Python publications. It
does not bypass leases or launch a separate heavy Python authority owner for
each read; isolated owner startup remains subject to its bounded deadline.

### Verification record and remaining release blocker

The [combined hosted matrix](https://github.com/KE2702/quant-framework/actions/runs/43174461137)
tested source `99cdb9d0cc12095786f662d67a04f0e49f27ff3d`. Ubuntu x64 passed
1,737 Python tests (four platform/fixture skips), 346 Node/frontend tests (two
Windows-only skips), 27 UI tests and 209 real Conexus tests. Windows x64 passed
348 Node/frontend tests, 27 UI tests and 209 Conexus tests. Its Python result was
1,737 passed, three skipped and **one setup error**: `saved_backup` in
`tests/api/test_configuration_recovery.py:392` received a sanitized `UNAVAILABLE`
from the unchanged `coordinated_offline` implementation. Another full run saw
an intermittent offline-entry failure in the worker-gate test, which subsequently
passed in isolation. Do not describe the complete Windows matrix as green.
A same-tick deadline-rounding defect was subsequently reproduced and corrected
by clamping each remaining timeout to its original validated budget. This does
not establish the historical failure's exact numeric argument. New complete
Windows verification is required; isolated passing reruns do not clear it.

The [final provenance/client regression run](https://github.com/KE2702/quant-framework/actions/runs/43176487068)
tested `ab3fec5a0a8de5fb138bb5994f48cb8210b94fa0`: 47 relevant Python API/backend
tests, all 28 final UI tests, and 209 Conexus tests passed on **each** platform,
without failures or skips. Typecheck, ESLint, desktop production build, Ruff,
facade, hygiene and documentation checks passed. ESLint retains one nonblocking
Fast Refresh warning for the shared controls/hook module. The local Windows
ARM64 desktop build, 28 UI tests, 53 existing frontend/Node compatibility tests
and 209 core tests also passed; local Python integration remains DLL-blocked.
The two changed legacy model Python files still fail whole-file formatting;
copies from the original HEAD fail the same check. Their unrelated formatting
was preserved. The changed transfer Python files pass formatting.

The real-bundle Python integration exercises the JSON-backed Agent's tool call,
report persistence and restart with a loopback model double. Durable SQLite
production API tests still use their explicitly substituted managed core. These
two kinds of evidence do not certify an end-to-end real-bundle SQLite cutover.
Native Electron `file:` transfer authorization, real macOS deployment and physical
power-loss certification remain outside the demonstrated coverage. This record
was appended after the verified implementation, without changing tested code.
