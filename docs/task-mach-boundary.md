# Explicit task Mach restriction

New materialization may request `task_mach_boundary=True` together with the
observer network contract. The frozen binding records the denied operations,
three execution roles and any exact service exceptions. Startup validation reconstructs the profiles and
rejects omitted rules even if the broader profile has a new file hash.

Both Claude lanes deny `mach-lookup` and `appleevent-send`. The Codex app-server
also denies both operations by default. The version 2 contract permits only
`com.apple.cfprefsd.daemon` and `com.apple.cfprefsd.agent` lookups on the server
lane, only `com.openai.codex` preference reads, and no preference writes.
These services allow the official startup loader to synchronize and read
administrator-managed configuration. The contract neither substitutes nor
disables managed configuration. Old version 1 bindings reproduce the original
blanket-denial bytes; they gain no exceptions.

Descendants inherit their lane's policy. The remote TUI and external controller
retain their existing profiles and can perform the original authorized UI and
research operations. Existing tasks and default materialization are unchanged.

Real temporary Darwin probes verify refused unrelated Mach lookup, denied
preference writes and kernel AppleEvent permission checks. The local integration
test uses the generated config and complete generated server profile with the
official Codex 0.164.0 `app-server --print-startup-config`, then starts the real
stdio server and closes stdin without creating a thread or model turn.

The old version-only check missed an actual startup failure: blanket lookup
denial prevents managed-preference synchronization. The normal app-server then
falls back to defaults, whose partial `-c model_providers.custom.base_url` table
lacks a provider name. That secondary error must not be mistaken for a missing
name in the original config. `--strict-config` and `--print-startup-config`
expose the original managed-preference error. Both preference services are
needed; allowing either one alone still fails.

These checks do not prove model calls or automatic hooks. Exact model-port,
observer and callback connectivity remain separate tests.

This policy does not revoke already inherited Mach rights or establish native
helper FD behavior, gateway semantics or complete broker isolation. Full TASK
adoption and launch remain unavailable until the remaining runtime evidence is
implemented and verified. No live profiles are changed by these tests.
