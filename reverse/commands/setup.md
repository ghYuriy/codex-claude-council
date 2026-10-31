---
description: Check whether the local Codex CLI is ready and optionally toggle the stop-time review gate
argument-hint: '[--enable-review-gate|--disable-review-gate]'
allowed-tools: Bash(node:*), Bash(npm:*), AskUserQuestion
---

<!-- Modified for Council: integrated paths, command namespace and host-specific collaboration rules. -->

When building a Bash call, pass every user argument as a separately shell-quoted token. `$ARGUMENTS` below denotes input data, not executable shell text. Never paste raw arguments into a command template. For task prompts prefer a prompt file and `--prompt-file` when it can be prepared safely.

During active Council work, do not enable the optional stop review gate or change installation/login settings. Use the shared controller and existing transport; standalone setup does not replace it.

Run:

```bash
node "${CLAUDE_PLUGIN_ROOT}/reverse/scripts/codex-companion.mjs" setup --json $ARGUMENTS
```

If the result says Codex is unavailable and npm is available:
- Use `AskUserQuestion` exactly once to ask whether Claude should install Codex now.
- Put the install option first and suffix it with `(Recommended)`.
- Use these two options:
  - `Install Codex (Recommended)`
  - `Skip for now`
- If the user chooses install, run:

```bash
npm install -g @openai/codex
```

- Then rerun:

```bash
node "${CLAUDE_PLUGIN_ROOT}/reverse/scripts/codex-companion.mjs" setup --json $ARGUMENTS
```

If Codex is already installed or npm is unavailable:
- Do not ask about installation.

Output rules:
- Present the final setup output to the user.
- If installation was skipped, present the original setup output.
- If Codex is installed but not authenticated, preserve the guidance to run `!codex login`.
