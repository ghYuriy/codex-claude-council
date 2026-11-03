#!/usr/bin/env node
// Council adapter: the Python controller owns budgets, cancellation and acceptance.
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { isDeepStrictEqual } from "node:util";
import { parseArgs } from "./lib/args.mjs";
import { runAppServerTurn } from "./lib/codex.mjs";

const { options } = parseArgs(process.argv.slice(2), {
  valueOptions: ["codex", "session-id", "session-file", "resume", "contract", "contract-sha256"]
});

function saveCheckpoint(file, record) {
  const temp = path.join(path.dirname(file), ".session-" + crypto.randomUUID());
  fs.writeFileSync(temp, JSON.stringify(record), { mode: 0o600 });
  fs.renameSync(temp, file);
}

async function main() {
  // stdin is the exact model prompt; the instruction and schema come from the saved request through their own channel.
  const prompt = fs.readFileSync(0, "utf8");
  const request = JSON.parse(prompt);
  if (!options.codex || !options["session-id"] || !options["session-file"] || !options.contract || !options["contract-sha256"]) {
    throw new Error("Council transport requires a binary, session identity, session checkpoint and saved contract with its hash.");
  }
  // Hash and parse the same bytes: the instruction and schema are not compared with the prompt, only with this hash.
  const contractBytes = fs.readFileSync(options.contract);
  if (crypto.createHash("sha256").update(contractBytes).digest("hex") !== options["contract-sha256"]) {
    throw new Error("The Council contract changed after the controller saved it.");
  }
  const contract = JSON.parse(contractBytes.toString("utf8"));
  if (contract.task_id !== request.task_id || contract.request_id !== request.request_id) {
    throw new Error("The saved Council contract belongs to another request.");
  }
  if (typeof contract.critic !== "string" || !contract.critic.trim() || !contract.output_schema) {
    throw new Error("The saved Council contract has no instruction or output schema.");
  }
  if ("critic" in request || "output_schema" in request) {
    throw new Error("The model prompt must not repeat the instruction or output schema.");
  }
  // Every other field, including the phase that selects the sandbox, must be exactly what the controller saved.
  const { critic: _critic, output_schema: _schema, ...saved } = contract;
  if (!isDeepStrictEqual(saved, request)) {
    throw new Error("The model prompt does not match the saved request.");
  }
  process.env.COUNCIL_CODEX_EXECUTABLE = options.codex;
  const sessionFile = options["session-file"];
  let nativeId = options.resume ?? null;
  try {
    const result = await runAppServerTurn(process.cwd(), {
      disableBroker: true,
      persistThread: true,
      resumeThreadId: nativeId,
      threadName: nativeId ? null : `Council ${request.phase}: ${request.task_id}`,
      sandbox: request.phase === "REPAIR" ? "workspace-write" : "read-only",
      developerInstructions: contract.critic,
      prompt,
      outputSchema: contract.output_schema,
      onProgress(event) {
        if (!event || typeof event !== "object" || !event.threadId) return;
        if (nativeId && nativeId !== event.threadId) throw new Error("Native session changed unexpectedly.");
        nativeId = event.threadId;
        saveCheckpoint(sessionFile, { id: nativeId, task_id: request.task_id,
          session_id: options["session-id"] });
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
  } catch (error) {
    // runAppServerTurn reports the thread and saves its checkpoint before sending the prompt.
    // No known thread on a fresh launch proves this request never reached a model turn.
    if (!nativeId && !options.resume) {
      saveCheckpoint(sessionFile, { status: "NOT_STARTED", task_id: request.task_id,
        session_id: options["session-id"], request_id: request.request_id });
    }
    throw error;
  }
}

main().catch(error => {
  process.stderr.write(String(error.message ?? error) + "\n");
  process.exitCode = 1;
});
