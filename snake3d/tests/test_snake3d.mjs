// node test_snake3d.mjs -- continuous 3D snake engine
import assert from "assert";
import fs from "fs";
import { Level, createWorld, addSnake, setTarget, setSpeed, setSpeedLevel, step, bodyPoints, turnTowards, unit } from "../snake3d.js";

const close = (a, b, e) => Math.abs(a - b) < e;
// synthetic 2 x 2 x 1 m arena, 0.1 m voxels, origin (-1, -1, 0); wall plane at x >= 0.5; one food object
function makeLevel() {
  const nx = 20, ny = 20, nz = 10, vox = new Uint8Array(nx * ny * nz);
  const set = (i, j, k, v) => vox[(k * ny + j) * nx + i] = v;
  for (let k = 0; k < nz; k++) for (let j = 0; j < ny; j++) for (let i = 15; i < nx; i++) set(i, j, k, 1);
  for (let k = 3; k < 6; k++) for (let j = 15; j < 17; j++) for (let i = 5; i < 7; i++) set(i, j, k, 2);   // food id 1
  const meta = { voxel: { size: 0.1, nx, ny, nz, origin: [-1, -1, 0] }, start: [-0.45, -0.45, 0.45],
                 objects: [{ id: 1, reachable: true }] };
  return new Level(meta, vox);
}

// turnTowards respects the max angle and reaches the target
let d = turnTowards([1, 0, 0], [0, 1, 0], 0.1);
assert.ok(close(Math.acos(d[0]), 0.1, 1e-6));
assert.deepEqual(turnTowards([1, 0, 0], [0, 1, 0], 2), [0, 1, 0]);
d = turnTowards([1, 0, 0], [-1, 0, 0], 0.5); assert.ok(close(Math.hypot(...d), 1, 1e-9) && close(d[0], Math.cos(0.5), 1e-9));

// waits for the first input, then moves at its speed
let w = createWorld(makeLevel(), { selfCollision: "ignore", pellets: 0 });
let s = addSnake(w, "p1", { dir: [0, 1, 0] });
step(w, 0.5); assert.deepEqual(s.head, [-0.45, -0.45, 0.45], "no input yet: no motion");
setTarget(w, "p1", [0, 1, 0]); setSpeed(w, "p1", 0.5);
for (let i = 0; i < 10; i++) step(w, 0.05);
assert.ok(close(s.head[1], -0.2, 1e-6), "0.5 m/s for 0.5 s = 0.25 m: " + s.head);

// out of the arena counts as solid; heading straight down stops on the floor
setTarget(w, "p1", [0, 0, -1]);
for (let i = 0; i < 60; i++) step(w, 0.05);
assert.ok(s.head[2] >= 0 && s.head[2] < 0.1 && s.blocked, "stopped on the floor: " + s.head);

// diagonal into a wall slides along it; head-on stops
w = createWorld(makeLevel(), { selfCollision: "ignore", pellets: 0 }); s = addSnake(w, "p1", { start: [0.3, -0.5, 0.45], dir: unit([1, 1, 0]) });
setTarget(w, "p1", [1, 1, 0]); setSpeed(w, "p1", 1);
for (let i = 0; i < 10; i++) step(w, 0.05);
assert.ok(s.head[0] < 0.5 && s.head[1] > -0.2 && s.blocked, "slid along the wall: " + s.head);
w = createWorld(makeLevel(), { selfCollision: "ignore", pellets: 0 }); s = addSnake(w, "p1", { start: [0.3, -0.5, 0.45], dir: [1, 0, 0] });
setTarget(w, "p1", [1, 0, 0]);
for (let i = 0; i < 20; i++) step(w, 0.05);
assert.ok(s.head[0] < 0.5 && close(s.head[1], -0.5, 1e-9) && s.blocked, "head-on: stopped: " + s.head);

// eating: fly into the food object -> eaten once, grows, wins (only food)
w = createWorld(makeLevel(), { selfCollision: "ignore", pellets: 0 }); s = addSnake(w, "p1", { start: [-0.45, -0.45, 0.45], dir: [0, 1, 0] });
setTarget(w, "p1", [0, 1, 0]); setSpeed(w, "p1", 1);
const len0 = s.length;
for (let i = 0; i < 40 && !w.over; i++) step(w, 0.05);
assert.equal(s.score, w.opts.scoreObject); assert.ok(close(s.length, len0 + w.opts.growPerFood, 1e-9));
assert.ok(w.won && w.events.some(e => e.type === "eat" && e.object === 1));

// body follows the path and has the snake's length
const b = bodyPoints(s, 0.05);
const blen = b.slice(1).reduce((acc, p, i) => acc + Math.hypot(p[0] - b[i][0], p[1] - b[i][1], p[2] - b[i][2]), 0);
assert.ok(blen <= s.length + 1e-6 && blen > s.length - 0.06, `body ${blen} vs length ${s.length}`);

// speed level 0..1 maps to min..max speed
setSpeedLevel(w, "p1", 0); assert.equal(s.speed, w.opts.minSpeed);
setSpeedLevel(w, "p1", 1); assert.equal(s.speed, w.opts.maxSpeed);
setSpeedLevel(w, "p1", 0.5); assert.ok(close(s.speed, (w.opts.minSpeed + w.opts.maxSpeed) / 2, 1e-9));

// self collision: a long snake turning in a tight circle hits itself
w = createWorld(makeLevel(), { selfCollision: "die", turnRate: 6, startLength: 3, pellets: 0 }); s = addSnake(w, "p1", { start: [-0.5, -0.5, 0.45], dir: [1, 0, 0] });
setSpeed(w, "p1", 0.6);
for (let i = 0; i < 400 && s.alive; i++) { setTarget(w, "p1", [-s.dir[1], s.dir[0], 0]); step(w, 0.02); }
assert.ok(!s.alive && w.events.some(e => e.type === "self"), "tight loop should hit itself");

// random pellets: spawned in free, reachable space; eating one grows the snake and respawns another
w = createWorld(makeLevel(), { selfCollision: "ignore", pellets: 5, pelletMaxH: 0.9, seed: 7 });
assert.equal(w.pellets.length, 5);
for (const p of w.pellets) { assert.equal(w.level.at(p.pos), 0); assert.ok(p.pos[2] >= 0.2 && p.pos[2] <= 0.9); }
const again = createWorld(makeLevel(), { selfCollision: "ignore", pellets: 5, pelletMaxH: 0.9, seed: 7 });
assert.deepEqual(again.pellets.map(p => p.pos), w.pellets.map(p => p.pos), "same seed, same pellets");
const target = w.pellets[0].pos;
s = addSnake(w, "p1", { start: [target[0], target[1] - 0.3, target[2]], dir: [0, 1, 0] });
setTarget(w, "p1", [0, 1, 0]); setSpeed(w, "p1", 0.5);
const len1 = s.length;
for (let i = 0; i < 20; i++) step(w, 0.05);
assert.ok(s.pelletsEaten >= 1 && w.events.some(e => e.type === "pellet"), "ate the pellet in front");
assert.ok(close(s.length, len1 + s.pelletsEaten * w.opts.growPerPellet, 1e-9));
assert.equal(w.pellets.length, 5, "eaten pellets respawn");
assert.ok(!w.pellets.some(p => p.pos === target), "a new pellet replaced it");
// height does not matter: passing over a pellet at a very different height still eats it
w = createWorld(makeLevel(), { selfCollision: "ignore", pellets: 1, pelletMaxH: 0.9, seed: 3 });
const pp = w.pellets[0].pos;
s = addSnake(w, "p1", { start: [pp[0], pp[1] - 0.6, pp[2] > 0.5 ? 0.15 : 0.85], dir: [0, 1, 0] });
setTarget(w, "p1", [0, 1, 0]); setSpeed(w, "p1", 0.5);
for (let i = 0; i < 30; i++) step(w, 0.05);
assert.equal(s.pelletsEaten, 1, "eaten from " + Math.abs(s.head[2] - pp[2]).toFixed(2) + " m above / below");

// the real level loads and its start is free
const dir = new URL("../levels/hall_0807/", import.meta.url).pathname;
if (fs.existsSync(dir + "level3d.json")) {
  const meta = JSON.parse(fs.readFileSync(dir + "level3d.json"));
  const L = new Level(meta, new Uint8Array(fs.readFileSync(dir + "voxels.bin")));
  assert.equal(L.vox.length, meta.voxel.nx * meta.voxel.ny * meta.voxel.nz);
  assert.equal(L.at(meta.start), 0, "start voxel is free");
  const W = createWorld(L);
  assert.equal(W.pellets.length, W.opts.pellets, "real level spawns all pellets");
  console.log(`real level: ${meta.objects.length} objects, ${L.foodTotal} reachable, ${W.pellets.length} pellets`);
}
console.log("snake3d tests passed");
