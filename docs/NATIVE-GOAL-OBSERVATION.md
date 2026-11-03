# Claude native Goal evidence

The experimental observer reads original Claude journals. It does not enable a
Goal, send input, settle a callback, or certify uninterrupted operation. The r25
business campaign did not register Goal activation and renewal, so its business
or ordinary-message results cannot close the continuous-Goal requirement.

## Format basis

Read-only inspection of Claude Code 2.1.170, SHA256
`e903646d8b7a31882a80ecd27569a27d8ac57b3708745f349709632c84117fdf`, found:

- Native `/goal` sets a condition or clears it. A prompt that merely says to
  keep working is not this operation.
- A top-level `attachment` containing `attachment.type = goal_status` records
  state. A `sentinel: true, met: false` record sets a condition. A sentinel with
  `met: true` can be clearing; it does not prove goal completion.
- A non-sentinel unmet status with a reason is an observed check. A later real
  assistant record is subsequent model activity, without proving that the check
  caused automatic waking or that useful business work succeeded.
- The separate wire `active_goal` event is not treated as a journal attachment.
- Resume can restore a condition from the last unmet attachment without writing
  a new SET sentinel. The restored iteration, time and token baseline restart;
  they cannot measure complete costs across processes. Restore alone does not
  establish a resumed query or automatic waking.

This is a static format basis. Startup-v3 now registers exact native `/goal`
arguments for both Claude lanes and immutable duties for all three clients.
Adoption requires the original session's matching SET, real startup probe and
automatic hook evidence. Actual operation still needs live verification.

## Observer request

`scripts/upgrade_goal_observer.py --request /absolute/request.json` prints an
observation. The request has schema `upgrade-goal-observation-request-v1` and
contains the original authenticated `session` descriptor, `start` and `end`
NativeReader EOF pins, and the exact `condition` string. Both fences and original
file identity are rechecked. There is no production fixture option. Tests that
use synthetic journals remain offline.

The result preserves record offsets and hashes, full/partial scan coverage,
separate counts for SET, unmet checks, and later model records. Unobserved state
at the interval end remains UNKNOWN. Quoted JSON, tool output, queued commands,
sidechains, unsupported formats and ordinary user text cannot establish Goal
state. The observer never returns continuous-Goal acceptance.

## Independent pressure acceptance still required

Register both arms and all 192 slots before input. The r26 continuation uses the
full original 33-run / 15-pair matrix and must end no later than the original
2026-10-19T06:47:39.605462Z deadline. It does not receive a fresh two-hour budget.
Measure first handshake with Goal active separately from handshake reuse; retain
all four directions, original refusals, pending messages and missing slots.
Preserve exact supported Goal activation and original native state evidence.
For each window, report separately native Goal observations, original message
reception, complete body exposure, subsequent action, and business correctness.
No channel-level or local-parser result substitutes for this live workload.

Shared MAC and thesis-refiner releases remain owned by surface40. Keep all r25
frozen controllers, attempts and denominators unchanged while developing this
independent candidate.
