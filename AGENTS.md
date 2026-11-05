# Integration workflow

- Use `pre-prod` as the integration and push target for this repository.
- `frontend/` is a separate Git repository/submodule. Fetch and integrate its own
  `origin/pre-prod` as well as the parent repository's `origin/pre-prod`.
- Preserve local work and integrate remote history without force-pushing or
  rewriting published commits. The frontend is connected to Lovable.
- When publishing a change that updates the frontend, push the tested frontend
  commit to its `pre-prod` branch first, then commit/update the parent gitlink and
  push the parent `pre-prod`. Never publish a parent gitlink whose commit is only
  available locally.
