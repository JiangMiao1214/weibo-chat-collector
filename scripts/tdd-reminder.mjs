import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const policy = new URL("../docs/tdd-development.md", import.meta.url);
const text = readFileSync(policy, "utf8");
const blocks = [...text.matchAll(/<!-- tdd-reminder:start -->([\s\S]*?)<!-- tdd-reminder:end -->/g)];
if (blocks.length !== 1 || !blocks[0][1].trim()) {
  throw new Error("TDD policy reminder block is missing or malformed");
}
console.log(`[TDD] 开发规则：${fileURLToPath(policy)}\n${blocks[0][1].trim()}\n检查：.venv/bin/python scripts/verify.py`);
