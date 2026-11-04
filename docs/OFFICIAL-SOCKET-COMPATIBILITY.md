# Official Codex socket compatibility

Generating an experiment with the selected official 0.164.0 package initially
failed with `SOCKET_BINARY_LAYOUT_UNVERIFIED`. The exact socket policy only
recognized the earlier fixed experimental executable. Neither package integrity
nor executable-name discovery could remove this separate refusal.

New controllers additionally recognize this exact macOS arm64 build:

- SHA256: `d8990e3ee7acfbc26f91265b12a1a83f0fee40d0e0f8f1f40a37d4ec21f499cf`
- Bytes: `260293200`
- Official source commit: `6aaac694803004a63c6f393d153c96d22521a6b9`

The official implementation canonicalizes the advertised socket's parent,
hashes the resulting path bytes with SHA256, and creates the physical endpoint
and `.lock` under the effective user's canonical `/tmp/codex-daemon-UID` directory.
Sources: [socket transport](https://github.com/openai/codex/blob/6aaac694803004a63c6f393d153c96d22521a6b9/codex-rs/app-server-transport/src/transport/unix_socket.rs)
and [shared directory](https://github.com/openai/codex/blob/6aaac694803004a63c6f393d153c96d22521a6b9/codex-rs/uds/src/daemon_directory.rs).

A bounded local listener probe used the actual signed executable in a private
CODEX_HOME, with outbound networking denied and the launcher's `umask 077`.
It observed the exact alias, physical socket and lock; the WebSocket upgrade
returned HTTP 101. Actual kernel checks allowed writes to those two exact files
and denied writes to their parent and unrelated neighbors. The listener exited
cleanly and its endpoints disappeared. No model/thread operation, existing
daemon contact or old experiment mutation occurred. Initial probe failures are
retained separately: a missing restrictive umask produced unsuitable lock
permissions, and comparing the shared directory's changing link count was an
incorrect test assertion. Neither justified relaxing endpoint checks.

The registry keeps the older exact build. Each boundary records its own build
hash and size; a different supported build cannot validate that existing
boundary. Unknown hashes, mismatched sizes and malformed declarations refuse.
Installed immutable controllers and existing attempts do not change. Future
builds require their own source and live layout evidence before registry entry;
the version string or official signature alone does not establish this layout.

This proves the isolated listener and its write boundary. It does not prove the
TUI, model/tool readiness, automatic hooks, multi-writer identity, callbacks or
the paired benchmark. Those retain their distinct live acceptance checks.
