import math
import time
from collections import defaultdict, deque
from datetime import datetime


class RollingBaseline:
    def __init__(self, config):
        self.window_seconds = config["window_seconds"]
        self.minimum_mean = config["minimum_mean"]
        self.minimum_stddev = config["minimum_stddev"]
        self.hour_slot_min_samples = config["hour_slot_min_samples"]
        self.window = deque(maxlen=self.window_seconds)
        self.hour_slots = defaultdict(lambda: deque(maxlen=self.window_seconds))
        self.last_stats = self._stats(())

    def add_second(self, timestamp, count):
        second = int(timestamp)
        hour_key = datetime.utcfromtimestamp(second).strftime("%H")
        self.window.append(count)
        self.hour_slots[hour_key].append(count)

    def effective_stats(self, timestamp=None):
        if timestamp is None:
            timestamp = time.time()

        hour_key = datetime.utcfromtimestamp(int(timestamp)).strftime("%H")
        current_hour = self.hour_slots[hour_key]
        if len(current_hour) >= self.hour_slot_min_samples:
            stats = self._stats(current_hour)
            stats["source"] = "current_hour_slot"
            return stats

        stats = self._stats(self.window)
        stats["source"] = "rolling_30m"
        return stats

    def recalculate(self, timestamp=None):
        self.last_stats = self.effective_stats(timestamp)
        return self.last_stats

    def _stats(self, samples):
        sample_list = list(samples)
        if not sample_list:
            return {
                "mean": self.minimum_mean,
                "stddev": self.minimum_stddev,
                "sample_count": 0,
                "source": "floor",
            }

        sample_count = len(sample_list)
        mean_value = sum(sample_list) / sample_count

        if sample_count > 1:
            variance = sum((value - mean_value) ** 2 for value in sample_list) / (
                sample_count - 1
            )
            stddev_value = math.sqrt(variance)
        else:
            stddev_value = self.minimum_stddev

        return {
            "mean": max(mean_value, self.minimum_mean),
            "stddev": max(stddev_value, self.minimum_stddev),
            "sample_count": sample_count,
            "source": "computed",
        }
