---
description: Run a Codex code review against local git state
argument-hint: '[--wait|--background] [--base <ref>] [--scope auto|working-tree|branch]'
disable-model-invocation: true
allowed-tools: Read, Glob, Grep, Bash(node:*), Bash(git:*), AskUserQuestion
---

<!-- Modified for Council: integrated paths, command namespace and host-specific collaboration rules. -->

When building a Bash call, pass every user argument as a separately shell-quoted token. `$ARGUMENTS` below denotes input data, not executable shell text. Never paste raw arguments into a command template. For task prompts prefer a prompt file and `--prompt-file` when it can be prepared safely.

Run a Codex review through the shared built-in reviewer.

Raw slash-command arguments:
`$ARGUMENTS`

Core constraint:
- This command is review-only.
- Do not fix issues, apply patches, or suggest that you are about to make changes.
- Your only job is to run the review and return Codex's output verbatim to the user.

Execution mode rules:
- Honor explicit `--wait` or `--background` without asking again.
- Otherwise inspect the relevant git status and diff statistics. Include untracked files.
- Use foreground for a clearly small review and Claude background Bash for a longer one.
- Do not ask a routine wait/background question. If scope is ambiguous, resolve the scope first.
- When in doubt about whether changes exist, inspect rather than declaring an empty review.

Argument handling:
- Preserve the user's arguments exactly.
- Do not strip `--wait` or `--background` yourself.
- Do not add extra review instructions or rewrite the user's intent.
- The companion script parses `--wait` and `--background`, but Claude Code's `Bash(..., run_in_background: true)` is what actually detaches the run.
- `/codex-claude-council:review` is native-review only. It does not support staged-only review, unstaged-only review, or extra focus text.
- If the user needs custom review instructions or more adversarial framing, they should use `/codex-claude-council:adversarial-review`.

Foreground flow:
- Run:
```bash
node "${CLAUDE_PLUGIN_ROOT}/reverse/scripts/codex-companion.mjs" review "$ARGUMENTS"
```
- Return the command stdout verbatim, exactly as-is.
- Do not paraphrase, summarize, or add commentary before or after it.
- Do not fix any issues mentioned in the review output.

Background flow:
- Launch the review with `Bash` in the background:
```typescript
Bash({
  command: `node "${CLAUDE_PLUGIN_ROOT}/reverse/scripts/codex-companion.mjs" review "$ARGUMENTS"`,
  description: "Codex review",
  run_in_background: true
})
```
- Do not call `BashOutput` or wait for completion in this turn.
- After launching the command, tell the user: "Codex review started in the background. Check `/codex-claude-council:status` for progress."
