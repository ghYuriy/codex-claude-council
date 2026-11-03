---
description: Check whether the local Codex CLI is ready and optionally toggle the stop-time review gate
argument-hint: '[--enable-review-gate|--disable-review-gate]'
allowed-tools: Bash(node:*), Bash(npm:*), AskUserQuestion
---

<!-- Modified for Council: integrated paths, command namespace and host-specific collaboration rules. -->

When building a Bash call, pass every user argument as a separately shell-quoted token. `$ARGUMENTS` below denotes input data, not executable shell text. Never paste raw arguments into a command template. For task prompts prefer a prompt file and `--prompt-file` when it can be prepared safely.

During active Council work, do not enable the optional stop review gate or change installation/login settings. Use the shared controller and existing transport; standalone setup does not replace it.

`<arguments>` stands for the user's arguments, each written as its own single-quoted token; write a single quote inside a token as `'\''`.

Run:

```bash
node "${CLAUDE_PLUGIN_ROOT}/reverse/scripts/codex-companion.mjs" setup --json <arguments>
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
node "${CLAUDE_PLUGIN_ROOT}/reverse/scripts/codex-companion.mjs" setup --json <arguments>
```

If Codex is already installed or npm is unavailable:
- Do not ask about installation.

Output rules:
- Present the final setup output to the user.
- If installation was skipped, present the original setup output.
- If Codex is installed but not authenticated, preserve the guidance to run `!codex login`.

Stop review gate behaviour, to explain when the user asks about the gate:
- Disabled (the default): stopping is never blocked, including when the hook itself fails.
- Enabled: each stop runs one bounded Codex review of the previous turn. Findings, a failed or timed-out review, missing Codex setup or a failure inside the hook block that stop once with the reason; the immediately following stop is not reviewed again.
- The review stops itself after 14 minutes counted from the start of the hook, including the Codex availability check (each of its two calls is limited to 20 seconds), before the host's 900-second hook limit. On timeout it first cancels the running Codex job, which interrupts its turn in the shared runtime, then stops the processes it started, by force after 5 seconds. If the interruption is not confirmed, the block reason says so and names the job, because the turn may still be running in the shared Codex runtime; the job record and the plain `/cancel` and `/status` output carry the same warning, which the gate records itself if the cancel command hangs.
- A failure inside the hook is judged by the gate of the project the stop belongs to (the `cwd` in the hook input).
- Limitation: if the hook process cannot start at all or the host kills it at its own time limit, the host lets the stop proceed. Claude Code's `onFailure` setting does not apply to Stop hooks, so the gate cannot block in that case.
