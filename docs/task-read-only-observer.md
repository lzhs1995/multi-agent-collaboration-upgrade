# Isolated TASK read-only observation

New `task_observer_network=True` materializations use the explicit v3 network
contract. All execution lanes can reach exactly the fixed observer Unix socket;
the two Claude lanes retain their separate fixed callback ingress. Neither raw
cmux nor raw app-server RPC is added to their outbound policy. V1/V2 defaults
and existing materialized controllers retain their original contracts.

The external owner explicitly calls `start_observer(runtime_ref)` and retains
the object. Before construction its import scope must contain the frozen
generated controller's `scripts` directory. Original controller module-origin
checks apply; the observer never swaps modules or changes import paths from its
thread. Importing or installing the modules does not start a service.

The separate observation thread holds no campaign ledger, actor or response
writer. It can read the original tree and task metadata while the actor thread
is executing an operation. Requests are only `tree` or `task` plus a nonce;
commands, arbitrary RPC methods/paths, input text and task selection are refused.
Kernel Unix peers and original process ancestry are checked before and after
each observation. The client authenticates the owner, endpoint, descriptor,
nonce and original response evidence. ACK means transport bytes only.

Early tree requests require the three allocated endpoint records, but only
authenticate the requesting lane's native process. They do not wait for another
lane's transcript or a task journal. Task requests additionally require the
original TUI, app-server, both Claude sessions, their separate filesystem kernel
checks and the exact task journal/RPC metadata. These remain distinct claims.

Generated bridge/tree, workspace caller and task target readers use the proxy
only for confined original execution descendants. Claude caller resolution
checks the local native process, environment UUIDs and TTY against the returned
observation. It does not manufacture a Codex foreground proof. External owners
retain direct metadata readers. Native input metadata uses a two-read projection
marked `requester_direct_rpc_peer=false`; it contains no input method and cannot
respond to dynamic tools. Latest provider-error details are not observed by this
projection and are explicitly marked unavailable.

The service limits accepted connections to 2048 and invocation time to 1800 s.
Frames are bounded, reads expire after 30 s, and clients reject results after
their deadline. Synchronous kernel/filesystem/RPC calls may outlive a time limit;
the observer collects and rejects late results instead of claiming hard realtime
cancellation. `stop()` reports an alive thread when a read has not yet returned.
Original socket directories, failure records and used nonces are retained;
restart/replacement of that instance is refused.

This increment does **not** enable live TASK adoption or formal launch. Busy
callback input is explicitly refused before an input intent, pending its owner
route. Idle metadata can reach the existing callback sender, but the generated
TASK input gate now reports `TASK_INPUT_ADOPTION_CONSUMER_UNAVAILABLE` instead
of importing an unshipped external validator. The external full validator stays
intact. The final adoption producer/reader must replace this explicit refusal
together, with real hooks, broker/Mach/native-helper/FD evidence and both input
paths. Passing observer component tests is not callback or business acceptance.
