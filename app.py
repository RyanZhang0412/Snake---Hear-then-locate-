"""Local browser interface for the dual XVF3800 array."""
import argparse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, urlsplit
import webbrowser

import numpy as np

from localize import Localizer, RATE
from mic_preview import RecentAudio, preview_pair
from sound_events import ClapMonitor, Yamnet, pcm_levels
from speech_events import KEYWORD_MODEL_NAME, MODEL_NAME, SnakeRecognizer, SpeechTrigger
from stream import prepare_esp, receive
from tracking import DirectionTracker, SoundActivity

ROOT = Path(__file__).resolve().parent
WEAK_CLAP_SCORE = .36
CLAP_MIN_INTERVAL = .075


class Engine:
    def __init__(self, mode, model, speech_model=None, keyword_model=None):
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
        self.game_until = 0.
        self.game_cursor = 0
        self.game_events = deque(maxlen=48)
        self.speech = None
        self.speech_context = None
        self.speech_until = 0.
        self.speech_direction = None
        self.speech_matched = False
        self.recent_audio = RecentAudio() if mode == "live" else None
        if mode == "live":
            try:
                self.monitor = ClapMonitor(Yamnet(ROOT / "yamnet.tflite"))
            except (ImportError, OSError, RuntimeError, ValueError) as exc:
                print("Clap recognition unavailable:", exc, flush=True)
            if speech_model is not None:
                try:
                    self.speech = SpeechTrigger(SnakeRecognizer(speech_model, keyword_model), model.positions)
                except (ImportError, OSError, RuntimeError, ValueError) as exc:
                    print("Speech recognition unavailable:", exc, flush=True)
        self.state = {
            "mode": mode, "azimuth": None, "elevation": None, "score": None,
            "boards": [False, False], "gaps": 0, "skips": 0, "windows": 0,
            "gap_cm": model.gap_cm, "status": "正在启动双板", "calibration": "ready",
            "source": "learning", "level_dbfs": None, "noise_floor_dbfs": None, "software_gain_db": 0,
            "sensitivity": "standard", "clap_available": self.monitor is not None,
            "clap_count": 0, "transient_count": 0, "clap_kind": None,
            "speech_available": self.speech is not None, "speech_count": 0, "speech_attempts": 0,
            "keyword_model": KEYWORD_MODEL_NAME if self.speech and keyword_model else None,
            "trigger_input": "speech" if self.speech else "clap", "denoise": True,
            "transport_format": "pcm16", "format_available": False, "format_active": None,
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
            speech_recent = view["trigger_input"] == "speech" and now < self.speech_until and all(view["boards"])
            speech_active = speech_recent and self.speech_matched
            view.update(speech_active=speech_active,
                        speech_missed=speech_recent and not self.speech_matched,
                        speech_direction=self.speech_direction if speech_active else None)
            view["preview_ready"] = bool(self.recent_audio and self.recent_audio.chunks
                                         and now - self.last_audio <= 4.)
            if not view["boards"][0] or not view["boards"][1] or now - self.last_audio > .8:
                view.update(level_dbfs=None, noise_floor_dbfs=None)
            elif view["trigger_input"] == "clap" and view["clap_active"] and self.clap_direction is not None:
                view.update(self.clap_direction)
            return view

    def capture_preview(self):
        with self.lock:
            if self.recent_audio is None or time.monotonic() - self.last_audio > 4.:
                return None
            clip = self.recent_audio.snapshot()
            if clip is None:
                return None
            direction = None
            if time.monotonic() - self.last_direction <= 4.:
                direction = {key: self.state[key] for key in ("azimuth", "elevation")}
            noise = self.noise_spectrum
        return preview_pair(*clip, self.model.positions, direction, noise)

    def set_audio(self, field, value):
        allowed = {"software_gain_db": (0, 6, 12), "sensitivity": ("standard", "high"),
                   "trigger_input": ("speech", "clap"), "transport_format": ("original", "pcm16")}
        if field == "denoise":
            if type(value) is not bool:
                return False
        elif field not in allowed or type(value) not in (str, int) or value not in allowed[field]:
            return False
        with self.lock:
            if field == "transport_format" and not self.state["format_available"]:
                return False
            if field == "trigger_input" and value == "speech" and not self.state["speech_available"]:
                return False
            self.state[field] = value
            if field in ("trigger_input", "denoise"):
                self.speech_until = self.clap_until = 0.
                self.game_events.clear()
        return True

    def game_input(self, after):
        with self.lock:
            now = time.monotonic()
            self.game_until = now + 2.
            return {
                "cursor": self.game_cursor,
                "mode": self.mode,
                "ready": bool(self.monitor and self.state["trigger_input"] == "clap"
                              and all(self.state["boards"]) and not self.deadline),
                "calibrated": self.offset is not None,
                "events": [event for event in self.game_events
                           if after is not None and event["id"] > after and now - event["received"] < 2.]
            }

    def publish_clap(self, impulse, estimate, sample, now, tracker):
        # One verified onset contributes one direction. Distinct onsets can
        # contribute to the same round without counting residual peaks as people.
        at = sample / RATE
        with self.lock:
            if self.deadline or self.tracker is not tracker:
                return
            if self.game_events and 0 <= at - self.game_events[-1]["at"] < CLAP_MIN_INTERVAL:
                return
            dbfs, _ = pcm_levels(impulse)
            self.game_cursor += 1
            self.game_events.append({
                "id": self.game_cursor, "at": at, "received": now,
                "groups": [{"azimuth": estimate["azimuth"], "elevation": estimate["elevation"],
                            "rms": 10 ** (dbfs / 20)}],
            })

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
            self.speech_until = 0.
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
            use_denoise = self.state["denoise"]
            noise = self.noise_spectrum if use_denoise and not self.deadline else None
        corr, levels, profile = self.model.measure(audio, noise)
        now = time.monotonic()
        level, quiet_level = pcm_levels(audio)
        with self.lock:
            self.state["windows"] += 1
            if self.recent_audio is not None:
                self.recent_audio.append(origin, audio)
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
            trigger_input = self.state["trigger_input"]
            active = self.activity.update(
                levels, learn=not self.deadline,
                continuing=now - self.last_direction < self.tracker.expiry,
                sensitive=sensitivity == "high" or trigger_input == "speech",
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
            voice = self.speech is not None and trigger_input == "speech" and not self.deadline
            play = now <= self.game_until and not self.deadline
        if self.monitor is not None and trigger_input == "clap":
            # Multiple independently localized onsets can arrive during a display alert.
            events = self.monitor.process(audio, origin, sensitivity, collect_game=play)
            estimates = {}
            for kind, impulse, rise, sample in events:
                if sample not in estimates:
                    estimates[sample] = self.model.clap_direction(impulse, phase, offset, noise)
                estimate = estimates[sample]
                if kind in ("game", "weak_game"):
                    if estimate is not None and (kind == "game" or estimate["score"] >= WEAK_CLAP_SCORE):
                        self.publish_clap(impulse, estimate, sample, now, tracker)
                    continue
                with self.lock:
                    if kind == "transient":
                        # Display a raw trigger in high mode so misses can be separated
                        # from classification. A spatially unreliable trigger gets no angle.
                        self.state["transient_count"] += 1
                    if kind == "confirmed":
                        self.state["clap_count"] += 1
                    self.state["clap_kind"] = kind
                    self.state["clap_rise_db"] = round(rise, 1)
                    self.clap_until = now + 1.2
                    self.clap_direction = estimate if tracker is self.tracker else None
        estimate = None
        if active == "active" and corr is not None:
            estimate = self.model.direction(corr, phase, offset)
            with self.lock:
                if tracker is self.tracker:
                    direction = tracker.update(estimate, now)
                    self.state["source"] = "tracking" if direction else "uncertain"
                    if direction is not None:
                        self.state.update(direction)
                        self.last_direction = now

        context = (tracker, use_denoise, offset) if voice else None
        if self.speech is not None and self.speech_context != context:
            self.speech.pause()
            self.speech_context = context
        if voice:
            self.process_voice(audio, origin, active == "active", estimate, noise, context)

    def process_voice(self, audio, origin, active, estimate, noise, context):
        try:
            result = self.speech.process(audio, origin, active, estimate, noise)
        except (RuntimeError, ValueError) as exc:
            print("Speech recognition stopped:", exc, flush=True)
            self.speech.close()
            self.speech = None
            self.update(speech_available=False, trigger_input="clap")
            return
        if result is not None:
            with self.lock:
                if (self.state["trigger_input"] == "speech" and not self.deadline
                        and context == (self.tracker, self.state["denoise"], self.offset)):
                    self.speech_matched, self.speech_direction = result
                    self.state["speech_attempts"] += 1
                    if self.speech_matched:
                        self.state["speech_count"] += 1
                    self.speech_until = time.monotonic() + 1.8

    def signal_status(self, boards, gaps, skips, transport):
        status = "双板接收中" if all(boards) else "正在等待两块板的数据"
        self.update(boards=boards, gaps=gaps, skips=skips, status=status, **transport)

    def reset_reference(self):
        with self.lock:
            self.offset = None
            self.deadline = 0.
            self.votes = []
            self.state.update(calibration="ready", offset_samples=None)
            self.tracker = DirectionTracker()
            self.last_direction = 0.
            self.clap_direction = None
            self.speech_until = 0.

    def start_live(self, args):
        link = None
        try:
            host = args.host
            if host is None:
                link, host = prepare_esp(args.serial, lambda status: self.update(status=status), self.stop)
            self.update(status="等待 ESP32 音频")
            receive(host, self.stop, self.on_window, self.signal_status, self.reset_reference,
                    lambda: self.state["transport_format"])
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
            "/snake": ("snake.html", "text/html; charset=utf-8"),
            "/snake.js": ("snake.js", "application/javascript; charset=utf-8"),
            "/game_rules.mjs": ("game_rules.mjs", "application/javascript; charset=utf-8"),
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
                             "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, value, code=200):
            self.send_content(json.dumps(value).encode(), "application/json", code)

        def do_GET(self):
            url = urlsplit(self.path)
            path = url.path
            if path == "/api/state":
                self.send_json(engine.snapshot())
            elif path == "/api/claps":
                value = parse_qs(url.query).get("after", [None])[0]
                try:
                    after = None if value is None else int(value)
                except ValueError:
                    self.send_json({"error": "invalid event cursor"}, 400)
                    return
                self.send_json(engine.game_input(after))
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
            elif self.path == "/api/audio-preview":
                clip = engine.capture_preview()
                if clip is None:
                    self.send_json({"error": "no recent audio"}, 409)
                else:
                    self.send_json(clip)
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
    parser.add_argument("--speech-model", type=Path, help="local Vosk English model directory")
    parser.add_argument("--keyword-model", type=Path, help="local English sherpa-onnx GigaSpeech keyword model directory")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--game", action="store_true", help="open the Snake page directly")
    args = parser.parse_args()
    if (args.host or args.serial) and not args.live:
        parser.error("--host and --serial require --live")
    if args.host and args.serial:
        parser.error("Specify an ESP32 IP or serial port, not both")
    mode = "live" if args.live else "demo"
    speech_model = ROOT / "models" / MODEL_NAME
    keyword_model = ROOT / "models" / KEYWORD_MODEL_NAME
    engine = Engine(mode, Localizer(args.array), args.speech_model or (speech_model if speech_model.is_dir() else None),
                    args.keyword_model or (keyword_model if keyword_model.is_dir() else None))
    if args.game:
        engine.set_audio("trigger_input", "clap")
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(engine)) as server:
        worker = None
        if args.live:
            worker = threading.Thread(target=engine.start_live, args=(args,), daemon=True)
            worker.start()
        url = f"http://127.0.0.1:{server.server_port}/" + ("snake" if args.game else "")
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
            if engine.speech is not None:
                engine.speech.close()


if __name__ == "__main__":
    main()
