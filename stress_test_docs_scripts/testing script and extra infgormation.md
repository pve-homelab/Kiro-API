I've written the script (kiro_env_report.py, one file, Python standard library only) and the rewritten rationale (rationale_template.md). Every row in the script's table is labelled with the {{PLACEHOLDER}} used in the rationale, and I checked that the two match. I could only test it against a simulated environment (a fake service, kiro-cli, pi and git repo), not your real one.

### How to use it
```
bash
# Before the test: sanity check, on the service host (and the pi host if different)
python3 kiro_env_report.py

# After the test: collect everything and fill the rationale automatically
python3 kiro_env_report.py --run-dir ~/kiro-stress/run-<timestamp> \
  --prepared-by "Name, Role" --reviewed-by "Pending" --status "Draft" \
  --fill rationale_template.md
```

The filled document is written to <run-dir>/baseline/rationale_template.filled.md. If you'd rather type values in yourself, skip --fill and copy them from the table.

### What it does
- Read-only: it only runs version queries, kiro-api config, git log/status and systemctl show, reads /proc, and sends GET requests to /health, /ready, /stats and /v1/models. It never restarts anything and never records secrets. In my test the auth key appeared nowhere in the output, and it's reported only as "Yes" or "No".
- Detects the real running config: it reads the running service's flags and environment, so a --workers 120 flag beats whatever kiro-api config shows from your shell. Each row has a Source column so you can see where a value came from.
- Marks how sure each value is: ✔ detected, ≈ documented default (unconfirmed), ✎ enter manually, ✖ not found. --fill only inserts ✔ values, so an unconfirmed default can't slip silently into a report. Add --accept-defaults to allow them.
- Two hosts: if pi runs on a different machine from the service, run the script on both and add --merge service_host/run_metadata.json on the second. It fills gaps from the other file and builds a combined topology line.

### Limits to know about
- Guessed field names: the script guesses key names in kiro-api config output and /stats, since the docs don't specify them. If a guess is wrong, that row shows as an unconfirmed default or not found, never a wrong value. Please check the first real run and tell me what shows up.
- Test window: it comes from the t field in data/timeseries.jsonl, which matches the data contract in the stress-test prompt. Otherwise pass --start and --end.
- Optional prompt tweak: so the orchestrator captures the baseline itself, replace step (g) in Phase 0 of the stress-test prompt with:
```
text
g) If ~/kiro_env_report.py exists, run `python3 ~/kiro_env_report.py --run-dir <RUN_DIR> --no-color` (read-only) and keep its output in baseline/. Otherwise write baseline/run_metadata.md by hand.
```


By default the report goes to ~/kiro-stress/run-<UTC timestamp>/report/report.html on the machine where pi's commands run. The prompt sets the run folder, and the orchestrator creates one folder per run, so nothing is overwritten.

```
text
~/kiro-stress/run-<UTC timestamp>/
├── report/
│   ├── report.html      <- the executive report (self-contained, open in any browser)
│   ├── report.pdf       <- only if chromium or wkhtmltopdf is installed
│   ├── report.md        <- one-page summary
│   ├── metrics.json     <- every computed number the report uses
│   └── charts/*.svg
├── data/                <- raw JSONL: agents, requests, timeseries, canary, burst, events, logs/
├── baseline/            <- environment snapshots; kiro_env_report.py output if run with --run-dir
├── rehearsal/           <- archive of the 5-agent dry run
├── agents/<agent_id>/   <- each agent's private working folder
└── STATE.md, DECISIONS.md, orchestrator_incidents.md, narrative.json
```

- What the prompt pins down: it fixes report/report.html and data/ exactly. For report.md, metrics.json and charts/, it lists them without stating their folder. I intended them inside report/, but the orchestrator may place them slightly differently, so check once.
- ~ is the home of whoever runs the commands. That is normally the account running pi. If Phase 0e finds that tool calls actually execute server-side inside kiro-cli, the folder could land under the service account's home instead. The orchestrator's final message must list the absolute paths, and you can find the folder any time with ls -d ~/kiro-stress/run-*.
- Copying it out: the HTML is self-contained, so one file is enough, for example scp user@host:~/kiro-stress/run-*/report/report.html .. Copy the whole run folder if you want the raw data too.

To pin a fixed location, change the first part of RULE 2 in the prompt to an absolute path:
```
text
2. Work only under RUN_DIR=/srv/kiro-stress/run-<UTC timestamp>/ (create it if missing). ...
```
To make the newest run easy to find, add this to the end of Phase 0:
```
text
h) After creating RUN_DIR, run: ln -sfn RUN_DIR ~/kiro-stress/latest
```
kiro_env_report.py auto-detects the newest ~/kiro-stress/run-* folder anyway. Writing to a folder other than ~/kiro-stress/ means you'll need to pass --run-dir explicitly.