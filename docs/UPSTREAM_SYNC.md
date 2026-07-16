# Upstream sync policy

This fork of [YaddyVirus/darktable-mcp](https://github.com/YaddyVirus/darktable-mcp)
intentionally diverges from upstream.  It is **not** a long-running
fork-of-fork — features added here stay here.  We do, however, want to
be aware of upstream changes so we can adopt useful fixes.

## Cadence

- **Weekly:** run `./scripts/sync-upstream.sh status` to see how far
  the fork has fallen behind.
- **Per release:** when upstream tags a release, fetch and review.
  Particularly look for:
  - security fixes
  - new MCP tools the fork should also expose
  - new RAW format support (the file-extension list in `processor.py`)

## How to bring a single upstream commit into the fork

Prefer **cherry-pick** over full rebase — the fork's history is
intentionally different and we don't want to rewrite it.

```bash
# 1. Pull upstream refs locally
./scripts/sync-upstream.sh pull

# 2. Find the commit you want
git log upstream-tracking --oneline | head -20

# 3. Cherry-pick onto a feature branch
git checkout -b fix/import-upstream-xyz master
git cherry-pick <sha>
# ...resolve any conflicts...
git push origin fix/import-upstream-xyz
# Open a PR against master
```

## What NOT to do

- **Don't fast-forward `master` to `upstream/master`.**  That would
  erase the fork's work.
- **Don't open PRs against YaddyVirus/darktable-mcp.**  This fork is
  private to `yuri-schmaltz` and we don't push back.  (If you want to
  contribute upstream, do it from a separate clone.)
- **Don't rebase feature branches onto `upstream-tracking`** unless
  you're explicitly porting an upstream fix — rebase conflict noise is
  not worth the cosmetic gain.

## Conflict-prone areas

- `pyproject.toml` — both sides evolve dependencies.
- `src/darktable_mcp/server.py` — both add tools; signature changes
  may conflict.
- `src/darktable_mcp/processor.py` — upstream may add Pillow-only
  effects; the fork delegates to `darktable-cli` instead.
- `tests/` — both sides add tests; if they touch the same file,
  expect manual merges.

When conflicts hit, prefer **the fork's version of integration code**
(`server.py`, `processor.py`) and **upstream's version of metadata**
(version, README) — judgment call, document your reasoning in the
commit message.
