# HNG Stage 3: Nextcloud Anomaly Detection Engine

This project deploys the provided `kefaslungu/hng-nextcloud` image behind Nginx and runs a custom Python daemon that watches JSON access logs in real time, learns a rolling baseline, detects anomalies, blocks abusive IPs with `iptables`, and exposes a live dashboard.

## Live Submission Fields

- Server IP: `YOUR_SERVER_IP`
- Metrics dashboard URL: `http://YOUR_DOMAIN_OR_SUBDOMAIN:8081`
- Public GitHub repo: `YOUR_PUBLIC_REPO_URL`
- Blog post: `YOUR_BLOG_POST_URL`

## Why Python

Python keeps the log-processing, sliding-window bookkeeping, baseline math, Slack notification flow, and small web dashboard in one readable codebase. For this task, iteration speed and clarity mattered more than raw throughput.

## Architecture

- `nginx/` terminates HTTP traffic, proxies to the provided Nextcloud container, and writes JSON access logs to `/var/log/nginx/hng-access.log`.
- The named Docker volume `HNG-nginx-logs` is mounted read-only into both Nextcloud and the detector.
- `detector/main.py` runs the daemon loop, coordinates baselines and detection, and keeps dashboard state current.
- `detector/dashboard.py` serves live metrics on port `8081`.
- `detector/blocker.py` manages `iptables` DROP rules.
- `detector/unbanner.py` manages ban durations and automatic releases.
- `detector/notifier.py` sends Slack alerts and writes audit log entries.

## Sliding Window Design

The detector keeps two deque-based 60-second windows:

- One global deque for all request timestamps.
- One per-IP deque keyed by source IP.

Every new request appends the current event timestamp. Old timestamps are evicted from the left while they are 60 seconds or older. That keeps each deque as an exact view of requests seen in the last 60 seconds without using any rate-limiting libraries or fake minute buckets.

## Baseline Design

- Baseline source data is stored as per-second request counts for the last 30 minutes.
- Recalculation runs every 60 seconds.
- Hourly slots are tracked separately so the detector can prefer the current hour when it has enough samples.
- Floor values are configurable in `detector/config.yaml`:
  - `minimum_mean: 1.0`
  - `minimum_stddev: 0.5`

The detector uses the current hour slot first once it has at least `hour_slot_min_samples`. Otherwise, it falls back to the rolling 30-minute window.

## Detection Logic

- Global anomaly: fire when z-score exceeds `3.0` or current rate is over `5x` the baseline mean.
- Per-IP anomaly: same logic, with tighter thresholds automatically applied when the IP's 4xx/5xx rate is at least `3x` its baseline error rate.
- Per-IP anomaly response: add an `iptables` DROP rule, write an audit entry, and send a Slack ban alert.
- Global anomaly response: Slack alert only.

## Ban Lifecycle

Ban durations are configured in `detector/config.yaml`:

1. 10 minutes
2. 30 minutes
3. 120 minutes
4. Permanent on the next offense after those three durations

Every automatic unban sends a Slack notification and writes an audit log entry.

## Dashboard

The live dashboard refreshes every 3 seconds and shows:

- Active banned IPs
- Global request rate over the last 60 seconds
- Top 10 source IPs
- CPU and memory usage
- Effective baseline mean and stddev
- Uptime

## Setup From a Fresh VPS

1. Install Docker and Docker Compose plugin.
2. Clone this repository.
3. Create a local `.env` file and set your Slack webhook there.

```bash
cp .env.example .env
```

Then edit `.env`:

```env
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

4. Start the stack:

```bash
docker compose up --build -d
```

5. Confirm services:

```bash
docker compose ps
docker compose logs -f detector
```

6. Verify:
   - Nextcloud is reachable on the server IP through Nginx.
   - The dashboard is reachable on `http://SERVER_IP:8081` or your mapped domain/subdomain.
   - The detector is writing `detector/audit.log`.

## Required Submission Artifacts

Capture and add these files under `screenshots/` before submitting:

1. `Tool-running.png`
2. `Ban-slack.png`
3. `Unban-slack.png`
4. `Global-alert-slack.png`
5. `Iptables-banned.png`
6. `Audit-log.png`
7. `Baseline-graph.png`

Add your architecture diagram to `docs/architecture.png`.

## Important Notes

- The Nextcloud image was not modified or replaced.
- All thresholds are kept in `detector/config.yaml`.
- The detector is a continuous daemon, not a cron job.
- `Fail2Ban` and rate-limiting libraries are not used.
