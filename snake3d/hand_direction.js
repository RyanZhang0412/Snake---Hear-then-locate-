// Continuous 3D hand direction from MediaPipe hand landmarks. Pure math, no DOM, so it runs in
// node tests too. No binning into game directions here: that is a later step.
//
// Player frame used everywhere (mirrored like a selfie, the way the player sees themselves):
//   x = to the player's right, y = up, z = towards the screen / camera (away from the player)
// MediaPipe world landmarks are metres around the hand centre with x right / y down in the raw
// camera image and z growing away from the camera, so player = (-x, -y, -z).

export const toPlayer = p => [-p.x, -p.y, -p.z];

const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
export const norm = a => { const n = Math.hypot(...a); return n > 1e-9 ? a.map(v => v / n) : [0, 0, 0]; };

// Principal axis of a few 3D points (power iteration on the 3x3 covariance), oriented from the
// first point towards the last. Using all 4 index-finger joints is less jumpy than tip - knuckle.
export function fitLine(pts) {
  const n = pts.length, c = [0, 0, 0];
  for (const p of pts) for (let k = 0; k < 3; k++) c[k] += p[k] / n;
  const C = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (const p of pts) { const d = sub(p, c); for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) C[i][j] += d[i] * d[j]; }
  let v = norm(sub(pts[n - 1], pts[0]));
  if (!(Math.abs(v[0]) + Math.abs(v[1]) + Math.abs(v[2]))) return { dir: [0, 0, 0], straightness: 0 };
  for (let it = 0; it < 30; it++) v = norm([dot(C[0], v), dot(C[1], v), dot(C[2], v)]);
  if (dot(v, sub(pts[n - 1], pts[0])) < 0) v = v.map(x => -x);
  // straightness: share of the spread along the line (1 = perfectly straight finger)
  const total = C[0][0] + C[1][1] + C[2][2];
  const along = dot(v, [dot(C[0], v), dot(C[1], v), dot(C[2], v)]);
  return { dir: v, straightness: total > 0 ? along / total : 0 };
}

// Three candidate "hand directions", all unit vectors in the player frame.
//   index : line through the index finger joints 5-6-7-8 (knuckle -> tip)
//   hand  : wrist (0) -> middle-finger knuckle (9), the direction the whole hand points
//   palm  : normal of the palm plane, pointing out of the palm (needs handedness)
export function handDirections(world, handedness = "Right") {
  const P = world.map(toPlayer);
  const idx = fitLine([P[5], P[6], P[7], P[8]]);
  const hand = norm(sub(P[9], P[0]));
  // palm plane from wrist, index knuckle, pinky knuckle; the sign makes it point out of the palm.
  // Geometry in the player frame: a real LEFT hand's palm normal is (index - wrist) x (pinky - wrist),
  // a real RIGHT hand's is the opposite. MediaPipe labels handedness as if the image were mirrored;
  // we feed it the raw (unmirrored) webcam frame, so its label is swapped: "Right" = real left hand.
  // Hence: keep the cross product for label "Right", negate it for label "Left".
  let palm = norm(cross(sub(P[5], P[0]), sub(P[17], P[0])));
  if (handedness === "Left") palm = palm.map(v => -v);
  // how far the index finger is extended: tip-to-wrist vs knuckle-to-wrist distance
  const ext = Math.hypot(...sub(P[8], P[0])) / Math.max(1e-6, Math.hypot(...sub(P[5], P[0])));
  return { index: idx.dir, hand, palm, straightness: idx.straightness, extension: ext };
}

// Angles of a unit vector in the player frame, in degrees:
//   yaw   : left/right, 0 = straight at the screen, +90 = right, -90 = left, +-180 = back at you
//   pitch : up/down, +90 = up, -90 = down
export function toAngles(v) {
  return { yaw: Math.atan2(v[0], v[2]) * 180 / Math.PI,
           pitch: Math.atan2(v[1], Math.hypot(v[0], v[2])) * 180 / Math.PI };
}

// One Euro filter (Casiez et al. 2012) per component: smooth when the hand is still, little lag
// when it moves fast. mincutoff in Hz (lower = smoother), beta = speed coefficient.
export class OneEuro3 {
  constructor(mincutoff = 1.5, beta = 0.05, dcutoff = 1.0) { Object.assign(this, { mincutoff, beta, dcutoff }); this.reset(); }
  reset() { this.x = null; this.dx = [0, 0, 0]; this.t = null; }
  static alpha(cutoff, dt) { const r = 2 * Math.PI * cutoff * dt; return r / (r + 1); }
  filter(v, tMs) {
    if (this.x === null || this.t === null) { this.x = v.slice(); this.t = tMs; return norm(this.x); }
    const dt = Math.max(1e-3, (tMs - this.t) / 1000); this.t = tMs;
    const ad = OneEuro3.alpha(this.dcutoff, dt);
    for (let k = 0; k < 3; k++) {
      const d = (v[k] - this.x[k]) / dt;
      this.dx[k] += ad * (d - this.dx[k]);
      const a = OneEuro3.alpha(this.mincutoff + this.beta * Math.abs(this.dx[k]), dt);
      this.x[k] += a * (v[k] - this.x[k]);
    }
    return norm(this.x);
  }
}
