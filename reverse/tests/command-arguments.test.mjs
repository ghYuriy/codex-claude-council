import fs from "node:fs";
import path from "node:path";
import test, { after } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

import { buildEnv, installFakeCodex } from "./fake-codex-fixture.mjs";
import { createdTempDirs, initGitRepo, makeTempDir, run } from "./helpers.mjs";
import { loadBrokerSession } from "../scripts/lib/broker-lifecycle.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SCRIPT = path.join(ROOT, "scripts", "codex-companion.mjs");
const COMMANDS = path.join(ROOT, "commands");
const SESSION_HOOK = path.join(ROOT, "scripts", "session-lifecycle-hook.mjs");

// Reviews start a shared broker per workspace; stop every one these tests created.
after(() => {
  for (const directory of createdTempDirs) {
    if (loadBrokerSession(directory)) {
      const cleanup = run(process.execPath, [SESSION_HOOK, "SessionEnd"], {
        cwd: directory, env: process.env, input: JSON.stringify({ hook_event_name: "SessionEnd", cwd: directory })
      });
      assert.equal(cleanup.status, 0, cleanup.stderr);
      assert.equal(loadBrokerSession(directory), null, `Broker left running for ${directory}`);
    }
  }
});

// The documented recipe: every user argument becomes its own single-quoted shell token.
function quote(token) {
  return `'${token.replaceAll("'", "'\\''")}'`;
}

function repoWithChange() {
  const repo = makeTempDir();
  initGitRepo(repo);
  fs.writeFileSync(path.join(repo, "app.js"), "export const value = items[0];\n");
  run("git", ["add", "app.js"], { cwd: repo });
  run("git", ["commit", "-m", "init"], { cwd: repo });
  fs.writeFileSync(path.join(repo, "app.js"), "export const value = items[0].id;\n");
  return repo;
}

test("documented quoting passes spaces, quotes and shell syntax to the script as literal data", () => {
  const repo = repoWithChange();
  const bin = makeTempDir();
  installFakeCodex(bin);
  const focus = "check don't trust $HOME, `touch BACKTICK`; $(touch SUBSHELL) \"quoted\" \\ end";
  const tokens = ["--scope", "working-tree", focus];
  const command = `${quote(process.execPath)} ${quote(SCRIPT)} adversarial-review ${tokens.map(quote).join(" ")}`;
  const result = run("/bin/sh", ["-c", command], { cwd: repo, env: buildEnv(bin) });
  assert.equal(result.status, 0, result.stderr);
  const state = JSON.parse(fs.readFileSync(path.join(bin, "fake-codex-state.json"), "utf8"));
  assert.ok(state.lastTurnStart.prompt.includes(focus), "the focus text must arrive unchanged");
  assert.equal(fs.existsSync(path.join(repo, "BACKTICK")), false);
  assert.equal(fs.existsSync(path.join(repo, "SUBSHELL")), false);
});

test("review run mode flags are accepted by the script but do not change it", () => {
  const repo = repoWithChange();
  const bin = makeTempDir();
  installFakeCodex(bin);
  for (const flag of ["--wait", "--background"]) {
    const result = run(process.execPath, [SCRIPT, "review", flag], { cwd: repo, env: buildEnv(bin) });
    assert.equal(result.status, 0, result.stderr);
    assert.doesNotMatch(result.stdout, /started in the background/);
  }
});

test("script usage does not advertise run mode flags it ignores for reviews", () => {
  const usage = run(process.execPath, [SCRIPT, "help"], { cwd: makeTempDir() });
  const reviewLines = usage.stdout.split("\n").filter((line) => / (adversarial-)?review /.test(line));
  assert.equal(reviewLines.length, 2, usage.stdout);
  for (const line of reviewLines) assert.doesNotMatch(line, /--wait|--background/);
  assert.match(usage.stdout, /task \[--background\]/);
});

function adversarialFocus(tokens) {
  const repo = repoWithChange();
  const bin = makeTempDir();
  installFakeCodex(bin);
  const result = run(process.execPath, [SCRIPT, "adversarial-review", ...tokens], { cwd: repo, env: buildEnv(bin) });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(fs.readFileSync(path.join(bin, "fake-codex-state.json"), "utf8")).lastTurnStart.prompt;
}

test("documented recipe keeps a lone focus argument with quotes exactly", () => {
  assert.ok(adversarialFocus(["--exact-args", 'Review "auth" paths']).includes('Review "auth" paths'));
});

test("documented recipe keeps option-like words inside a lone focus argument as text", () => {
  const prompt = adversarialFocus(["--exact-args", "Review --scope branch"]);
  assert.ok(prompt.includes("Review --scope branch"), "the focus text must not become a scope option");
});

test("documented recipe keeps the user's own separator meaning", () => {
  const prompt = adversarialFocus(["--exact-args", "--", "--scope", "branch"]);
  assert.ok(prompt.includes("--scope branch"));
});

test("a direct call without the recipe marker keeps its earlier meaning", () => {
  assert.ok(adversarialFocus(["--", "inspect", "--"]).includes("inspect --"));
});

test("command templates mark their arguments as exact tokens", () => {
  for (const name of ["review.md", "adversarial-review.md"]) {
    const lines = fs.readFileSync(path.join(COMMANDS, name), "utf8").split("\n")
      .filter((line) => line.includes("codex-companion.mjs") && line.includes("<arguments>"));
    assert.ok(lines.length > 0, name);
    for (const line of lines) assert.match(line, /--exact-args <arguments>(`|$)/, `${name}: ${line}`);
  }
});

test("a single raw argument string is still split with shell-like rules", () => {
  const repo = repoWithChange();
  const bin = makeTempDir();
  installFakeCodex(bin);
  const result = run(process.execPath, [SCRIPT, "adversarial-review", "--scope working-tree 'focus on auth'"],
    { cwd: repo, env: buildEnv(bin) });
  assert.equal(result.status, 0, result.stderr);
  const state = JSON.parse(fs.readFileSync(path.join(bin, "fake-codex-state.json"), "utf8"));
  assert.ok(state.lastTurnStart.prompt.includes("focus on auth"));
});

test("command templates never leave the script a lone argument to split again", () => {
  for (const name of fs.readdirSync(COMMANDS).filter((file) => file.endsWith(".md"))) {
    const text = fs.readFileSync(path.join(COMMANDS, name), "utf8");
    for (const line of text.split("\n").filter((item) => item.includes("codex-companion.mjs") && item.includes("<arguments>"))) {
      const tokens = line.split("codex-companion.mjs\"")[1].replace(/`.*$/, "").trim().split(/\s+/).slice(1);
      assert.ok(tokens.some((token) => token !== "<arguments>"), `${name}: ${line}`);
    }
  }
});

test("command files pass arguments only as separately quoted tokens", () => {
  for (const name of fs.readdirSync(COMMANDS).filter((file) => file.endsWith(".md"))) {
    const text = fs.readFileSync(path.join(COMMANDS, name), "utf8");
    const blocks = [...text.matchAll(/```(?:bash|typescript)\n([\s\S]*?)```/g)].map((match) => match[1]);
    for (const block of blocks) {
      assert.doesNotMatch(block, /\$ARGUMENTS/, `${name}: a command template must not paste raw arguments`);
    }
  }
});

test("the fake Codex logs launches with environment names and never values", () => {
  const repo = repoWithChange();
  const bin = makeTempDir();
  installFakeCodex(bin);
  const result = run(process.execPath, [SCRIPT, "review"], { cwd: repo,
    env: { ...buildEnv(bin), COUNCIL_TEST_SECRET: "secret-value-7d1f" } });
  assert.equal(result.status, 0, result.stderr);
  const log = fs.readFileSync(path.join(bin, "fake-codex-launches.jsonl"), "utf8");
  const launches = log.trim().split("\n").map((line) => JSON.parse(line));
  assert.ok(launches.some((launch) => launch.argv[0] === "app-server"));
  assert.ok(launches.every((launch) => launch.envNames.includes("COUNCIL_TEST_SECRET")));
  assert.doesNotMatch(log, /secret-value-7d1f/);
});
