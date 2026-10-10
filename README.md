# lich-gsiv-scripts

GemStone IV Lich scripts and tools by **loraque** (`loraque@gmail.com`).

Public repo for combat clones, Saga panels, community patches, and a Saga log summarizer. Drop `.lic` files into your Lich `scripts/` folder (or `scripts/custom/` for overrides).

**Repo:** https://github.com/loraque-git/lich-gsiv-scripts

## What’s here

### Original scripts (`scripts/`)

| Script | What it does |
|---|---|
| Combat clones (`rre`, `ssk`, `jab`, `kk`, …) | Aimed attack / UCS / maneuver wrappers with shared stance-dance blocks |
| `monks_keeper.lic` | Sympathy RT-gap Blink helper for monks |
| `gemstone_sagapanel.lic` | Saga panel for gemstone tracking |
| `alsohere.lic` / `alsohere_sagapanel.lic` | Who’s here helpers + Saga panel |
| `ret.lic` / `ttrs.lic` | Additional combat clones in the same family |

### Community patches (stock upstream stays untouched)

| File | What it does |
|---|---|
| `eloot-patched.lic` | eloot 2.12.1 fork: selectable loot stance (off…defensive), default path for Forward |
| `eloot-loot-stance.patch` | Unified diff to re-apply after an upstream `eloot.lic` upgrade |
| `eloot-loot-stance.README.md` | Install / re-apply / offer-upstream notes |
| `service_sales-patched.lic` | Local patch on peggyanne’s `service_sales` |
| `timers_sagapanel-patched.lic` | Local patch on RuseofFools’ `timers_sagapanel` |

Run patched scripts by their filename (e.g. `;eloot-patched`). Stock `;eloot` / `;repo` updates do not overwrite these.

### Saga log tools (`tools/saga-log-summary/`)

Python tools that turn Saga daily logs into JSON/HTML summaries (skins/furrier silver, Blink metrics, deaths, EBP, enhancive loadouts). See that folder’s scripts and run `serve.sh` for a local viewer.

## Quick install

```bash
git clone https://github.com/loraque-git/lich-gsiv-scripts.git
cp lich-gsiv-scripts/scripts/*.lic /path/to/Lich5/scripts/
# optional: only the eloot patch pack
cp lich-gsiv-scripts/scripts/eloot-patched.lic \
   lich-gsiv-scripts/scripts/eloot-loot-stance.patch \
   lich-gsiv-scripts/scripts/eloot-loot-stance.README.md \
   /path/to/Lich5/scripts/
```

For the eloot loot-stance patch specifically, read `scripts/eloot-loot-stance.README.md`.


## Auto-sync (this machine)

Live copies stay under `/home/loraque/Lich5/`. On this host they are mirrored into this repo automatically:

- **On-save:** user service `lich-gsiv-sync-watch.service` watches `Lich5/scripts` and `Lich5/tools/saga-log-summary`, debounces ~8s, then commits and pushes.
- **Nightly catch-all:** `lich-gsiv-sync.timer` runs at 03:45 local.

Manual run:

```bash
~/Projects/lich-gsiv-scripts/tools/sync-from-live.sh
systemctl --user status lich-gsiv-sync-watch.service lich-gsiv-sync.timer
```

What gets synced: every `Lich5/scripts` file that is **not** stock Elanthia-Online / Tillmen core (`go2`, `eloot`, `infomon`, `repository`, …), plus patched forks (`*-patched`, `eloot-loot-stance.*`) and `tools/saga-log-summary`. Backups (`*Copy*`, `*(1)*`, machine-specific `*HAL_5000*`) are skipped.

## Authorship

- Scripts signed `loraque` are originals from this account.
- `*-patched` files keep the upstream `author:` / `Author:` lines and list `loraque` under contributors where applicable.
- Do not confuse this repo with [elanthia-online/scripts](https://github.com/elanthia-online/scripts) (the community script tree).

## Not included

- The Lich engine itself
- Stock community scripts (`go2`, stock `eloot`, `infomon`, …)
- Character data, maps, databases, duplicate backups

## License / use

Free to use, copy, and adapt for GemStone / Lich. If you publish a derivative of a patched community script, keep the original author credit.
