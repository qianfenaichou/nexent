// Client-side preview render of a parameterized SKILL.md body. Mirrors
// skill_template_service.render_template (stdlib re): only known variable
// keys are substituted; unknown {braced} tokens survive. Preview/copy only -
// server-side apply (skill_template_apply / POST apply) is what bumps
// reuse_count; this helper never pretends to be that write path.

const PLACEHOLDER_RE = /\{([a-z_][a-z0-9_]*)\}/g;

export function renderSkillTemplate(
  bodyMd: string,
  variables: Record<string, string | undefined | null>,
  templateName = ""
): string {
  return bodyMd.replace(PLACEHOLDER_RE, (match, key: string) => {
    const value = variables[key];
    if (value !== undefined && value !== null && value !== "") {
      return String(value);
    }
    if (key === "template_name" && templateName) return templateName;
    return match;
  });
}
