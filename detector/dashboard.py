import threading
import time
from datetime import datetime

import psutil
from flask import Flask, jsonify, render_template_string


HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HNG Detector Metrics</title>
  <style>
    :root {
      --bg: #f4efe7;
      --card: rgba(255,255,255,0.82);
      --ink: #1e1b18;
      --muted: #6d6257;
      --accent: #c45c2e;
      --line: rgba(30,27,24,0.12);
    }
    body {
      margin: 0;
      font-family: "Trebuchet MS", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(196,92,46,0.18), transparent 30%),
        linear-gradient(135deg, #f8f3ed, #efe5d6 55%, #e7dbc8);
    }
    .wrap {
      max-width: 1120px;
      margin: 0 auto;
      padding: 24px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 18px;
      box-shadow: 0 18px 30px rgba(76, 52, 31, 0.08);
      backdrop-filter: blur(6px);
    }
    h1, h2 { margin: 0 0 10px; }
    h1 { font-size: 2rem; }
    h2 { font-size: 1rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }
    .value { font-size: 2rem; font-weight: 700; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 10px 0; border-bottom: 1px solid var(--line); }
    .pill {
      display: inline-block;
      padding: 5px 10px;
      border-radius: 999px;
      background: rgba(196,92,46,0.12);
      color: var(--accent);
      font-size: 0.85rem;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>HNG Traffic Detector</h1>
    <p>Auto-refreshing every 3 seconds.</p>
    <div class="grid">
      <div class="card"><h2>Global Req/s</h2><div class="value" id="global_rate">0</div></div>
      <div class="card"><h2>Effective Mean</h2><div class="value" id="effective_mean">0</div></div>
      <div class="card"><h2>Effective Stddev</h2><div class="value" id="effective_stddev">0</div></div>
      <div class="card"><h2>CPU / Memory</h2><div class="value" id="resources">0 / 0</div></div>
      <div class="card"><h2>Uptime</h2><div class="value" id="uptime">0s</div></div>
      <div class="card"><h2>Banned IPs</h2><div class="value" id="ban_count">0</div></div>
    </div>
    <div class="grid" style="margin-top: 16px;">
      <div class="card">
        <h2>Top 10 Source IPs</h2>
        <table><tbody id="top_ips"></tbody></table>
      </div>
      <div class="card">
        <h2>Active Bans</h2>
        <table><tbody id="bans"></tbody></table>
      </div>
    </div>
  </div>
  <script>
    function fmtUnix(epoch) {
      if (!epoch) return "permanent";
      return new Date(epoch * 1000).toLocaleString();
    }
    function load() {
      fetch("/api/metrics")
        .then((r) => r.json())
        .then((data) => {
          document.getElementById("global_rate").textContent = data.global_rate.toFixed(2);
          document.getElementById("effective_mean").textContent = data.effective_mean.toFixed(2);
          document.getElementById("effective_stddev").textContent = data.effective_stddev.toFixed(2);
          document.getElementById("resources").textContent = data.cpu_percent.toFixed(1) + "% / " + data.memory_percent.toFixed(1) + "%";
          document.getElementById("uptime").textContent = data.uptime;
          document.getElementById("ban_count").textContent = data.active_bans.length;
          document.getElementById("top_ips").innerHTML = data.top_ips.map((item) => "<tr><td>" + item[0] + "</td><td>" + item[1] + "</td></tr>").join("");
          document.getElementById("bans").innerHTML = data.active_bans.map((item) => "<tr><td>" + item.ip + "</td><td><span class='pill'>" + item.duration + "</span></td><td>" + fmtUnix(item.expires_at) + "</td></tr>").join("") || "<tr><td colspan='3'>No active bans</td></tr>";
        });
    }
    load();
    setInterval(load, 3000);
  </script>
</body>
</html>
"""


class DashboardState:
    def __init__(self):
        self.started_at = time.time()
        self.data = {
            "global_rate": 0,
            "top_ips": [],
            "effective_mean": 0.0,
            "effective_stddev": 0.0,
            "active_bans": [],
            "last_baseline": None,
            "last_global_alert": None,
        }
        self.lock = threading.Lock()

    def update_runtime(self, **kwargs):
        with self.lock:
            self.data.update(kwargs)

    def snapshot(self):
        with self.lock:
            payload = dict(self.data)

        uptime_seconds = int(time.time() - self.started_at)
        payload["cpu_percent"] = psutil.cpu_percent(interval=None)
        payload["memory_percent"] = psutil.virtual_memory().percent
        payload["uptime"] = _format_uptime(uptime_seconds)
        payload["generated_at"] = datetime.utcnow().isoformat()
        return payload


def start_dashboard(state, config):
    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_template_string(HTML)

    @app.get("/api/metrics")
    def metrics():
        return jsonify(state.snapshot())

    thread = threading.Thread(
        target=lambda: app.run(
            host=config["host"],
            port=config["port"],
            debug=False,
            use_reloader=False,
        ),
        daemon=True,
    )
    thread.start()
    return thread


def _format_uptime(total_seconds):
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes}m {seconds}s"
