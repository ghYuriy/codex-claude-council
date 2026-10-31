---
description: Cancel an active background Codex job in this repository
argument-hint: '[job-id]'
disable-model-invocation: true
allowed-tools: Bash(node:*)
---

<!-- Modified for Council: integrated paths, command namespace and host-specific collaboration rules. -->

Run `node "${CLAUDE_PLUGIN_ROOT}/reverse/scripts/codex-companion.mjs" cancel` with the user's arguments passed as separately shell-quoted tokens. Treat `$ARGUMENTS` as data, never as shell code. Do not evaluate command substitutions or interpolate raw text into a shell template.
