import threading
import time


class BanManager:
    def __init__(self, blocker, durations, audit_logger, slack_notifier):
        self.blocker = blocker
        self.durations = durations
        self.audit_logger = audit_logger
        self.slack_notifier = slack_notifier
        self.bans = {}
        self.strike_history = {}
        self.lock = threading.Lock()

    def ban(self, ip, result, timestamp, current_rate, baseline):
        with self.lock:
            record = self.bans.get(ip)
            if record:
                return {"action": "already_banned", **record}

            strike_count = self.strike_history.get(ip, 0) + 1
            duration_minutes = self._duration_for_strike(strike_count)
            expires_at = None if duration_minutes is None else time.time() + duration_minutes * 60

            if not self.blocker.block(ip):
                return {"action": "failed", "ip": ip}

            duration_label = "permanent" if duration_minutes is None else f"{duration_minutes}m"
            new_record = {
                "ip": ip,
                "condition": result["condition"],
                "strike_count": strike_count,
                "banned_at": timestamp,
                "expires_at": expires_at,
                "duration_label": duration_label,
                "duration_minutes": duration_minutes,
                "last_rate": current_rate,
                "baseline_mean": baseline["mean"],
            }
            self.bans[ip] = new_record
            self.strike_history[ip] = strike_count
            return {"action": "banned", **new_record}

    def release_due(self):
        now = time.time()
        released = []

        with self.lock:
            for ip, record in list(self.bans.items()):
                if record["expires_at"] is None or record["expires_at"] > now:
                    continue

                if self.blocker.unblock(ip):
                    released.append(record)
                    self.slack_notifier.send_unban_alert(record)
                    del self.bans[ip]

        return released

    def snapshot(self):
        with self.lock:
            ordered = sorted(
                self.bans.values(),
                key=lambda item: (item["expires_at"] is None, item["expires_at"] or 0),
            )
            return [
                {
                    "ip": item["ip"],
                    "condition": item["condition"],
                    "strike_count": item["strike_count"],
                    "duration": item["duration_label"],
                    "banned_at": item["banned_at"],
                    "expires_at": item["expires_at"],
                }
                for item in ordered
            ]

    def _duration_for_strike(self, strike_count):
        if strike_count > len(self.durations):
            return None
        return self.durations[strike_count - 1]
