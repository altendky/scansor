# AI Agent Context

Scansor is documentation-first and remains at concept stage. Do not imply that
open design choices, integrations, experiments, or product claims are settled.

For scope, status, architecture, decisions, and open questions, start with:

- [Project documentation](docs/src/project/index.md)

## Onshape Workspace

- Coordination folder: [Scansor](https://cad.onshape.com/documents?nodeId=d262d0122052ddf2b4851035&resourceType=folder), ID `d262d0122052ddf2b4851035`.
- Designated sandbox: [Agent Sandbox](https://cad.onshape.com/documents?nodeId=b788af3dad6250b9ed521e6a&resourceType=folder), ID `b788af3dad6250b9ed521e6a`, directly under `Scansor`.
- `Scansor` is a coordination layer. Agents must not create, modify, move,
  rename, or delete its direct contents without explicit user agreement, except
  to enter and operate through the designated `Agent Sandbox`.
- Within `Agent Sandbox`, agents may freely create, modify, rename, move, and
  delete disposable Scansor development or test documents and subfolders,
  including generated synthetic fixtures and data.
- Agents must not touch unrelated Onshape content or move items across the
  sandbox boundary without explicit user agreement.
