---
name: thorough-bug-review
description: "Review every major system in a repository for concrete bugs, reproduce each candidate, and report only confirmed findings with root cause, reproduction, proposed fix, and fix verification. Use for a thorough repository bug review."
---

# Thorough bug review

Produce a bug report covering every major system in the requested repository. Focus on incorrect behavior and regressions, rather than style preferences or speculative improvements. Review the current implementation, including relevant uncommitted changes, unless the user specifies a revision or narrower scope.

## Review the systems

Read repository instructions and architecture documentation, then map the major systems from the implementation and its entry points. Track each system's review status so none is silently skipped. Follow the important workflows through callers, state/storage, async boundaries, external integrations, and user-visible results. Inspect relevant tests as supporting evidence; do not assume passing tests prove a workflow is correct.

For each candidate bug, record privately:

- What is wrong, its concrete trigger, expected behavior, actual behavior, and impact.
- The root cause and responsible file/line locations, traced through the affected workflow.
- A minimal reproduction with prerequisites, inputs, commands or UI steps, and an observable failure.
- A proposed fix that addresses the cause and preserves surrounding behavior.
- How to verify the fix, including the failing reproduction as a regression check and relevant adjacent success/rejection cases.

Check edge cases where they affect the system's contracts: state changes and restart, stale/concurrent responses, ownership and isolation, invalid inputs, partial failures, cancellation, cleanup, and data preservation. Merge candidates sharing one root cause instead of reporting the same defect repeatedly.

## Reproduce and filter

After completing the system review and collecting all candidates, attempt to reproduce each one against the reviewed implementation. Use the smallest faithful test or workflow that reaches the actual failure. Use browser/computer tools for UI behavior when available and useful. Mock external dependencies only where they preserve the suspected behavior; a mock assertion or a rewritten imitation of the implementation is not reproduction evidence.

Capture the environment/revision, exact procedure, expected and observed results, and useful logs or test output. A failing test must fail because of the claimed bug, not a missing dependency, invalid fixture, or unrelated error. For races or intermittent failures, use bounded repeated trials and report the observed frequency. Refine an incorrect reproduction and retry when new evidence warrants it.

Remove every candidate that fails to reproduce or is contradicted by the implementation's intended behavior. Candidates blocked by missing access, prerequisites, or tooling remain unconfirmed and must also be excluded from the bug findings. State the resulting coverage/validation limitations separately without presenting those candidates as bugs. Never claim a reproduction was run when it was only reasoned about.

Use isolated synthetic data and task-owned temporary resources for reproductions. Preserve user changes, credentials, and existing processes; clean up test resources and verify cleanup. Do not apply product fixes, commit, push, or publish during this review unless the user requests those actions. Temporary reproduction fixtures may be created within the authorized scope.

## Final bug report

Start with a brief scope and coverage summary: systems reviewed, systems not fully exercised, reviewed revision/working-tree state, and validation environment. Then list only reproduced bugs, ordered by severity and impact. Include these fields for every bug:

### BUG-001 — Concrete title

- **What it is:** Expected versus actual behavior, trigger, affected users/workflow, and impact. Include severity with a short rationale.
- **Root cause:** The implementation mistake and verified file/line references.
- **How to reproduce:** Prerequisites and exact runnable commands or numbered UI steps, followed by the actual observed failure and evidence.
- **How to fix:** A specific proposed change addressing the cause and any compatibility or data-preservation considerations.
- **How to verify the fix:** The regression check and adjacent cases to rerun, with expected passing results. Distinguish this proposed verification from checks actually run during review.

Finish with concise validation limitations and test-resource cleanup results. Do not include discarded candidates in the report or imply that all possible bugs have been found. If no candidates reproduce, say "No reproducible bugs found" and summarize the systems and workflows actually checked.
