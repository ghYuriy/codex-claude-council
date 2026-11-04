import fs from "node:fs";
import path from "node:path";
import test, { after } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

import { buildEnv, installFakeCodex } from "./fake-codex-fixture.mjs";
import { createdTempDirs, initGitRepo, makeTempDir, run } from "./helpers.mjs";
import { loadBrokerSession } from "../scripts/lib/broker-lifecycle.mjs";
import { spawn, spawnSync } from "node:child_process";
import { ensureStateDir, listJobs, loadState, resolveJobFile, resolveStateFile, saveState, upsertJob, writeJobFile } from "../scripts/lib/state.mjs";
import { resolveWorkspaceRoot } from "../scripts/lib/workspace.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SCRIPT = path.join(ROOT, "scripts", "codex-companion.mjs");
const STOP_HOOK = path.join(ROOT, "scripts", "stop-review-gate-hook.mjs");
const SESSION_HOOK = path.join(ROOT, "scripts", "session-lifecycle-hook.mjs");

// A real review starts a shared broker per workspace; stop every one these tests created.
after(() => {
  for (const directory of createdTempDirs) {
    if (loadBrokerSession(directory)) {
      run(process.execPath, [SESSION_HOOK, "SessionEnd"], {
        cwd: directory, env: process.env, input: JSON.stringify({ hook_event_name: "SessionEnd", cwd: directory })
      });
      assert.equal(loadBrokerSession(directory), null, `Broker left running for ${directory}`);
    }
  }
});

function repoWithGate(enabled) {
  const repo = makeTempDir();
  initGitRepo(repo);
  fs.writeFileSync(path.join(repo, "README.md"), "hello\n");
  run("git", ["add", "README.md"], { cwd: repo });
  run("git", ["commit", "-m", "init"], { cwd: repo });
  const flag = enabled ? "--enable-review-gate" : "--disable-review-gate";
  const setup = run(process.execPath, [SCRIPT, "setup", flag, "--json"], { cwd: repo });
  assert.equal(setup.status, 0, setup.stderr);
  return repo;
}

function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

test("stop review timeout stops the review and a descendant that ignores SIGTERM", async () => {
  const { runStopReview } = await import(STOP_HOOK);
  const dir = makeTempDir();
  const pidFile = path.join(dir, "descendant.pid");
  const script = path.join(dir, "hanging-review.mjs");
  fs.writeFileSync(script, `
import { spawn } from "node:child_process";
import fs from "node:fs";
const child = spawn(process.execPath, ["-e", "process.on('SIGTERM', () => {}); setInterval(() => {}, 1000);"], { stdio: "ignore" });
fs.writeFileSync(${JSON.stringify(pidFile)}, String(child.pid));
process.on("SIGTERM", () => {});
setInterval(() => {}, 1000);
`);
  const started = Date.now();
  const review = await runStopReview(dir, {}, { scriptPath: script, timeoutMs: 1500, graceMs: 500 });
  assert.equal(review.ok, false);
  assert.match(review.reason, /timed out/);
  assert.ok(Date.now() - started < 10000, "the review must end soon after its own timeout");
  const descendant = Number(fs.readFileSync(pidFile, "utf8"));
  await new Promise((resolve) => setTimeout(resolve, 300));
  assert.equal(alive(descendant), false, "the hanging descendant must not survive the timeout");
});

test("stop review timeout stops a descendant that ignores SIGTERM after the review itself exits", async () => {
  const { runStopReview } = await import(STOP_HOOK);
  const dir = makeTempDir();
  const pidFile = path.join(dir, "descendant.pid");
  const script = path.join(dir, "exiting-review.mjs");
  fs.writeFileSync(script, `
import { spawn } from "node:child_process";
import fs from "node:fs";
const child = spawn(process.execPath, ["-e", "process.on('SIGTERM', () => {}); setInterval(() => {}, 1000);"], { stdio: "ignore" });
fs.writeFileSync(${JSON.stringify(pidFile)}, String(child.pid));
setInterval(() => {}, 1000);
`);
  const review = await runStopReview(dir, {}, { scriptPath: script, timeoutMs: 1000, graceMs: 500 });
  assert.match(review.reason, /timed out/);
  const descendant = Number(fs.readFileSync(pidFile, "utf8"));
  await new Promise((resolve) => setTimeout(resolve, 300));
  assert.equal(alive(descendant), false, "the descendant must not survive its exited parent");
});

test("stop review timeout interrupts the running Codex turn and closes its job", async () => {
  const { runStopReview } = await import(STOP_HOOK);
  const repo = repoWithGate(true);
  const bin = makeTempDir();
  installFakeCodex(bin, "interruptible-slow-task");
  const review = await runStopReview(repo, {}, { timeoutMs: 3500, graceMs: 2000, env: buildEnv(bin) });
  assert.match(review.reason, /timed out/);
  const state = JSON.parse(fs.readFileSync(path.join(bin, "fake-codex-state.json"), "utf8"));
  assert.ok(state.lastInterrupt?.turnId, "the turn must be interrupted, not left running in the shared runtime");
  assert.match(review.reason, /Codex turn was interrupted/);
  const jobs = listJobs(repo);
  assert.ok(jobs.length > 0);
  for (const job of jobs) assert.notEqual(job.status, "running", `job ${job.id} was left running`);
});

test("stop review timeout does not claim the Codex turn stopped when its interruption fails", async () => {
  const { runStopReview } = await import(STOP_HOOK);
  const repo = repoWithGate(true);
  const bin = makeTempDir();
  installFakeCodex(bin, "uninterruptible-slow-task");
  const review = await runStopReview(repo, {}, { timeoutMs: 3500, graceMs: 2000, env: buildEnv(bin) });
  assert.match(review.reason, /timed out/);
  assert.doesNotMatch(review.reason, /Codex turn was interrupted/);
  assert.match(review.reason, /could not be confirmed/);
  assert.match(review.reason, /may continue in the shared Codex runtime/);
  assert.doesNotMatch(review.reason, /session ends/, "ending the session is not proven to stop a stuck turn");
  assert.doesNotMatch(review.reason, /cancel it/, "a cancelled job cannot be cancelled again");
  const jobs = listJobs(repo);
  assert.ok(jobs.length > 0);
  for (const job of jobs) {
    assert.match(job.errorMessage ?? "", /turn interrupt failed/, "the job record must not claim a clean cancellation");
  }
});

test("a timed-out Codex availability check also stops the processes the CLI started", async () => {
  const { getCodexAvailability } = await import("../scripts/lib/codex.mjs");
  const bin = makeTempDir();
  const pidFile = path.join(bin, "child.pid");
  const hanging = path.join(bin, "codex");
  fs.writeFileSync(hanging, `#!/bin/sh\nsleep 30 &\necho $! > ${pidFile}\nwait\n`, { mode: 0o755 });
  const saved = process.env.COUNCIL_CODEX_EXECUTABLE;
  process.env.COUNCIL_CODEX_EXECUTABLE = hanging;
  try {
    // Long enough for the shell to start its child and record it even on a busy machine.
    assert.equal(getCodexAvailability(bin, { timeoutMs: 2000 }).available, false);
    await new Promise((resolve) => setTimeout(resolve, 200));
    assert.ok(fs.existsSync(pidFile), "the hanging CLI must have started its child before the timeout");
    assert.equal(alive(Number(fs.readFileSync(pidFile, "utf8"))), false, "the CLI's own child must not survive");
  } finally {
    if (saved === undefined) delete process.env.COUNCIL_CODEX_EXECUTABLE;
    else process.env.COUNCIL_CODEX_EXECUTABLE = saved;
  }
});

test("Codex availability check gives up on a CLI that hangs", async () => {
  const { getCodexAvailability } = await import("../scripts/lib/codex.mjs");
  const bin = makeTempDir();
  const hanging = path.join(bin, "codex");
  fs.writeFileSync(hanging, "#!/bin/sh\nexec sleep 30\n", { mode: 0o755 });
  const saved = process.env.COUNCIL_CODEX_EXECUTABLE;
  process.env.COUNCIL_CODEX_EXECUTABLE = hanging;
  try {
    const started = Date.now();
    const result = getCodexAvailability(bin, { timeoutMs: 500 });
    assert.equal(result.available, false);
    assert.match(result.detail, /timed out/);
    assert.ok(Date.now() - started < 5000);
  } finally {
    if (saved === undefined) delete process.env.COUNCIL_CODEX_EXECUTABLE;
    else process.env.COUNCIL_CODEX_EXECUTABLE = saved;
  }
});

test("stop review timeout records the unconfirmed interruption even when the cancel itself hangs", async () => {
  const { runStopReview } = await import(STOP_HOOK);
  const repo = repoWithGate(true);
  const bin = makeTempDir();
  installFakeCodex(bin, "unanswered-interrupt-slow-task");
  const review = await runStopReview(repo, {}, { timeoutMs: 3500, graceMs: 1500, env: buildEnv(bin) });
  assert.match(review.reason, /could not be confirmed/);
  const state = JSON.parse(fs.readFileSync(path.join(bin, "fake-codex-state.json"), "utf8"));
  assert.ok(state.lastInterrupt?.turnId, "the cancel must have reached the turn and then hung");
  const jobs = listJobs(repo);
  assert.ok(jobs.length > 0);
  for (const job of jobs) {
    assert.notEqual(job.status, "running", `job ${job.id} was left running`);
    assert.equal(job.turnInterrupted, false);
    assert.match(job.errorMessage ?? "", /may continue in the shared Codex runtime/);
  }
});

function runningJobWithFile(repo, fileRecord) {
  initGitRepo(repo);
  const root = resolveWorkspaceRoot(repo);
  const now = new Date().toISOString();
  const job = { id: "task-half-written", kind: "task", jobClass: "task", title: "Half written", status: "running",
    phase: "running", pid: null, threadId: "thr_half", turnId: "turn_half", workspaceRoot: root, createdAt: now };
  upsertJob(root, job);
  writeJobFile(root, job.id, job);
  fs.writeFileSync(path.join(path.dirname(resolveStateFile(repo)), "jobs", `${job.id}.json`),
    typeof fileRecord === "string" ? fileRecord : JSON.stringify({ ...job, ...fileRecord }));
  return job.id;
}

test("the gate reconciles the index when cancel was killed between its file and index writes", async () => {
  const { recordUnconfirmedCancellation } = await import(STOP_HOOK);
  const repo = makeTempDir();
  const id = runningJobWithFile(repo, { status: "cancelled", turnInterrupted: false,
    errorMessage: "Cancelled by user; the Codex turn interrupt failed (detail), so the turn may continue in the shared Codex runtime." });
  recordUnconfirmedCancellation(repo, id);
  const job = listJobs(repo).find((item) => item.id === id);
  assert.equal(job.status, "cancelled");
  assert.equal(job.turnInterrupted, false);
  assert.match(job.errorMessage, /\(detail\)/, "the cancel command's own detail is kept");
});

test("the gate records the unconfirmed interruption even when the job file cannot be read", async () => {
  const { recordUnconfirmedCancellation } = await import(STOP_HOOK);
  const repo = makeTempDir();
  const id = runningJobWithFile(repo, '{"status": "cancel');
  recordUnconfirmedCancellation(repo, id);
  const job = listJobs(repo).find((item) => item.id === id);
  assert.equal(job.status, "cancelled");
  assert.match(job.errorMessage, /may continue in the shared Codex runtime/);
});

test("the shared state index and job files are replaced whole, so a killed writer cannot leave them cut off", () => {
  const repo = makeTempDir();
  initGitRepo(repo);
  const root = resolveWorkspaceRoot(repo);
  const job = { id: "task-atomic", kind: "task", status: "running", workspaceRoot: root, createdAt: new Date().toISOString() };
  upsertJob(root, job);
  writeJobFile(root, job.id, job);
  const stateFile = resolveStateFile(repo);
  const jobFile = path.join(path.dirname(stateFile), "jobs", `${job.id}.json`);
  const before = [fs.statSync(stateFile).ino, fs.statSync(jobFile).ino];
  upsertJob(root, { id: job.id, status: "cancelled" });
  writeJobFile(root, job.id, { ...job, status: "cancelled" });
  // A new inode means the content was written to a separate file and renamed into place.
  assert.notEqual(fs.statSync(stateFile).ino, before[0]);
  assert.notEqual(fs.statSync(jobFile).ino, before[1]);
  assert.equal(listJobs(repo).find((item) => item.id === job.id).status, "cancelled");
});

test("replacing state and job files keeps their access permissions", () => {
  const repo = makeTempDir();
  initGitRepo(repo);
  const root = resolveWorkspaceRoot(repo);
  const job = { id: "task-private", kind: "task", status: "running", workspaceRoot: root, createdAt: new Date().toISOString() };
  upsertJob(root, job);
  writeJobFile(root, job.id, job);
  const files = [resolveStateFile(repo), resolveJobFile(root, job.id)];
  for (const file of files) fs.chmodSync(file, 0o600);
  const saved = process.umask(0o022);
  try {
    upsertJob(root, { id: job.id, status: "cancelled" });
    writeJobFile(root, job.id, { ...job, status: "cancelled" });
  } finally {
    process.umask(saved);
  }
  for (const file of files) assert.equal(fs.statSync(file).mode & 0o777, 0o600, file);
});

// Platform tools to set, pass on from a directory and read one access control entry that denies another user reading.
const ACL_TOOLS = process.platform === "darwin"
  ? { set: (file) => ["/bin/chmod", ["+a", "user:nobody deny read", file]], get: (file) => ["/bin/ls", ["-le", file]],
      inherit: (directory) => ["/bin/chmod", ["+a", "user:nobody deny read,file_inherit", directory]],
      entry: /user:nobody (inherited )?deny read/ }
  : { set: (file) => ["setfacl", ["-m", "u:nobody:---", file]], get: (file) => ["getfacl", ["-c", file]],
      inherit: (directory) => ["setfacl", ["-d", "-m", "u:nobody:---", directory]],
      entry: /user:nobody:---/ };
const ACL_SUPPORTED = (() => {
  const probe = path.join(makeTempDir(), "probe");
  fs.writeFileSync(probe, "probe\n");
  const [command, args] = ACL_TOOLS.set(probe);
  return spawnSync(command, args, { stdio: "ignore" }).status === 0;
})();

test("new and replaced state and job files keep the access control their directory passes on",
  { skip: !ACL_SUPPORTED && "no ACL tools here" }, () => {
  const repo = makeTempDir();
  initGitRepo(repo);
  const root = resolveWorkspaceRoot(repo);
  const job = { id: "task-acl", kind: "task", status: "running", workspaceRoot: root, createdAt: new Date().toISOString() };
  ensureStateDir(root);
  const files = [resolveStateFile(repo), resolveJobFile(root, job.id)];
  for (const directory of new Set(files.map((file) => path.dirname(file)))) {
    const [command, args] = ACL_TOOLS.inherit(directory);
    assert.equal(spawnSync(command, args).status, 0);
  }
  upsertJob(root, job);
  writeJobFile(root, job.id, job);
  upsertJob(root, { id: job.id, status: "cancelled" });
  writeJobFile(root, job.id, { ...job, status: "cancelled" });
  for (const file of files) {
    const [command, args] = ACL_TOOLS.get(file);
    assert.match(spawnSync(command, args, { encoding: "utf8" }).stdout, ACL_TOOLS.entry, file);
  }
  assert.equal(listJobs(repo).find((item) => item.id === job.id).status, "cancelled");
});

test("removing a job also removes a copy left by a writer killed midway", () => {
  const repo = makeTempDir();
  initGitRepo(repo);
  const root = resolveWorkspaceRoot(repo);
  const job = { id: "task-leftover", kind: "task", status: "completed", workspaceRoot: root, createdAt: new Date().toISOString() };
  upsertJob(root, job);
  writeJobFile(root, job.id, job);
  const jobFile = resolveJobFile(root, job.id);
  const leftover = `${jobFile}.99999.abc.tmp`;
  fs.writeFileSync(leftover, "{\"prompt\": \"private");
  saveState(root, { ...loadState(root), jobs: [] });
  assert.equal(fs.existsSync(jobFile), false);
  assert.equal(fs.existsSync(leftover), false, "a cut-off copy of the job must go with the job");
});

test("a write leaves no copy behind", () => {
  const repo = makeTempDir();
  initGitRepo(repo);
  const root = resolveWorkspaceRoot(repo);
  const job = { id: "task-clean", kind: "task", status: "running", workspaceRoot: root, createdAt: new Date().toISOString() };
  upsertJob(root, job);
  writeJobFile(root, job.id, job);
  upsertJob(root, { id: job.id, status: "cancelled" });
  writeJobFile(root, job.id, { ...job, status: "cancelled" });
  for (const directory of [path.dirname(resolveStateFile(repo)), path.dirname(resolveJobFile(root, job.id))]) {
    assert.deepEqual(fs.readdirSync(directory).filter((name) => name.startsWith(".") || name.endsWith(".tmp")), [], directory);
  }
});

test("a hard-linked job file is written in place, so every link sees the new content", () => {
  const repo = makeTempDir();
  initGitRepo(repo);
  const root = resolveWorkspaceRoot(repo);
  const job = { id: "task-linked", kind: "task", status: "running", workspaceRoot: root, createdAt: new Date().toISOString() };
  writeJobFile(root, job.id, job);
  const jobFile = resolveJobFile(root, job.id);
  const link = path.join(makeTempDir(), "linked.json");
  fs.linkSync(jobFile, link);
  writeJobFile(root, job.id, { ...job, status: "cancelled" });
  assert.equal(JSON.parse(fs.readFileSync(link, "utf8")).status, "cancelled");
  assert.equal(fs.statSync(jobFile).ino, fs.statSync(link).ino);
});

test("a symlinked job file stays a link and its target gets the new content", () => {
  const repo = makeTempDir();
  initGitRepo(repo);
  const root = resolveWorkspaceRoot(repo);
  const job = { id: "task-symlink", kind: "task", status: "running", workspaceRoot: root, createdAt: new Date().toISOString() };
  writeJobFile(root, job.id, job);
  const jobFile = resolveJobFile(root, job.id);
  const target = path.join(makeTempDir(), "target.json");
  fs.renameSync(jobFile, target);
  fs.symlinkSync(target, jobFile);
  writeJobFile(root, job.id, { ...job, status: "cancelled" });
  assert.equal(fs.lstatSync(jobFile).isSymbolicLink(), true);
  assert.equal(JSON.parse(fs.readFileSync(target, "utf8")).status, "cancelled");
});

test("a job file in a directory that forbids new files is still written in place",
  { skip: process.getuid?.() === 0 && "root ignores directory permissions" }, () => {
  const repo = makeTempDir();
  initGitRepo(repo);
  const root = resolveWorkspaceRoot(repo);
  const job = { id: "task-locked-dir", kind: "task", status: "running", workspaceRoot: root, createdAt: new Date().toISOString() };
  writeJobFile(root, job.id, job);
  const jobFile = resolveJobFile(root, job.id);
  const directory = path.dirname(jobFile);
  fs.chmodSync(directory, 0o500);
  try {
    writeJobFile(root, job.id, { ...job, status: "cancelled" });
  } finally {
    fs.chmodSync(directory, 0o700);
  }
  assert.equal(JSON.parse(fs.readFileSync(jobFile, "utf8")).status, "cancelled");
});

test("an unreadable state index is never overwritten with an empty one", () => {
  const repo = makeTempDir();
  initGitRepo(repo);
  const root = resolveWorkspaceRoot(repo);
  const job = { id: "task-torn", kind: "task", status: "running", workspaceRoot: root, createdAt: new Date().toISOString() };
  upsertJob(root, job);
  const stateFile = resolveStateFile(repo);
  const torn = '{"version": 1, "config": {"stopReviewGate": true}, "jobs": [{"id": "task-to';
  fs.writeFileSync(stateFile, torn);
  assert.throws(() => upsertJob(root, { id: job.id, status: "cancelled" }), /unreadable/);
  assert.equal(fs.readFileSync(stateFile, "utf8"), torn);
});

test("a replaced job file keeps its group", (t) => {
  const repo = makeTempDir();
  initGitRepo(repo);
  const root = resolveWorkspaceRoot(repo);
  const job = { id: "task-group", kind: "task", status: "running", workspaceRoot: root, createdAt: new Date().toISOString() };
  writeJobFile(root, job.id, job);
  const jobFile = resolveJobFile(root, job.id);
  const other = (process.getgroups?.() ?? []).find((gid) => {
    if (gid === fs.statSync(jobFile).gid) return false;
    try { fs.chownSync(jobFile, process.getuid(), gid); return true; } catch { return false; }
  });
  if (other === undefined) {
    t.skip("no second group to give the file");
    return;
  }
  writeJobFile(root, job.id, { ...job, status: "cancelled" });
  assert.equal(fs.statSync(jobFile).gid, other);
});

test("session end removes copies left by writers that died, including jobs never indexed", () => {
  const repo = makeTempDir();
  initGitRepo(repo);
  const root = resolveWorkspaceRoot(repo);
  const job = { id: "task-session", kind: "task", status: "completed", sessionId: "session-orphans", workspaceRoot: root, createdAt: new Date().toISOString() };
  upsertJob(root, job);
  const deadPid = spawnSync(process.execPath, ["-e", ""]).pid;
  const stateFile = resolveStateFile(repo);
  const orphanJob = `${resolveJobFile(root, "task-never-indexed")}.${deadPid}.abc.tmp`;
  const orphanIndex = `${stateFile}.${deadPid}.def.tmp`;
  const liveCopy = `${resolveJobFile(root, "task-live")}.${process.pid}.ghi.tmp`;
  for (const file of [orphanJob, orphanIndex, liveCopy]) fs.writeFileSync(file, "{\"prompt\": \"private");
  const ended = run(process.execPath, [SESSION_HOOK, "SessionEnd"], {
    cwd: repo, env: process.env, input: JSON.stringify({ hook_event_name: "SessionEnd", cwd: repo, session_id: "session-orphans" })
  });
  assert.equal(ended.status, 0, ended.stderr);
  assert.equal(fs.existsSync(orphanJob), false);
  assert.equal(fs.existsSync(orphanIndex), false);
  assert.equal(fs.existsSync(liveCopy), true, "a copy of a writer that is still running stays");
});

test("a cancel that cannot interrupt a known Codex turn says so in the job and in plain output", () => {
  const repo = makeTempDir();
  initGitRepo(repo);
  const root = resolveWorkspaceRoot(repo);
  const worker = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { detached: true, stdio: "ignore" });
  const now = new Date().toISOString();
  const job = { id: "task-stuck-turn", kind: "task", jobClass: "task", title: "Stuck turn", status: "running",
    phase: "running", pid: worker.pid, threadId: "thr_stuck", turnId: "turn_stuck", workspaceRoot: root,
    createdAt: now, startedAt: now, logFile: null };
  writeJobFile(root, job.id, job);
  upsertJob(root, job);
  // An unavailable Codex CLI means the interrupt cannot even be attempted, while the turn is known.
  const env = { ...process.env, COUNCIL_CODEX_EXECUTABLE: path.join(repo, "missing-codex") };
  const cancelled = run(process.execPath, [SCRIPT, "cancel", job.id], { cwd: repo, env });
  assert.equal(cancelled.status, 0, cancelled.stderr);
  assert.match(cancelled.stdout, /Warning: .*turn interrupt failed/);
  const status = run(process.execPath, [SCRIPT, "status", job.id], { cwd: repo, env });
  assert.equal(status.status, 0, status.stderr);
  assert.match(status.stdout, /Warning: .*turn interrupt failed/);
  try { process.kill(-worker.pid, "SIGKILL"); } catch { /* cancel already stopped it */ }
});

function breakJobList(repo) {
  const file = resolveStateFile(repo);
  const state = JSON.parse(fs.readFileSync(file, "utf8"));
  fs.writeFileSync(file, JSON.stringify({ ...state, jobs: [null] }));
}

test("a handler failure is judged by the gate of the project the stop belongs to", () => {
  const enabled = repoWithGate(true);
  const other = repoWithGate(false);
  breakJobList(enabled);
  const failed = run(process.execPath, [STOP_HOOK], { cwd: other, env: { ...process.env, CLAUDE_PROJECT_DIR: other },
    input: JSON.stringify({ cwd: enabled }) });
  assert.equal(failed.status, 0, failed.stderr);
  assert.equal(JSON.parse(failed.stdout).decision, "block");
});

test("a handler failure in a project with a disabled gate is not blocked by another project's gate", () => {
  const disabled = repoWithGate(false);
  const other = repoWithGate(true);
  breakJobList(disabled);
  const failed = run(process.execPath, [STOP_HOOK], { cwd: other, env: { ...process.env, CLAUDE_PROJECT_DIR: other },
    input: JSON.stringify({ cwd: disabled }) });
  assert.equal(failed.stdout.trim(), "");
  assert.notEqual(failed.status, 2);
});

test("stop review timeout leaves a margin below the hook timeout", async () => {
  const { STOP_REVIEW_TIMEOUT_MS, STOP_REVIEW_GRACE_MS } = await import(STOP_HOOK);
  const hooks = JSON.parse(fs.readFileSync(path.join(ROOT, "hooks", "hooks.json"), "utf8"));
  const hookSeconds = hooks.hooks.Stop[0].hooks[0].timeout;
  // The timeout counts from the hook start and covers the Codex preflight; cancelling and killing each take one grace period.
  assert.ok(STOP_REVIEW_TIMEOUT_MS + 2 * STOP_REVIEW_GRACE_MS + 30000 <= hookSeconds * 1000);
});

test("an enabled gate blocks when its own handler fails", () => {
  const repo = repoWithGate(true);
  const failed = run(process.execPath, [STOP_HOOK], { cwd: repo, env: { ...process.env, CLAUDE_PROJECT_DIR: repo },
    input: "not json" });
  assert.equal(failed.status, 0, failed.stderr);
  const payload = JSON.parse(failed.stdout);
  assert.equal(payload.decision, "block");
  assert.match(payload.reason, /stop review gate failed/i);
});

test("a disabled gate keeps letting the stop proceed when its handler fails", () => {
  const repo = repoWithGate(false);
  const failed = run(process.execPath, [STOP_HOOK], { cwd: repo, env: { ...process.env, CLAUDE_PROJECT_DIR: repo },
    input: "not json" });
  assert.equal(failed.stdout.trim(), "");
  assert.notEqual(failed.status, 2);
});

test("an enabled gate without Codex blocks once with setup instructions", () => {
  const repo = repoWithGate(true);
  const env = { ...process.env, PATH: "" };
  const blocked = run(process.execPath, [STOP_HOOK], { cwd: repo, env, input: JSON.stringify({ cwd: repo }) });
  assert.equal(blocked.status, 0, blocked.stderr);
  const payload = JSON.parse(blocked.stdout);
  assert.equal(payload.decision, "block");
  assert.match(payload.reason, /Codex is not set up for the review gate/);
  assert.match(payload.reason, /\/codex-claude-council:setup/);
  const repeated = run(process.execPath, [STOP_HOOK], { cwd: repo, env,
    input: JSON.stringify({ cwd: repo, stop_hook_active: true }) });
  assert.equal(repeated.status, 0, repeated.stderr);
  assert.equal(repeated.stdout.trim(), "");
});

test("the hook still runs when the plugin path is a symlink", () => {
  const repo = repoWithGate(true);
  const linkDir = makeTempDir();
  const link = path.join(linkDir, "plugin");
  fs.symlinkSync(ROOT, link);
  const blocked = run(process.execPath, [path.join(link, "scripts", "stop-review-gate-hook.mjs")], {
    cwd: repo, env: { ...process.env, PATH: "" }, input: JSON.stringify({ cwd: repo }) });
  assert.equal(blocked.status, 0, blocked.stderr);
  assert.equal(JSON.parse(blocked.stdout).decision, "block");
});
