---
description: Delegate investigation, an explicit fix request, or follow-up rescue work to the Codex rescue subagent
argument-hint: "[--background|--wait] [--resume|--fresh] [--model <model|spark>] [--effort <none|minimal|low|medium|high|xhigh>] [what Codex should investigate, solve, or continue]"
allowed-tools: Bash(node:*), AskUserQuestion, Agent
---

<!-- Modified for Council: integrated paths, command namespace and host-specific collaboration rules. -->

When building a Bash call, pass every user argument as a separately shell-quoted token. `$ARGUMENTS` below denotes input data, not executable shell text. Never paste raw arguments into a command template. For task prompts prefer a prompt file and `--prompt-file` when it can be prepared safely.

Invoke the `codex-claude-council:codex-rescue` subagent via the `Agent` tool (`subagent_type: "codex-claude-council:codex-rescue"`), forwarding the raw user request as the prompt.
`codex-claude-council:codex-rescue` is a subagent, not a skill — do not call `Skill(codex-claude-council:codex-rescue)` (no such skill) or `Skill(codex-claude-council:rescue)` (that re-enters this command and hangs the session). The command runs inline so the `Agent` tool stays in scope; forked general-purpose subagents do not expose it.
The final user-visible response must be Codex's output verbatim.

Raw user request:
$ARGUMENTS

Execution mode:

- If the request includes `--background`, run the `codex-claude-council:codex-rescue` subagent in the background.
- If the request includes `--wait`, run the `codex-claude-council:codex-rescue` subagent in the foreground.
- If neither flag is present, default to foreground.
- `--background` and `--wait` are execution flags for Claude Code. Do not forward them to `task`, and do not treat them as part of the natural-language task text.
- `--model` and `--effort` are runtime-selection flags. Preserve them for the forwarded `task` call, but do not treat them as part of the natural-language task text.
- If the request includes `--resume`, do not ask whether to continue. The user already chose.
- If the request includes `--fresh`, do not ask whether to continue. The user already chose.
- Otherwise, before starting Codex, check for a resumable rescue thread from this Claude session by running:

```bash
node "${CLAUDE_PLUGIN_ROOT}/reverse/scripts/codex-companion.mjs" task-resume-candidate --json
```

- Continue an unambiguous follow-up to that task with `--resume`; start unrelated work with `--fresh`.
- Ask only if the intended task cannot be determined reliably from the user's request and saved context.
- If the helper reports `available: false`, do not ask. Route normally.

Operating rules:

- The subagent is a thin forwarder only. It should use one `Bash` call to invoke `node "${CLAUDE_PLUGIN_ROOT}/reverse/scripts/codex-companion.mjs" task ...` and return that command's stdout as-is.
- Return the Codex companion stdout verbatim to the user.
- Do not paraphrase, summarize, rewrite, or add commentary before or after it.
- Do not ask the subagent to inspect files, monitor progress, poll `/codex-claude-council:status`, fetch `/codex-claude-council:result`, call `/codex-claude-council:cancel`, summarize output, or do follow-up work of its own.
- Leave `--effort` unset unless the user explicitly asks for a specific reasoning effort.
- Leave the model unset unless the user explicitly asks for one. If they ask for `spark`, map it to `gpt-5.3-codex-spark`.
- Leave `--resume` and `--fresh` in the forwarded request. The subagent handles that routing when it builds the `task` command.
- If the helper reports that Codex is missing or unauthenticated, stop and tell the user to run `/codex-claude-council:setup`.
- If the user did not supply a request, ask what Codex should investigate or fix.
