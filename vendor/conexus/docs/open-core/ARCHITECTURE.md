# Core and local service boundary

```text
Application (for example AlphaLab)
    -> local-host (HTTP, local identity, queue, persisted workspace)
        -> runtime-core (Agent/Harness execution, native tools, releases)
        -> node-host-runtime (OS adapters, tool execution, node storage)
            -> runtime-core
        -> runtime-protocol (runtime events, commands and graph types)
```

The four exported workspaces are `runtime-protocol`, `runtime-core`,
`node-host-runtime` and `local-host`. Graph and node semantics needed for execution
are part of the core. The Canvas editor and rendering packages are not exported.

The private application protocol imports and re-exports the shared runtime
protocol, adding account and commercial contracts within the private repository.
The dependency direction is private applications to public core, never the reverse.
The core's optional host authorization context is an adapter contract; it contains
no account lookup, credential store, billing decision or gateway implementation.

The local service calls `executeHostedRelease` and `AgentRunController`. It does
not implement another Agent loop or reinterpret completion. Both local and private
Web hosts use the core's `compileHarnessWorkspace` compiler. The local transport
owns a single user's state and token, with no tenant or payment policies.

Exports use an explicit package/file allowlist and omit the private repository's
history. Automated checks reject dependencies outside that list. A clean export
must install, build and run its tests without access to the private checkout.
License selection and publication are separate from preparation of this source
candidate; neither is performed by the export script.
