# Official Codex names in new private controllers

The installed official client can have a platform-qualified executable name,
such as `codex-aarch64-apple-darwin`. The private adapter previously replaced the
source controller's native discovery with a `codex` basename filter and read
only the older foreground format. A complete package could therefore pass file
checks while its actual client remained unrecognized.

New private controllers discover the registered Codex executable's name. That
filter is only candidate discovery: native process verification and the frozen
executable path/content checks remain necessary. Standalone collection delegates
official executable recognition and foreground reading to the original source
controller's supported methods. Controllers without those methods retain their
legacy behavior and refuse an unfamiliar executable.

The official verifier still checks the selected launcher, actual executable,
signature, arguments and file identity. The native foreground reader must still
identify the same session, and process/ancestry/foreground evidence is checked
again before returning. An ambiguous or changed foreground fails rather than
falling back to older evidence. This change does not add support for multiple
writable rollout files, infer identity from environment variables, change an
installed client, or migrate any existing controller or experiment.

Offline tests establish adapter behavior and rejection boundaries only. A new
experiment still needs the original signed-package checks and actual tool,
automatic-hook, delivery and acceptance evidence before a readiness claim.

## Discovery preserves ambiguity

Private discovery includes the registered executable name, plain `codex`, and
every additional candidate recognized by the original controller's official
verifier. Registering one filename must not hide a second client with a different
filename. The source collector still checks the selected process, foreground
thread and uniqueness; discovering a candidate never admits it. Controllers
without an official verifier retain the registered and legacy names only.
