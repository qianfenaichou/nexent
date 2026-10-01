// Unit tests for the client-side template renderer. Must stay aligned with
// backend skill_template_service.render_template: only known variable keys
// substitute; unknown {braced} tokens survive; template_name is special.
import assert from "node:assert/strict";
import test from "node:test";

import {
  renderSkillTemplate,
  // @ts-ignore -- Node's built-in TypeScript runner needs the extension.
} from "./renderTemplate.ts";

test("known variables substitute and unknown braces survive", () => {
  const body = "domain={domain}\nkeep={unknown_key}\nname={template_name}";
  const out = renderSkillTemplate(
    body,
    { domain: "医疗", task_type: "fact_lookup" },
    "tpl-a"
  );
  assert.equal(out, "domain=医疗\nkeep={unknown_key}\nname=tpl-a");
});

test("empty / null / undefined leave the placeholder untouched", () => {
  assert.equal(
    renderSkillTemplate("x={domain} y={task_type}", {
      domain: "",
      task_type: null,
    }),
    "x={domain} y={task_type}"
  );
});

test("mustache-style braces are not special; inner {key} still matches", () => {
  // Backend regex \\{([a-z_][a-z0-9_]*)\\} matches the inner {domain}
  // inside {{domain}}, so the outer braces remain. Pin that behaviour.
  assert.equal(
    renderSkillTemplate("{{domain}} {Domain} {a-b}", { domain: "x" }),
    "{x} {Domain} {a-b}"
  );
});

test("template_name is used only when the caller passes one", () => {
  assert.equal(
    renderSkillTemplate("n={template_name}", {}),
    "n={template_name}"
  );
  assert.equal(
    renderSkillTemplate("n={template_name}", {}, "skill-x"),
    "n=skill-x"
  );
});
