# Operations

## MacBook validation

From the project directory, with the existing private `config.json`:

```bash
python3 zepp_health.py sync-db --days 30
python3 zepp_health.py db-status
python3 zepp_health.py daily-status --days 14 --from-db
python3 zepp_health.py sync-db --days 30
python3 zepp_health.py sync-db --days 30 --json
python3 zepp_health.py db-status --json
python3 zepp_health.py daily-status --days 14 --from-db --json
python3 zepp_health.py db-check --json
python3 zepp_health.py db-backup --output backups/zepp_health_$(date +%Y-%m-%d).db --json
python3 zepp_health.py db-restore --input backups/zepp_health_YYYY-MM-DD.db --db restore-test/zepp_health.db --json
```

The second synchronization is expected to report unchanged records and no
duplicate logical rows. A failed domain is recorded while other domains remain
committed. JSON output contains provenance and counts, never credentials.

## Ubuntu deployment

The project remains the code source and the database remains runtime state:

```bash
cd /opt/zepp-health-cli
git pull origin main
sudo install -d -o "$(id -un)" -g "$(id -gn)" -m 700 /opt/zepp-health-cli/data /opt/zepp-health-cli/backups
python3 -m venv .venv  # only for initial setup, if absent
. .venv/bin/activate
python3 zepp_health.py sync-db --days 30 --db /opt/zepp-health-cli/data/zepp_health.db
python3 zepp_health.py db-status --db /opt/zepp-health-cli/data/zepp_health.db
python3 zepp_health.py db-check --db /opt/zepp-health-cli/data/zepp_health.db
python3 zepp_health.py db-backup --db /opt/zepp-health-cli/data/zepp_health.db --output /opt/zepp-health-cli/backups/zepp_health_$(date +%Y-%m-%d).db
python3 zepp_health.py db-restore --input /opt/zepp-health-cli/backups/zepp_health_YYYY-MM-DD.db --db /opt/zepp-health-cli/restore-test/zepp_health.db
```

The `sudo install` command is one-time directory setup; normal synchronization
and checks run as the runtime user. Do not expose the database through a web
server or network port. Remove a test restore only after checking its exact
path, for example `rm -f /opt/zepp-health-cli/restore-test/zepp_health.db`.

## Backup policy and troubleshooting

Keep daily backups for 7 days, weekly backups for 4 weeks, and monthly backups
for 6 months. This project does not schedule or delete backups automatically.
Use timestamped output and keep the files private. If `db-check` reports a
failure, stop synchronization, preserve the database and WAL sidecars, and
copy the concise error plus `db-status --json` (with sensitive values removed)
for diagnosis. A missing LifeLoad or sleep domain is an API availability result,
not a locally calculated substitute.
