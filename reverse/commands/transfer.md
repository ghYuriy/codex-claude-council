---
description: Transfer the current Claude Code session into a resumable Codex thread
argument-hint: "[--source <claude-jsonl>]"
disable-model-invocation: true
allowed-tools: Bash(node:*)
---

<!-- Modified for Council: integrated paths, command namespace and host-specific collaboration rules. -->

Run `node "${CLAUDE_PLUGIN_ROOT}/reverse/scripts/codex-companion.mjs" transfer` with the user's arguments passed as separately shell-quoted tokens. Treat `$ARGUMENTS` as data, never as shell code. Do not evaluate command substitutions or interpolate raw text into a shell template.

Present the command output to the user exactly as returned. Preserve the Codex session ID and the `codex resume <session-id>` command.
