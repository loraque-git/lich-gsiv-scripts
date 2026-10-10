#!/usr/bin/env bash
# Sync live Lich work into this repo and push to origin.
# Includes everything under Lich5/scripts that is not stock Elanthia-Online /
# Tillmen core, plus tools/saga-log-summary. Patched EO forks (*-patched,
# eloot-loot-stance.*) are included.
set -euo pipefail

LIVE_SCRIPTS="${LIVE_SCRIPTS:-/home/loraque/Lich5/scripts}"
LIVE_TOOLS="${LIVE_TOOLS:-/home/loraque/Lich5/tools/saga-log-summary}"
REPO="${REPO:-/home/loraque/Projects/lich-gsiv-scripts}"
LOCK="${LOCK:-/tmp/lich-gsiv-sync.lock}"
DRY_RUN="${DRY_RUN:-0}"
PUSH="${PUSH:-1}"

exec 200>"$LOCK"
if ! flock -n 200; then
  echo "lich-gsiv-sync: another sync holds the lock; skipping"
  exit 0
fi

cd "$REPO"

is_backup_name() {
  local base="$1"
  case "$base" in
    *Copy*|*'(1)'*|*DESKTOP*|*HAL_5000*) return 0 ;;
  esac
  # numbered go2 variants
  case "$base" in
    go2_*.lic|go2-*.lic) return 0 ;;
  esac
  return 1
}

is_force_include() {
  local base="$1"
  case "$base" in
    *-patched.lic|eloot-loot-stance.patch|eloot-loot-stance.README.md) return 0 ;;
  esac
  return 1
}

# Stock EO / Tillmen core (and close variants). Force-includes override this.
is_stock_eo_name() {
  local base="$1" stem="${1%.lic}"
  case "$stem" in
    alias|autostart|dependency|ebounty|ecaster|echild|ego2|eherbs|eloot|\
    enhance|ewaggle|ewatchdog|fletchit|foreach|go2|infomon|jinx|ledger|\
    lich5-update|lnet|log|logxml|map|narost|repository|resource|signore|\
    step2|useherbs|version|waggle|xnarost)
      return 0 ;;
  esac
  return 1
}

header_looks_stock_eo() {
  local file="$1" head
  # Only the authorship block; keep it cheap.
  head=$(head -n 80 "$file" 2>/dev/null || true)
  if printf '%s' "$head" | rg -qi '^\s*(author|maintainer|Author|creator):\s*.*(elanthia-online|tillmen@lichproject)'; then
    # Keep if loraque is explicitly a contributor AND this is a patched fork name
    # (handled by is_force_include). Plain stock with loraque in contributors stays out.
    return 0
  fi
  return 1
}

should_sync_script() {
  local file="$1" base
  base=$(basename "$file")

  if is_backup_name "$base"; then
    return 1
  fi
  if is_force_include "$base"; then
    return 0
  fi
  if is_stock_eo_name "$base"; then
    return 1
  fi
  if [[ "$base" == *.lic ]] && header_looks_stock_eo "$file"; then
    return 1
  fi
  case "$base" in
    *.lic|*.patch|*.md) return 0 ;;
  esac
  return 1
}

sync_scripts() {
  local src base dest
  mkdir -p "$REPO/scripts"
  shopt -s nullglob
  for src in "$LIVE_SCRIPTS"/*; do
    [[ -f "$src" ]] || continue
    base=$(basename "$src")
    if should_sync_script "$src"; then
      dest="$REPO/scripts/$base"
      if [[ "$DRY_RUN" == 1 ]]; then
        echo "INCLUDE $base"
      else
        cp -a "$src" "$dest"
      fi
    fi
  done

  # Drop tracked scripts that no longer qualify / no longer exist live
  if [[ "$DRY_RUN" != 1 ]]; then
    for dest in "$REPO/scripts"/*; do
      [[ -f "$dest" ]] || continue
      base=$(basename "$dest")
      case "$base" in
        README.md) continue ;;
      esac
      src="$LIVE_SCRIPTS/$base"
      if [[ ! -f "$src" ]] || ! should_sync_script "$src"; then
        git -C "$REPO" rm -f --quiet "scripts/$base" 2>/dev/null || rm -f "$dest"
      fi
    done
  fi
}

sync_tools() {
  mkdir -p "$REPO/tools/saga-log-summary"
  if [[ ! -d "$LIVE_TOOLS" ]]; then
    echo "lich-gsiv-sync: missing $LIVE_TOOLS" >&2
    return 0
  fi
  if [[ "$DRY_RUN" == 1 ]]; then
    echo "INCLUDE tools/saga-log-summary/{saga_log_summary.py,enhancive_loadout.py,serve.sh}"
    return 0
  fi
  cp -a "$LIVE_TOOLS/saga_log_summary.py" "$REPO/tools/saga-log-summary/"
  cp -a "$LIVE_TOOLS/enhancive_loadout.py" "$REPO/tools/saga-log-summary/"
  cp -a "$LIVE_TOOLS/serve.sh" "$REPO/tools/saga-log-summary/"
  # keep tools README if present; do not copy __pycache__
  rm -rf "$REPO/tools/saga-log-summary/__pycache__"
}

commit_and_push() {
  git -C "$REPO" add -A scripts tools/saga-log-summary
  # Keep sync helpers tracked
  git -C "$REPO" add tools/sync-from-live.sh tools/sync-watch.sh 2>/dev/null || true

  if git -C "$REPO" diff --cached --quiet; then
    echo "lich-gsiv-sync: nothing to commit"
    return 0
  fi

  local summary
  summary=$(git -C "$REPO" diff --cached --stat | tail -n 1 | sed 's/^ *//')
  git -C "$REPO" commit -m "Auto-sync from live Lich5

${summary}"

  if [[ "$PUSH" == 1 ]]; then
    git -C "$REPO" push origin HEAD
    echo "lich-gsiv-sync: pushed"
  else
    echo "lich-gsiv-sync: committed (push skipped)"
  fi
}

main() {
  echo "lich-gsiv-sync: start $(date -Is)"
  sync_scripts
  sync_tools
  if [[ "$DRY_RUN" == 1 ]]; then
    echo "lich-gsiv-sync: dry-run done"
    return 0
  fi
  commit_and_push
  echo "lich-gsiv-sync: done $(date -Is)"
}

main "$@"
