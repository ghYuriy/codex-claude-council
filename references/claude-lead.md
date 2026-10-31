# Claude leads, Codex assists

Use this branch only in the leading Claude Code conversation. Claude retains the user's requirements and final assessment. Codex is the helper. This branch uses the bundled app-server runtime, not `scripts/council.py`, its state files or its `PASSED` gate.

## Start and choose the useful call

1. Inspect the actual task, project instructions, relevant sources and existing checks. Preserve the user's scope and prior edits. Read the focused questions in [quality-contract.md](quality-contract.md#focused-questions-within-the-existing-workflow) when conclusions, decisive assumptions or realistic failure mechanisms need examination. Apply their reasoning principles, not the other direction's controller commands.
2. Use the installed Codex CLI and its existing login and configuration. Run `setup --json` if readiness is unknown. Do not reinstall, change model/auth, add an API fallback or enable the stop review gate without the user's request. Leave model and reasoning effort unset unless requested.
3. Choose one call for the actual need. `review` checks local changes or a branch with the native read-only reviewer. `adversarial-review` also accepts focus text about assumptions, alternatives and failure modes. `task` handles diagnosis, planning, research or an authorized implementation. Do not run both review kinds automatically.
4. For a task handoff, give Codex the original requirements, source locations, concrete question, known observations, scope and relevant checks. Label facts, hypotheses and unknowns. Use the bundled [prompt guidance](../reverse/skills/gpt-5-4-prompting/SKILL.md) only when useful. It does not choose a model. Keep decisive evidence available; a summary is not a substitute for primary material.

## Call the bundled runtime

In the Claude plugin, the executable is:

```bash
node "${CLAUDE_PLUGIN_ROOT}/reverse/scripts/codex-companion.mjs" setup --json
```

If loaded as a standalone skill, resolve the skill root from the actual `SKILL.md` location and use its `reverse/scripts/codex-companion.mjs`. Do not search for a separately installed Codex plugin. Native slash commands and automatic session hooks require the Claude plugin installation.

| Need | Runtime command / options |
| --- | --- |
| Check readiness | `setup --json` |
| Native review | `review --scope working-tree` or `review --base main` |
| Challenge a decision | `adversarial-review --base main <focus>` |
| Investigate or advise | `task --prompt-file <file>`; read-only by default |
| Implement within authorized scope | `task --write --prompt-file <file>` |
| Continue the same task | `task --resume-last --prompt-file <file>` |
| Discover a resumable task | `task-resume-candidate --json` |
| Observe or retrieve a result | `status <job-id>` / `result <job-id>` |
| Cancel an owned job | `cancel <job-id>` |
| Continue the Claude conversation directly in Codex | `transfer` or `transfer --source <claude-jsonl>` |

Run from the user's project directory. Pass paths and text as separately shell-quoted arguments. For substantial task text, write a prompt file outside the product tree and pass `--prompt-file`; never interpolate raw user text into shell code. Use `--` before positional text that might start with a flag.

For an independent Codex opinion, do not supply Claude's preferred answer or prior model verdict. After that assessment, compare claims against sources and check disagreements. Every follow-up needs new evidence, a concrete repair or a distinguishing question; never repeat calls just to obtain agreement.

For background `task`, use the runtime's `--background` and record its job ID. For background review, use Claude's background Bash execution; the runtime's review `--background` flag alone does not detach it. Prefer foreground for a small call and background for a long one, without asking a ceremonial scheduling question. Never edit the reviewed files during review or let Claude and Codex write the same files concurrently.

The explicit `/codex-claude-council:rescue` command uses the bundled `codex-rescue` forwarding agent. It makes one runtime task call and returns its output. Direct runtime use in this workflow does not require another agent or an extra model round.

## Verify, continue or stop

Keep read-only mode for analysis and review. Add `--write` only for an implementation already authorized by the user. Codex's workspace-write sandbox does not authorize publication, destructive operations or unrelated changes. Neither helper may invoke another council or the other model.

After an authorized Codex edit, inspect the actual diff and run the project's relevant checks, including affected callers or user flows for consequential changes. Retain the original requirements. Treat unsupported findings as questions; reject harmful proposals with evidence. Report missing checks and failures explicitly. An empty result, exit code 0, `completed` or a model's approval is not proof of acceptance. Never claim the reverse mode passed the Python controller's gates.

For a standalone review request, return findings and stop without edits. For an already authorized repair, assess findings and continue the required repair without asking for the same authorization again. Preserve the full raw helper result in its job record; the final Council report may summarize it accurately with file references, performed checks and remaining limits. Explicit review/result commands still return the full result.

Resume only the matching task. Preserve the job and Codex session IDs in handoffs. Use `--resume-last` for an unambiguous continuation; use a fresh task for unrelated work. Ask only if more than one plausible task remains. A failed run stays failed; do not silently create a fresh run or report Claude's own answer as Codex output. Poll status rather than launching another model call. Cancel only the selected job.

`transfer` imports the actual Claude transcript into a resumable Codex conversation. Use it only when the user asks to move the conversation; it transfers the transcript's contents. SessionStart identifies the transcript in plugin mode; otherwise require the actual `--source` path. A missing or unsupported import is an explicit limitation, not permission to invent a session.

The optional stop review gate is off by default. Enable it only on request using `setup --enable-review-gate`; disable with `setup --disable-review-gate`. It can trigger repeated model calls and has no shared Council task budget. It does not replace tests or the original direction's checkpoints.

Plugin jobs live under Claude's plugin data directory, outside the project. SessionEnd stops jobs belonging to that Claude session and removes their transient records. Persistent Codex task threads can still be resumed by their saved IDs. Standalone runtime state falls back to a temporary directory. Save required results before ending the session; do not promise the original controller's durable evidence ledger in this direction.
