# Temporary minimatch compatibility bridge

GHSA-g3hv-75wg-m249 affects minimatch versions before 11.0.1. The desktop
dependency graph still contains consumers of the pre-v6 callable CommonJS API
(including `@electron/asar` and `dir-compare`). Simply overriding these to v11
breaks packaging. This package preserves that export shape and delegates all
matching to the pinned, patched upstream minimatch 11.0.3 dependency. It does
not vendor or reimplement the matching algorithm, and npm audits the real
upstream dependency normally.

The CommonJS class also adapts parsed path segments for glob 7's `_glob` and
`String.match` protocol. It calls the new nonrecursive matcher's `test` method;
it never translates patterns back into potentially unsafe regular expressions.

The root dependency and `$minimatch` override route requests through this bridge.
ES module exports are forwarded unchanged, with a legacy default export added.
The `minimatch-upstream` npm alias avoids a circular override and retains the
registry package's integrity hash and audit identity in the lockfile.
Remove the bridge once upstream consumers support the named
exports. Run the desktop tests, lint, build and both npm audits after updates.
