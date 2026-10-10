# saga-log-summary

Parse Saga client daily logs into JSON/HTML summaries (skins & furrier silver, Blink metrics, unique deaths, EBP, enhancive loadouts).

## Files

| File | Role |
|---|---|
| `saga_log_summary.py` | Main parser + HTML/JSON writer |
| `enhancive_loadout.py` | Enhancive inventory snapshots |
| `serve.sh` | Serve the summary HTML on `http://127.0.0.1:8765/` |

## Defaults (adjust paths for your machine)

- Saga logs: `~/.config/saga/logs/{Character} - Prime/`
- Summaries: `~/.config/saga/log-summaries/`

## Typical use

```bash
python3 saga_log_summary.py --all
python3 enhancive_loadout.py
./serve.sh
# open http://127.0.0.1:8765/index.html
```

Useful flags on `saga_log_summary.py`: `--date YYYY-MM-DD`, `--rebuild-index`.
