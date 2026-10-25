# Claude Code transport

The controller uses the installed `claude` executable and its existing authentication. It sends prompts through stdin with `claude -p` and reads JSON output. A new discussion uses `--session-id`; later calls use `--resume` with that same session ID.

## Reviewer access

The reviewer receives only `Read`, `Grep` and `Glob`. `--disable-slash-commands` suppresses nested skills. `--strict-mcp-config --mcp-config '{"mcpServers":{}}'` disables MCP for the child process without changing global configuration. These restrictions do not isolate the filesystem.

The controller adds the reviewer instructions and JSON schema to each call. It validates the process result, session ID, response structure and evidence references before accepting a report. Partial or unparsed output cannot count as a successful review.

## Runtime settings

The controller does not override the model, change credentials, install a separate API client or start another Codex. It preserves the installed CLI's model selection and saved sessions. Each call has a finite timeout within the task's shared budget; there is no internal `--max-turns` setting. The supervisor runs only while its local command is active.

## Compatibility

Python 3.10+, Git and a POSIX environment are required. The controller uses standard-library file locks, pipes and process groups.

After a Claude Code update, use `claude --help` to check supported flags if a call fails. Preserve the error and task state, then verify any adapter change before resuming. Do not treat a changed output format as a clean review.
