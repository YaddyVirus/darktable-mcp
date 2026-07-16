#!/usr/bin/env bash
# Sync the fork with the upstream YaddyVirus/darktable-mcp.
#
# Strategy: keep a local 'upstream-tracking' branch rebased onto
# upstream/master, and a 'upstream-diff' branch that contains the
# diverging fork commits replayed on top.  That way `git log
# upstream-diff ^upstream-tracking` shows exactly what the fork has
# added on top of upstream, and `git log upstream-tracking ^master`
# shows what upstream has that the fork doesn't.
#
# Usage:
#   scripts/sync-upstream.sh            # fetch + show status
#   scripts/sync-upstream.sh pull       # rebase upstream-tracking
#   scripts/sync-upstream.sh diff       # show upstream commits not in master
#   scripts/sync-upstream.sh merge      # replay master commits onto upstream
set -euo pipefail

UPSTREAM_REMOTE="${UPSTREAM_REMOTE:-upstream}"
UPSTREAM_BRANCH="${UPSTREAM_BRANCH:-upstream-tracking}"
FORK_BRANCH="${FORK_BRANCH:-master}"

die() { printf "error: %s\n" "$*" >&2; exit 1; }
note() { printf "==> %s\n" "$*"; }

ensure_remote() {
    if ! git remote get-url "$UPSTREAM_REMOTE" >/dev/null 2>&1; then
        note "Adding remote $UPSTREAM_REMOTE → https://github.com/YaddyVirus/darktable-mcp.git"
        git remote add "$UPSTREAM_REMOTE" https://github.com/YaddyVirus/darktable-mcp.git
    fi
}

cmd_status() {
    ensure_remote
    note "Fetching $UPSTREAM_REMOTE"
    git fetch --prune "$UPSTREAM_REMOTE"
    note "Upstream HEAD: $UPSTREAM_REMOTE/master ($(git rev-parse --short "$UPSTREAM_REMOTE/master"))"
    note "Local  HEAD: $FORK_BRANCH ($(git rev-parse --short "$FORK_BRANCH"))"
    local ahead behind
    ahead=$(git rev-list --count "$UPSTREAM_REMOTE/master".."${FORK_BRANCH}" 2>/dev/null || echo "?")
    behind=$(git rev-list --count "${FORK_BRANCH}".."$UPSTREAM_REMOTE/master" 2>/dev/null || echo "?")
    note "Fork is $ahead commits ahead, $behind commits behind upstream."
    if [ "$behind" != "0" ] && [ "$behind" != "?" ]; then
        printf "\nUpstream commits not in %s:\n" "$FORK_BRANCH"
        git log --oneline "${FORK_BRANCH}..$UPSTREAM_REMOTE/master" | head -20
    fi
}

cmd_pull() {
    ensure_remote
    git fetch --prune "$UPSTREAM_REMOTE"
    if git show-ref --verify --quiet "refs/heads/$UPSTREAM_BRANCH"; then
        note "Rebasing $UPSTREAM_BRANCH onto $UPSTREAM_REMOTE/master"
        git checkout "$UPSTREAM_BRANCH"
        git rebase "$UPSTREAM_REMOTE/master"
    else
        note "Creating $UPSTREAM_BRANCH from $UPSTREAM_REMOTE/master"
        git branch --track "$UPSTREAM_BRANCH" "$UPSTREAM_REMOTE/master"
    fi
    note "Done. Review:  git log $FORK_BRANCH..$UPSTREAM_BRANCH"
}

cmd_diff() {
    ensure_remote
    git fetch --prune "$UPSTREAM_REMOTE"
    note "Commits upstream has that $FORK_BRANCH does not:"
    git log --oneline "${FORK_BRANCH}..$UPSTREAM_REMOTE/master"
    note "Commits $FORK_BRANCH has that upstream does not:"
    git log --oneline "$UPSTREAM_REMOTE/master..${FORK_BRANCH}"
}

case "${1:-status}" in
    status|"") cmd_status ;;
    pull)       cmd_pull ;;
    diff)       cmd_diff ;;
    *) die "unknown command: $1 (use status|pull|diff)" ;;
esac
