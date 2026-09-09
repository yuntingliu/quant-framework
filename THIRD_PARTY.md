# Included Conexus Core snapshot

`vendor/conexus` is a verified source export from the Conexus project.
The exact upstream commit and per-file hashes are recorded in its `EXPORT.json`;
`integrations/conexus/runtime.lock.json` pins the deployed version.

The owner has selected the runtime core and local service for opening. The Canvas
editor/UI and commercial services remain private and are not in this snapshot.
The Conexus license has not yet been selected; the snapshot is an internal source
candidate, not a published open-source release. AlphaLab's package license metadata
does not select or replace the Conexus license.

JavaScript dependencies are recorded in the snapshot's own package lock and retain
their upstream licenses. Generated dependencies, credentials, workspaces and Git
history are excluded from this source snapshot. See its README for the source
boundary and standalone build instructions.
