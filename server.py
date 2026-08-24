#!/usr/bin/env python3
"""Static file server + Discord invite counts proxy (/api/counts).
Caches Discord responses ~1s so viewers can poll every second without
rate-limit issues; serves stale cache during backoff."""
import json, time, threading, urllib.request, urllib.error, functools, os
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

DISCORD_API = "https://discord.com/api/v10/invites/sw8pUwUgYD?with_counts=true"
CACHE_TTL = 1.0
PORT = int(os.environ.get("PORT", 8000))  # Render $PORT'a bağlanır

_cache = {"ts": 0.0, "data": None, "blocked_until": 0.0}
_lock = threading.Lock()


def fetch_discord():
    req = urllib.request.Request(
        DISCORD_API, headers={"User-Agent": "Mozilla/5.0 (member-countdown)"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode())


def get_counts():
    """Return (data_dict_or_None, http_status)."""
    now = time.time()
    with _lock:
        should_fetch = (now - _cache["ts"] >= CACHE_TTL
                        and now >= _cache["blocked_until"])
    if should_fetch:
        with _lock:  # reserve this slot so concurrent requests don't double-fetch
            if now - _cache["ts"] < CACHE_TTL:
                should_fetch = False
            else:
                _cache["ts"] = now  # attempt marker; overwritten on success anyway
        if should_fetch:
            try:
                data = fetch_discord()
                with _lock:
                    _cache["data"], _cache["ts"] = data, time.time()
            except urllib.error.HTTPError as e:
                wait = 5.0
                if e.code == 429:
                    try:
                        wait = float(json.loads(e.read().decode()).get("retry_after", 5))
                    except Exception:
                        pass
                with _lock:
                    _cache["blocked_until"] = time.time() + wait + 0.3
            except Exception:
                with _lock:
                    _cache["blocked_until"] = time.time() + 3.0
    with _lock:
        return _cache["data"]


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] == "/api/counts":
            data = get_counts()
            if data is None:
                body = json.dumps({"error": "discord unreachable"}).encode()
                self.send_response(502)
            else:
                body = json.dumps(data).encode()
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

    def end_headers(self):
        # never let the browser cache HTML — data is live-polled anyway
        if self.path.endswith((".html", "/")) or self.path == "/":
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *args):
        pass  # quiet


if __name__ == "__main__":
    handler = functools.partial(
        Handler, directory=os.path.dirname(os.path.abspath(__file__)))
    print(f"listening on 0.0.0.0:{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), handler).serve_forever()
