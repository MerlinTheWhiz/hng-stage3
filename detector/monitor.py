import json
import logging
import os
import queue
import threading
import time
from datetime import datetime


class LogMonitor:
    def __init__(self, log_path, stop_event):
        self.log_path = log_path
        self.stop_event = stop_event
        self.events = queue.Queue(maxsize=10000)
        self.thread = threading.Thread(target=self._follow, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=2)

    def get_event(self, timeout=1.0):
        try:
            return self.events.get(timeout=timeout)
        except queue.Empty:
            return None

    def _follow(self):
        while not self.stop_event.is_set() and not os.path.exists(self.log_path):
            logging.info("waiting for access log at %s", self.log_path)
            time.sleep(1)

        if self.stop_event.is_set():
            return

        with open(self.log_path, "r", encoding="utf-8") as handle:
            handle.seek(0, os.SEEK_END)

            while not self.stop_event.is_set():
                line = handle.readline()
                if not line:
                    time.sleep(0.2)
                    continue

                event = self._parse_line(line)
                if event is not None:
                    try:
                        self.events.put(event, timeout=1)
                    except queue.Full:
                        logging.warning("dropping log event because queue is full")

    def _parse_line(self, line):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            logging.debug("skipping malformed JSON log line")
            return None

        source_ip = payload.get("source_ip", "").split(",")[0].strip() or "unknown"
        timestamp = payload.get("timestamp")

        try:
            epoch = int(datetime.fromisoformat(timestamp).timestamp())
        except (TypeError, ValueError):
            epoch = int(time.time())

        return {
            "source_ip": source_ip,
            "timestamp": timestamp or datetime.utcnow().isoformat(),
            "epoch": epoch,
            "method": payload.get("method", "UNKNOWN"),
            "path": payload.get("path", "/"),
            "status": int(payload.get("status", 0)),
            "response_size": int(payload.get("response_size", 0)),
        }
