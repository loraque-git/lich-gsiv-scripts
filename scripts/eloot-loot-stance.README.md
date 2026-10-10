# eloot loot_stance community patch

Selectable loot stance (Off / Offensive / Advanced / Forward / Neutral / Guarded / Defensive) instead of the boolean **Loot in defensive** checkbox.

## Local files

| File | Role |
|---|---|
| `eloot.lic` | Stock upstream (safe to `;repo` / `;lich5-update` refresh) |
| `eloot-patched.lic` | Patched copy you run day-to-day |
| `eloot-loot-stance.patch` | Unified diff to re-apply after an `eloot.lic` upgrade |
| `eloot-loot-stance.README.md` | This note |

Settings still live in `Data/<game>/<Char>/eloot.yaml` (shared with stock eloot).

## Use

```
;eloot-patched setup
;eloot-patched settings loot_stance forward
;eloot-patched settings help
```

Set the Looting-tab dropdown to **Loot stance: Forward**, or use the CLI above.

If you previously had **Loot in defensive** checked, the first load migrates that to `loot_stance: defensive`.

## Re-apply after upstream eloot update

```bash
cd ~/Lich5/scripts
# refresh stock eloot.lic however you normally do, then:
cp -a eloot.lic eloot-patched.lic
patch -p0 < eloot-loot-stance.patch
# if the patch fails (upstream moved lines), re-merge by hand using the
# "# BEGIN loot_stance patch" / "# END loot_stance patch" markers
ruby -c eloot-patched.lic
```

Regenerate the patch after a successful manual merge:

```bash
diff -u eloot.lic eloot-patched.lic > eloot-loot-stance.patch
```

## Offer upstream

Open a PR against [elanthia-online/scripts](https://github.com/elanthia-online/scripts) `scripts/eloot.lic`:

1. Start from current upstream `eloot.lic`.
2. Apply `eloot-loot-stance.patch` (or cherry-pick the marked hunks).
3. Drop the `name: eloot-patched` header line and rename the changelog from “community patch” to a normal `v2.12.1` feature entry.
4. Keep `contributors: …, loraque`.

## Behaviour notes

- `loot_stance: off` — leave current stance alone (old checkbox unchecked).
- Any other value — `stance <name>` via `change_stance(percent, exact: true)` so Forward still applies when you were already Guarded/Defensive.
- Legacy `loot_defensive` remains in the YAML for migration only; the UI and loot paths read `loot_stance`.
