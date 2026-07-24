# Documentation File Set

Use this reference to decide which files to generate and how detailed each file should be.

## Core Files

| File | Purpose | Content Grain |
|---|---|---|
| `AGENTS.md` | Immediate collaboration rules for agents working in the repository. | 30-80 lines. High-frequency rules, safety boundaries, doc update rules, and pointers to deeper docs or skills. No architecture detail dumps. |
| `README.md` | Public entry point for humans running or evaluating the project. | 120-300 lines. What it is for, main capabilities, system shape, quick start, key config, test commands, and links. Avoid full internal architecture. |
| `docs/README.md` | Documentation navigation. | Short index grouped by product, collaboration, architecture, reference, UI, and skills if present. Include recommended reading order. |
| `docs/vision.md` | Product scope and stable boundaries. | Product-level. State target users/scenarios, core capabilities, non-goals, and concepts that must not be lost during refactors. |
| `docs/agent-rules/general.md` | Deeper collaboration and coding rules. | Rule-level. Code change policy, tests, docs, safety/data handling. Keep project-agnostic where possible. |

## Architecture Files

| File | Purpose | Content Grain |
|---|---|---|
| `docs/architecture/overview.md` | System overview. | Broad. Current shape, top-level components, code structure, and hard boundaries. |
| `docs/architecture/runtime-topology.md` | Runtime processes and deployment topology. | Process-level. API/app processes, workers, schedulers, queues, database, external services, container topology. |
| `docs/architecture/backend-modules.md` | Backend module responsibilities. | Module-level. Directories, entry points, API layers, services, storage, parsers, integrations. |
| `docs/architecture/data-model.md` | Data model and storage facts. | Concept/table-level. Business entities, storage ownership, table groups, migration posture. Point to code as schema source when migrations are absent. |
| `docs/architecture/request-flows.md` | Important user/system flows. | Flow-level. Admin/public/client flows, async job chains, sync flows, periodic tasks. |
| `docs/architecture/frontend-spa.md` | Frontend structure, if the project has a frontend. | App-shell/module-level. Entry points, route loading, shared assets, CSS build, runtime resource loading. Skip if no frontend exists. |

## Reference Files

| File | Purpose | Content Grain |
|---|---|---|
| `docs/reference/api.md` | HTTP/API contract, if routes exist. | Endpoint-level. Group by surface. Include method/path and concise request/response behavior, not handler internals. |
| `docs/reference/configuration.md` | Configuration contract. | Variable-level. Deployment variables, app env vars, runtime config, defaults, source precedence, secret handling. |
| `docs/reference/mcp.md` | MCP/tooling integration, if present. | Tool-level. Entry points, auth, environment variables, tool groups, safety rules, client examples. Skip if absent. |

## UI Files

| File | Purpose | Content Grain |
|---|---|---|
| `docs/ui/theme.md` | Current UI theme facts. | Token/semantic-level. Brand colors, surfaces, state colors, per-surface differences. Prefer current implementation over desired redesigns. |
| `docs/ui/utility-authoring.md` | Utility/CSS authoring rules. | Style-governance-level. What belongs in template utilities, shared semantic classes, or CSS variables. Skip if project has no utility/CSS convention. |

## Writing Rules

- Generate only facts supported by repository evidence.
- Use exact paths and command names from the target project.
- Keep one responsibility per file. If a paragraph belongs in a deeper file, link there instead of duplicating it.
- Do not include secrets, personal data, sample candidate details, raw logs, or private tokens.
- Do not preserve stale docs merely because they exist. Current code wins.
- Do not create diagrams unless they clarify topology better than text.
