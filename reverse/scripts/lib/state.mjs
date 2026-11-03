// Modified for Council: integrated paths, command namespace and host-specific collaboration rules.
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { resolveWorkspaceRoot } from "./workspace.mjs";

const STATE_VERSION = 1;
const PLUGIN_DATA_ENV = "CLAUDE_PLUGIN_DATA";
const FALLBACK_STATE_ROOT_DIR = path.join(os.tmpdir(), "codex-companion");
const STATE_FILE_NAME = "state.json";
const JOBS_DIR_NAME = "jobs";
const MAX_JOBS = 50;

function nowIso() {
  return new Date().toISOString();
}

function defaultState() {
  return {
    version: STATE_VERSION,
    config: {
      stopReviewGate: false
    },
    jobs: []
  };
}

export function resolveStateDir(cwd) {
  const workspaceRoot = resolveWorkspaceRoot(cwd);
  let canonicalWorkspaceRoot = workspaceRoot;
  try {
    canonicalWorkspaceRoot = fs.realpathSync.native(workspaceRoot);
  } catch {
    canonicalWorkspaceRoot = workspaceRoot;
  }

  const slugSource = path.basename(workspaceRoot) || "workspace";
  const slug = slugSource.replace(/[^a-zA-Z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "workspace";
  const hash = createHash("sha256").update(canonicalWorkspaceRoot).digest("hex").slice(0, 16);
  const pluginDataDir = process.env[PLUGIN_DATA_ENV];
  const stateRoot = pluginDataDir ? path.join(pluginDataDir, "state") : FALLBACK_STATE_ROOT_DIR;
  return path.join(stateRoot, `${slug}-${hash}`);
}

export function resolveStateFile(cwd) {
  return path.join(resolveStateDir(cwd), STATE_FILE_NAME);
}

export function resolveJobsDir(cwd) {
  return path.join(resolveStateDir(cwd), JOBS_DIR_NAME);
}

export function ensureStateDir(cwd) {
  fs.mkdirSync(resolveJobsDir(cwd), { recursive: true });
}

export function loadState(cwd) {
  const stateFile = resolveStateFile(cwd);
  if (!fs.existsSync(stateFile)) {
    return defaultState();
  }

  try {
    const parsed = JSON.parse(fs.readFileSync(stateFile, "utf8"));
    return {
      ...defaultState(),
      ...parsed,
      config: {
        ...defaultState().config,
        ...(parsed.config ?? {})
      },
      jobs: Array.isArray(parsed.jobs) ? parsed.jobs : []
    };
  } catch {
    return defaultState();
  }
}

function pruneJobs(jobs) {
  return [...jobs]
    .sort((left, right) => String(right.updatedAt ?? "").localeCompare(String(left.updatedAt ?? "")))
    .slice(0, MAX_JOBS);
}

function removeFileIfExists(filePath) {
  if (filePath && fs.existsSync(filePath)) {
    fs.unlinkSync(filePath);
  }
}

// Write to a separate file in the same directory and rename it into place: a writer killed midway leaves the old file
// intact, never a cut-off one. The copy is created in the file's own directory, so it takes the access control that
// directory passes on, as any file created there would. An existing file's copy is created readable by its owner only
// (0600) and gets the old file's mode and group before the rename. Access control entries added by hand to one
// particular file are not carried over. When a replacement could not keep the file as it is (a symlink, a hard link, a
// file owned by someone else, a group that cannot be kept, or a directory that does not allow new files), the file is
// written in place instead, as before 1.4.5.
function writeFileAtomic(filePath, text) {
  let current = null;
  try {
    current = fs.lstatSync(filePath);
  } catch {
    current = null;
  }
  if (current && (current.isSymbolicLink() || current.nlink > 1 || (process.getuid && current.uid !== process.getuid()))) {
    fs.writeFileSync(filePath, text, "utf8");
    return;
  }
  const temp = `${filePath}.${process.pid}.${Math.random().toString(36).slice(2)}.tmp`;
  let fd;
  try {
    fd = fs.openSync(temp, "wx", current ? 0o600 : 0o666);
  } catch (error) {
    if (current && ["EACCES", "EPERM", "EROFS"].includes(error.code)) {
      fs.writeFileSync(filePath, text, "utf8");
      return;
    }
    throw error;
  }
  let replace = true;
  try {
    fs.writeFileSync(fd, text, "utf8");
    if (current) {
      if (fs.fstatSync(fd).gid !== current.gid) {
        try {
          fs.fchownSync(fd, current.uid, current.gid);
        } catch {
          replace = false;
        }
      }
      if (replace) {
        fs.fchmodSync(fd, current.mode & 0o7777);
      }
    }
  } catch (error) {
    fs.closeSync(fd);
    removeFileIfExists(temp);
    throw error;
  }
  fs.closeSync(fd);
  if (!replace) {
    removeFileIfExists(temp);
    fs.writeFileSync(filePath, text, "utf8");
    return;
  }
  try {
    fs.renameSync(temp, filePath);
  } catch (error) {
    removeFileIfExists(temp);
    throw error;
  }
}

// Copies are named <file>.<pid>.<random>.tmp; one whose writer is gone was left by a writer killed midway.
const WRITE_COPY = /^(.+)\.(\d+)\.[a-z0-9]+\.tmp$/;

function writerIsGone(pid) {
  try {
    process.kill(pid, 0);
    return false;
  } catch (error) {
    return error.code === "ESRCH";
  }
}

export function removeOrphanWriteCopies(cwd) {
  for (const directory of [resolveStateDir(cwd), resolveJobsDir(cwd)]) {
    if (!fs.existsSync(directory)) {
      continue;
    }
    for (const name of fs.readdirSync(directory)) {
      const match = WRITE_COPY.exec(name);
      if (match && writerIsGone(Number(match[2]))) {
        removeFileIfExists(path.join(directory, name));
      }
    }
  }
}

// A state index that exists but cannot be read is never replaced: loadState reads it as empty, and saving that would
// silently turn the gate off and drop every other job.
function requireReadableIndex(cwd) {
  const stateFile = resolveStateFile(cwd);
  if (!fs.existsSync(stateFile)) {
    return;
  }
  try {
    JSON.parse(fs.readFileSync(stateFile, "utf8"));
  } catch {
    throw new Error(`The Codex job index is unreadable and was left as it is: ${stateFile}. Move it aside to start a new one.`);
  }
}

export function saveState(cwd, state) {
  requireReadableIndex(cwd);
  const previousJobs = loadState(cwd).jobs;
  ensureStateDir(cwd);
  const nextJobs = pruneJobs(state.jobs ?? []);
  const nextState = {
    version: STATE_VERSION,
    config: {
      ...defaultState().config,
      ...(state.config ?? {})
    },
    jobs: nextJobs
  };

  const retainedIds = new Set(nextJobs.map((job) => job.id));
  for (const job of previousJobs) {
    if (retainedIds.has(job.id)) {
      continue;
    }
    removeJobFile(resolveJobFile(cwd, job.id));
    removeFileIfExists(job.logFile);
  }

  writeFileAtomic(resolveStateFile(cwd), `${JSON.stringify(nextState, null, 2)}\n`);
  return nextState;
}

export function updateState(cwd, mutate) {
  const state = loadState(cwd);
  mutate(state);
  return saveState(cwd, state);
}

export function generateJobId(prefix = "job") {
  const random = Math.random().toString(36).slice(2, 8);
  return `${prefix}-${Date.now().toString(36)}-${random}`;
}

export function upsertJob(cwd, jobPatch) {
  return updateState(cwd, (state) => {
    const timestamp = nowIso();
    const existingIndex = state.jobs.findIndex((job) => job.id === jobPatch.id);
    if (existingIndex === -1) {
      state.jobs.unshift({
        createdAt: timestamp,
        updatedAt: timestamp,
        ...jobPatch
      });
      return;
    }
    state.jobs[existingIndex] = {
      ...state.jobs[existingIndex],
      ...jobPatch,
      updatedAt: timestamp
    };
  });
}

export function listJobs(cwd) {
  return loadState(cwd).jobs;
}

export function setConfig(cwd, key, value) {
  return updateState(cwd, (state) => {
    state.config = {
      ...state.config,
      [key]: value
    };
  });
}

export function getConfig(cwd) {
  return loadState(cwd).config;
}

export function writeJobFile(cwd, jobId, payload) {
  ensureStateDir(cwd);
  const jobFile = resolveJobFile(cwd, jobId);
  writeFileAtomic(jobFile, `${JSON.stringify(payload, null, 2)}\n`);
  return jobFile;
}

export function readJobFile(jobFile) {
  return JSON.parse(fs.readFileSync(jobFile, "utf8"));
}

function removeJobFile(jobFile) {
  if (fs.existsSync(jobFile)) {
    fs.unlinkSync(jobFile);
  }
  // A writer killed midway may have left a copy of the job beside it; it goes with the job.
  const prefix = `${path.basename(jobFile)}.`;
  const directory = path.dirname(jobFile);
  if (fs.existsSync(directory)) {
    for (const name of fs.readdirSync(directory)) {
      if (name.startsWith(prefix) && name.endsWith(".tmp")) {
        removeFileIfExists(path.join(directory, name));
      }
    }
  }
}

export function resolveJobLogFile(cwd, jobId) {
  ensureStateDir(cwd);
  return path.join(resolveJobsDir(cwd), `${jobId}.log`);
}

export function resolveJobFile(cwd, jobId) {
  ensureStateDir(cwd);
  return path.join(resolveJobsDir(cwd), `${jobId}.json`);
}
