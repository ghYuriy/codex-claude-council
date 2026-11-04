#!/usr/bin/env node
// Council adapter: the Python controller owns budgets, cancellation and acceptance.
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { isDeepStrictEqual } from "node:util";
import { parseArgs } from "./lib/args.mjs";
import { runAppServerTurn } from "./lib/codex.mjs";

const { options } = parseArgs(process.argv.slice(2), {
  valueOptions: ["codex", "session-id", "session-file", "resume", "contract", "contract-sha256", "usage-file"]
});

function saveCheckpoint(file, record) {
  const temp = path.join(path.dirname(file), ".session-" + crypto.randomUUID());
  fs.writeFileSync(temp, JSON.stringify(record), { mode: 0o600 });
  fs.renameSync(temp, file);
}

// The app-server reports the thread's running token total. Before this run's turn starts, a resumed thread repeats
// the total it already had: that is where this run starts. A new thread starts from zero. Only totals are kept, so a
// repeated event cannot be counted twice; a total below the one kept marks the counter as gone back, and the
// controller then records the run as unknown. Nothing is taken before our own thread is known. The controller takes
// the difference; completed says the turn finished, so the last total is the whole run, not a lower bound.
const ZERO_TOTAL = { totalTokens: 0, inputTokens: 0, cachedInputTokens: 0, outputTokens: 0, reasoningOutputTokens: 0 };

function usageRecorder(file, resumed) {
  const usage = { thread_id: null, start: resumed ? null : ZERO_TOTAL, end: null, completed: false, went_back: false };
  let threadId = null;
  let turnId = null;
  const save = () => {
    const temp = path.join(path.dirname(file), ".usage-" + crypto.randomUUID());
    fs.writeFileSync(temp, JSON.stringify(usage), { mode: 0o600 });
    fs.renameSync(temp, file);
  };
  const below = (total, kept) => Boolean(kept) && Object.keys(ZERO_TOTAL).some((field) => total[field] < kept[field]);
  return {
    thread(id) {
      threadId = id;
      usage.thread_id = id;
    },
    notification(message) {
      const params = message?.params ?? {};
      if (!threadId || params.threadId !== threadId) return;
      if (message.method === "turn/started" && !turnId) {
        turnId = params.turn?.id ?? null;
      } else if (message.method === "turn/completed" && turnId && params.turn?.id === turnId) {
        usage.completed = params.turn.status === "completed";
        save();
      } else if (message.method === "thread/tokenUsage/updated" && params.tokenUsage?.total) {
        const total = params.tokenUsage.total;
        if (!turnId) {
          if (below(total, usage.start)) usage.went_back = true;
          usage.start = total;
        } else if (params.turnId === turnId) {
          if (below(total, usage.end ?? usage.start)) usage.went_back = true;
          usage.end = total;
        } else return;
        save();
      }
    }
  };
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
  const helperEnv = { ...process.env };
  process.env.COUNCIL_CODEX_EXECUTABLE = options.codex;
  const sessionFile = options["session-file"];
  let nativeId = options.resume ?? null;
  // The thread is ready only just before turn/start: until then this request has not reached a model turn.
  let threadReady = false;
  const usage = options["usage-file"] ? usageRecorder(options["usage-file"], Boolean(options.resume)) : null;
  if (nativeId) usage?.thread(nativeId);
  try {
    const result = await runAppServerTurn(process.cwd(), {
      disableBroker: true,
      // Codex gets the names the controller recorded (plus any the system adds to Node, such as
      // __CF_USER_TEXT_ENCODING on macOS), without the adapter's own binary setting.
      env: helperEnv,
      persistThread: true,
      resumeThreadId: nativeId,
      threadName: nativeId ? null : `Council ${request.phase}: ${request.task_id}`,
      sandbox: request.phase === "REPAIR" ? "workspace-write" : "read-only",
      developerInstructions: contract.critic,
      prompt,
      outputSchema: contract.output_schema,
      onNotification: usage ? (message) => usage.notification(message) : undefined,
      onProgress(event) {
        if (!event || typeof event !== "object" || !event.threadId) return;
        if (nativeId && nativeId !== event.threadId) throw new Error("Native session changed unexpectedly.");
        nativeId = event.threadId;
        threadReady = true;
        usage?.thread(nativeId);
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
    // runAppServerTurn reports the ready thread and we save its checkpoint before the prompt is sent, so a failure
    // before that proves this request never reached a model turn, in a new thread and in a resumed one alike.
    if (!threadReady) {
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
