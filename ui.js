const get = id => document.getElementById(id);
const sphere = new DirectionSphere(get("sphere"));
let mode, running = false, demoElevation = 36, frame;
const pendingSettings = new Set();
const staticAzimuth = 72;

get("reset").onclick = () => sphere.reset();

function render(azimuth, elevation) {
    const roundedElevation = Math.round(elevation);
    get("azimuth").textContent = azimuth === null ? "—" : Math.round(azimuth) % 360 + "°";
    get("elevation").textContent = elevation === null ? "—" :
        (roundedElevation > 0 ? "+" : roundedElevation < 0 ? "−" : "") + Math.abs(roundedElevation) + "°";
    sphere.setDirection({ azimuth, elevation });
}

function demoButtons() {
    for (const button of document.querySelectorAll("[data-el]")) {
        button.setAttribute("aria-pressed", String(!running && Number(button.dataset.el) === demoElevation));
    }
    get("sweep").textContent = running ? "暂停绕场演示" : "播放绕场演示";
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
        if (!response.ok) get("calibration-note").textContent = "校准未启动，请确认两板在线且麦克风位置上下对应。";
    } catch {
        get("calibration-note").textContent = "本机服务已断开。";
    }
};

for (const field of ["sensitivity", "software_gain_db"]) {
    get(field).onchange = async event => {
        const selection = event.target.value;
        const value = field === "software_gain_db" ? Number(selection) : selection;
        pendingSettings.add(field);
        event.target.disabled = true;
        const note = get("audio-settings-note");
        try {
            const response = await fetch("/api/audio-settings", {
                method: "POST",
                headers: { "X-Array-UI": "local", "Content-Type": "application/json" },
                body: JSON.stringify({ field, value }),
            });
            if (!response.ok) throw new Error("setting rejected");
            note.textContent = "灵敏度影响检测阈值。增益用于响度显示。";
        } catch {
            note.textContent = "设置应用失败，请重试。";
        } finally {
            event.target.disabled = false;
            pendingSettings.delete(field);
        }
    };
}

function renderSound(state) {
    const input = state.level_dbfs;
    const level = Number.isFinite(input) ? Math.max(-100, Math.min(0, input + state.software_gain_db)) : null;
    get("level-meter").value = level === null ? 0 : level + 100;
    get("level-text").textContent = level === null ? "—" : level.toFixed(1) + " dBFS";
    const rise = Number.isFinite(state.noise_floor_dbfs) && level !== null
        ? Math.round(input - state.noise_floor_dbfs) : null;
    get("level-reference").textContent = rise === null ? "" :
        `原始 ${input.toFixed(1)} dBFS · 相对背景 ${rise >= 0 ? "+" : ""}${rise} dB`;
    const clap = { transient: "检测到短促声", possible: "疑似拍手", confirmed: "检测到拍手" }[state.clap_kind];
    get("clap-status").textContent = !state.clap_available ? "拍手检测不可用" :
        state.clap_active ? clap + (state.clap_direction_valid ? "" : " · 方向不稳") : "等待拍手";
    get("clap-status").classList.toggle("detected", Boolean(state.clap_active));
    get("clap-count").textContent = state.clap_count ?? 0;
    get("transient-count").textContent = state.transient_count ?? 0;
    for (const field of ["sensitivity", "software_gain_db"]) {
        if (!pendingSettings.has(field) && state[field] !== undefined) get(field).value = state[field];
    }
}

async function refresh() {
    try {
        const response = await fetch("/api/state");
        const state = await response.json();
        if (!mode) {
            mode = state.mode;
            get("mode").textContent = mode === "demo" ? "模拟模式" : "实机采集";
            get("live-panel").hidden = mode !== "live";
            get("demo-panel").hidden = mode !== "demo";
            get("geometry").textContent = "设定板间距 " + state.gap_cm.toFixed(1) + " cm";
            if (mode === "demo") {
                demoButtons();
                render(staticAzimuth, demoElevation);
                return;
            }
        }
        for (const key of ["windows", "skips", "gaps", "status"]) get(key).textContent = state[key];
        get("board-a").textContent = state.boards[0] ? "在线" : "离线";
        get("board-b").textContent = state.boards[1] ? "在线" : "离线";
        renderSound(state);
        get("calibrate").disabled = !state.boards.every(Boolean) || state.source === "learning" || state.calibration === "sampling";
        get("calibrate").textContent = state.calibration === "calibrated" ? "重新校准俯仰零点" : "校准俯仰零点";
        get("tracking-note").textContent = {
            learning: "正在测背景声，请先安静一秒。",
            quiet: "等待发声",
            uncertain: "正在确认方向，请保持位置继续发声。",
            tracking: "正在跟踪声源。"
        }[state.source];
        get("calibration-note").textContent = {
            ready: "让声源位于两板中间高度，点击后持续发声约 5 秒。",
            sampling: "正在校准，请保持声源高度并继续发声。",
            calibrated: "校准完成。可从上方和下方发声，检查俯仰角正负。",
            retry: "校准未收到足够稳定的声音。请在中间高度用清楚的连续人声重试。"
        }[state.calibration];
        render(state.azimuth, state.elevation);
    } catch {
        get("mode").textContent = "本机服务已断开";
        render(null, null);
    }
    setTimeout(refresh, 160);
}
refresh();
