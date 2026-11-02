// Modified for Council: integrated paths, command namespace and host-specific collaboration rules.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";

// Tests run in isolation from any installed copy of this plugin, whose hooks export these variables.
export const HOST_SESSION_VARIABLES = /^(CLAUDE_PLUGIN_DATA|CLAUDE_PLUGIN_ROOT|CLAUDE_ENV_FILE|CLAUDE_PROJECT_DIR|CODEX_COMPANION_.*)$/;
for (const key of Object.keys(process.env)) {
  if (HOST_SESSION_VARIABLES.test(key)) {
    delete process.env[key];
  }
}

export const createdTempDirs = [];

export function makeTempDir(prefix = "codex-plugin-test-") {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  createdTempDirs.push(directory);
  return directory;
}

export function writeExecutable(filePath, source) {
  fs.writeFileSync(filePath, source, { encoding: "utf8", mode: 0o755 });
}

export function run(command, args, options = {}) {
  return spawnSync(command, args, {
    cwd: options.cwd,
    env: options.env,
    encoding: "utf8",
    input: options.input,
    timeout: options.timeout,
    shell: options.shell ?? (process.platform === "win32" && !path.isAbsolute(command)),
    windowsHide: true
  });
}

export function initGitRepo(cwd) {
  run("git", ["init", "-b", "main"], { cwd });
  run("git", ["config", "user.name", "Codex Plugin Tests"], { cwd });
  run("git", ["config", "user.email", "tests@example.com"], { cwd });
  run("git", ["config", "commit.gpgsign", "false"], { cwd });
  run("git", ["config", "tag.gpgsign", "false"], { cwd });
}
