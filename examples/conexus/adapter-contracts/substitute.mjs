// A complete replacement adapter set, assembled from the contract doubles.
//
// This is the shape a benchmark harness should use. It supplies the same
// account-resolved adapters the shipped launcher supplies — model completion
// and the `web.search` host capability — but resolves neither from an account:
//
//   integrations/conexus/local-host.mjs   substitute.mjs
//   -----------------------------------   -----------------------------------
//   configuredCompletion(providerPath)    contractCompletion(script)
//   configuredWebSearch(providerPath)     contractSearch({ sources })
//
// The OS runtime adapter is deliberately *not* replaced. It is the one adapter
// a benchmark must not simulate: tool child processes, their environment
// allowlist and their timeouts are what the Agent is measured through, so
// substituting them would measure the double. What the substitution has to
// prove instead is that the runtime still publishes an equivalent profile —
// the same node types, tool runtimes, permissions and capability ids — because
// that profile is all `inspectHostedRelease` checks a Release against.
import { assertEquivalentProfiles } from '../../../integrations/conexus/contracts/adapters.mjs'
import { contractCompletion, contractSearch, substituteProfile } from '../../../integrations/conexus/contracts/doubles.mjs'

/** The account-resolved adapters, replaced. Pass straight to startMinimalHost. */
export function substituteAdapters({ script, sources = [] }) {
  return {
    complete: contractCompletion(script),
    capabilities: { 'web.search': contractSearch({ sources }) },
  }
}

/**
 * Asserts the Host running on these adapters can still deploy the Release the
 * real Host deploys.
 *
 * `observed` is the profile the live runtime adapter published; `reference` is
 * the profile being stood in for. Checked before a benchmark scores anything,
 * so a harness offering a narrower surface fails loudly instead of producing a
 * number that looks comparable. Capability ids are part of the profile, so a
 * Host that forgot to wire `web.search` fails here too.
 */
export function assertSubstitutable(observed, reference) {
  assertEquivalentProfiles(reference, substituteProfile(observed))
}
