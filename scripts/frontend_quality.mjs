// Read-only TypeScript AST measurements; no application module is executed.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(path.join(root, "frontend/package.json"));
const ts = require("typescript");
const files = [];
function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory() && entry.name !== "test") walk(full);
    else if (entry.isFile() && /\.tsx?$/.test(full) && !/\.test\./.test(full)) files.push(full);
  }
}
walk(path.join(root, "frontend/src"));
const metrics = {}, graph = {}, bodies = {}, violations = [];
const relative = (file) => path.relative(root, file).split(path.sep).join("/");
const branches = new Set([ts.SyntaxKind.IfStatement, ts.SyntaxKind.ConditionalExpression,
  ts.SyntaxKind.ForStatement, ts.SyntaxKind.ForOfStatement, ts.SyntaxKind.ForInStatement,
  ts.SyntaxKind.WhileStatement, ts.SyntaxKind.DoStatement, ts.SyntaxKind.CatchClause,
  ts.SyntaxKind.CaseClause]);
function complexity(body) {
  let count = 1;
  function visit(node) {
    if (ts.isFunctionLike(node)) return;
    if (branches.has(node.kind)) count++;
    if (ts.isBinaryExpression(node) && [ts.SyntaxKind.AmpersandAmpersandToken,
      ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken].includes(node.operatorToken.kind)) count++;
    ts.forEachChild(node, visit);
  }
  visit(body);
  return count;
}
for (const full of files.sort()) {
  const file = relative(full), source = fs.readFileSync(full, "utf8");
  const ast = ts.createSourceFile(full, source, ts.ScriptTarget.Latest, true);
  metrics[`lines:${file}`] = source.trimEnd().split("\n").length;
  graph[file] = [];
  function visit(node, scope = "module") {
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
      const specifier = node.moduleSpecifier?.text;
      if (specifier?.startsWith(".")) {
        const base = path.resolve(path.dirname(full), specifier);
        const target = [base, `${base}.ts`, `${base}.tsx`, path.join(base, "index.ts")].find((p) => files.includes(p));
        if (target) {
          const dependency = relative(target);
          graph[file].push(dependency);
          if (file.includes("/features/") && /\/(App|main)\.tsx$/.test(dependency)) violations.push(`${file} -> entry ${dependency}`);
          if (file.includes("/shared/") && !dependency.includes("/shared/")) violations.push(`${file} -> non-shared ${dependency}`);
          if (file.endsWith("/model.ts") && dependency.endsWith("/http.ts")) violations.push(`${file} -> IO ${dependency}`);
        }
      }
    }
    if (file.endsWith("/model.ts") && ts.isIdentifier(node) && ["fetch", "localStorage", "sessionStorage", "XMLHttpRequest"].includes(node.text)) violations.push(`${file}: IO identifier ${node.text}`);
    if (ts.isFunctionLike(node) && node.body) {
      const name = node.name?.getText(ast) || (ts.isVariableDeclaration(node.parent) ? node.parent.name.getText(ast) : "callback");
      scope = `${scope}/${name}`;
      const key = `complexity:${file}:${scope}`;
      metrics[key] = Math.max(metrics[key] || 0, complexity(node.body));
      if (name !== "callback") {
        const body = node.body.getText(ast).replace(/\s+/g, " ").trim();
        if (body.length >= 240) (bodies[body] ||= []).push(`${file}:${scope}`);
      }
    }
    ts.forEachChild(node, (child) => visit(child, scope));
  }
  visit(ast);
}
const duplicates = Object.values(bodies).filter((locations) => locations.length > 1).map((locations) => locations.sort().join(" | ")).sort();
console.log(JSON.stringify({ metrics, graph, duplicates, violations }));
