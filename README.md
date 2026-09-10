# lich-gsiv-scripts

Personal GemStone IV Lich scripts signed `loraque`. Combat clones, `monks_keeper`, and `gemstone_sagapanel`.

This folder is the **git backup**. The copies Lich actually runs stay in `/home/loraque/Lich5/scripts/`. GitHub only updates when you commit and push from here.

## Why this is a separate repo

Omarchy/Grok desktop glue and Lich game scripts are different projects. Two GitHub repos means a mistake in one does not tangle the other.

Not backed up here (on purpose):

- The Lich engine (`/home/loraque/Lich5/lib`, `lich.rbw`)
- Community scripts (`go2`, `eloot`, `infomon`, …)
- Duplicate backups (`*Copy*`, `*(1)*`)
- Character data, maps, databases

## Typical loop

After we edit a live script under `Lich5/scripts/`:

```bash
cp /home/loraque/Lich5/scripts/rre.lic ~/Projects/lich-gsiv-scripts/scripts/
cd ~/Projects/lich-gsiv-scripts
git add scripts/rre.lic
git commit -m "rre: describe the change"
git push
```

Or copy every signed script at once:

```bash
# from this repo, after pulling live files in
git add -A
git status
git commit -m "Sync Lich scripts"
git push
```

Restore onto a machine that already has Lich:

```bash
cp ~/Projects/lich-gsiv-scripts/scripts/*.lic /home/loraque/Lich5/scripts/
```
