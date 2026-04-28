class DetectionEngine:
    def __init__(self, config):
        self.ip_zscore = config["ip_zscore"]
        self.global_zscore = config["global_zscore"]
        self.spike_multiplier = config["spike_multiplier"]
        self.error_multiplier = config["error_rate_multiplier"]
        self.tightened_zscore = config["tightened_ip_zscore"]
        self.tightened_multiplier = config["tightened_spike_multiplier"]

    def evaluate_global(self, current_rate, baseline):
        return self._evaluate(
            current_rate,
            baseline,
            zscore_threshold=self.global_zscore,
            spike_multiplier=self.spike_multiplier,
        )

    def evaluate_ip(self, current_rate, baseline, error_surge=False):
        # Tighten the per-IP thresholds whenever the current error ratio is
        # materially above that IP's learned baseline error ratio.
        zscore_threshold = self.tightened_zscore if error_surge else self.ip_zscore
        spike_multiplier = (
            self.tightened_multiplier if error_surge else self.spike_multiplier
        )
        result = self._evaluate(
            current_rate,
            baseline,
            zscore_threshold=zscore_threshold,
            spike_multiplier=spike_multiplier,
        )
        if result["fired"] and error_surge:
            result["condition"] = f"{result['condition']} + error surge"
        return result

    def error_surge(self, current_error_ratio, baseline):
        baseline_mean = max(baseline["mean"], 0.001)
        return current_error_ratio >= baseline_mean * self.error_multiplier

    def _evaluate(self, current_rate, baseline, zscore_threshold, spike_multiplier):
        # A request rate is anomalous as soon as either threshold fires:
        # z-score over baseline or a simple mean-multiplier spike.
        baseline_mean = max(baseline["mean"], 0.01)
        baseline_stddev = max(baseline["stddev"], 0.01)
        zscore = (current_rate - baseline_mean) / baseline_stddev

        if zscore > zscore_threshold:
            return {
                "fired": True,
                "condition": f"z-score>{zscore_threshold:.1f}",
                "zscore": zscore,
            }

        if current_rate > baseline_mean * spike_multiplier:
            return {
                "fired": True,
                "condition": f"rate>{spike_multiplier:.1f}x_mean",
                "zscore": zscore,
            }

        return {"fired": False, "condition": "", "zscore": zscore}
