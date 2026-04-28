import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests


class SlackNotifier:
    def __init__(self, webhook_url):
        self.webhook_url = webhook_url

    def send_ban_alert(self, record):
        self._post(
            {
                "text": (
                    f"BAN {record['ip']} | {record['condition']} | "
                    f"rate={record['last_rate']} | baseline={record['baseline_mean']:.2f} | "
                    f"duration={record['duration_label']} | ts={record['banned_at']}"
                )
            }
        )

    def send_unban_alert(self, record):
        self._post(
            {
                "text": (
                    f"UNBAN {record['ip']} | {record['condition']} | "
                    f"rate={record['last_rate']} | baseline={record['baseline_mean']:.2f} | "
                    f"duration={record['duration_label']} | ts={self._timestamp()}"
                )
            }
        )

    def send_global_alert(self, record):
        self._post(
            {
                "text": (
                    f"GLOBAL ALERT | {record['condition']} | "
                    f"rate={record['rate']} | baseline={record['baseline_mean']:.2f} "
                    f"(stddev={record['baseline_stddev']:.2f}) | ts={record['timestamp']}"
                )
            }
        )

    def _post(self, payload):
        if not self.webhook_url:
            logging.info("slack webhook not configured; payload=%s", payload["text"])
            return

        try:
            response = requests.post(self.webhook_url, json=payload, timeout=5)
            response.raise_for_status()
        except requests.RequestException as exc:
            logging.warning("failed to send Slack alert: %s", exc)

    def _timestamp(self):
        return datetime.now(timezone.utc).isoformat()


class AuditLogger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, action, ip, condition, rate, baseline, duration):
        timestamp = datetime.now(timezone.utc).isoformat()
        line = (
            f"[{timestamp}] {action} {ip} | {condition} | "
            f"{rate} | {baseline} | {duration}\n"
        )
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line)
        logging.info(line.strip())

    def write_json(self, payload):
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")
