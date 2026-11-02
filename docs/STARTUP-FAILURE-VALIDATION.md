# Claude failures before readiness

A Claude startup can end with a native API error before executing its readiness
probe. Previously the inspector kept reporting missing readiness and the Driver
spent the rest of the original startup window waiting. The new controller
recognizes the terminal original failure and ends that registered comparison
immediately; the overall authorized task can continue in SOLO.

The inspector first verifies the original live process and session. Its read-only
parser checks a complete fenced journal, the pinned settings, the exact initial
prompt or native Goal attachment, the original parent chain and a terminal API
error with a numeric HTTP error status. It reuses the existing native-outcome
grammar. A configured model is never treated as an observed model response.

Internal retry records, text-only errors, other sessions, sidechains, half-written
tails, changed Goals/settings, executed readiness probes and later model work
cannot establish this pre-response failure. Native local-command records may be
appended one millisecond out of timestamp order; original file offsets and parent
links establish their order, with the startup-to-terminal time bound retained.

The Driver consumes the exact frozen inspector observation, records `FAILED`
and retains all 33 business slots and 15 pair slots. It neither retries launch
nor issues another permit. Existing stopped attempts keep their original source,
deadlines, outcomes and evidence.

Validation on 2026-11-02: the affected startup, inspector, Driver and native-outcome
regressions passed **177 tests**, zero failures/errors, in 437.728 seconds, with
the original host return rc0. Both original j5 HTTP429 terminals were recognized
in a separate read-only historical reparse. That reparse did not reauthenticate
live processes, update j5, adopt a lane or grant new input authority.

The original candidate `52efd3c` passed 3,108 local tests and all four CI jobs.
Those results belong to that candidate; they do not certify this changed source.
The new full-suite/CI disposition is recorded separately when available.

Original evidence:
`/Users/lzhs/uz/state/B/recovery-20261024/r89-u22-held-verification-v1/R657-ROOT-CLAUDE-STARTUP-FAILURE-REPAIR-v1/`.

This repair reduces time wasted after a proven startup failure. It does not
restore provider capacity or establish workflow efficiency.
