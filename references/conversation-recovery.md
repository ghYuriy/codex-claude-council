# Conversation continuity in Codex

Council task state and the host conversation are different identities. Bind the native Codex conversation explicitly. Never choose a task by the newest file in a shared project directory. This hook does not run a helper or recover an unknown conversation by guessing.

## Automatic global connection

The default global installer registers the four Council handlers in the native **user configuration**, preserves other settings, trusts only their exact native definition hashes, and verifies them with `hooks/list`. New projects need no `.codex/hooks.json`, project trust change or user setup. User-level hooks also load in projects whose local configuration is untrusted. Disabled or administrator-restricted hooks are reported; the installer does not bypass those controls.

For an existing installed skill, the leading agent can repair the same global registration without asking the user to configure each project:

```bash
python3 "$CONVERSATION" install-hooks
```

This command uses the installed host from `CODEX_APP_RUNTIME_CLI_PATH`, or the existing `codex` on PATH; `--codex` can name an inspected existing executable. It does not call a model or change model/login defaults. Repeated installation preserves handler order and adds no duplicate. An explicitly supplied `--project` retains the old project-only installer for compatibility; it is not the normal global workflow. When upgrading a known 1.4.0 project installation, remove only its exact Council handlers after global registration is verified, preserving unrelated hooks and settings.

`SessionStart/compact|resume` restores the bound task; `UserPromptSubmit` retains later requests for bound conversations; `Stop` requests at most one continuation of unfinished work; `Interrupt` records a pause. Unbound conversations are no-ops. The reverse Claude host retains its own existing handlers.

## Automatic binding during Council use

In a native Codex conversation, ordinary `council.py init` uses `CODEX_THREAD_ID` and automatically saves the original intake source, macro goal, Council task path and next step. No separate `begin` command or project configuration is required. Run `init` from the native chat cwd, using `--project` for the actual reviewed tree. If the shell command runs elsewhere, the agent supplies `--conversation-cwd` from the chat environment. This internal path distinction does not require user setup.

The default record root is `~/.local/state/codex-claude-council/conversations`. A deliberately custom `--conversation-root` must match the globally registered handler root; the normal installer and init use the same default. The selected cwd and root are saved in the task. Mutating commands and `recover` restore a missing initial binding from that same task without changing sessions or budgets. `status` stays read-only.

An existing active objective cannot be replaced. A later freshly initialized task can start a new generation after completion/cancellation when a later user message is retained or its original source is new; the previous generation remains saved. The explicit native ID, canonical cwd, original source hash and original Council binding remain checked. Reusing the task preserves its saved objective, unfinished step and running operations. Plain CLI use without a native Codex ID and Claude-led tasks do not silently create Codex recovery records.

`begin` remains available for an explicitly identified standalone record and older workflows. It is unnecessary for normal Codex-led Council initialization.

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
