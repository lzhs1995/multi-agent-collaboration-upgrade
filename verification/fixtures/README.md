These files exercise terminal layout parsing. They are regression inputs, not evidence of live message delivery or current session identity.

`claude-c2596-screen-20261008.txt` and `claude-c41-screen-20261008.txt` are minimal synthetic reconstructions. Earlier revisions contained private transcript bodies despite a broader synthetic-fixture description. The current files replace those bodies completely and use neutral project and branch labels. Only the tested composer borders, empty prompt, model/status footer, activity spinner and tool-status shapes remain. Numeric durations and counts are fixed parser test values, not current operational measurements. The filenames retain their historical regression identifiers for test compatibility.

`codex-compacting-behind-queued-followups-20261009.txt` and `codex-stranded-below-queued-followups-20261009.txt` describe the PASTE_INTENT and POST_ENTER layouts of one earlier observed attempt: Codex was compacting while an earlier ask sat in “• Queued follow-up inputs”, and the next payload stayed in the composer. Their existing fixture description records that rows from “• Compacting context” down preserve the layout with caller UUID, task ID and path replacements; the transcript above them is synthetic. Those files are outside the two-file replacement described here.

The five Claude fixtures added for 2026-10-09 are minimal synthetic reconstructions. They retain the tested border/footer fields, folded-draft shape, active spinner and historical status lookalikes. Their names identify regression origins, not live proof or current session identities.

Current-file cleanup does not remove earlier versions from Git history. See [publication privacy](../../docs/publication-privacy.md) for that distinction.

## Additional public terminal layouts

The `claude-default-{a,b}-20261018.txt` and
`claude-busy-goal-split-{a,b}-20261026.txt` files are synthetic public layouts.
They preserve parser-relevant footer, composer, activity and goal shapes while
removing private terminal conversation bodies and machine paths. Test hashes
identify these replacement bytes; older capture hashes are historical archive
references, not proof that these files are original transcripts. The earlier
`claude-c2596-screen-20261008.txt` and `claude-c41-screen-20261008.txt` synthetic
fixtures are unchanged. See [publication privacy](../../docs/publication-privacy.md).
