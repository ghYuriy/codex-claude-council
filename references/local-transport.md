# Claude Code transport

This reference describes the Claude reviewer transport in either direction. The Codex adapter is described in [protocol.md](protocol.md). The controller uses the installed `claude` executable and its existing authentication. It sends prompts through stdin with `claude -p` and reads JSON output. A new discussion uses `--session-id`; later calls use `--resume` with that same session ID.

## Reviewer access

The reviewer receives only `Read`, `Grep` and `Glob`. `--disable-slash-commands` suppresses nested skills. `--strict-mcp-config --mcp-config '{"mcpServers":{}}'` disables MCP for the child process without changing global configuration. Per-call `--settings` also disables hooks, so a review hook cannot launch another model. These restrictions do not isolate the filesystem.

The controller adds the reviewer instructions and JSON schema to each call. It validates the process result, session ID, response structure and evidence references before accepting a report. Partial or unparsed output cannot count as a successful review.

## Runtime settings

The controller does not override the model, change credentials, install a separate API client or start another Codex when Codex is the leading host. It preserves the installed CLI's model selection and saved sessions; with a filtered environment the CLI's documented model, effort, client-certificate and privacy variables still pass. Each call has a finite timeout within the task's shared budget; there is no internal `--max-turns` setting. The Codex adapter distinguishes an intact, request-bound `NOT_STARTED` checkpoint from a missing checkpoint. Only affirmative pre-start evidence permits a new native thread on the same task; established native threads are resumed and uncertain outcomes stay blocked. The supervisor runs only while its local command is active.

## Affirmative Claude startup recovery

A quiet process, missing output, timeout, cancellation or authentication wording alone never proves that a conversation can be replaced. The controller recognizes one observed structured pre-model failure: the expected session UUID, `is_error: true`, `terminal_reason: api_error`, exact result `Not logged in · Please run /login`, one turn, empty modelUsage and explicit zero input/output/cache token counters. On Claude Code 2.1.310 without a saved login the same envelope was observed for a new session and for a resumed one. It proves that this call did not reach the model, so the same call may be repeated as is, also in a conversation that already has turns. Whether the conversation itself may start anew is a separate question: only when every earlier attempt in that discussion is also affirmatively unstarted; otherwise the repeat resumes the same UUID. The CLI keeps each refused prompt and its `Not logged in` reply in the conversation transcript; whether a later resume shows them to the model was not checked. The probe had no login from the start, so the envelope after a conversation that already has a model reply is not verified; a different envelope keeps the repeat blocked. Request, stdout, stderr and process-result hashes form the retained proof; `classify_error` is not that proof.

After resolving the environment/authentication cause, the next explicit call can use `--session-id` with the same UUID. If Claude instead returns the exact existing-session error, the following explicit call uses `--resume` for that UUID. The installed CLI was verified to reject a collision without changing the existing transcript. Legacy exact resume-not-found output can likewise support recovery only after the same no-model history. A prior completed/model-output/uncertain run prevents replacement. Supervisor-proven spawn failure can also be retried. No automatic CLI/model call, fresh UUID, login change or budget reset is added. Other failure envelope shapes stay blocked until they are understood and tested.

## Helper environment and instructions

Verified on Claude Code 2.1.310 and codex-cli 0.164.0 with a new session and a resumed one, each with the whole environment and with only `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `TMPDIR` and `LANG`:

- The saved Claude Code login (subscription) and the saved `codex login` (ChatGPT) work with the reduced environment. These are the only sign-in methods Council supports.
- In print mode Claude uses `ANTHROPIC_API_KEY` from the environment even over the saved login (its `claude auth status` still reports `claude.ai`). This is why a helper of a task created from 1.5.0 gets no variable of the CLIs' own namespaces except the listed ones. The installed CLIs also read variables of other sign-in methods with no fixed list (for example `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_CODE_USE_MANTLE`, `OPENAI_FEDERATION_RULE_ID`), so the rule covers the whole namespace.
- A missing sign-in is reported by the installed CLIs as, for example, `Not logged in · Run /login` or `Not signed in. Please run 'codex login'`; `classify_error` treats these and other sign-in messages as `AUTH`.
- Claude loads the user and project `CLAUDE.md`; Codex loads the user and project `AGENTS.md`. On resume, a changed project file is read again.
- The Codex helper also starts the user's Codex hooks and MCP servers.

## Token usage

- Claude: the result's `usage` is that call; `modelUsage` is the session so far. In the probe (two sessions, one model, four counters), `modelUsage` after a resume equalled the earlier value plus the new `usage`. The controller keeps the reported `usage` as is and sums only the four counters.
- Codex: `thread/tokenUsage/updated` carries `total`, the thread's running total, and `last`, the latest update; in the probe it came before `turn/completed`. `totalTokens` is input plus output; `cachedInputTokens` is part of the input, reasoning part of the output. In the observed resume, the app-server first repeated the previous turn's event; that total is the start of the new run, not new usage. Without such an event the start is unknown.

## Compatibility

Python 3.10+, Git and a POSIX environment are required. The controller uses standard-library file locks, pipes and process groups.

After a Claude Code update, use `claude --help` to check supported flags if a call fails. Preserve the error and task state, then verify any adapter change before resuming. Do not treat a changed output format as a clean review.
