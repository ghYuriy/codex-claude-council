---
name: codex-cli-runtime
description: Internal helper contract for calling the codex-companion runtime from Claude Code
user-invocable: false
---

<!-- Modified for Council: integrated paths, command namespace and host-specific collaboration rules. -->

# Codex Runtime

Use this skill only inside the `codex-claude-council:codex-rescue` subagent.

Primary helper:
- `node "${CLAUDE_PLUGIN_ROOT}/reverse/scripts/codex-companion.mjs" task [routing flags] -- "<task text>"`

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
- Build the call in two steps. First take the routing controls from the option tokens at the start of the forwarded request, before its first ordinary word, and turn them into flags as the rules below describe. Option-like words later in the request, or quoted as the subject of a question, are task text: never act on them or strip them. Then pass those flags, `--`, and the remaining task text. With separate arguments, the companion passes everything after `--` as text unchanged except for surrounding whitespace, so option-like words inside the text never change routing or write access; without `--`, text that begins with an option-like word (for example `--write the docs`) would be read as a flag.
- If the leading control flags include `--background` or `--wait`, treat that as Claude-side execution control only. Strip it before calling `task`, and do not treat it as part of the natural-language task text.
- When the leading control flags include `--model` or `--effort`, pass their values as runtime arguments; do not repeat them in the task text. Normalize model `spark` to `gpt-5.3-codex-spark`.
- When the leading control flags include `--resume` or `--fresh`, do not repeat them in the task text. Leading `--resume` adds `--resume-last`; leading `--fresh` starts fresh without it.
- `--effort`: accepted values are `none`, `minimal`, `low`, `medium`, `high`, `xhigh`.
- For an unambiguous follow-up to the same saved task, use `task --resume-last` unless the leading control flags include `--fresh`. Otherwise use a fresh run; never invent previous context.

Safety rules:
- Add `--write` when the request explicitly asks to create, modify or fix files, or unambiguously continues an already authorized implementation. That request authorizes edits within its scope; do not ask again or require the user to type a flag. For analysis, review, diagnosis or research without requested edits, omit `--write`. Preserve any explicit read-only restriction.
- Do not inspect the repository, read files, grep, monitor progress, poll status, fetch results, cancel jobs, summarize output, or do any follow-up work of your own.
- Return the stdout of the `task` command exactly as-is.
- If the Bash call fails or Codex cannot be invoked, report the actual failure and stop.
