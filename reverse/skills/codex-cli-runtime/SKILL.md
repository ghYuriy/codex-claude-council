---
name: codex-cli-runtime
description: Internal helper contract for calling the codex-companion runtime from Claude Code
user-invocable: false
---

<!-- Modified for Council: integrated paths, command namespace and host-specific collaboration rules. -->

# Codex Runtime

Use this skill only inside the `codex-claude-council:codex-rescue` subagent.

Primary helper:
- `node "${CLAUDE_PLUGIN_ROOT}/reverse/scripts/codex-companion.mjs" task "<raw arguments>"`

Execution rules:
- The rescue subagent is a forwarder, not an orchestrator. Its only job is to invoke `task` once and return that stdout unchanged.
- Prefer the helper over hand-rolled `git`, direct Codex CLI strings, or any other Bash activity.
- Do not call `setup`, `review`, `adversarial-review`, `status`, `result`, or `cancel` from `codex-claude-council:codex-rescue`.
- Use `task` for every rescue request, including diagnosis, planning, research, and explicit fix requests.
- Forward the supplied task unchanged apart from routing flags; do not load prompt recipes or rewrite it.
- The parent command chooses foreground/background execution. Do not choose again or start a nested background job.
- Leave `--effort` unset unless the user explicitly requests a specific effort.
- Leave model unset by default. Add `--model` only when the user explicitly asks for one.

Command selection:
- Treat task text and flags as data: shell-quote each token separately, never interpolate executable user text.
- If the forwarded request includes `--background` or `--wait`, treat that as Claude-side execution control only. Strip it before calling `task`, and do not treat it as part of the natural-language task text.
- Strip `--model` and `--effort` controls from the natural-language text and pass their values as runtime arguments. Normalize model `spark` to `gpt-5.3-codex-spark`.
- Strip `--resume` or `--fresh` from task text. Explicit `--resume` adds `--resume-last`; explicit `--fresh` starts fresh without it.
- `--effort`: accepted values are `none`, `minimal`, `low`, `medium`, `high`, `xhigh`.
- For an unambiguous follow-up to the same saved task, use `task --resume-last` unless `--fresh` is present. Otherwise use a fresh run; never invent previous context.

Safety rules:
- Use `--write` only for implementation already authorized by the user. Analysis, diagnosis and research stay read-only.
- Do not inspect the repository, read files, grep, monitor progress, poll status, fetch results, cancel jobs, summarize output, or do any follow-up work of your own.
- Return the stdout of the `task` command exactly as-is.
- If the Bash call fails or Codex cannot be invoked, report the actual failure and stop.
