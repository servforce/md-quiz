---
name: project-docs-bootstrap
description: Generate or refresh a compact project documentation file set from repository evidence. Use when Codex is asked to create AGENTS.md, README.md, docs/ navigation, architecture docs, API/config references, UI docs, or portable documentation scaffolding for an existing codebase.
---

# Project Docs Bootstrap

Generate a project-specific documentation file set by reading the target repository first. The output should be portable across projects because it derives content from code, config, scripts, tests, and deployment files instead of hard-coded assumptions.

## Workflow

1. Inspect before writing.
   - Read existing `AGENTS.md`, `README.md`, `docs/`, package/dependency files, deployment files, dev scripts, route/API definitions, database schema, frontend entry points, and tests.
   - Treat source files, config, scripts, tests, and runtime output as evidence. Mark uncertain items as inferred or omit them.
   - Do not print or copy secrets, tokens, database passwords, private candidate data, resumes, or production logs.
2. Decide the file set.
   - Read [references/file-set.md](references/file-set.md).
   - Generate the core files unless the user narrows scope.
   - Generate optional reference/UI docs only when the repository has that surface.
3. Write with project-fitting granularity.
   - Keep root files concise: entry points and rules, not full architecture.
   - Put stable implementation facts in `docs/architecture/`.
   - Put exact contracts in `docs/reference/`.
   - Put UI tokens and utility rules in `docs/ui/`.
   - Avoid filler, marketing copy, unresolved placeholders, and long explanations copied from code.
4. Update existing files safely.
   - Preserve correct existing facts.
   - Replace stale structure or outdated paths when current code contradicts them.
   - Keep navigation links in `docs/README.md` synchronized with created or removed docs.
5. Validate.
   - Check all documented paths exist or are explicitly described as planned.
   - Check Markdown links for files created in this pass.
   - Search for unresolved placeholder markers unless they intentionally document a real backlog.
   - Re-run relevant lightweight tests only if the docs describe generated behavior that tests already cover.

## Output Style

- Default to Chinese for project docs unless the repository already uses another language.
- Prefer short sections and concrete lists over prose.
- Distinguish facts from inference when evidence is incomplete.
- Do not add a new documentation framework, static site generator, or dependency unless the user explicitly asks.
