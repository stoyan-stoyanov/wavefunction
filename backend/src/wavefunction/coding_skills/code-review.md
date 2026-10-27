---
name: code-review
description: "Review a code change for concrete bugs and regressions. Use when asked to inspect a diff or pull request."
---

Read the complete changed behavior, its callers, and relevant tests. Explain concrete failure cases with the trigger, effect, and file location. Prioritize correctness, data loss, isolation, concurrency, and security boundaries.

Validate each proposed finding against the implementation and stated intent. Distinguish a demonstrated bug from a preference or hypothetical improvement. Do not change code during a review unless the user asks for a fix.

Use targeted checks to reproduce important findings when feasible. If no consequential issue is found, say so and state the validation performed and what remains untested.
