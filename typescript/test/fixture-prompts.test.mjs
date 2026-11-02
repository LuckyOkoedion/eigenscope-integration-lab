import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

test("live fixture prompts use natural inputs without sensitivity or trust wrappers", () => {
  const source = ts.createSourceFile(
    "live.ts",
    readFileSync(new URL("../examples/live.ts", import.meta.url), "utf8"),
    ts.ScriptTarget.Latest,
    true,
  );
  let cases;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(source) === "cases")
      cases = node.initializer;
    ts.forEachChild(node, visit);
  }
  visit(source);
  assert.ok(cases && ts.isArrayLiteralExpression(cases));
  assert.ok(cases.elements.length > 0);
  for (const fixture of cases.elements) {
    assert.ok(ts.isObjectLiteralExpression(fixture));
    const input = fixture.properties.find(
      (property) => ts.isPropertyAssignment(property) && property.name.getText(source) === "text",
    );
    assert.ok(input && ts.isStringLiteral(input.initializer));
    assert.doesNotMatch(
      input.initializer.text.normalize("NFKC").replace(/\s+/gu, " "),
      /\b(private|confidential|secret|hidden|untrusted|redact|scrub|mask|anonymi[sz]e|sanitize)\b|do not send/i,
    );
  }
});
