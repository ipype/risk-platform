# ACTIVE.md — in-flight work

In-flight only. Target under 100 lines. Anything not being worked on right now goes to
`BACKLOG.md`.

## Now

- [ ] Repo scaffold: `api/`, `core/`, `sim/`, `parse/`, `workers/`, `web/`, `scripts/`,
      `migrations/`, `tests/`, plus `Makefile`, `pyproject.toml`, `docker-compose.yml`.
      Nothing under "Build commands" in `CLAUDE.md` is real until this lands.
- [ ] Grant the GitHub MCP connector write access to `ipype/Risk-Platform`. `push_files`
      currently returns 403 on tree creation, so no automated commits are possible.
- [ ] Confirm the open architecture questions before any code (see `BACKLOG.md` → Blocked).

## Notes

- Repo currently contains `README.md` and this docs scaffold only.
