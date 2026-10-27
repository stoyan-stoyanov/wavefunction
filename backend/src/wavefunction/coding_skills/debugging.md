---
name: debugging
description: "Investigate and fix a reproducible software failure. Use for bugs, failing tests, exceptions, or incorrect behavior."
---

Start with the actual failure and expected behavior. Reproduce it with the smallest meaningful case or failing test, then trace data from the trigger through the relevant implementation. Inspect logs and state without printing credentials.

Form a specific explanation and check it against evidence. Fix the root cause with a focused change, preserving behavior outside the failing case. Use a regression test when it protects a consequential behavior; avoid tests that merely restate the implementation.

Verify the original failure, relevant rejection or edge cases, and nearby workflows. Report the cause, fix, and checks actually run. Do not claim an untested deployment succeeded.
