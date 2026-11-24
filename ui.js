const get = id => document.getElementById(id);
const languageMenu = get("language");
const languageKey = "sphere.language";
const labels = new Map();
try {
    if (localStorage.getItem(languageKey) === "zh-CN") languageMenu.value = "zh-CN";
} catch {}

function displayLabel(element, [en, zh]) {
    const text = languageMenu.value === "zh-CN" ? zh : en;
    if (element.textContent !== text) element.textContent = text;
}

function setLabel(id, en, zh) {
    const element = get(id);
    const pair = [en, zh];
    labels.set(element, pair);
    displayLabel(element, pair);
}

for (const element of document.querySelectorAll("[data-zh]")) {
    labels.set(element, [element.textContent, element.dataset.zh]);
}
const ariaLabels = Array.from(document.querySelectorAll("[data-zh-label]"), element =>
    [element, element.getAttribute("aria-label"), element.dataset.zhLabel]);

function applyLanguage() {
    document.documentElement.lang = languageMenu.value;
    for (const [element, pair] of labels) displayLabel(element, pair);
    for (const [element, en, zh] of ariaLabels) {
        element.setAttribute("aria-label", languageMenu.value === "zh-CN" ? zh : en);
    }
}
languageMenu.onchange = () => {
    applyLanguage();
    try { localStorage.setItem(languageKey, languageMenu.value); } catch {}
};
applyLanguage();

const sphere = new DirectionSphere(get("sphere"));
let mode, running = false, demoElevation = 36, frame;
const pendingSettings = new Set();
const staticAzimuth = 72;
const directionColors = ["direction-one", "direction-two", "direction-three", "direction-four"];
const roundSeconds = .34;
const markerHoldMs = 1600;
const sameDirection = Math.cos(30 * Math.PI / 180);
let clapCursor, clapRound, renderedRound, markers = [];
const previewUrls = {};
let previewLoading = false;

get("reset").onclick = () => sphere.reset();

function formatAzimuth(azimuth) {
    return azimuth === null ? "—" : Math.round(azimuth) % 360 + "°";
}

function formatElevation(elevation) {
    const roundedElevation = Math.round(elevation);
    return elevation === null ? "—" :
        (roundedElevation > 0 ? "+" : roundedElevation < 0 ? "−" : "") + Math.abs(roundedElevation) + "°";
}

function render(azimuth, elevation) {
    get("azimuth").textContent = formatAzimuth(azimuth);
    get("elevation").textContent = formatElevation(elevation);
    sphere.setDirection({ azimuth, elevation }, markers);
}

function addClapEvent(event, now) {
    if (!clapRound || now >= clapRound.until || event.at < clapRound.at ||
        event.at - clapRound.at > roundSeconds) {
        clapRound = { at: event.at, until: now + markerHoldMs, directions: [] };
    }
    const directions = clapRound.directions;
    for (const { azimuth, elevation } of event.groups) {
        if (directions.length === directionColors.length) break;
        const vector = sphere.vector(azimuth ?? 0, elevation ?? 0);
        if (directions.some(item => item.vector.reduce((dot, v, i) => dot + v * vector[i], 0) >= sameDirection)) {
            continue;
        }
        directions.push({ azimuth, elevation, vector });
    }
    clapRound.until = now + markerHoldMs;
}

async function refreshClaps() {
    try {
        const query = clapCursor === undefined ? "" : "?after=" + clapCursor;
        const response = await fetch("/api/claps" + query);
        if (!response.ok) throw new Error("clap feed unavailable");
        const feed = await response.json();
        const now = performance.now();
        if (!feed.ready || (clapCursor !== undefined && feed.cursor < clapCursor) ||
            (clapRound && now >= clapRound.until)) clapRound = null;
        clapCursor = feed.cursor;
        if (feed.ready) for (const event of feed.events) addClapEvent(event, now);
    } catch {
        clapCursor = undefined;
        clapRound = null;
    }
    showClapDirections();
}

function showClapDirections(speechDirection, speechCount) {
    const directions = speechDirection ? [speechDirection] : clapRound?.directions ?? [];
    const source = speechDirection ? speechCount : clapRound;
    if (renderedRound === source && markers.length === directions.length) return;
    renderedRound = source;
    const readout = get("clap-directions");
    readout.replaceChildren();
    get("clap-readout").hidden = !directions.length;
    get("continuous-readout").hidden = Boolean(directions.length);
    markers = directions.map(({ azimuth, elevation }, i) => {
        const row = readout.insertRow();
        const number = document.createElement("span");
        number.className = "direction-number " + directionColors[i];
        number.textContent = i + 1;
        row.insertCell().append(number);
        row.insertCell().textContent = formatAzimuth(azimuth);
        row.insertCell().textContent = formatElevation(elevation);
        return { azimuth, elevation, number: i + 1, color: getComputedStyle(number).backgroundColor };
    });
}

function demoButtons() {
    for (const button of document.querySelectorAll("[data-el]")) {
        button.setAttribute("aria-pressed", String(!running && Number(button.dataset.el) === demoElevation));
    }
    setLabel("sweep", running ? "Pause orbit demo" : "Play orbit demo", running ? "暂停绕场演示" : "播放绕场演示");
    get("sweep").setAttribute("aria-pressed", String(running));
}

for (const button of document.querySelectorAll("[data-el]")) {
    button.onclick = () => {
        running = false;
        cancelAnimationFrame(frame);
        demoElevation = Number(button.dataset.el);
        render(staticAzimuth, demoElevation);
        demoButtons();
    };
}
get("sweep").onclick = () => {
    running = !running;
    demoButtons();
    if (!running) {
        cancelAnimationFrame(frame);
        return;
    }
    const started = performance.now();
    function animate(now) {
        const time = (now - started) / 1000;
        render((staticAzimuth + time * 37) % 360, 54 * Math.cos(time * .82));
        frame = requestAnimationFrame(animate);
    }
    frame = requestAnimationFrame(animate);
};

get("calibrate").onclick = async () => {
    get("calibrate").disabled = true;
    try {
        const response = await fetch("/api/calibrate", { method: "POST", headers: { "X-Array-UI": "local" } });
        if (!response.ok) setLabel("calibration-note", "Calibration could not start. Check that both boards are online and their microphones align vertically.", "校准未启动，请确认两板在线且麦克风位置上下对应。");
    } catch {
        setLabel("calibration-note", "Local service disconnected.", "本机服务已断开。");
    }
};

const previewPlayer = get("preview-player");
for (const version of ["raw", "cleaned"]) {
    get("preview-" + version).onclick = async () => {
        previewPlayer.src = previewUrls[version];
        get("preview-raw").setAttribute("aria-pressed", String(version === "raw"));
        get("preview-cleaned").setAttribute("aria-pressed", String(version === "cleaned"));
        try {
            await previewPlayer.play();
        } catch (error) {
            if (error.name !== "AbortError") setLabel("preview-status", "Click Play on the audio player to start playback.", "试听未启动，请点播放器的播放键。");
        }
    };
}

get("capture-preview").onclick = async () => {
    previewLoading = true;
    get("capture-preview").disabled = true;
    setLabel("preview-status", "Preparing audio…", "正在准备试听…");
    try {
        const response = await fetch("/api/audio-preview", { method: "POST", headers: { "X-Array-UI": "local" } });
        if (!response.ok) throw new Error("no preview");
        const clip = await response.json();
        previewPlayer.pause();
        previewPlayer.removeAttribute("src");
        previewPlayer.load();
        for (const version of ["raw", "cleaned"]) {
            if (previewUrls[version]) URL.revokeObjectURL(previewUrls[version]);
            const bytes = Uint8Array.from(atob(clip[version]), char => char.charCodeAt(0));
            previewUrls[version] = URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
            get("preview-" + version).setAttribute("aria-pressed", "false");
        }
        get("preview-controls").hidden = false;
        get("preview-cleaned").disabled = !clip.noise_ready;
        const missing = clip.missing_ms ? ` · 缺音 ${clip.missing_ms} ms，已补静音` : "";
        const seconds = clip.duration.toFixed(1);
        const gap = clip.missing_ms ? ` (${clip.missing_ms} ms of missing audio filled with silence)` : "";
        setLabel("preview-status", `Captured ${seconds} seconds${gap}. Both clips use the same playback gain.`,
            `已抓取 ${seconds} 秒${missing}。两版使用相同试听增益。`);
    } catch {
        setLabel("preview-status", "Audio capture failed. Check that both boards are receiving audio.", "暂时无法抓取音频，请确认双板正在收音。");
    } finally {
        previewLoading = false;
    }
};

const audioFields = ["sensitivity", "software_gain_db", "trigger_input", "denoise", "transport_format"];
for (const field of audioFields) {
    get(field).onchange = async event => {
        const selection = event.target.value;
        const value = field === "denoise" ? event.target.checked :
            field === "software_gain_db" ? Number(selection) : selection;
        pendingSettings.add(field);
        event.target.disabled = true;
        try {
            const response = await fetch("/api/audio-settings", {
                method: "POST",
                headers: { "X-Array-UI": "local", "Content-Type": "application/json" },
                body: JSON.stringify({ field, value }),
            });
            if (!response.ok) throw new Error("setting rejected");
            setLabel("audio-settings-note", "Sensitivity adjusts the detection threshold. Gain adjusts the displayed level.", "灵敏度影响检测阈值。增益用于响度显示。");
        } catch {
            setLabel("audio-settings-note", "Could not apply settings. Try again.", "设置应用失败，请重试。");
        } finally {
            event.target.disabled = false;
            pendingSettings.delete(field);
        }
    };
}

let lastWifiSample, gapPercent;
function renderTransport(state) {
    const selector = get("transport_format");
    selector.disabled = pendingSettings.has("transport_format") || !state.format_available;
    if (!state.boards.some(Boolean)) {
        setLabel("transport-note", "Waiting for audio", "等待接收音频");
    } else if (!state.format_available) {
        setLabel("transport-note", "Original stream · format switching unavailable", "原始传输 · 设备尚未报告支持切换格式");
    } else if (state.transport_format !== state.format_active) {
        setLabel("transport-note", "Waiting for the requested format…", "等待所选格式的数据…");
    } else {
        setLabel("transport-note", "Selected format is streaming", "正在接收所选格式");
    }
    const {format_active: format, wifi_records: received, wifi_missing: missing} = state;
    const now = performance.now();
    if (!lastWifiSample || format !== lastWifiSample.format || received < lastWifiSample.received ||
        missing < lastWifiSample.missing || !state.boards.every(Boolean)) {
        lastWifiSample = {format, received, missing, now};
        gapPercent = null;
    } else if (now - lastWifiSample.now >= 5000) {
        const lost = missing - lastWifiSample.missing;
        const total = received - lastWifiSample.received + lost;
        gapPercent = total > 0 ? (100 * lost / total).toFixed(2) : null;
        lastWifiSample = {format, received, missing, now};
    }
    const rate = state.wifi_mbps?.toFixed(2);
    const enGaps = gapPercent == null ? "" : ` · ${gapPercent}% sequence gaps (5 s)`;
    const zhGaps = gapPercent == null ? "" : ` · 近 5 秒序号缺口 ${gapPercent}%`;
    setLabel("transport-rate", rate ? `Received ${rate} Mbps (IPv4/UDP)${enGaps}` : "",
        rate ? `实收 ${rate} Mbps（IPv4/UDP）${zhGaps}` : "");
}

function renderSound(state) {
    renderTransport(state);
    const input = state.level_dbfs;
    const level = Number.isFinite(input) ? Math.max(-100, Math.min(0, input + state.software_gain_db)) : null;
    get("level-meter").value = level === null ? 0 : level + 100;
    get("level-text").textContent = level === null ? "—" : level.toFixed(1) + " dBFS";
    const rise = Number.isFinite(state.noise_floor_dbfs) && level !== null
        ? Math.round(input - state.noise_floor_dbfs) : null;
    const relative = rise >= 0 ? "+" + rise : String(rise);
    setLabel("level-reference",
        rise === null ? "" : `Raw ${input.toFixed(1)} dBFS · ${relative} dB relative to background`,
        rise === null ? "" : `原始 ${input.toFixed(1)} dBFS · 相对背景 ${relative} dB`);
    const speech = state.trigger_input === "speech";
    const live = speech ? state.speech_active : state.clap_active;
    const clap = {
        transient: ["Transient detected", "检测到短促声"],
        possible: ["Possible clap", "疑似拍手"],
        confirmed: ["Clap detected", "检测到拍手"]
    }[state.clap_kind];
    let triggerLabel;
    if (speech) {
        triggerLabel = live ? ["Snake recognized", "已识别 Snake"] : state.speech_missed
            ? ["Speech heard, no keyword match", "收到语音片段，未匹配口令"] : ["Listening for Snake", "等待说 Snake"];
    } else {
        triggerLabel = !state.clap_available ? ["Clap detection unavailable", "拍手检测不可用"]
            : live && clap ? clap : ["Listening for a clap", "等待拍手"];
    }
    const unstable = live && (speech ? !state.speech_direction : state.clap_available && !state.clap_direction_valid);
    setLabel("clap-status", triggerLabel[0] + (unstable ? " · direction uncertain" : ""),
        triggerLabel[1] + (unstable ? " · 方向不稳" : ""));
    get("clap-status").classList.toggle("detected", Boolean(live));
    get("clap-count").textContent = (speech ? state.speech_count : state.clap_count) ?? 0;
    get("speech-attempts").hidden = !speech;
    get("speech-attempts").textContent = ` / ${state.speech_attempts ?? 0}`;
    get("capture-preview").disabled = previewLoading || !state.preview_ready;
    get("transient-count").textContent = state.transient_count ?? 0;
    get("voice-option").disabled = !state.speech_available;
    setLabel("voice-option", state.keyword_model ? "Keyword model · Snake" : "Voice · Snake",
        state.keyword_model ? "关键词模型 · Snake" : "语音 · Snake");
    for (const field of audioFields) {
        if (pendingSettings.has(field) || state[field] === undefined) continue;
        if (field === "denoise") get(field).checked = state[field];
        else get(field).value = field === "transport_format" && !state.format_available ? "original" : state[field];
    }
}

const serviceMessages = {
    "正在启动双板": "Starting both boards",
    "双板接收中": "Receiving audio from both boards",
    "正在等待两块板的数据": "Waiting for data from both boards",
    "等待 ESP32 音频": "Waiting for ESP32 audio",
    "正在检查 ESP32 和两块麦克风板": "Checking the ESP32 and both microphone boards",
    "正在连接 Wi-Fi": "Connecting to Wi-Fi",
    "请接上 ESP32 的 USB，或用 --serial COM14 指定端口": "Connect the ESP32 by USB or specify its port with --serial COM14.",
    "ESP32 未读到两块板的预期固件版本，请确认 USB 与 A/B 接线": "The ESP32 could not verify both firmware versions. Check USB and the A/B wiring.",
    "麦克风路由设置失败": "Microphone routing failed.",
    "ESP32 未连接到 Windows 当前的 Wi-Fi": "The ESP32 could not join the current Windows Wi-Fi network.",
    "I²S 没有持续收帧，请检查 BCLK/LRCK": "I²S frames stopped. Check BCLK and LRCK."
};

async function refresh() {
    try {
        const response = await fetch("/api/state");
        const state = await response.json();
        setLabel("mode", ...(state.mode === "demo" ? ["Demo mode", "模拟模式"] : ["Live capture", "实机采集"]));
        if (!mode) {
            mode = state.mode;
            get("live-panel").hidden = mode !== "live";
            get("demo-panel").hidden = mode !== "demo";
            const gap = state.gap_cm.toFixed(1) + " cm";
            setLabel("geometry", "Board spacing " + gap, "设定板间距 " + gap);
            if (mode === "demo") {
                demoButtons();
                render(staticAzimuth, demoElevation);
                return;
            }
        }
        for (const key of ["windows", "skips", "gaps"]) get(key).textContent = state[key];
        setLabel("status", serviceMessages[state.status] ?? state.status, state.status);
        for (const [i, id] of ["board-a", "board-b"].entries()) {
            setLabel(id, ...(state.boards[i] ? ["Online", "在线"] : ["Offline", "离线"]));
        }
        renderSound(state);
        get("calibrate").disabled = !state.boards.every(Boolean) || state.source === "learning" || state.calibration === "sampling";
        setLabel("calibrate", ...(state.calibration === "calibrated"
            ? ["Recalibrate zero elevation", "重新校准俯仰零点"] : ["Calibrate zero elevation", "校准俯仰零点"]));
        const tracking = {
            learning: ["Measuring background noise. Keep quiet for one second.", "正在测背景声，请先安静一秒。"],
            quiet: ["Listening for sound", "等待发声"],
            uncertain: ["Checking direction. Stay in place and keep making sound.", "正在确认方向，请保持位置继续发声。"],
            tracking: ["Tracking sound source", "正在跟踪声源。"]
        }[state.source];
        setLabel("tracking-note", ...(tracking ?? ["", ""]));
        const calibration = {
            ready: ["Place the sound source halfway between the boards. Click Calibrate and make a steady sound for about 5 seconds.", "让声源位于两板中间高度，点击后持续发声约 5 秒。"],
            sampling: ["Calibrating. Keep the source at the same height and continue making sound.", "正在校准，请保持声源高度并继续发声。"],
            calibrated: ["Calibrated. Make a sound above and below the array to check the elevation sign.", "校准完成。可从上方和下方发声，检查俯仰角正负。"],
            retry: ["Calibration needs a steadier sound. Speak continuously halfway between the boards and try again.", "校准未收到足够稳定的声音。请在中间高度用清楚的连续人声重试。"]
        }[state.calibration];
        setLabel("calibration-note", ...(calibration ?? ["", ""]));
        if (state.trigger_input === "speech") {
            clapCursor = undefined;
            clapRound = null;
            showClapDirections(state.speech_direction, state.speech_count);
        } else {
            await refreshClaps();
        }
        render(state.azimuth, state.elevation);
    } catch {
        setLabel("mode", "Local service disconnected", "本机服务已断开");
        get("transport_format").disabled = true;
        setLabel("transport-note", "Waiting for audio", "等待接收音频");
        setLabel("transport-rate", "", "");
        get("capture-preview").disabled = true;
        clapCursor = undefined;
        clapRound = null;
        showClapDirections();
        render(null, null);
    }
    setTimeout(refresh, 160);
}
refresh();
