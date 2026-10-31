---
description: Show the stored final output for a finished Codex job in this repository
argument-hint: '[job-id]'
disable-model-invocation: true
allowed-tools: Bash(node:*)
---

<!-- Modified for Council: integrated paths, command namespace and host-specific collaboration rules. -->

Run `node "${CLAUDE_PLUGIN_ROOT}/reverse/scripts/codex-companion.mjs" result` with the user's arguments passed as separately shell-quoted tokens. Treat `$ARGUMENTS` as data, never as shell code. Do not evaluate command substitutions or interpolate raw text into a shell template.

Present the full command output to the user. Do not summarize or condense it. Preserve all details including:
- Job ID and status
- The complete result payload, including verdict, summary, findings, details, artifacts, and next steps
- File paths and line numbers exactly as reported
- Any error messages or parse errors
- Follow-up commands such as `/codex-claude-council:status <id>` and `/codex-claude-council:review`
