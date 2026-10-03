# Internship alert operation

`internship_monitor.py --dry-run` remains read-only. `internship_alerts.py` has three explicit modes:

- `--dry-run`: evaluates alert state without sending or saving it.
- `--baseline`: creates `internship_alert_state.json`, records every current job from successful supported sources, and sends zero messages. It refuses to replace an existing state file.
- `--production`: requires an existing baseline and `INTERNSHIP_DISCORD_WEBHOOK_URL`. It sends only newly discovered, alertable jobs and retries failed deliveries.

The GitHub workflow is **manual only**. Run it once with `mode=baseline`, verify that the state commit succeeded and the job reported zero sends, then run with `mode=production`. Add an hourly `schedule` trigger only after reviewing both manual runs. Keep the workflow concurrency group and repository `contents: write` permission.

The state contains public job metadata and anonymous profile IDs only. Keep the file in Git so Actions runs share history. The workflow commits only if the state changed, checks whether remote alert state changed during the run, rebases onto the latest `main`, and pushes. A state save or push failure makes the workflow fail visibly. Provider failures also make the workflow fail after successful jobs have been processed and state has been saved.

Delivery is at least once across the Discord/Git boundary: if Discord accepts a message but the process or Git push fails before the alerted state reaches `main`, that job can be sent again. Discord webhooks have no transactional commit with Git. Inspect the failed run and reconcile its local state with the repository before rerunning production. Do not delete or regenerate the baseline to recover from a failure.

Only sources marked `monitoring_ready` with a supported adapter are fetched. The report still lists unavailable and unimplemented coverage separately.
