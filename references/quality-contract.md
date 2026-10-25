# Evidence, depth and proportionality

## Shared objective

The task outcome has priority over the identity of the model proposing a solution. Codex and Claude must change their positions when evidence warrants it. Neither model must find a defect, defend an earlier answer, express confidence without basis or agree to end the conversation. A sound result can be an implemented solution, a rejected alternative, an unresolved factual question or a decision reserved for the user.

## Strict factual boundary

Forbidden: invented observations, sources, line numbers, command results or measurements; unstated assumptions used as facts; statements that an entire system works because one component passed; superficial assertions without examination; weakened requirements; partial implementations presented as completion; workarounds that conceal the root cause; silent omission of inaccessible material; deletion or dilution of useful tests for a green result.

Use explicit categories:

- **Observed fact:** names the actual source, current artifact, command/result or direct observation and its scope.
- **Derived conclusion:** explains how the cited facts support it, and where that implication ends.
- **Unverified question:** names the missing fact and the smallest useful observation or experiment. It cannot establish acceptance.
- **Proposal:** describes a possible action and its tradeoffs. It is not evidence that the action works.
- **User decision:** preserves the actual authorized choice, not a model's inference about what the user would probably accept.

Diagnosing a bug often requires testable hypotheses. Prohibiting their existence would prevent causal investigation. The strict boundary is that a hypothesis is an unverified question until the discriminating check is performed; it cannot silently become a fact. Where evidence is unattainable, report the limit rather than invent the answer.

## Focused questions within the existing workflow

Use these questions only where they can change a decision or its verification. Apply them during source inspection, an existing DESIGN/CONSULT discussion or REVIEW; they are not additional stages or a checklist that every small task must repeat. Combine relevant questions in one discussion and keep its existing session and budget.

- **Does the evidence support the conclusion?** Separate factual accuracy, inference and compliance with requirements. Check whether the conclusion overstates scope, certainty or causality, uses a different population/timeframe, or omits counterevidence. State the supported conclusion precisely. If a necessary fact is missing, identify the source or check needed to establish it; do not silently weaken an acceptance criterion to fit available evidence.
- **Which assumption could change the decision?** Identify the premise on which the proposed approach depends. Distinguish a verified constraint from a preference or an unverified assumption. Compare a plausible alternative, including reuse or a smaller change when appropriate. Choose the smallest observation that distinguishes the options. An alternative must still satisfy the original requirements; do not invent objections or defend an approach because a model proposed it.
- **How could this plan fail in practice?** For consequential changes, examine plausible failure paths grounded in the actual dependencies and operating conditions, such as retries, stale data or rollback. Explain the mechanism, an observable early signal and a proportionate check or prevention within scope. Prioritize by supported plausibility and impact, without invented probabilities, arbitrary future deadlines or speculative infrastructure.

Record answers in the existing coverage, issues, unverified items and exchanges. A possible failure is a hypothesis until evidence establishes a defect. Preserve a material unknown as unverified when it prevents acceptance; a well-supported result may need no finding or follow-up. These questions improve inspection, not the controller's ability to prove reasoning correct.

## Macro and micro depth

At the macro level identify the intended user outcome, constraints, major blocks, interfaces between them, risks and final integration criteria. At the micro level break each block into behavior that can be inspected or checked: inputs, relevant states, transitions, outputs, failures, ownership and dependencies. Link every leaf to a requirement and a meaningful check.

Sequence work by prerequisites and uncertainty. Before committing to a costly approach, check the assumption most likely to invalidate it. Prefer small blocks that exercise a meaningful path through the affected components, so integration failures appear early; use separate component work when real dependencies require it. This does not replace the final integration criterion. If evidence invalidates the approach, revisit it before adding more fixes, preserving the requirements and obtaining a decision when the immutable intake must change.

Stop splitting when a leaf has a clear observable completion condition and no material ambiguity about dependencies or failure behavior. Do not decompose every line or enumerate an unbounded space of hypothetical cases. A one-line repair may have one macro block and two micro obligations: the failing case and one related preserved behavior. A migration may require several blocks, transition checks and a final end-to-end check.

For analysis or documents, leaves may be source verification, factual reconciliation, constraint evaluation or checking whether a recommendation follows from the evidence. Use saved primary source excerpts/URLs with retrieval dates and appropriate manual evidence. No code changes are required for an analytical task.

## Trace effects beyond the changed file

For a substantial modification, establish the observed dependency route before changing it. For example, a request handler may call a parser, update storage and notify a consumer. Check the local change and the meaningful route that uses it. Include data compatibility, error paths and reversibility where the actual task makes them relevant.

`work_plan.impact` records a route of project-relative files, the evidence for the relation, its relevance and related declared checks. This is a task-specific map, not a speculative system inventory. The controller verifies the records and required current checks; the models remain responsible for whether the route map is complete and the checks actually exercise it.

When new evidence reveals an omitted material dependency, stop the affected completion claim. Preserve the finding and obtain an explicit scope/plan decision if the immutable intake needs changing; do not conceal it to satisfy an existing matrix. The current controller intentionally does not silently rewrite the original requirements.

## Full work without overengineering

Prefer the least complex solution that satisfies every requirement and real constraint. Simplicity may remove redundant mechanisms; it may not remove required behavior, data protection, error handling or meaningful validation. Add no framework, layer, option, dependency or test merely because it could be useful someday.

Every check must correspond to an acceptance criterion, observed failure, plausible regression or actual affected route. Combine equivalent cases when doing so preserves the observable guarantee. Do not inflate confidence through test counts. Do not reduce the guarantee to obtain a smaller count either.

For each disputed proposal, identify its benefit, actual cost/risk, supporting evidence, counterevidence and a discriminating check when one exists. Consider different perspectives directly, without spawning fictional personas or encouraging them to argue one side regardless of evidence.

## What an exchange must accomplish

A useful exchange changes the evidence state: a new observation, reproduced failure, narrowed uncertainty, verified repair, reasoned rejection or a precise user decision. Renaming an issue, rephrasing a claim, rerunning the same passing test or repeating a conclusion is not progress by itself.

Keep the full primary material accessible and send a compact index plus the relevant argument. Do not impose a fixed number of sentences or replace inspection with summaries. End once the acceptance obligations are satisfied; additional approval rounds are unnecessary. If the budget expires first, preserve unresolved matters and request an explicit extension only if there is a concrete next check worth performing.
