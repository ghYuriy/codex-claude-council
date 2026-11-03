#!/usr/bin/env node
// Modified for Council: integrated paths, command namespace and host-specific collaboration rules.

import fs from "node:fs";
import process from "node:process";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

import { getCodexAvailability } from "./lib/codex.mjs";
import { loadPromptTemplate, interpolateTemplate } from "./lib/prompts.mjs";
import { terminateProcessTree } from "./lib/process.mjs";
import { getConfig, listJobs, upsertJob, writeJobFile } from "./lib/state.mjs";
import { readStoredJob, sortJobsNewestFirst } from "./lib/job-control.mjs";
import { SESSION_ID_ENV } from "./lib/tracked-jobs.mjs";
import { resolveWorkspaceRoot } from "./lib/workspace.mjs";

// Counted from the hook start, the Codex preflight and the review, then cancelling and killing it, must end
// before the host's own 900-second hook timeout discards the result.
export const STOP_REVIEW_TIMEOUT_MS = 14 * 60 * 1000;
export const STOP_REVIEW_GRACE_MS = 5 * 1000;
const STOP_PREFLIGHT_TIMEOUT_MS = 20 * 1000;
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const ROOT_DIR = path.resolve(SCRIPT_DIR, "..");
const STOP_REVIEW_TASK_MARKER = "Run a stop-gate review of the previous Claude turn.";

function readHookInput() {
  const raw = fs.readFileSync(0, "utf8").trim();
  if (!raw) {
    return {};
  }
  return JSON.parse(raw);
}

function emitDecision(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function logNote(message) {
  if (!message) {
    return;
  }
  process.stderr.write(`${message}\n`);
}

function filterJobsForCurrentSession(jobs, input = {}) {
  const sessionId = input.session_id || process.env[SESSION_ID_ENV] || null;
  if (!sessionId) {
    return jobs;
  }
  return jobs.filter((job) => job.sessionId === sessionId);
}

function buildStopReviewPrompt(input = {}) {
  const lastAssistantMessage = String(input.last_assistant_message ?? "").trim();
  const template = loadPromptTemplate(ROOT_DIR, "stop-review-gate");
  const claudeResponseBlock = lastAssistantMessage
    ? ["Previous Claude response:", lastAssistantMessage].join("\n")
    : "";
  return interpolateTemplate(template, {
    CLAUDE_RESPONSE_BLOCK: claudeResponseBlock
  });
}

function buildSetupNote(cwd, timeoutMs) {
  const availability = getCodexAvailability(cwd, { timeoutMs });
  if (availability.available) {
    return null;
  }

  const detail = availability.detail ? ` ${availability.detail}.` : "";
  return `Codex is not set up for the review gate.${detail} Run /codex-claude-council:setup.`;
}

function parseStopReviewOutput(rawOutput) {
  const text = String(rawOutput ?? "").trim();
  if (!text) {
    return {
      ok: false,
      reason:
        "The stop-time Codex review task returned no final output. Run /codex-claude-council:review --wait manually or bypass the gate."
    };
  }

  const firstLine = text.split(/\r?\n/, 1)[0].trim();
  if (firstLine.startsWith("ALLOW:")) {
    return { ok: true, reason: null };
  }
  if (firstLine.startsWith("BLOCK:")) {
    const reason = firstLine.slice("BLOCK:".length).trim() || text;
    return {
      ok: false,
      reason: `Codex stop-time review found issues that still need fixes before ending the session: ${reason}`
    };
  }

  return {
    ok: false,
    reason:
      "The stop-time Codex review task returned an unexpected answer. Run /codex-claude-council:review --wait manually or bypass the gate."
  };
}

function cancelReviewJob(scriptPath, pid, cwd, env, timeoutMs) {
  // The Codex turn runs in the shared runtime, outside this process group; only its job's cancel interrupts it.
  let jobId = null;
  try {
    const job = listJobs(resolveWorkspaceRoot(cwd)).find(
      (item) => item.pid === pid && (item.status === "running" || item.status === "queued")
    );
    if (!job) {
      return { jobId, interrupted: false };
    }
    jobId = job.id;
    const result = spawnSync(process.execPath, [scriptPath, "cancel", job.id, "--json"], {
      cwd, env, timeout: timeoutMs, killSignal: "SIGKILL", encoding: "utf8"
    });
    return { jobId, interrupted: result.status === 0 && JSON.parse(result.stdout).turnInterrupted === true };
  } catch {
    // The process group is still killed; the turn may then finish on its own in the shared runtime.
    return { jobId, interrupted: false };
  }
}

export function recordUnconfirmedCancellation(cwd, jobId) {
  // The gate reports this outcome, so it records it too: the cancel command may have hung or been killed between
  // writing the job file and the index. Both are brought to the same state; the command's own detail is kept.
  try {
    const workspaceRoot = resolveWorkspaceRoot(cwd);
    let existing = null;
    try {
      existing = readStoredJob(workspaceRoot, jobId);
    } catch {
      existing = null; // A file cut off by the killed command is replaced below.
    }
    const detail = existing?.turnInterrupted === false ? existing.errorMessage : null;
    const patch = {
      status: "cancelled",
      phase: "cancelled",
      pid: null,
      completedAt: existing?.completedAt ?? new Date().toISOString(),
      turnInterrupted: false,
      errorMessage:
        detail ??
        "Stopped by the stop review gate after its timeout; the Codex turn interrupt could not be confirmed, so the turn may continue in the shared Codex runtime."
    };
    writeJobFile(workspaceRoot, jobId, { ...(existing ?? { id: jobId }), ...patch });
    upsertJob(workspaceRoot, { id: jobId, ...patch });
  } catch {
    // The block reason still states that the interruption was not confirmed.
  }
}

function runReviewProcess(scriptPath, prompt, cwd, env, timeoutMs, graceMs) {
  return new Promise((resolve) => {
    // A separate process group lets a timeout stop the review together with the Codex processes it started.
    const child = spawn(process.execPath, [scriptPath, "task", "--json", prompt], {
      cwd,
      env,
      detached: process.platform !== "win32",
      stdio: ["ignore", "pipe", "pipe"]
    });
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    let cancellation = null;
    let settled = false;
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ stdout, stderr, timedOut, cancellation, ...result });
    };
    const timer = setTimeout(() => {
      timedOut = true;
      cancellation = cancelReviewJob(scriptPath, child.pid, cwd, env, graceMs);
      terminateProcessTree(child.pid);
      // A descendant may ignore SIGTERM after the review itself exited, so the group is always killed.
      setTimeout(() => {
        for (const target of [-child.pid, child.pid]) {
          try { process.kill(target, "SIGKILL"); } catch { /* already gone */ }
        }
        // Written after the group is dead, so no process of the review can overwrite it.
        if (cancellation?.jobId && !cancellation.interrupted) {
          recordUnconfirmedCancellation(cwd, cancellation.jobId);
        }
        finish({ status: null, error: null });
      }, graceMs);
    }, timeoutMs);
    child.on("error", (error) => { if (!timedOut) finish({ status: null, error }); });
    child.on("close", (status) => { if (!timedOut) finish({ status, error: null }); });
  });
}

export async function runStopReview(cwd, input = {}, options = {}) {
  const scriptPath = options.scriptPath ?? path.join(SCRIPT_DIR, "codex-companion.mjs");
  const timeoutMs = options.timeoutMs ?? STOP_REVIEW_TIMEOUT_MS;
  const graceMs = options.graceMs ?? STOP_REVIEW_GRACE_MS;
  const prompt = buildStopReviewPrompt(input);
  const childEnv = {
    ...(options.env ?? process.env),
    ...(input.session_id ? { [SESSION_ID_ENV]: input.session_id } : {})
  };
  const result = await runReviewProcess(scriptPath, prompt, cwd, childEnv, timeoutMs, graceMs);

  if (result.timedOut) {
    const minutes = Math.round(timeoutMs / 60000);
    const { jobId, interrupted } = result.cancellation ?? {};
    // Only a confirmed interruption means the Codex turn stopped; its runtime is outside the killed process group.
    const turn = interrupted
      ? "Its Codex turn was interrupted and its processes were stopped."
      : `Its processes were stopped, but the interruption of the Codex turn could not be confirmed${jobId ? ` (job ${jobId})` : ""}; the turn may continue in the shared Codex runtime.`;
    return {
      ok: false,
      reason:
        `The stop-time Codex review task timed out after ${minutes ? `${minutes} minutes` : `${timeoutMs} ms`}. ${turn} Run /codex-claude-council:review --wait manually or bypass the gate.`
    };
  }

  if (result.error) {
    return {
      ok: false,
      reason: `The stop-time Codex review task could not start: ${result.error.message}`
    };
  }

  if (result.status !== 0) {
    const detail = String(result.stderr || result.stdout || "").trim();
    return {
      ok: false,
      reason: detail
        ? `The stop-time Codex review task failed: ${detail}`
        : "The stop-time Codex review task failed. Run /codex-claude-council:review --wait manually or bypass the gate."
    };
  }

  try {
    const payload = JSON.parse(result.stdout);
    return parseStopReviewOutput(payload?.rawOutput);
  } catch {
    return {
      ok: false,
      reason:
        "The stop-time Codex review task returned invalid JSON. Run /codex-claude-council:review --wait manually or bypass the gate."
    };
  }
}

// The project this stop belongs to, once the hook input names it; a handler failure is judged by its gate.
let stopCwd = null;

async function main() {
  const deadline = Date.now() + STOP_REVIEW_TIMEOUT_MS;
  const input = readHookInput();
  if (input.stop_hook_active === true) {
    return; // Do not restart the review that caused this continuation; this grants no task acceptance.
  }
  const cwd = input.cwd || process.env.CLAUDE_PROJECT_DIR || process.cwd();
  stopCwd = cwd;
  const workspaceRoot = resolveWorkspaceRoot(cwd);
  const config = getConfig(workspaceRoot);

  const jobs = sortJobsNewestFirst(filterJobsForCurrentSession(listJobs(workspaceRoot), input));
  const runningJob = jobs.find((job) => job.status === "queued" || job.status === "running");
  const runningTaskNote = runningJob
    ? `Codex task ${runningJob.id} is still running. Check /codex-claude-council:status and use /codex-claude-council:cancel ${runningJob.id} if you want to stop it before ending the session.`
    : null;

  if (!config.stopReviewGate) {
    logNote(runningTaskNote);
    return;
  }

  const setupNote = buildSetupNote(cwd, Math.min(STOP_PREFLIGHT_TIMEOUT_MS, deadline - Date.now()));
  if (setupNote) {
    // The gate was enabled deliberately; stop_hook_active above lets the next stop through, so this blocks only once.
    emitDecision({ decision: "block", reason: runningTaskNote ? `${runningTaskNote} ${setupNote}` : setupNote });
    return;
  }

  const review = await runStopReview(cwd, input, { timeoutMs: Math.max(1000, deadline - Date.now()) });
  if (!review.ok) {
    emitDecision({
      decision: "block",
      reason: runningTaskNote ? `${runningTaskNote} ${review.reason}` : review.reason
    });
    return;
  }

  logNote(runningTaskNote);
}

function gateEnabledForFailure() {
  try {
    const cwd = stopCwd || process.env.CLAUDE_PROJECT_DIR || process.cwd();
    return getConfig(resolveWorkspaceRoot(cwd)).stopReviewGate === true;
  } catch {
    return false;
  }
}

async function runHook() {
  try {
    await main();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    // An enabled gate must not silently pass because its own handler failed; a disabled gate keeps the old behaviour.
    if (gateEnabledForFailure()) {
      emitDecision({ decision: "block", reason: `The stop review gate failed before it could review the turn: ${message}` });
      return;
    }
    process.stderr.write(`${message}\n`);
    process.exitCode = 1;
  }
}

// Compare real paths: Node resolves a symlinked plugin path for import.meta.url but not for argv.
if (process.argv[1] && import.meta.url === pathToFileURL(fs.realpathSync(process.argv[1])).href) {
  await runHook();
}
