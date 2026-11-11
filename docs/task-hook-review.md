# Review private hooks before the first model turn

An external task thread does not necessarily receive the hook-trust override
used by a TUI-created thread. A `hooks/list` row marked `untrusted` is also not
proof that the handler did not execute: the effective thread may have a bypass
override. Automatic execution must still be established by the original native
PreToolUse/PostToolUse evidence. j13 remains timed out and is never replayed.

New task materialization can explicitly set `review_codex_hooks=True`. It starts
one official, network-denied **metadata server** in the new private config home,
before `config.toml` is frozen. It does not create a thread or run a model or hook.
This option therefore is not a file-only preparation and must be recorded as
one metadata process, separately from the formal held clients and releases.
It is disabled by default and cannot be added to an existing frozen attempt.

The finite inventory is generated from the frozen controller. Each command must
use the pinned Python interpreter, isolated mode, a script in that controller,
and the supported arguments. Source changes, foreign handlers, config layers,
disabled handlers, or unknown inventory entries refuse preparation. The helper
then uses the same official API as the TUI's per-handler trust operation:

1. Read `hooks/list` for the exact work directory and verify every handler.
2. Read the private user-config layer and its version.
3. Issue one `config/batchWrite` per reviewed key, assigning that row's actual
   `currentHash` to `trusted_hash`, with `upsert`, `expectedVersion`, and
   `reloadUserConfig=true`. There is no trust-all or calculated replacement hash.
4. Re-list and require the same hashes and exact trusted inventory. Confirm that
   only `hooks.state` changed, close the metadata process normally, and freeze
   the resulting config and review receipt. The new TUI argv omits the bypass
   flag. A failed or unknown write is preserved, with no retry.
5. Immediately before task bootstrap, the original authenticated server is
   queried again. A missing, changed, or untrusted hook prevents the first model
   turn. This check neither writes trust nor proves automatic hook execution.

Official source: OpenAI Codex commit
`6aaac694803004a63c6f393d153c96d22521a6b9`,
`codex-rs/tui/src/hooks_rpc.rs` (`write_hook_trust` and `write_hook_trusts`).
The local official 0.164.0 diagnostic verified that trusting one returned key
leaves the other handler untrusted, and changing the reviewed command makes its
status `modified`. The metadata process exited normally with zero model turns.
The materialization integration test uses the complete official package and
checks the frozen result, absent bypass flag, and normal process exit. Neither
test is a pilot, a callback, or a business acceptance.

The same prospective startup repair separates the initial single `ready` call
from later business work. For restricted Claude network mode it uses the native
wrapper's documented `CMUX_CLAUDE_HOOKS_DISABLED=1` branch, retaining the original
terminal UUIDs and private `--settings` hooks while omitting unavailable cmux UI
integration. The provider's observed 403/insufficient-balance failure is separate;
neither this setting nor hook review resolves it.
