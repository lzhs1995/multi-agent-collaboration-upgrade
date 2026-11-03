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

Candidate `adc70616f443658273da1ca614667208d1148036`, which includes this repair,
subsequently passed the original full local suite: 3,120 tests, zero failures or
errors, one skip, rc0, in 2,515.418 seconds. All four jobs in
[CI run 41375226072](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/actions/runs/41375226072)
reported 3,120 tests with zero failures/errors. R660 retains the original logs,
source pins and result records. The previous 3,108-test result belongs to
`52efd3c`; neither candidate's code checks establish native workflow efficiency.

Original evidence:
`/Users/lzhs/uz/state/B/recovery-20261024/r89-u22-held-verification-v1/R657-ROOT-CLAUDE-STARTUP-FAILURE-REPAIR-v1/`.

This repair reduces time wasted after a proven startup failure. It does not
restore provider capacity or establish workflow efficiency.
