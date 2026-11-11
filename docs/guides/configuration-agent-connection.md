# Authenticated managed SQLite Agent (inactive)

This describes the original inactive connection contract. The
[durable activation runtime](configuration-durable-activation.md) now reuses it
for the complete production API and actual owned Node child, adds live owner/PID
proof, and authenticates public proxy operations on the same socket. The
standalone JSON Agent still cannot activate itself from a handoff or flag.

Phase 4B.5B.3A adds an explicit private Python composition, not a cutover or
production launcher. Normal `local_agent`, `local-host.mjs`, `apps.api.main` and
deployment startup remain JSON-backed. There is no environment toggle,
persistent activation record, discovery authorization or HTTP binding endpoint.

## Authority and agreement

Trusted Python code may enter `managed_configuration_agent(backend, node=...)`
from `alphalab.utils.configuration_agent`, then pass the yielded private
`OwnedAgentConnection` as `agent_connection` to `create_configuration_app`.
The backend must already be an explicitly owned SQLite installation. The fixed
`configuration-agent-host.mjs` child receives a fresh secret, workspace token,
launch identifier and the complete backend contract through owned stdin.
Nothing private is placed in process arguments, a connection file or logs.
The child validates the canonical root and constructs one coordinated runtime.
Provider environment input is empty in this isolated host: environment-selected
profiles remain unconfigured rather than borrowing ambient credentials.

The child's private stdout reports only its bound IPv4 loopback HTTP origin.
That descriptor is untrusted until endpoint agreement completes. A fresh random
nonce and HMAC-SHA256 request authenticate the parent without sending the bearer
token. The response MAC binds protocol version, canonical root and root key,
backend, decision ID, installation ID, actual listening origin, fixed slug,
actual managed child PID, launch ID, fresh nonce, sequence and private token. Ordered JSON
arrays with UTF-8 encoding are the MAC input; request/response inputs are
distinct. Public backend identity, `/health`, matching files, arbitrary
dictionaries and inherited environment values cannot mint this capability.

Private HTTP requests use `application/vnd.alphalab-agent+json`. Its registered
raw-buffer parser rejects duplicate decoded keys (including escaped aliases),
malformed JSON/UTF-8, non-integer number spellings, unsafe integers, excessive
depth/node counts and messages larger than 64 KiB. Ordinary JSON content type
cannot bypass this parser. The private stdin handoff uses the same grammar.
Python also rejects duplicate keys, floats and non-finite constants and checks
exact field types; a boolean cannot satisfy an integer version or PID.

The request MAC input is `["request", sequence, nonce, token]`. Sequence numbers
are positive safe integers, increasing under the Python operation lock. The
response MAC covers the ordered identity values followed by nonce, sequence and
token. Node verifies the MAC before atomically advancing its per-launch sequence
high-water mark. Replays, including concurrent copies on other sockets, fail.
There is no nonce cache: replay state is one integer and at most one pending
socket grant. A newer valid proof replaces that grant. The search POST requires
the matching `X-AlphaLab-Proof-Sequence`, the private bearer token, and the exact
socket that completed proof. The grant is consumed before provider work begins.
Fresh tests establish fresh proofs; sequence exhaustion fails closed. Agent
shutdown permanently closes authentication and discards pending authorization.

Every search dispatch repeats agreement. Python uses one explicit
`http.client.HTTPConnection`, with automatic opening disabled, for proof and
private search POST on **the same socket**. A closed keepalive connection fails;
it never reconnects after proof. No proxy configuration, redirects, pooled
transport or ambient Agent origin/token is used. This also closes the gap where
an endpoint could be replaced between a separate health probe and credential
dispatch. No remote HTTP/HTTPS or alternative hostnames are accepted.

The trusted-local-process/storage assumption remains: arbitrary trusted Python
introspection, malicious same-user code, compromised Node/core/preloads or OS
pipe interception are not isolated by this protocol. It is not TLS or an
authorization scheme for remotely hosted Agents.

## Lifetime, failure and results

The connection is private, slotted and nonserializable, with redacted repr.
It is bound to the exact Python owner and Popen process instance, not merely
matching IDs. Readiness must complete before the launch context yields.
Exit, replacement, explicit close, stale evidence, failed agreement or socket
failure cannot authorize dispatch. The backend remains borrowed through actual
child exit; closing an owner with active borrows fails. Closing a connection
invalidates it before interrupting its active socket. Requests serialize through
a bounded lock and have bounded network waits plus an exchange deadline.
Shutdown sends parent-pipe EOF and waits five seconds, then terminates only the
owned child and waits another five seconds. A separate exit observer retains
the owner borrow until actual exit even if bounded shutdown fails.

Helper starts are tracked separately; unstarted threads are never joined.
Startup failures stop and reap only the owned child, with bounded waits. Cleanup
attempts every pipe close and started-helper join even after an earlier failure.
Unbuffered pipes avoid helper-held buffered-I/O locks, and partial handoff writes
are completed explicitly. The child borrow uses a counted reservation rather
than a generator finalizer: confirmed exit releases it exactly once, including
when observer startup failed. If exit cannot be confirmed it stays fenced even
after local cleanup records are discarded. If no observer can later confirm
exit, that exceptional owner requires manual operational review/restart; there
is no automatic unfencing or recovery. Startup/cleanup errors are static and
interruption categories remain redacted cancellation/interruption exceptions.

SQLite non-Off tests require this capability before extracting settings. Missing
or inconsistent connections return HTTP 503. Before transmitting credentials,
the endpoint is authenticated anew and process lifetime is rechecked. SQLite
responses contain only a boolean and static message; arbitrary Agent links,
diagnostics and provider payloads are not reflected. Off stays a successful
local no-op. Legacy JSON search testing retains its existing contract.
Sanitized storage exceptions do not retain underlying network/parser failures.
There are no live provider calls during readiness or tests in the test suite.

## Verification and remaining prerequisites

Tests use temporary SQLite stores, real owned Node children, the actual
authentication module and coordinated runtime, and fake HTTP/provider handlers.
They test foreign identities even when correctly signed, public/forged health,
stale/replayed evidence, endpoint changes/redirects, shutdown, timeouts and two
independent installations. An isolated negative control substitutes the old
ambient dispatch only in a test; the cross-Agent assertion detects synthetic
credential delivery to the wrong fake endpoint.
Additional isolated negative controls restore unstarted-helper joins, ordinary
JSON decoding and omitted replay enforcement only in test memory. Regressions
detect each original failure, and corrected behavior is checked afterwards.

The optional generated Conexus distribution is absent in the current test
environment. Fake-Agent tests verify authentication and Python/Node SQLite
composition, not real Conexus hosting, run execution or deployed shutdown.
Actual host startup fails closed without that bundle. Windows ARM64 tests do
not prove macOS/POSIX deployment or physical power-loss behavior. Supported
Node remains 22.18+ (SQLite is experimental); no runtime is installed here.

Phase 4B.5B.3B must still establish explicit offline durable activation, one
authoritative backend across all processes, full admission/shutdown exclusion,
credential-history retention/revocation policy, real-bundle integration,
macOS/POSIX deployment verification and interrupted recovery safety. This
authenticated connection alone does not authorize activation, historical
restore, automatic rollback or migration of real settings.
