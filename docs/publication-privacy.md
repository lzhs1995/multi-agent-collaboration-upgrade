# Publication privacy

The current tree replaces the private transcript bodies in `verification/fixtures/claude-c2596-screen-20261008.txt` and `verification/fixtures/claude-c41-screen-20261008.txt` with minimal synthetic terminal layouts. The replacements retain only parser-relevant composer, footer and activity shapes, with neutral project and branch labels. The fixture README now states this scope explicitly; an earlier synthetic-fixture description did not accurately describe those transcript bodies.

This is a current-tree cleanup. Git history has not been rewritten, so earlier commits may still contain the removed private content. Neither replacing these files nor passing their parser tests establishes that the published history has been cleared. No force-push is part of this change.

The reported historical Gitleaks scan produced one finding in `scripts/fixtures/mac-0.4.58/executor_reask_identity.py` for a protocol schema identifier (`executor-reply-consumer-proof-v1`). It is not an authentication secret. This finding is a false positive, but a secret scanner does not establish that history is free of personal paths, transcript text or other private context. The schema finding and the retained-history privacy issue are separate conclusions.

Validation for this cleanup includes 24 tests in the two targeted Claude footer parser modules and all 1,290 tests in the full offline suite. They verify synthetic layouts and other offline behavior without sending input to a live terminal. This note makes no claim of a comprehensive privacy audit of every file or historical commit.
