import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { installFakeCodex, buildEnv } from "./fake-codex-fixture.mjs";
import { makeTempDir, initGitRepo, run } from "./helpers.mjs";

const root = fileURLToPath(new URL("../../", import.meta.url));
const runtime = path.join(root, "reverse/scripts/codex-companion.mjs");
const endHook = path.join(root, "reverse/scripts/session-lifecycle-hook.mjs");

test("Council preserves helper boundaries on fresh, resumed, write and native review calls", () => {
  const repo = makeTempDir();
  const bin = makeTempDir();
  installFakeCodex(bin);
  initGitRepo(repo);
  fs.writeFileSync(path.join(repo, "sample.txt"), "original\n");
  run("git", ["add", "."], { cwd: repo });
  run("git", ["commit", "-m", "fixture"], { cwd: repo });
  const env = { ...buildEnv(bin), CLAUDE_PLUGIN_DATA: makeTempDir(), CODEX_COMPANION_SESSION_ID: "council-test" };
  const state = () => JSON.parse(fs.readFileSync(path.join(bin, "fake-codex-state.json")));
  const call = (...args) => {
    const result = run(process.execPath, [runtime, ...args], { cwd: repo, env });
    assert.equal(result.status, 0, result.stderr);
    return result;
  };
  try {
    call("task", "--fresh", "Investigate only");
    const first = state().lastThreadStart;
    assert.equal(first.sandbox, "read-only");
    assert.equal(first.approvalPolicy, "never");
    assert.equal(first.model, null);
    assert.match(first.developerInstructions, /Do not invoke Claude/);
    assert.match(first.developerInstructions, /facts, hypotheses and unknowns/);
    call("task", "--resume-last", "Inspect new evidence");
    const resumed = state().lastThreadResume;
    assert.equal(resumed.developerInstructions, first.developerInstructions);
    assert.equal(resumed.sandbox, "read-only");
    call("task", "--write", "--fresh", "Apply the authorized repair");
    assert.equal(state().lastThreadStart.sandbox, "workspace-write");
    call("review", "--scope", "working-tree");
    assert.equal(state().lastThreadStart.sandbox, "read-only");
    assert.equal(state().lastThreadStart.developerInstructions, first.developerInstructions);
  } finally {
    run(process.execPath, [endHook, "SessionEnd"], { cwd: repo, env,
      input: JSON.stringify({ cwd: repo, session_id: "council-test" }) });
  }
});

test("installed Council includes its own working runtime and all plugin components", () => {
  const target = path.join(makeTempDir(), "codex-claude-council");
  const installed = run("python3", ["-B", path.join(root, "scripts/install.py"), "--target", target]);
  assert.equal(installed.status, 0, installed.stderr);
  const manifest = JSON.parse(fs.readFileSync(path.join(target, ".claude-plugin/plugin.json")));
  assert.equal(manifest.name, "codex-claude-council");
  for (const relative of [...manifest.skills, manifest.commands, ...manifest.agents, manifest.hooks]) {
    assert.ok(fs.existsSync(path.resolve(target, relative)), relative);
  }
  assert.deepEqual(fs.readdirSync(path.join(target, manifest.commands)).sort(), [
    "adversarial-review.md", "cancel.md", "rescue.md", "result.md", "review.md", "setup.md", "status.md", "transfer.md"
  ]);
  const help = run(process.execPath, [path.join(target, "reverse/scripts/codex-companion.mjs"), "help"]);
  assert.equal(help.status, 0, help.stderr);
  assert.match(help.stdout, /adversarial-review/);
  assert.match(help.stdout, /transfer/);
  const hooks = JSON.parse(fs.readFileSync(path.join(target, manifest.hooks)));
  for (const groups of Object.values(hooks.hooks)) {
    for (const group of groups) for (const hook of group.hooks) {
      const match = hook.command.match(/\$\{CLAUDE_PLUGIN_ROOT\}([^" ]+)/);
      assert.ok(match, hook.command);
      assert.ok(fs.existsSync(target + match[1]), hook.command);
    }
  }
});
