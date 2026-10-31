---
name: gpt-5-4-prompting
description: Optional guidance for a host composing a standalone Codex request; not for the rescue forwarder or the shared Council protocol
user-invocable: false
---

<!-- Modified for Council: integrated paths, command namespace and host-specific collaboration rules. -->

# Composing a bounded Codex request

Use this guidance only when the leading host needs to compose a standalone request. The rescue forwarder passes the supplied request unchanged. An active Council task already has its own request schema, reviewer instructions, budget and stopping rules; do not layer these recipes onto it.

- Preserve the original user request and authorized scope. Put supplied context, constraints and observable completion criteria alongside it; do not invent facts or requirements.
- State the job, relevant evidence and expected result. Use the smallest structure that makes them clear. XML blocks are optional examples, not required ceremony.
- Ground claims in inspected material and label hypotheses and missing facts. An unavailable essential observation is a limit to report, not a reason to repeat the same search indefinitely.
- Choose checks tied to the task and real affected behavior. Once they pass and no material uncertainty remains, finish. A new observation, failed check or changed result may justify further work; confidence alone does not.
- Keep related work together. Do not split diagnosis, its authorized fix and necessary verification into extra model calls merely to follow a template. A review-only request still authorizes no edits.
- Do not select models, change effort or permissions, invoke tools or create follow-up runs on the strength of a prompt example. The calling command and existing authorization control execution.

Use only relevant examples from [prompt blocks](references/prompt-blocks.md), [task recipes](references/codex-prompt-recipes.md) or [anti-patterns](references/codex-prompt-antipatterns.md). Remove redundant instructions before sending. These references inherit the scope and stopping rules above.
