# THIRD_PARTY_NOTICE

> Third-party dependency register for the KnowEvo additions on top of upstream Nexent.
> Dependencies that ship with upstream Nexent are not listed again here — see
> `backend/pyproject.toml`, `frontend/package.json` and the `docker/` images for those.

## Added / explicitly consumed dependencies

| Dependency | Version | Purpose | License | Source |
|---|---|---|---|---|
| fastmcp | >=2.14.2,<3.0 | MCP tool service for the KnowEvo tool family (standalone FastMCP service and Local MCP pipeline registration from a single schema source) | Apache-2.0 | pip (present in upstream pyproject, activated by KnowEvo) |
| mcp (official SDK) | >=1.24.0,<1.30 | MCP protocol layer for local and remote tool calls | MIT | pip (present in upstream sdk/pyproject) |
| @antv/g6 | ^5.1.1 | Ontology tree / graph visualization in the knowledge-graph panel | MIT | npm (present in upstream `frontend/package.json`) |
| recharts | ^2.15.0 | Evaluation charts | MIT | npm (present in upstream `frontend/package.json`) |
| react-d3-tree | ^3.6.6 | Agent call-relationship tree view | MIT | npm (present in upstream `frontend/package.json`); transitive `@antv/*` lockfile footprint is 16 packages, 15 MIT and `@antv/vendor` as `MIT AND ISC` |

## Evaluated but not adopted (not runtime dependencies)

| Candidate | Role in evaluation | License |
|---|---|---|
| goldenmatch | Reference implementation for the MCP tool surface comparison (70+ tools) | MIT |
| docling | PDF parsing candidate, never imported | MIT |
| graphiti | Design reference for bi-temporal edges | see upstream |
| instructor | Rejected: pulling it in would downgrade the pinned upstream `openai` and `rich` requirements; structured output uses the repository's existing YAML prompt + JSON parsing + Pydantic validation path instead | MIT |
| networkx | Kept as a fallback for multi-hop path ranking; the shipped scorer uses lexical overlap within the latency budget, so the dependency is not added | BSD-3 |

## Fork attribution

This repository is a fork of **[ModelEngine-Group/Nexent](https://github.com/ModelEngine-Group/nexent)**.
Upstream `LICENSE` is **MIT** (Copyright (c) 2025 Huawei Technologies Co., Ltd.) and the upstream
`NOTICE` file registers Unstructured.io / SmolAgents / Ray; both are preserved verbatim.
The KnowEvo additions (`backend/services/knowevo/**`, `mcp_servers/knowevo_mcp/**`, the
`v2.5.5_kw_*` migrations, and the KnowEvo frontend panels) are distributed under the same MIT
terms. Do not remove or rename the upstream license and notice files.

## Adding a dependency

New top-level dependencies must be added to the table above with their version, purpose and
license before being committed. Transitive dependencies do not need individual rows, but the
top-level addition must be mentioned in the commit message. Prefer MIT / Apache-2.0 / BSD
licenses; anything copyleft needs an explicit compatibility note.
