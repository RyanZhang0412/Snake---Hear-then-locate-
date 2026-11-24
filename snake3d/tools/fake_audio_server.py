#!/usr/bin/env python3
"""Stand-in for the mic-array program: a tiny WebSocket server (stdlib only) that streams audio-mode
messages to the game (index.html). Use it to test audio mode, and as a reference for the real sender (AUDIO_API.md).

  python3 tools/fake_audio_server.py [--port 8765] [--hz 20]
  then open  http://localhost:8000/?mode=audio&audio=ws://localhost:8765

It sends {"azimuth_deg", "elevation_deg", "level"}: the direction slowly circles in the horizontal
plane while bobbing up and down, and the loudness level swings between 0 and 1 (= slow .. fast).
"""
import argparse, base64, hashlib, json, math, socket, struct, threading, time

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def handshake(conn):
    req = b""
    while b"\r\n\r\n" not in req:
        chunk = conn.recv(4096)
        if not chunk:
            raise ConnectionError("closed during handshake")
        req += chunk
    key = next(l.split(":", 1)[1].strip() for l in req.decode().split("\r\n") if l.lower().startswith("sec-websocket-key"))
    accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())


def send_text(conn, text):
    data = text.encode()
    n = len(data)
    head = bytes([0x81]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n))
    conn.sendall(head + data)          # server -> client frames are not masked


def serve(conn, addr, hz):
    try:
        handshake(conn)
        print("client connected", addr, flush=True)
        t0 = time.time()
        while True:
            t = time.time() - t0
            msg = {"azimuth_deg": round((90 + 25 * t) % 360, 1),          # starts forward, circles slowly
                   "elevation_deg": round(25 * math.sin(t * 0.7), 1),
                   "level": round(0.5 + 0.5 * math.sin(t * 0.5), 3)}
            send_text(conn, json.dumps(msg))
            time.sleep(1 / hz)
    except (ConnectionError, BrokenPipeError, OSError) as e:
        print("client gone", addr, e, flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--hz", type=float, default=20)
    a = ap.parse_args()
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", a.port)); srv.listen()
    print(f"fake audio sender on ws://localhost:{a.port}", flush=True)
    while True:
        c, ad = srv.accept()
        threading.Thread(target=serve, args=(c, ad, a.hz), daemon=True).start()
