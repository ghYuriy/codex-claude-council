#!/usr/bin/env node
// Council adapter: the Python controller owns budgets, cancellation and acceptance.
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { parseArgs } from "./lib/args.mjs";
import { runAppServerTurn } from "./lib/codex.mjs";

const { options } = parseArgs(process.argv.slice(2), {
  valueOptions: ["codex", "session-id", "session-file", "resume"]
});

async function main() {
  const request = JSON.parse(fs.readFileSync(0, "utf8"));
  if (!options.codex || !options["session-id"] || !options["session-file"]) {
    throw new Error("Council transport requires a binary, session identity and session checkpoint.");
  }
  process.env.COUNCIL_CODEX_EXECUTABLE = options.codex;
  const sessionFile = options["session-file"];
  let nativeId = options.resume ?? null;
  const result = await runAppServerTurn(process.cwd(), {
    disableBroker: true,
    persistThread: true,
    resumeThreadId: nativeId,
    threadName: nativeId ? null : `Council ${request.phase}: ${request.task_id}`,
    sandbox: request.phase === "REPAIR" ? "workspace-write" : "read-only",
    developerInstructions: request.critic,
    prompt: JSON.stringify(request),
    outputSchema: request.output_schema,
    onProgress(event) {
      if (!event || typeof event !== "object" || !event.threadId) return;
      if (nativeId && nativeId !== event.threadId) throw new Error("Native session changed unexpectedly.");
      nativeId = event.threadId;
      const temp = path.join(path.dirname(sessionFile), ".session-" + crypto.randomUUID());
      fs.writeFileSync(temp, JSON.stringify({ id: nativeId, task_id: request.task_id,
        session_id: options["session-id"] }), { mode: 0o600 });
      fs.renameSync(temp, sessionFile);
    }
  });
  if (result.permissionDenials?.length) throw new Error("Codex requested unavailable permission or user input; review is incomplete.");
  if (result.status !== 0 || result.error || !result.finalMessage?.trim() || !nativeId) {
    throw new Error(result.error?.message || result.stderr || "Incomplete Codex result.");
  }
  const report = JSON.parse(result.finalMessage);
  process.stdout.write(JSON.stringify({ type: "result", subtype: "success", is_error: false,
    session_id: options["session-id"], native_session_id: nativeId,
    permission_denials: [], structured_output: report }) + "\n");
}

main().catch(error => {
  process.stderr.write(String(error.message ?? error) + "\n");
  process.exitCode = 1;
});
