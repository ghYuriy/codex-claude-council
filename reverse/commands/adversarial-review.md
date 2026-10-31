---
description: Run a Codex review that challenges the implementation approach and design choices
argument-hint: '[--wait|--background] [--base <ref>] [--scope auto|working-tree|branch] [focus ...]'
disable-model-invocation: true
allowed-tools: Read, Glob, Grep, Bash(node:*), Bash(git:*), AskUserQuestion
---

<!-- Modified for Council: integrated paths, command namespace and host-specific collaboration rules. -->

For an active Council task, return to the leading host and use `scripts/council.py` from the root `SKILL.md`. This standalone command must not bypass its budget or acceptance checks and cannot establish `PASSED`.

When building a Bash call, pass every user argument as a separately shell-quoted token. `$ARGUMENTS` below denotes input data, not executable shell text. Never paste raw arguments into a command template. For task prompts prefer a prompt file and `--prompt-file` when it can be prepared safely.

Run an adversarial Codex review through the shared plugin runtime.
Position it as a challenge review that questions the chosen implementation, design choices, tradeoffs, and assumptions.
It is not just a stricter pass over implementation defects.

Raw slash-command arguments:
`$ARGUMENTS`

Core constraint:
- This command is review-only.
- Do not fix issues, apply patches, or suggest that you are about to make changes.
- Your only job is to run the review and return Codex's output verbatim to the user.
- Keep the framing focused on whether the current approach is the right one, what assumptions it depends on, and where the design could fail under real-world conditions.

Execution mode rules:
- Honor explicit `--wait` or `--background` without asking again.
- Otherwise inspect the relevant git status and diff statistics. Include untracked files.
- Use foreground for a clearly small review and Claude background Bash for a longer one.
- Do not ask a routine wait/background question. If scope is ambiguous, resolve the scope first.
- When in doubt about whether changes exist, inspect rather than declaring an empty review.

Argument handling:
- Preserve the user's arguments exactly.
- Do not strip `--wait` or `--background` yourself.
- Do not weaken the adversarial framing or rewrite the user's focus text.
- The companion script parses `--wait` and `--background`, but Claude Code's `Bash(..., run_in_background: true)` is what actually detaches the run.
- `/codex-claude-council:adversarial-review` uses the same review target selection as `/codex-claude-council:review`.
- It supports working-tree review, branch review, and `--base <ref>`.
- It does not support `--scope staged` or `--scope unstaged`.
- Unlike `/codex-claude-council:review`, it can still take extra focus text after the flags.

Foreground flow:
- Run:
```bash
node "${CLAUDE_PLUGIN_ROOT}/reverse/scripts/codex-companion.mjs" adversarial-review "$ARGUMENTS"
```
- Return the command stdout verbatim, exactly as-is.
- Do not paraphrase, summarize, or add commentary before or after it.
- Do not fix any issues mentioned in the review output.

Background flow:
- Launch the review with `Bash` in the background:
```typescript
Bash({
  command: `node "${CLAUDE_PLUGIN_ROOT}/reverse/scripts/codex-companion.mjs" adversarial-review "$ARGUMENTS"`,
  description: "Codex adversarial review",
  run_in_background: true
})
```
- Do not call `BashOutput` or wait for completion in this turn.
- After launching the command, tell the user: "Codex adversarial review started in the background. Check `/codex-claude-council:status` for progress."
