# AGENTS.md

> Conventions for AI coding agents (and humans) working in this repo.

## Project

`darktable-mcp` is a fork of [YaddyVirus/darktable-mcp](https://github.com/YaddyVirus/darktable-mcp) maintained at
[yuri-schmaltz/mcp-darktable](https://github.com/yuri-schmaltz/mcp-darktable).
It exposes photo-editing tools to Claude via the Model Context Protocol.

**Do not push back to upstream.**  The fork intentionally diverges — new
work goes onto a feature branch and is reviewed by the fork maintainer
before merging to `master`.

## Quick start

```bash
# 1. Work in a worktree, never the main checkout
git worktree add .worktrees/feature-xxx -b feature/xxx origin/master

# 2. Install dev deps (includes pyright, rawpy, pytest)
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 3. Verify
pytest -v
pyright src/ tests/
```

## Project layout

```
src/darktable_mcp/
  edits.py        # state dataclasses + JSON sidecar persistence
  processor.py    # image rendering (delegates to darktable-cli)
  xmp_writer.py   # Darktable-compatible XMP sidecar generator
  server.py       # MCP tools (the FastMCP surface)
tests/            # pytest suite, mirrors the module layout
.github/
  workflows/      # CI (matrix: 3 OS × 3 Python × lint)
  dependabot.yml
.claude/          # project-specific Claude config (if any)
```

## Coding conventions

- **Python 3.10+.**  Use `from __future__ import annotations` in every
  file.  Modern union syntax (`X | None`) is preferred over `Optional[X]`.
- **Type hints everywhere.**  This project runs `pyright` in CI; PRs
  that introduce type errors will fail.
- **Pillow API:** use `Image.Resampling.LANCZOS` etc. — the bare
  `LANCZOS` / `BICUBIC` constants are deprecated.
- **No silent failures.**  If a tool can't do its job, return
  `{"error": "..."}` or raise.  Never swallow exceptions in
  bare `except Exception: pass`.
- **Always validate user input** via `darktable_mcp.edits.RANGES` and
  the `validate_param()` helper.  The `@handle_path_errors` decorator
  turns `ValidationError` into a structured response automatically.
- **Tests are required** for new tools.  At minimum: a happy-path
  roundtrip, one validation failure, and one edge case (empty input,
  missing file, etc.).  See `tests/test_batch.py` for a template.
- **No new runtime deps** without a discussion in the PR.  Edit
  `pyproject.toml`'s `dependencies` list and explain why.

## Branches

| Branch pattern | Purpose |
|----------------|---------|
| `master` | released code.  Fast-forward only from a reviewed feature branch. |
| `feature/<name>` | one feature or refactor per branch.  Branched from `master`. |
| `fix/<name>` | bug fix branched from `master`. |

Every change goes through a feature branch in a worktree.  Do **not**
edit the main checkout directly.

## Commit messages

- One logical change per commit.
- Title ≤ 72 chars, imperative mood ("Add X", "Fix Y", "Refactor Z").
- Body explains *why* if the change isn't obvious from the diff.

## CI

- `pytest` runs on Linux / macOS / Windows × Python 3.10 / 3.11 / 3.12
  (`.github/workflows/test.yml`).
- `pyright` runs in `basic` mode on Linux × Python 3.10 / 3.12.
- Dependabot opens weekly PRs for pip and GitHub Actions.

All checks must pass before merge.  The `lint` job is allowed to fail
locally if you don't have `pyright` installed, but the CI run is the
source of truth.

## Testing the darktable-cli path

Real `darktable-cli` integration can't run in CI (no darktable install
in the runner).  Use the `fake_darktable` fixture in
`tests/test_darktable_cli_integration.py` as a template — it creates a
Python script that emulates just enough of the darktable-cli CLI
(positional args, `--library` cleanup) to exercise the control flow.

Before opening a PR, smoke-test the real path locally with
`darktable-cli --version` available and a real RAW file:

```bash
pytest tests/test_darktable_cli_integration.py -v   # mocked
darktable-cli --version                            # real
# Apply some edits via Claude Desktop and verify the export.
```

## Syncing with upstream

```bash
# One-time: add the upstream remote
git remote add upstream https://github.com/YaddyVirus/darktable-mcp.git

# Periodically fetch and review
git fetch upstream
./scripts/sync-upstream.sh   # see scripts/ for the helper
```

See `docs/UPSTREAM_SYNC.md` for the full policy.
