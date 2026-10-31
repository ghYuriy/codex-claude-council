# Stop repair cycles without adding routine rounds

Use this only when a repair deteriorates or repeats without progress. Ordinary tasks keep the existing implement, check, review sequence. The host manages these commands; the user does not fill out an extra checklist.

## Diagnose the change in evidence

Compare the same block, requirements and stable issue IDs. A smaller issue count is not proof of progress; a larger count is not proof the author caused new bugs. Distinguish:

- A previously verified behavior now fails. Reproduce the prior case and keep its ID and closure history.
- A repair creates a different failure along an affected dependency. Cite the actual before/after evidence and call path.
- A reviewer discovers a pre-existing defect. Record it honestly as a discovery, not a regression or evidence of an incompetent author.
- A report renames, splits or restates an existing finding, changes the review scope, proposes a preference, or lacks a counterexample. Reconcile IDs and scope; dispute unsupported advice rather than changing correct code.
- A check fails because of unavailable dependencies, stale state, test instability or an environmental change. Determine that from evidence before attributing it to the patch.

Read the original requirements, current diff, prior closure evidence and the smallest distinguishing case. State the causal explanation as verified or as a hypothesis. Trace the relevant caller/state/consumer path. Prefer repairing a shared cause once over applying independent patches to every symptom. A proposal must preserve the intended behavior and all accepted fixes. If the original premise is wrong, change the approach within the original requirements rather than piling on more exceptions.

## Automatic interruption

The shared controller stops the ordinary loop on any of these events:

- A `VERIFIED_FIXED` issue is reported again, even when the total count falls.
- A recorded check protecting a verified fix fails.
- Open findings grow after a changed snapshot in the same block. This is a precautionary trigger to reassess the repair, not a causal verdict.
- The same root issue survives two claimed fixes on different snapshots.
- Unresolved findings recur with the same product and equivalent check evidence. Merely changing the goal text, timestamps or artifact paths is not progress.

Reopening preserves the canonical ID, occurrences and closure history. Every closing check remains a required preservation check at later acceptance. Keep its reproducing scenario in the actual test suite; the controller cannot determine whether a rewritten test still exercises the same behavior. The reviewer must inspect that, and cannot request weaker tests to reach agreement.

## One bounded intervention

Prepare a JSON repair plan outside the product tree. For example:

```json
{
  "issue_ids": ["I-001", "I-002"],
  "cause": "Both failures may come from applying the update before validating its version.",
  "cause_status": "hypothesis",
  "evidence": ["src/update.py:24", "check:update-tests"],
  "files": ["src/update.py", "tests/test_update.py"],
  "checks": ["update-tests"],
  "strategy": "Reproduce the stale update, validate before mutation, and preserve the retry and empty-input cases."
}
```

The examples are illustrative. Use real issue IDs, files, lines and recorded checks. Name a connected repair target, not every file in the project. Explain why this strategy differs from the failed attempt. Do not turn the plan into a new architecture project. A write-capable handoff requires an already authorized implementation; analysis-only requests still authorize no edits.

```bash
python3 "$COUNCIL" repair --task "$TASK" --stage implementation --plan "$REPAIR_PLAN"
```

When Claude leads, this command calls Codex as the sole implementer for that block. It uses the same task budget, deadline and cancellation supervisor. Codex gets the evidence and file allowlist; the controller compares the actual observed files before/after. Out-of-scope changes block acceptance and require inspection; they are never silently reverted over user work. The workspace sandbox covers the project, not the narrower allowlist, so the latter is checked after execution. Normal snapshot coverage still applies, including the non-Git scope limitations in the protocol.

When Codex leads, this command records the revised plan without launching another Codex or granting Claude write access. The current Codex performs the repair. In both cases, the helper must distinguish justified fixes from unsupported suggestions; a disputed suggestion receives evidence and a subsequent independent check instead of a cosmetic patch.

After implementation, run the related checks and preserving checks through `check`. Record manual implementer decisions with `resolve` where needed. Call REVIEW as usual. In a Claude-led intervention, the controller selects a fresh read-only Claude session to verify the Codex repair. It supplies the stable issue ledger needed for closure, not an invitation to defend the previous author. Follow-ups resume that verification session. There is no required extra debate before this review.

The replacement reviewer must verify the full affected block, related routes and retained fixes. It cannot approve only the changed lines. Successful repair output leaves issues pending; only valid independent closure and fresh passing checks can complete the intervention and then establish `PASSED` through `finalize`. The prior reviewer record remains in history. Only that block's replaced review is superseded; unrelated discussions and user decisions remain obligations.

## Stop instead of swapping again

If the intervention deteriorates again, it becomes `FAILED`. There is no automatic switch back, budget reset, fresh-task escape or repeated identical repair. Explain the unresolved cause, evidence, attempted strategies and concrete alternatives: a different bounded approach, a narrower authorized scope, or a real missing fact/decision. A user may explicitly authorize a new evidence-backed plan with `repair --user-decision <saved-decision-file>`; that records the decision and keeps prior attempts and consumed budget. A separate budget extension still needs its own explicit authorization.

Model agreement, confidence and issue counts cannot prove correctness. The controller enforces process and evidence integrity; source interpretation, adequacy of tests and causal attribution still require judgment. When no useful next check exists, preserve the unresolved result and stop.
