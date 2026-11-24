"""Local browser interface for the dual XVF3800 array."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import webbrowser

import numpy as np

from localize import Localizer
from sound_events import ClapMonitor, Yamnet, pcm_levels
from stream import prepare_esp, receive
from tracking import DirectionTracker, SoundActivity

ROOT = Path(__file__).resolve().parent


class Engine:
    def __init__(self, mode, model):
        self.model = model
        self.mode = mode
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.last_direction = 0.
        self.offset = None
        self.deadline = 0.
        self.votes = []
        self.activity = SoundActivity()
        self.tracker = DirectionTracker()
        self.noise_spectrum = None
        self.monitor = None
        self.clap_until = 0.
        self.clap_direction = None
        self.last_audio = 0.
        if mode == "live":
            try:
                self.monitor = ClapMonitor(Yamnet(ROOT / "yamnet.tflite"))
            except (ImportError, OSError, RuntimeError, ValueError) as exc:
                print("Clap recognition unavailable:", exc, flush=True)
        self.state = {
            "mode": mode, "azimuth": None, "elevation": None, "score": None,
            "boards": [False, False], "gaps": 0, "skips": 0, "windows": 0,
            "gap_cm": model.gap_cm, "status": "正在启动双板", "calibration": "ready",
            "source": "learning", "level_dbfs": None, "noise_floor_dbfs": None, "software_gain_db": 0,
            "sensitivity": "standard", "clap_available": self.monitor is not None,
            "clap_count": 0, "transient_count": 0, "clap_kind": None,
        }

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            direction_old = now - self.last_direction > self.tracker.expiry
            if self.deadline and now > self.deadline:
                self.finish_reference()
            view = dict(self.state)
            if direction_old:
                view.update(azimuth=None, elevation=None, score=None)
            view["clap_active"] = now < self.clap_until
            view["clap_direction_valid"] = self.clap_direction is not None
            if not view["boards"][0] or not view["boards"][1] or now - self.last_audio > .8:
                view.update(level_dbfs=None, noise_floor_dbfs=None)
            elif view["clap_active"] and self.clap_direction is not None:
                view.update(self.clap_direction)
            return view

    def set_audio(self, field, value):
        allowed = {"software_gain_db": (0, 6, 12), "sensitivity": ("standard", "high")}
        if field not in allowed or type(value) not in (str, int) or value not in allowed[field]:
            return False
        self.update(**{field: value})
        return True

    def start_reference(self):
        with self.lock:
            if (self.mode != "live" or not all(self.state["boards"])
                    or self.deadline or self.activity.floor is None
                    or len(self.model.calibration_pairs) < 3):
                return False
            self.deadline = time.monotonic() + 5
            self.votes = []
            self.offset = None
            self.last_direction = 0.
            self.tracker = DirectionTracker()
            self.clap_direction = None
            self.state.update(calibration="sampling", offset_samples=None)
            return True

    def finish_reference(self):
        self.deadline = 0.
        if len(self.votes) >= 6:
            median = float(np.median(self.votes))
            support = [v for v in self.votes if abs(v - median) <= .22]
            if len(support) >= max(6, .72 * len(self.votes)):
                self.offset = float(np.median(support))
                self.state["calibration"] = "calibrated"
                self.state["offset_samples"] = round(self.offset, 3)
                self.tracker = DirectionTracker()
                self.last_direction = 0.
                self.votes = []
                return
        self.state["calibration"] = "retry"

    def on_window(self, window):
        audio, phase, origin = window
        with self.lock:
            noise = None if self.deadline else self.noise_spectrum
        corr, levels, profile = self.model.measure(audio, noise)
        now = time.monotonic()
        level, quiet_level = pcm_levels(audio)
        with self.lock:
            self.state["windows"] += 1
            previous_level = self.state["level_dbfs"]
            if previous_level is not None:
                level = max(level, previous_level - 26 * (now - self.last_audio))
            self.state["level_dbfs"] = round(level, 1)
            floor = self.state["noise_floor_dbfs"]
            if floor is None or now - self.last_audio > 2:
                floor = quiet_level
            else:
                # Fast enough to follow a quieter room, slow enough to ignore a clap.
                floor += float(np.clip(.12 * (quiet_level - floor), -.9, .15))
            self.state["noise_floor_dbfs"] = floor
            self.last_audio = now
            if self.deadline and now >= self.deadline:
                self.finish_reference()
            sensitivity = self.state["sensitivity"]
            active = self.activity.update(
                levels, learn=not self.deadline,
                continuing=now - self.last_direction < self.tracker.expiry,
                sensitive=sensitivity == "high",
            )
            self.state["source"] = active
            if active in ("learning", "quiet") and not self.deadline:
                self.noise_spectrum = (profile if self.noise_spectrum is None else
                                       .91 * self.noise_spectrum + .09 * profile)
            if active != "active" or corr is None:
                self.tracker.update(None, now)
                if active == "active":
                    self.state["source"] = "uncertain"
            elif self.deadline and now <= self.deadline - .45:
                vote = self.model.reference_offset(corr, phase)
                if vote is not None:
                    self.votes.append(vote)
            offset = self.offset
            tracker = self.tracker
        if self.monitor is not None:
            for kind, impulse, rise in self.monitor.process(audio, origin, sensitivity):
                event_corr = self.model.correlations(impulse, self.noise_spectrum)
                estimate = (self.model.direction(event_corr, phase, offset)
                            if event_corr is not None else None)
                with self.lock:
                    has_direction = (estimate is not None and
                                     (estimate["azimuth"] is not None or estimate["elevation"] is not None))
                    if kind == "transient":
                        # Display a raw trigger in high mode so misses can be separated
                        # from classification. A spatially unreliable trigger gets no angle.
                        self.state["transient_count"] += 1
                    if kind == "confirmed":
                        self.state["clap_count"] += 1
                    self.state["clap_kind"] = kind
                    self.state["clap_rise_db"] = round(rise, 1)
                    self.clap_until = now + 1.2
                    self.clap_direction = estimate if tracker is self.tracker and has_direction else None
        if active != "active" or corr is None:
            return
        estimate = self.model.direction(corr, phase, offset)
        with self.lock:
            if tracker is self.tracker:
                direction = tracker.update(estimate, now)
                self.state["source"] = "tracking" if direction else "uncertain"
                if direction is not None:
                    self.state.update(direction)
                    self.last_direction = now

    def signal_status(self, boards, gaps, skips):
        status = "双板接收中" if all(boards) else "正在等待两块板的数据"
        self.update(boards=boards, gaps=gaps, skips=skips, status=status)

    def reset_reference(self):
        with self.lock:
            self.offset = None
            self.deadline = 0.
            self.votes = []
            self.state.update(calibration="ready", offset_samples=None)
            self.tracker = DirectionTracker()
            self.last_direction = 0.
            self.clap_direction = None

    def start_live(self, args):
        link = None
        try:
            host = args.host
            if host is None:
                link, host = prepare_esp(args.serial, lambda status: self.update(status=status), self.stop)
            self.update(status="等待 ESP32 音频")
            receive(host, self.stop, self.on_window, self.signal_status, self.reset_reference)
        except InterruptedError:
            pass
        except Exception as exc:
            print("Live capture:", exc, flush=True)
            self.update(status=str(exc), boards=[False, False], error=True)
        finally:
            if link is not None:
                try:
                    link.write(b"\nPAUSE\n")
                finally:
                    link.close()


def handler_for(engine):
    class Handler(BaseHTTPRequestHandler):
        files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
            "/sphere.js": ("sphere.js", "application/javascript; charset=utf-8"),
            "/ui.js": ("ui.js", "application/javascript; charset=utf-8"),
        }

        def send_content(self, data, media, code=200):
            self.send_response(code)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, value, code=200):
            self.send_content(json.dumps(value).encode(), "application/json", code)

        def do_GET(self):
            path = self.path.partition("?")[0]
            if path == "/api/state":
                self.send_json(engine.snapshot())
            elif path in self.files:
                file, media = self.files[path]
                self.send_content((ROOT / file).read_bytes(), media)
            else:
                self.send_error(404)

        def do_POST(self):
            if self.headers.get("X-Array-UI") != "local":
                self.send_error(404)
                return
            if self.path == "/api/calibrate":
                started = engine.start_reference()
                self.send_json({"started": started}, 200 if started else 409)
            elif self.path == "/api/audio-settings":
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 1 <= size <= 128:
                        raise ValueError("invalid request size")
                    request = json.loads(self.rfile.read(size))
                    if not isinstance(request, dict):
                        raise ValueError("expected object")
                    changed = engine.set_audio(request.get("field"), request.get("value"))
                except (TypeError, ValueError):
                    changed = False
                self.send_json({"changed": changed}, 200 if changed else 400)
            else:
                self.send_error(404)

        def log_message(self, *args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="start dual-board capture")
    parser.add_argument("--host", help="ESP32 IP with Wi-Fi and capture already started")
    parser.add_argument("--serial", help="serial override when starting the ESP32")
    parser.add_argument("--port", type=int, default=0, help="localhost HTTP port; default finds a free port")
    parser.add_argument("--array", type=Path, default=ROOT / "array.json", help="microphone geometry file")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if (args.host or args.serial) and not args.live:
        parser.error("--host and --serial require --live")
    if args.host and args.serial:
        parser.error("Specify an ESP32 IP or serial port, not both")
    mode = "live" if args.live else "demo"
    engine = Engine(mode, Localizer(args.array))
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(engine)) as server:
        worker = None
        if args.live:
            worker = threading.Thread(target=engine.start_live, args=(args,), daemon=True)
            worker.start()
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"Open {url}   |   Ctrl+C to stop", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            engine.stop.set()
            if worker is not None:
                worker.join(timeout=3)


if __name__ == "__main__":
    main()
