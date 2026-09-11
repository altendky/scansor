# AI Agent Context

Scansor is documentation-first and remains at concept stage. Do not imply that
open design choices, integrations, experiments, or product claims are settled.

For scope, status, architecture, decisions, and open questions, start with:

- [Project documentation](docs/src/project/index.md)

## Dependency and PLY Reader Boundaries

- Avoid GPL-, LGPL-, and AGPL-licensed dependencies for now. Explicitly alert the
  user whenever considering one, including a transitive dependency; do not adopt
  it without a new user decision.
- Do not consult, copy, translate, or port `plyfile` source when working on
  Scansor.
- Keep the PLY reader isolated from Scansor-specific semantics so it can be
  extracted into a separate package with little work.

## Onshape Workspace

- Coordination folder: [Scansor][scansor-folder], ID
  `d262d0122052ddf2b4851035`.
- Designated sandbox: [Agent Sandbox][sandbox-folder], ID
  `b788af3dad6250b9ed521e6a`, directly under `Scansor`.
- `Scansor` is a coordination layer. Agents must not create, modify, move,
  rename, or delete its direct contents without explicit user agreement, except
  to enter and operate through the designated `Agent Sandbox`.
- Within `Agent Sandbox`, agents may freely create, modify, rename, move, and
  delete disposable Scansor development or test documents and subfolders,
  including generated synthetic fixtures and data.
- Agents must not touch unrelated Onshape content or move items across the
  sandbox boundary without explicit user agreement.

[sandbox-folder]: https://cad.onshape.com/documents?nodeId=b788af3dad6250b9ed521e6a&resourceType=folder
[scansor-folder]: https://cad.onshape.com/documents?nodeId=d262d0122052ddf2b4851035&resourceType=folder
