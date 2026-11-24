// Webcam + MediaPipe Hand Landmarker, shared by camera_control.js (game input) and hand3d.html
// (3D direction bench). Runs offline from vendor/mediapipe/ (tools/fetch_vendor.sh).

import { FilesetResolver, HandLandmarker } from "./vendor/mediapipe/vision_bundle.mjs";

const BASE = new URL("./vendor/mediapipe/", import.meta.url).href;

export async function createHandLandmarker(numHands = 2) {
  const fileset = await FilesetResolver.forVisionTasks(BASE + "wasm");
  return HandLandmarker.createFromOptions(fileset, {
    baseOptions: { modelAssetPath: BASE + "hand_landmarker.task", delegate: "GPU" },
    runningMode: "VIDEO", numHands,
  });
}

// Laptops often have an infrared (Windows Hello) camera next to the colour one, and the browser may
// pick it by default; hand tracking on the dark IR image is poor. After the first open (labels are
// only visible once permission is granted) switch to a non-IR camera if there is one.
//   cameraLabel: a substring of the camera name to force a specific device
//   audio: also open the microphone (for clap detection)
// Returns { stream, label }.
export async function openCamera({ cameraLabel, audio = false, width = 640, height = 480 } = {}) {
  const want = id => ({ video: { width, height, ...(id ? { deviceId: { exact: id } } : { facingMode: "user" }) }, audio });
  let stream = await navigator.mediaDevices.getUserMedia(want());
  const cams = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === "videoinput");
  const cur = stream.getVideoTracks()[0]?.label || "";
  const isIR = l => /\b(ir|infrared)\b/i.test(l);
  const pick = cameraLabel ? cams.find(c => c.label.toLowerCase().includes(cameraLabel.toLowerCase()))
             : isIR(cur) ? cams.find(c => !isIR(c.label)) : null;
  if (pick && pick.label !== cur) {
    stream.getTracks().forEach(t => t.stop());
    stream = await navigator.mediaDevices.getUserMedia(want(pick.deviceId));
  }
  return { stream, label: stream.getVideoTracks()[0]?.label || "unknown camera" };
}

export const BONES = [[0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11], [11, 12],
                      [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [17, 18], [18, 19], [19, 20], [0, 17]];
