// Continuous 3D snake engine for the game (index.html). No DOM, so it runs in node tests.
// Game frame: metres, x = right, y = forward (away from the fixed camera), z = up.
// A world holds any number of snakes (multiplayer later); each is steered by a target direction and a speed.

export const DEFAULTS = {
  speed: 0.7,          // m/s
  minSpeed: 0.15, maxSpeed: 1.8,
  turnRate: 2.2,       // rad/s the heading can rotate towards the target (~125 deg/s)
  radius: 0.09,        // head / body radius, m
  startLength: 0.6,    // body length, m
  growPerFood: 0.3,    // extra length per eaten object, m
  selfCollision: "die",  // "die" | "ignore"
  pellets: 25,         // random small food kept in the arena; eaten ones respawn elsewhere
  pelletRadius: 0.1,   // drawn size
  pelletHitXY: 0.3,    // a pellet is eaten when the head is this close horizontally, at any height
  growPerPellet: 0.1,
  scorePellet: 1, scoreObject: 5,
  pelletMinH: 0.2, pelletMaxH: 1.7,   // spawn height range above the floor, m
  seed: 1,
};

// small deterministic RNG (mulberry32) so tests and replays are repeatable
function rng(seed) {
  let a = seed >>> 0;
  return () => { a = (a + 0x6D2B79F5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1);
                 t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const add = (a, b, s = 1) => [a[0] + b[0] * s, a[1] + b[1] * s, a[2] + b[2] * s];
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
export const unit = v => { const n = Math.hypot(v[0], v[1], v[2]); return n > 1e-9 ? [v[0] / n, v[1] / n, v[2] / n] : null; };

// Rotate unit vector a towards unit vector b by at most maxAngle (rad).
export function turnTowards(a, b, maxAngle) {
  const c = Math.max(-1, Math.min(1, dot(a, b))), ang = Math.acos(c);
  if (ang <= maxAngle || ang < 1e-6) return b.slice();
  let perp = add(b, a, -c);                                   // b's component orthogonal to a
  if (Math.hypot(...perp) < 1e-6) perp = Math.abs(a[2]) < 0.9 ? [-a[1], a[0], 0] : [0, -a[2], a[1]];  // b = -a
  perp = unit(perp);
  return unit(add(a.map(x => x * Math.cos(maxAngle)), perp, Math.sin(maxAngle)));
}

export class Level {
  constructor(meta, voxels) {
    this.meta = meta;
    const v = meta.voxel;
    Object.assign(this, { size: v.size, nx: v.nx, ny: v.ny, nz: v.nz, origin: v.origin, vox: voxels });
    this.objects = meta.objects;
    this.foodTotal = meta.objects.filter(o => o.reachable !== false).length;
  }
  // 0 free, 1 solid (also outside the arena), 2+i food object i
  at(p) {
    const i = Math.floor((p[0] - this.origin[0]) / this.size), j = Math.floor((p[1] - this.origin[1]) / this.size),
          k = Math.floor((p[2] - this.origin[2]) / this.size);
    if (i < 0 || j < 0 || k < 0 || i >= this.nx || j >= this.ny || k >= this.nz) return 1;
    return this.vox[(k * this.ny + j) * this.nx + i];
  }
}

// Free voxels reachable from the start (6-connected through free and food voxels), computed once per level.
function reachableFree(level) {
  if (level._reach) return level._reach;
  const { nx, ny, nz, vox } = level, seen = new Uint8Array(vox.length), q = new Int32Array(vox.length);
  const st = level.meta.start, s = level.size, o = level.origin;
  const idx = (i, j, k) => (k * ny + j) * nx + i;
  let head = 0, tail = 0;
  const i0 = Math.floor((st[0] - o[0]) / s), j0 = Math.floor((st[1] - o[1]) / s), k0 = Math.floor((st[2] - o[2]) / s);
  seen[idx(i0, j0, k0)] = 1; q[tail++] = idx(i0, j0, k0);
  while (head < tail) {
    const c = q[head++], i = c % nx, j = Math.floor(c / nx) % ny, k = Math.floor(c / (nx * ny));
    for (const [di, dj, dk] of [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]) {
      const a = i + di, b = j + dj, cc = k + dk;
      if (a < 0 || b < 0 || cc < 0 || a >= nx || b >= ny || cc >= nz) continue;
      const n = idx(a, b, cc);
      if (!seen[n] && vox[n] !== 1) { seen[n] = 1; q[tail++] = n; }
    }
  }
  // spawn candidates: reachable, free (not food), all 6 neighbours free, within the height range
  const cand = [];
  for (let n = 0; n < vox.length; n++) {
    if (!seen[n] || vox[n] !== 0) continue;
    const i = n % nx, j = Math.floor(n / nx) % ny, k = Math.floor(n / (nx * ny));
    if (i < 1 || j < 1 || k < 1 || i >= nx - 1 || j >= ny - 1 || k >= nz - 1) continue;
    if (vox[n + 1] || vox[n - 1] || vox[n + nx] || vox[n - nx] || vox[n + nx * ny] || vox[n - nx * ny]) continue;
    cand.push(n);
  }
  return (level._reach = { seen, cand });
}

export function createWorld(level, opts = {}) {
  const w = { level, opts: { ...DEFAULTS, ...opts }, snakes: new Map(), eaten: new Set(), events: [], time: 0,
              foodLeft: level.foodTotal, over: false, won: false, pellets: [], pelletSeq: 0 };
  w.rand = rng(w.opts.seed);
  for (let i = 0; i < w.opts.pellets; i++) spawnPellet(w);
  return w;
}

// Put one pellet at a random reachable free spot: in the height range, away from snake heads and other pellets.
export function spawnPellet(world) {
  const L = world.level, o = world.opts, { cand } = reachableFree(L);
  if (!cand.length) return null;
  for (let tries = 0; tries < 200; tries++) {
    const n = cand[Math.floor(world.rand() * cand.length)];
    const i = n % L.nx, j = Math.floor(n / L.nx) % L.ny, k = Math.floor(n / (L.nx * L.ny));
    const p = [L.origin[0] + (i + 0.5) * L.size, L.origin[1] + (j + 0.5) * L.size, L.origin[2] + (k + 0.5) * L.size];
    if (p[2] < o.pelletMinH || p[2] > o.pelletMaxH) continue;
    if ([...world.snakes.values()].some(s => dist(s.head, p) < 1.0)) continue;
    if (world.pellets.some(q => dist(q.pos, p) < 0.5)) continue;
    const pel = { id: ++world.pelletSeq, pos: p };
    world.pellets.push(pel);
    return pel;
  }
  return null;
}

export function addSnake(world, id, { start = world.level.meta.start, dir = [0, 1, 0] } = {}) {
  const o = world.opts, d = unit(dir);
  const s = { id, head: start.slice(), dir: d, target: d, speed: o.speed, length: o.startLength,
              path: [start.slice()], score: 0, alive: true, blocked: false, started: false };
  world.snakes.set(id, s);
  return s;
}

// Steering inputs. Directions are in the game frame and need not be unit length.
export function setTarget(world, id, v) {
  const s = world.snakes.get(id), u = v && unit(v);
  if (s && u) { s.target = u; s.started = true; }
}
export function setSpeed(world, id, mps) {
  const s = world.snakes.get(id), o = world.opts;
  if (s && Number.isFinite(mps)) s.speed = Math.max(o.minSpeed, Math.min(o.maxSpeed, mps));
}
// level in 0..1 (e.g. normalised loudness) -> speed between minSpeed and maxSpeed
export function setSpeedLevel(world, id, level) {
  const o = world.opts, l = Math.max(0, Math.min(1, level));
  setSpeed(world, id, o.minSpeed + l * (o.maxSpeed - o.minSpeed));
}

// free space and food (it gets eaten) are passable; walls and the outside are not
const free = (world, p) => world.level.at(p) !== 1;

// Body sample points from the head backwards, every `spacing` metres along the path, up to the length.
export function bodyPoints(s, spacing) {
  // polyline head -> newest path point -> ... -> oldest, sampled at arc length 0, spacing, 2*spacing ... <= length
  const pts = [s.head];
  for (let i = s.path.length - 1; i >= 0; i--) pts.push(s.path[i]);
  const out = [s.head.slice()];
  let travelled = 0, nextAt = spacing;
  for (let i = 1; i < pts.length && nextAt <= s.length + 1e-9; i++) {
    const a = pts[i - 1], b = pts[i], d = dist(a, b);
    while (d > 0 && nextAt <= travelled + d + 1e-12 && nextAt <= s.length + 1e-9) {
      const t = (nextAt - travelled) / d;
      out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]);
      nextAt += spacing;
    }
    travelled += d;
  }
  return out;
}

export function step(world, dt) {
  if (world.over) return world.events;
  world.time += dt;
  const L = world.level, o = world.opts, sub = L.size * 0.4;
  for (const s of world.snakes.values()) {
    if (!s.alive || !s.started) continue;
    s.dir = turnTowards(s.dir, s.target, o.turnRate * dt);
    let todo = s.speed * dt;
    s.blocked = false;
    while (todo > 1e-9) {
      const ds = Math.min(sub, todo); todo -= ds;
      let next = add(s.head, s.dir, ds);
      if (!free(world, next)) {
        // slide: drop one axis of the motion at a time and keep the first move that is free
        const mv = s.dir.map(x => x * ds);
        const tries = [0, 1, 2].map(ax => { const m = mv.slice(); m[ax] = 0; return m; })
                               .sort((a, b) => Math.hypot(...b) - Math.hypot(...a));
        next = null;
        for (const m of tries) {
          if (Math.hypot(...m) < ds * 0.2) continue;
          const c = add(s.head, m);
          if (free(world, c)) { next = c; break; }
        }
        if (!next) { s.blocked = true; break; }
        s.blocked = true;
      }
      s.head = next;
      const v = L.at(s.head);
      if (v >= 2 && !world.eaten.has(v - 1)) {
        world.eaten.add(v - 1);
        s.length += o.growPerFood; s.score += o.scoreObject; s.objects = (s.objects || 0) + 1;
        const obj = L.objects[v - 2];
        if (obj.reachable !== false) world.foodLeft--;
        world.events.push({ type: "eat", snake: s.id, object: obj.id, t: world.time });
        if (world.foodLeft <= 0) { world.over = world.won = true; world.events.push({ type: "won", snake: s.id }); }
      }
      for (let pi = world.pellets.length - 1; pi >= 0; pi--) {     // random pellets
        const pel = world.pellets[pi];
        if (Math.hypot(s.head[0] - pel.pos[0], s.head[1] - pel.pos[1]) < o.pelletHitXY) {
          world.pellets.splice(pi, 1);
          s.length += o.growPerPellet; s.score += o.scorePellet; s.pelletsEaten = (s.pelletsEaten || 0) + 1;
          world.events.push({ type: "pellet", snake: s.id, pellet: pel.id, pos: pel.pos, t: world.time });
          spawnPellet(world);
        }
      }
      if (dist(s.head, s.path[s.path.length - 1]) >= 0.02) s.path.push(s.head.slice());
    }
    // keep the path a bit longer than the body
    let acc = 0;
    for (let i = s.path.length - 1; i > 0; i--) {
      acc += dist(s.path[i], s.path[i - 1]);
      if (acc > s.length + 0.5) { s.path.splice(0, i - 1); break; }
    }
    // self collision: body samples further than a few radii back along the body
    if (o.selfCollision === "die") {
      const body = bodyPoints(s, o.radius);
      for (let i = 6; i < body.length; i++) {
        if (dist(s.head, body[i]) < o.radius * 1.2) {
          s.alive = false; world.events.push({ type: "self", snake: s.id, t: world.time });
          if ([...world.snakes.values()].every(x => !x.alive)) world.over = true;
          break;
        }
      }
    }
  }
  return world.events;
}
