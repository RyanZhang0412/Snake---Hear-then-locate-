# Audio mode: how the mic-array program steers the snake

The game (`index.html`) has an **Audio** mode. In it the snake's direction comes from the sound direction, and its
speed comes from how loud the sound is. Your program only has to send small JSON messages.

## Connect

Run a **WebSocket server**, then open the game with its address:

```
http://localhost:8000/?mode=audio&audio=ws://localhost:8765
```

You can also type the address into the box in the Audio panel and press *connect*. The game is the
client, so it can reconnect whenever you restart your program. Send messages as often as you like; 10–30 per
second is plenty. Every message replaces the previous one, so there is nothing to acknowledge.

If your code runs inside the same web page instead, call `window.audioInput({...})` with the same object.

## Message

One JSON object per WebSocket text message. Every field is optional; send only what you have.

```json
{"azimuth_deg": 120.0, "elevation_deg": 15.0, "level": 0.7}
{"azimuth_cw_deg": 30.0, "elevation_deg": 15.0, "level": 0.7}
```
The second line is the Hear-then-Locate localizer's own angles: 30° clockwise from the front, i.e. front-right.

| Field | Meaning |
|---|---|
| `azimuth_deg` | horizontal direction. **0 = right, 90 = forward**, i.e. into the screen, away from the player, **180 = left, 270 = back**. Counter-clockwise seen from above, the usual math / DOA convention |
| `elevation_deg` | up / down. 0 = level, +90 = straight up, −90 = straight down. Default 0 |
| `azimuth_cw_deg` | the same direction in the **Hear-then-Locate localizer's own convention**: **0 = front** (forward, into the screen), **clockwise seen from above**, so 90 = right, 180 = back, 270 = left. Send the localizer's azimuth here unchanged. Pair it with `elevation_deg` |
| `dir` | alternative to the two angles: a 3D vector `[x, y, z]` with x = right, y = forward, z = up; any length |
| `level` | loudness normalised to **0..1**. Speed = 0.15 + level × (1.8 − 0.15) m/s |
| `speed` | alternative to `level`: the speed in m/s directly, clamped to 0.15..1.8 |

Directions are relative to the **fixed game camera**, not to the room. "Forward" always means into the
screen. The snake does not jump to the new direction; it turns towards it at up to about 125°/s, so
noisy estimates are smoothed a little by the game too.

The first message with a direction starts the game. Without new directions the snake keeps flying
straight; without new levels it keeps its speed.

## Test without the mic array

`tools/fake_audio_server.py` is a complete, stdlib-only reference sender. The direction circles and the
loudness swings:

```bash
python3 tools/fake_audio_server.py --port 8765
# then open http://localhost:8000/?mode=audio&audio=ws://localhost:8765
```

Its `send_text()` and the message loop are all you need to copy. If you use the `websockets` package
instead, the whole sender is `await ws.send(json.dumps({"azimuth_deg": a, "elevation_deg": e, "level": l}))`.

## Multiplayer, later

The engine, `snake3d.js`, already holds several snakes by id. When multiplayer arrives, messages will get an
optional `"player": "<id>"` field; without it they steer the local player, so senders written against
this page keep working.
