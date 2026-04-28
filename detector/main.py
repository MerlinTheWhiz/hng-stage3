import logging
import os
import signal
import threading
import time
from collections import Counter, defaultdict, deque
from pathlib import Path

import yaml

from baseline import RollingBaseline
from blocker import IptablesBlocker
from dashboard import DashboardState, start_dashboard
from detector import DetectionEngine
from monitor import LogMonitor
from notifier import AuditLogger, SlackNotifier
from unbanner import BanManager


BASE_DIR = Path(__file__).resolve().parent


def load_config(path="config.yaml"):
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = BASE_DIR / config_path

    with open(config_path, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)

    config["slack"]["webhook_url"] = os.getenv(
        "SLACK_WEBHOOK_URL",
        config["slack"].get("webhook_url", ""),
    )

    for key in ("audit_log",):
        path_value = Path(config["paths"][key])
        if not path_value.is_absolute():
            config["paths"][key] = str((BASE_DIR / path_value).resolve())

    return config


def configure_logging(level_name):
    logging.basicConfig(
        level=getattr(logging, level_name.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def flush_second(
    second_ts,
    second_total,
    second_ip_counts,
    second_ip_errors,
    global_baseline,
    error_baselines,
):
    global_baseline.add_second(second_ts, second_total)

    for ip, count in second_ip_counts.items():
        error_baselines["ip_rates"][ip].add_second(second_ts, count)

    for ip, count in second_ip_errors.items():
        error_baselines["ip_errors"][ip].add_second(second_ts, count)

    error_baselines["global_errors"].add_second(
        second_ts,
        sum(second_ip_errors.values()),
    )


def process_event(
    event,
    runtime,
    thresholds,
    dashboard_state,
    detection_engine,
    ban_manager,
    slack_notifier,
    audit_logger,
):
    event_ts = event["epoch"]
    ip = event["source_ip"]
    status = event["status"]

    while runtime["current_second"] < event_ts:
        flush_second(
            runtime["current_second"],
            runtime["second_total"],
            runtime["second_ip_counts"],
            runtime["second_ip_errors"],
            runtime["global_baseline"],
            runtime["error_baselines"],
        )
        runtime["current_second"] += 1
        runtime["second_total"] = 0
        runtime["second_ip_counts"] = Counter()
        runtime["second_ip_errors"] = Counter()

    runtime["second_total"] += 1
    runtime["second_ip_counts"][ip] += 1
    if status >= 400:
        runtime["second_ip_errors"][ip] += 1

    runtime["global_window"].append(event_ts)
    while runtime["global_window"] and event_ts - runtime["global_window"][0] >= 60:
        runtime["global_window"].popleft()

    ip_window = runtime["ip_windows"][ip]
    ip_window.append(event_ts)
    while ip_window and event_ts - ip_window[0] >= 60:
        ip_window.popleft()

    if status >= 400:
        error_window = runtime["ip_error_windows"][ip]
        error_window.append(event_ts)
        while error_window and event_ts - error_window[0] >= 60:
            error_window.popleft()

    global_rate = len(runtime["global_window"]) / 60.0
    global_stats = runtime["global_baseline"].effective_stats(event_ts)
    global_result = detection_engine.evaluate_global(global_rate, global_stats)

    if global_result["fired"]:
        global_key = (
            "global",
            int(event_ts / thresholds["global_alert_cooldown_seconds"]),
            global_result["condition"],
        )
        if global_key not in runtime["recent_global_alerts"]:
            runtime["recent_global_alerts"].add(global_key)
            runtime["last_global_alert"] = {
                "condition": global_result["condition"],
                "rate": global_rate,
                "baseline_mean": global_stats["mean"],
                "baseline_stddev": global_stats["stddev"],
                "timestamp": event["timestamp"],
            }
            slack_notifier.send_global_alert(runtime["last_global_alert"])

    ip_rate = len(ip_window) / 60.0
    ip_stats = runtime["error_baselines"]["ip_rates"][ip].effective_stats(event_ts)
    error_stats = runtime["error_baselines"]["ip_errors"][ip].effective_stats(event_ts)
    error_rate = len(runtime["ip_error_windows"][ip]) / 60.0
    error_surge = detection_engine.error_surge(error_rate, error_stats)
    ip_result = detection_engine.evaluate_ip(ip_rate, ip_stats, error_surge)

    if ip_result["fired"]:
        ban_record = ban_manager.ban(ip, ip_result, event["timestamp"], ip_rate, ip_stats)
        if ban_record["action"] == "banned":
            slack_notifier.send_ban_alert(ban_record)
            audit_logger.write(
                "BAN",
                ip,
                ip_result["condition"],
                ip_rate,
                ip_stats["mean"],
                ban_record["duration_label"],
            )

    dashboard_state.update_runtime(
        global_rate=global_rate,
        top_ips=current_top_ips(runtime),
        effective_mean=global_stats["mean"],
        effective_stddev=global_stats["stddev"],
        active_bans=ban_manager.snapshot(),
        last_baseline=runtime["last_baseline_recalc"],
        last_global_alert=runtime["last_global_alert"],
    )


def recalculate_baselines(runtime, audit_logger):
    timestamp = int(time.time())
    global_stats = runtime["global_baseline"].recalculate(timestamp)
    runtime["last_baseline_recalc"] = {
        "timestamp": timestamp,
        "mean": global_stats["mean"],
        "stddev": global_stats["stddev"],
        "sample_count": global_stats["sample_count"],
        "source": global_stats["source"],
    }

    audit_logger.write(
        "BASELINE_RECALC",
        "global",
        global_stats["source"],
        global_stats["mean"],
        global_stats["stddev"],
        f"samples={global_stats['sample_count']}",
    )


def prune_windows(runtime, now_epoch):
    while runtime["global_window"] and now_epoch - runtime["global_window"][0] >= 60:
        runtime["global_window"].popleft()

    for ip in list(runtime["ip_windows"].keys()):
        window = runtime["ip_windows"][ip]
        while window and now_epoch - window[0] >= 60:
            window.popleft()
        if not window:
            del runtime["ip_windows"][ip]

    for ip in list(runtime["ip_error_windows"].keys()):
        window = runtime["ip_error_windows"][ip]
        while window and now_epoch - window[0] >= 60:
            window.popleft()
        if not window:
            del runtime["ip_error_windows"][ip]


def current_top_ips(runtime):
    pairs = sorted(
        (
            (ip, round(len(window) / 60.0, 2))
            for ip, window in runtime["ip_windows"].items()
            if window
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    return pairs[:10]


def main():
    config = load_config()
    configure_logging(config["app"]["log_level"])

    stop_event = threading.Event()

    def handle_signal(signum, _frame):
        logging.info("received signal %s, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    slack_notifier = SlackNotifier(config["slack"]["webhook_url"])
    audit_logger = AuditLogger(config["paths"]["audit_log"])
    blocker = IptablesBlocker(
        chain=config["blocking"]["chain"],
        enabled=config["blocking"]["enabled"],
    )
    ban_manager = BanManager(
        blocker=blocker,
        durations=config["blocking"]["durations_minutes"],
        audit_logger=audit_logger,
        slack_notifier=slack_notifier,
    )
    detection_engine = DetectionEngine(config["thresholds"])
    dashboard_state = DashboardState()
    start_dashboard(dashboard_state, config["dashboard"])

    runtime = {
        "current_second": int(time.time()),
        "second_total": 0,
        "second_ip_counts": Counter(),
        "second_ip_errors": Counter(),
        "global_window": deque(),
        "ip_windows": defaultdict(deque),
        "ip_error_windows": defaultdict(deque),
        "recent_global_alerts": set(),
        "last_global_alert": None,
        "last_baseline_recalc": None,
        "global_baseline": RollingBaseline(config["baseline"]),
        "error_baselines": {
            "global_errors": RollingBaseline(config["baseline"]),
            "ip_rates": defaultdict(lambda: RollingBaseline(config["baseline"])),
            "ip_errors": defaultdict(lambda: RollingBaseline(config["baseline"])),
        },
    }

    monitor = LogMonitor(config["paths"]["access_log"], stop_event)
    monitor.start()
    logging.info("detector daemon started")

    baseline_interval = config["baseline"]["recalculation_interval_seconds"]
    next_recalc = time.time() + baseline_interval

    while not stop_event.is_set():
        event = monitor.get_event(timeout=1.0)

        if event is not None:
            process_event(
                event,
                runtime,
                config["thresholds"],
                dashboard_state,
                detection_engine,
                ban_manager,
                slack_notifier,
                audit_logger,
            )

        now = time.time()
        current_epoch = int(now)

        while runtime["current_second"] < current_epoch:
            flush_second(
                runtime["current_second"],
                runtime["second_total"],
                runtime["second_ip_counts"],
                runtime["second_ip_errors"],
                runtime["global_baseline"],
                runtime["error_baselines"],
            )
            runtime["current_second"] += 1
            runtime["second_total"] = 0
            runtime["second_ip_counts"] = Counter()
            runtime["second_ip_errors"] = Counter()

        prune_windows(runtime, current_epoch)

        if now >= next_recalc:
            recalculate_baselines(runtime, audit_logger)
            next_recalc = now + baseline_interval

        for unbanned in ban_manager.release_due():
            audit_logger.write(
                "UNBAN",
                unbanned["ip"],
                unbanned["condition"],
                unbanned["last_rate"],
                unbanned["baseline_mean"],
                "released",
            )
            dashboard_state.update_runtime(active_bans=ban_manager.snapshot())

        global_stats = runtime["global_baseline"].effective_stats(current_epoch)
        dashboard_state.update_runtime(
            global_rate=len(runtime["global_window"]) / 60.0,
            top_ips=current_top_ips(runtime),
            effective_mean=global_stats["mean"],
            effective_stddev=global_stats["stddev"],
            active_bans=ban_manager.snapshot(),
            last_baseline=runtime["last_baseline_recalc"],
            last_global_alert=runtime["last_global_alert"],
        )

    monitor.stop()
    logging.info("detector daemon stopped")


if __name__ == "__main__":
    main()
