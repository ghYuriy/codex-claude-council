---
name: codex-result-handling
description: Internal guidance for presenting Codex helper output back to the user
user-invocable: false
---

<!-- Modified for Council: integrated paths, command namespace and host-specific collaboration rules. -->

# Codex Result Handling

When the helper returns Codex output:
- Preserve the helper's verdict, summary, findings, and next steps structure.
- For review output, present findings first and keep them ordered by severity.
- Use the file paths and line numbers exactly as the helper reports them.
- Preserve evidence boundaries. If Codex marked something as an inference, uncertainty, or follow-up question, keep that distinction.
- Preserve output sections when the prompt asked for them, such as observed facts, inferences, open questions, touched files, or next steps.
- If there are no findings, say that explicitly and keep the residual-risk note brief.
- If Codex made edits, say so explicitly and list the touched files when the helper provides them.
- For `codex-claude-council:codex-rescue`, do not turn a failed or incomplete Codex run into a Claude-side implementation attempt. Report the failure and stop.
- For `codex-claude-council:codex-rescue`, if Codex was never successfully invoked, report that failure without generating a substitute Codex answer.
- A standalone review request authorizes no edits. For an already authorized repair, the leading Claude may assess the findings and continue within that scope; do not require duplicate permission. The forwarding agent itself never implements.
- If the helper reports malformed output or a failed Codex run, include the most actionable stderr lines and stop there instead of guessing.
- If the helper reports that setup or authentication is required, direct the user to `/codex-claude-council:setup` and do not improvise alternate auth flows.
