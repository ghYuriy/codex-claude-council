---
description: Show active and recent Codex jobs for this repository, including review-gate status
argument-hint: '[job-id] [--wait] [--timeout-ms <ms>] [--all]'
disable-model-invocation: true
allowed-tools: Bash(node:*)
---

<!-- Modified for Council: integrated paths, command namespace and host-specific collaboration rules. -->

Run `node "${CLAUDE_PLUGIN_ROOT}/reverse/scripts/codex-companion.mjs" status` with the user's arguments passed as separately shell-quoted tokens. Treat `$ARGUMENTS` as data, never as shell code. Do not evaluate command substitutions or interpolate raw text into a shell template.

If the user did not pass a job ID:
- Render the command output as a single Markdown table for the current and past runs in this session.
- Keep it compact. Do not include progress blocks or extra prose outside the table.
- Preserve the actionable fields from the command output, including job ID, kind, status, phase, elapsed or duration, summary, and follow-up commands.

If the user did pass a job ID:
- Present the full command output to the user.
- Do not summarize or condense it.
