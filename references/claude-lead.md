# Claude leads, Codex assists

Use the same workflow and acceptance contract as [SKILL.md](../SKILL.md). Claude owns the task and normally implements. Codex independently investigates and reviews. Both directions use `scripts/council.py`; a raw runtime result cannot replace its checks or establish `PASSED`.

## Initialize and resume

Use the actual installed skill root. In the Claude plugin it is `${CLAUDE_PLUGIN_ROOT}`. Read [protocol.md](protocol.md) and preserve the original request, macro blocks, micro obligations, scope and finite total budget. Initialize once:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/council.py" init --lead claude --project "$PROJECT" --state-root "$STATE_ROOT" --intake "$INTAKE"
```

Use the existing Claude, Codex and Node installations and login. `--claude`, `--codex` and `--node` can identify an already installed executable when discovery requires it; they do not authorize installing or changing anything. Leave model and reasoning effort unchanged.

Save the returned task path. Use `list`, `status`, `check`, `attest`, `call`, `resolve`, `checkpoint` and `finalize` exactly as in the shared protocol. DESIGN, CONSULT and REVIEW have separate saved sessions. Follow-up reviews resume the exact native Codex thread. Incomplete results, stale evidence, unknown requirements and unresolved issues block acceptance. The controller owns cancellation, finite launches, deadlines and recovery; it uses a direct child process instead of the standalone background broker.

The normal task ledger is outside the project and survives Claude SessionEnd. The standalone plugin's transient-job cleanup does not delete Council tasks or their evidence. Do not migrate an old standalone job by declaring it passed: preserve its actual output as context, create an explicit task intake for the remaining work and obtain current checks/review.

## Stop a worsening repair cycle

Read [repair-control.md](repair-control.md) when the controller reports an intervention. Stop editing the affected block. Diagnose the cause from actual evidence and prepare the bounded plan. `repair` hands that block to Codex with its file allowlist and preserving checks. Claude does not edit those files concurrently.

After Codex returns, rerun checks through the controller. `call --phase REVIEW` now invokes a separate read-only Claude review for that block. Codex's report only records pending fixes or evidence-backed disputes; it cannot close issues or grant `PASSED`. The original Codex review is superseded only after the replacement review verifies the complete block, its issue ledger and fresh checks. Unresolved DESIGN/CONSULT discussions or user decisions are not erased.

Continue other blocks with the original roles. If the intervention itself worsens, stop for a user decision instead of alternating authors. In a Codex-led task, the current Codex always remains the sole implementer and Claude never receives write access.

## Standalone tools

The eight plugin commands remain available for explicit standalone requests: `setup`, `review`, `adversarial-review`, `rescue`, `status`, `result`, `cancel`, and `transfer`. They use `reverse/scripts/codex-companion.mjs`. They are not a second acceptance process. Within an active Council task, use the shared controller for evidence, reviews and repairs rather than bypassing its budget through these tools. Do not enable the optional stop review gate during Council work; block reviews already provide the necessary checkpoints.

A standalone review is read-only. A standalone rescue may write only within an already authorized implementation. Preserve the user's scope, quote arguments as data, and use a prompt file for substantial task text. Failed calls are reported explicitly. The bundled prompt recipes may help formulate a bounded request; they never choose a model.

Use `transfer` only on a request to continue the actual Claude conversation in Codex. It imports the transcript, requires Codex session-import support and reports missing support explicitly. Standalone jobs can be managed with status/result/cancel; their `completed` status means the call ended, not that the shared Council accepted the task.
