import { fitLine, handDirections, toAngles, OneEuro3, norm } from "../hand_direction.js";
import assert from "assert";
const close = (a, b, e = 1e-6) => a.every((v, i) => Math.abs(v - b[i]) < e);
// straight line, noisy middle points
let r = fitLine([[0,0,0],[0.01,0.3,0.002],[0,0.6,-0.001],[0.002,1,0]]);
assert.ok(close(r.dir, norm([0.002,1,0]), 0.02) && r.straightness > 0.99);
assert.deepEqual(fitLine([[1,1,1],[1,1,1],[1,1,1],[1,1,1]]).dir, [0,0,0]);
// angles
const A = v => { const a = toAngles(norm(v)); return [Math.round(a.yaw), Math.round(a.pitch)]; };
assert.deepEqual(A([0,0,1]), [0,0]); assert.deepEqual(A([1,0,0]), [90,0]); assert.deepEqual(A([-1,0,0]), [-90,0]);
assert.deepEqual(A([0,1,0]), [0,90]); assert.deepEqual(A([1,1,0]), [90,45]); assert.deepEqual(A([0,0,-1]), [180,0]);
// synthetic hand in MediaPipe raw-camera world coords: x right(image), y down, z away from camera.
// Real right hand, palm towards the camera, fingers up, index finger pointing up.
// In the raw (unmirrored) image the person's right hand thumb is on the image RIGHT side... build
// directly in the player frame and convert back: player=(-x,-y,-z) -> mp = (-px,-py,-pz)
const toMP = p => ({ x: -p[0], y: -p[1], z: -p[2] });
const playerHand = Array.from({ length: 21 }, () => [0, 0, 0]);
playerHand[0] = [0, 0, 0];             // wrist
playerHand[5] = [-0.03, 0.09, 0];      // index knuckle, thumb side = player's left for a right hand
playerHand[6] = [-0.03, 0.13, 0]; playerHand[7] = [-0.03, 0.155, 0]; playerHand[8] = [-0.03, 0.18, 0];
playerHand[9] = [0, 0.095, 0]; playerHand[17] = [0.035, 0.08, 0];
const W = playerHand.map(toMP);
let d = handDirections(W, "Left");     // MediaPipe calls a real right hand "Left" on a raw frame
assert.ok(close(d.index, [0,1,0], 1e-6), "index points up");
assert.ok(close(d.hand, norm([0,0.095,0]), 1e-6), "hand points up");
assert.ok(close(d.palm, [0,0,1], 1e-6), "palm faces the screen: " + d.palm);
assert.ok(d.extension > 1.9);
// One Euro: converges to a constant input, unit length
const f = new OneEuro3(); let out; for (let t = 0; t < 2000; t += 33) out = f.filter([0.2, 0.9, 0.1], t);
assert.ok(close(out, norm([0.2, 0.9, 0.1]), 1e-3) && Math.abs(Math.hypot(...out) - 1) < 1e-9);
console.log("hand_direction tests passed");
