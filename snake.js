import { Contest, SnakeBoard } from "./game_rules.mjs";

const byId = id => document.getElementById(id);
const canvas = byId("snake-board");
const ctx = canvas.getContext("2d");
const players = [
    { azimuth: 0, elevation: 0, dbfs: -42 },
    { azimuth: 90, elevation: 0, dbfs: -36 },
    { azimuth: 210, elevation: 56, dbfs: -31 },
    { azimuth: 300, elevation: -55, dbfs: -39 },
];

let board = new SnakeBoard();
let contest = new Contest();
let mode = null;
let round = 0;
let active = false;
let calibrated = false;
let cursor = 0;
let elapsed = 0;
let prev = performance.now();
let direction = "—";
let status = "待开始";
let connected = true;
let lastRankText = "";
const gameStep = 390;

const groupName = group => group.player === undefined ? `方向组 ${group.id}` : `玩家 ${group.player}`;

function choose(result) {
    if (!result) return;
    if (result.winner) {
        board.turn(result.winner.heading);
        direction = result.winner.heading.name;
        status = result.ranks.length > 1 ? `${groupName(result.winner)} 获胜` : "移动中";
    } else if (result.tied) {
        status = "平均响度接近，继续沿原方向";
    }
}

function observe(groups, at) {
    if (!active || board.ended) return;
    choose(contest.observe(groups, at, performance.now()));
}

function updateLabels(now) {
    byId("game-status").textContent = board.ended ? "游戏结束" :
        !connected ? "等待两块麦克风板" :
        contest.phase === "slow" ? "时停 · 比较平均响度" :
        contest.phase === "joining" ? "等待其他方向" : status;
    byId("game-speed").textContent = contest.speed + "×";
    byId("game-score").textContent = board.score;
    byId("game-direction").textContent = "方向 " + direction;
    byId("game-timer").textContent = contest.phase === "slow" ?
        "还可拍手 " + Math.max(0, (contest.deadline - now) / 1000).toFixed(1) + " 秒" : "";
    const values = contest.groups.length ? contest.ranks() : contest.result?.ranks ?? [];
    const rows = values.map(g =>
        `${groupName(g)}（${g.heading.name}） · ${g.count} 次 · 平均 ${g.dbfs.toFixed(1)} dBFS`);
    const text = rows.join("\n");
    if (text !== lastRankText) {
        lastRankText = text;
        byId("game-ranks").replaceChildren(...rows.map(row => {
            const li = document.createElement("li");
            li.textContent = row;
            return li;
        }));
    }
}

function draw() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const [nx, ny, nz] = board.dimensions;
    const unit = Math.min((w - 48) / (nx + ny), (h - 70) / (0.47 * (nx + ny) + 1.18 * nz));
    if (unit <= 0) return;
    const origin = [w * .5, h * .47];
    const project = ([x, y, z]) => [origin[0] + (y - x) * unit,
        origin[1] + (x + y) * .47 * unit - 1.18 * z * unit];
    function line(path, color) {
        ctx.beginPath();
        path.forEach((p, i) => {
            const [x, y] = project(p);
            if (i) ctx.lineTo(x, y);
            else ctx.moveTo(x, y);
        });
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        ctx.stroke();
    }
    for (let x = 0; x <= nx; x++) line([[x, 0, 0], [x, ny, 0]], "#ddd");
    for (let y = 0; y <= ny; y++) line([[0, y, 0], [nx, y, 0]], "#ddd");
    const corners = [[0, 0], [0, ny], [nx, ny], [nx, 0]];
    corners.forEach(([x, y]) => line([[x, y, 0], [x, y, nz]], "#ddd"));
    line([...corners, corners[0]].map(([x, y]) => [x, y, nz]), "#ddd");
    const items = board.body.map((cell, index) => ({ cell, type: index ? "body" : "head" }));
    if (board.food) items.push({ cell: board.food, type: "food" });
    const depth = ([x, y, z]) => x + y + z * .34;
    items.sort((a, b) => depth(a.cell) - depth(b.cell));
    for (const { cell: [x, y, z], type } of items) {
        const [sx, sy] = project([x + .5, y + .5, z + .4]);
        if (type === "food") {
            ctx.beginPath();
            ctx.arc(sx, sy, unit * .24, 0, 2 * Math.PI);
            ctx.strokeStyle = "#333";
            ctx.lineWidth = 1.7;
            ctx.stroke();
            continue;
        }
        const layer = type === "head" ? "#202020" : "#656565";
        const faces = [
            [[x, y, z + .75], [x + .84, y, z + .75], [x + .84, y + .84, z + .75], [x, y + .84, z + .75]],
            [[x + .84, y, z + .75], [x + .84, y + .84, z + .75], [x + .84, y + .84, z], [x + .84, y, z]],
            [[x, y + .84, z + .75], [x + .84, y + .84, z + .75], [x + .84, y + .84, z], [x, y + .84, z]],
        ];
        for (const [i, face] of faces.entries()) {
            ctx.beginPath();
            for (const [j, corner] of face.entries()) {
                const p = project(corner);
                if (j) ctx.lineTo(...p);
                else ctx.moveTo(...p);
            }
            ctx.closePath();
            ctx.fillStyle = layer;
            ctx.fill();
            ctx.strokeStyle = "#999";
            ctx.stroke();
            if (i === 2 && type === "head" && board.direction?.[2]) {
                line([[x + .12, y + .84, z + .4], [x + .72, y + .84, z + .4]], "#fff");
            }
        }
    }
    ctx.font = "13px system-ui, sans-serif";
    ctx.fillStyle = "#555";
    ctx.textAlign = "right";
    ctx.fillText("USB 前", ...project([nx + .7, 0., 0.]));
    ctx.textAlign = "left";
    ctx.fillText("右", ...project([0, ny + .45, 0.]));
}

function animate(now) {
    const dt = Math.min(95, now - prev);
    prev = now;
    if (active && !board.ended && connected) {
        choose(contest.advance(now));
        if (board.direction) {
            elapsed += dt * contest.speed;
            if (elapsed >= gameStep) {
                board.step();
                elapsed %= gameStep;
            }
        }
    }
    updateLabels(now);
    draw();
    requestAnimationFrame(animate);
}

async function poll(generation) {
    try {
        const r = await fetch("/api/claps?after=" + cursor);
        if (!r.ok) throw new Error("game input unavailable");
        const state = await r.json();
        if (round !== generation) return;
        connected = state.ready;
        if (state.cursor < cursor) contest = new Contest();
        cursor = state.cursor;
        for (const event of state.events) observe(event.groups, event.at * 1000);
    } catch {
        connected = false;
    }
    if (active && !board.ended && round === generation) setTimeout(() => poll(generation), 85);
}

byId("game-start").onclick = async () => {
    const startButton = byId("game-start");
    const generation = ++round;
    startButton.disabled = true;
    active = false;
    try {
        if (mode === "live") {
            const r = await fetch("/api/claps");
            if (!r.ok) throw new Error("array feed unavailable");
            const state = await r.json();
            calibrated = state.calibrated;
            connected = state.ready;
            cursor = state.cursor;
        }
        board = new SnakeBoard(Math.random, mode === "live" && !calibrated);
        contest = new Contest();
        active = true;
        direction = "—";
        status = "等待拍手";
        elapsed = 0;
        byId("game-mode").textContent = mode === "demo" ? "模拟 · 最多四人" :
            "实机 · " + (calibrated ? "三维方向" : "水平模式，校准后重开可用俯仰");
        startButton.textContent = "重新开始";
        if (mode === "live") poll(generation);
    } catch {
        status = "无法连接阵列，请重试";
    } finally {
        startButton.disabled = false;
    }
};

function mock(indices) {
    const now = performance.now();
    observe(indices.map(index => {
        const { azimuth, elevation, dbfs } = players[index];
        return { azimuth, elevation, rms: 10 ** (dbfs / 20), player: index + 1 };
    }), now);
}

players.forEach((player, index) => {
    const row = document.createElement("div");
    row.className = "mock-player";
    const hit = document.createElement("button");
    hit.textContent = `玩家 ${index + 1} 拍手`;
    hit.onclick = () => mock([index]);
    const control = document.createElement("input");
    control.type = "range";
    control.min = "-62";
    control.max = "-22";
    control.value = player.dbfs;
    control.setAttribute("aria-label", `玩家 ${index + 1} 响度`);
    const value = document.createElement("output");
    value.textContent = player.dbfs + " dBFS";
    control.oninput = () => {
        player.dbfs = Number(control.value);
        value.textContent = player.dbfs + " dBFS";
    };
    row.append(hit, control, value);
    byId("mock-players").append(row);
});
byId("mock-all").onclick = () => mock([0, 1, 2, 3]);

async function setup() {
    const start = byId("game-start");
    start.disabled = true;
    try {
        const r = await fetch("/api/state");
        if (!r.ok) throw new Error("not connected");
        mode = (await r.json()).mode;
        byId("game-mode").textContent = mode === "demo" ? "模拟 · 最多四人" : "实机 · 最多四人";
        byId("mock-input").hidden = mode !== "demo";
        start.disabled = false;
    } catch {
        status = "无法连接游戏服务";
    }
}
setup();
requestAnimationFrame(animate);
