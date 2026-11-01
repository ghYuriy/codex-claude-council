# Conversation continuity in Codex

Council task state and the host conversation are different identities. Bind the native Codex conversation explicitly. Never choose a task by the newest file in a shared project directory. This hook does not run a helper or recover an unknown conversation by guessing.

## Connect the host

Set `CONVERSATION` to this skill's absolute `scripts/conversation.py` path. `CODEX_THREAD_ID` supplies the current native conversation ID. The default record root is `~/.local/state/codex-claude-council/conversations`; keep an explicitly chosen root on every command and hook.

```bash
python3 "$CONVERSATION" install-hooks --project "$PROJECT"
```

This merges only the Council handler into `<project>/.codex/hooks.json`, preserving other handlers, and is idempotent. It does not grant trust. The project and the exact hook definitions must be trusted through Codex's normal hook review. Inspect `/hooks` and the loaded conversation's `hooks/list` result. A file on disk is not proof of activation. Existing loaded conversations may need their configuration reloaded before they see the hooks; verify the actual loaded set rather than restarting unrelated sessions.

The tested events are `SessionStart` with `compact|resume`, `UserPromptSubmit`, `Stop` and `Interrupt`. `SessionStart/compact` returns the bound task before continuation; `UserPromptSubmit` retains subsequent user messages only for bound conversations. `Stop` requests one continuation if the bound task remains active, regardless of the answer's wording. It does not start an unbounded loop. `Interrupt` records a pause and does not resume work. Unbound conversations are no-ops.

The installer and event format here target Codex. The reverse Claude host keeps its existing lifecycle handlers; do not claim that installing Codex hooks also activates a new Claude handler.

## Bind and maintain one conversation

Save the exact original user request in a file, separately from the derived requirements catalogue. Then:

```bash
python3 "$CONVERSATION" begin --cwd "$PROJECT" --source-file "$USER_REQUEST" --objective "The concrete requested outcome" --next-step "The next authorized action"
```

`begin` uses the native ID from the environment. `--session` is available for an explicitly identified conversation. The record contains the native ID, canonical cwd, original request hash, objective, status, next action, evidence and operations. The Council binding checks the exact original request hash, related workspace and native conversation ID saved by `init --conversation` or its native environment default. Once bound, another Council task cannot replace it. A passing unrelated task cannot complete this request. Legacy tasks without a native ID retain their existing protocol, but must match the request and workspace. An active record cannot be replaced by a new `begin`. A completed record can start another task only after a later user message was retained; the previous generation is preserved.

After a substantive stage and before waiting, write a checkpoint JSON file with:

```json
{
  "status": "waiting",
  "next_step": "Read the existing review result; do not launch another review.",
  "operations": [
    {"id": "existing-operation-id", "kind": "review", "status": "running", "reference": "/absolute/path/to/its/task"}
  ],
  "evidence": ["/absolute/path/to/a/saved/check.log"],
  "council_task": "/absolute/path/to/the/original/council/task"
}
```

Use actual paths and IDs, not these example values. Update the record:

```bash
python3 "$CONVERSATION" update --cwd "$PROJECT" --checkpoint "$CHECKPOINT"
python3 "$CONVERSATION" show --cwd "$PROJECT"
```

`active` and `waiting` need an unfinished step. `needs-user` needs the actual missing decision or access in `blocker`. `paused` does not authorize automatic resumption. `complete` requires no queued/running operations, an empty next step, a nonempty `result_file`, and the original bound Council task with current `PASSED` acceptance. `cancelled` needs the actual cancellation in `user_decision`. Neither the host nor the hook may manufacture that decision. These records establish process obligations and evidence links, not a semantic proof that an arbitrary written result fulfils every user intention.

After a context transition, read the returned card and its original request/checkpoint. Apply later user messages first. Inspect recorded operations and preserve their session IDs and budgets before deciding what to run. A completed/cancelled/paused record is past or suspended work, not a command to repeat it. If a record is corrupt, inaccessible or bound to another cwd, the hook reports unavailable recovery rather than claiming there is no task. The normal history and saved task files are fallback sources; their unavailability does not complete the task.

## Verification boundary

Tests of hook text or a fabricated event alone do not verify host delivery. Use the installed Codex host with a loopback response fixture to exercise actual manual and mid-turn automatic compaction, verify the next outbound request contains the task card, and verify the recorded waiting operation is unchanged. A control run without the hook must detect missing recovery. Also check two native IDs in one cwd, missing history, terminal and paused states, exact hook trust, and the bounded Stop continuation. Do not launch another real model merely to test this plumbing. Record the host version and distinguish event transport from semantic model performance.
