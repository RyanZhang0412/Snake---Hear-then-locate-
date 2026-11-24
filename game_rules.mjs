export const MAX_PLAYERS = 4;
export const JOIN_MS = 340;
export const CONTEST_MS = 1800;
export const SLOW_SPEED = 0.2;

const MATCH = Math.cos(30 * Math.PI / 180);
const MIN_SEPARATION = Math.cos(34 * Math.PI / 180);
const AXES = [
    { name: "USB 正前", step: [1, 0, 0] },
    { name: "右", step: [0, 1, 0] },
    { name: "后", step: [-1, 0, 0] },
    { name: "左", step: [0, -1, 0] },
    { name: "上", step: [0, 0, 1] },
    { name: "下", step: [0, 0, -1] },
];

const dot = (a, b) => a.reduce((sum, x, i) => sum + x * b[i], 0);
const unit = v => {
    const size = Math.hypot(...v);
    return size ? v.map(x => x / size) : v;
};
const equal = (a, b) => a.every((v, i) => v === b[i]);
const nextCell = (a, step) => a.map((x, i) => x + step[i]);

function vector(azimuth, elevation) {
    const az = (azimuth ?? 0) * Math.PI / 180, el = (elevation ?? 0) * Math.PI / 180;
    return [Math.cos(az) * Math.cos(el), Math.sin(az) * Math.cos(el), Math.sin(el)];
}

function heading(v) {
    return AXES.reduce((best, axis) => dot(v, axis.step) > dot(v, best.step) ? axis : best);
}

export class Contest {
    constructor() {
        this.phase = "waiting";
        this.groups = [];
        this.opened = 0;
        this.joinedAt = 0;
        this.deadline = 0;
        this.result = null;
    }

    get speed() { return this.phase === "slow" ? SLOW_SPEED : 1; }

    observe(groups, at, now) {
        let decision = this.advance(now);
        const candidates = groups.filter(item =>
            (Number.isFinite(item.azimuth) || (item.azimuth === null && Math.abs(item.elevation) === 90)) &&
            Number.isFinite(item.rms) && item.rms > 0 &&
            (item.elevation == null || Number.isFinite(item.elevation)));
        if (!candidates.length) return decision;
        if (this.phase === "joining" && at - this.joinedAt > JOIN_MS) {
            decision = this.finish();
        }
        if (this.phase === "waiting") {
            this.result = null;
            this.phase = "joining";
            this.joinedAt = at;
            this.opened = now;
            this.deadline = now + JOIN_MS;
        }
        this.add(candidates);
        if (this.phase === "joining" && this.groups.length > 1) {
            this.phase = "slow";
            this.deadline = now + CONTEST_MS;
        }
        return decision;
    }

    add(raw) {
        const used = new Set();
        for (const item of [...raw].sort((a, b) => b.rms - a.rms)) {
            const v = vector(item.azimuth, item.elevation);
            let closest = null, similarity = -1;
            for (const group of this.groups) {
                const match = dot(v, group.direction);
                if (match > similarity) {
                    closest = group;
                    similarity = match;
                }
            }
            if (closest && similarity >= MATCH) {
                if (used.has(closest)) continue;
                closest.sum += item.rms;
                closest.count++;
                closest.direction = unit(closest.direction.map((x, i) => x * (closest.count - 1) + v[i]));
                used.add(closest);
            } else if (this.groups.length < MAX_PLAYERS && similarity < MIN_SEPARATION) {
                const group = { id: this.groups.length + 1, player: item.player,
                    direction: v, sum: item.rms, count: 1 };
                this.groups.push(group);
                used.add(group);
            }
        }
    }

    ranks() {
        return this.groups.map(group => {
            const [x, y, z] = group.direction;
            const average = group.sum / group.count;
            return {
                id: group.id, player: group.player, count: group.count, rms: average,
                dbfs: 20 * Math.log10(average),
                azimuth: ((Math.atan2(y, x) * 180 / Math.PI + 360) % 360),
                elevation: Math.asin(Math.max(-1, Math.min(1, z))) * 180 / Math.PI,
                heading: heading(group.direction),
            };
        });
    }

    advance(now) {
        if (this.phase === "waiting" || now < this.deadline) return null;
        return this.finish();
    }

    finish() {
        const ranks = this.ranks().sort((a, b) => b.rms - a.rms);
        const winner = ranks[0];
        const tied = ranks.length > 1 && winner.dbfs - ranks[1].dbfs < .6;
        this.result = { winner: tied ? null : winner, tied, ranks };
        this.groups = [];
        this.phase = "waiting";
        this.deadline = 0;
        return this.result;
    }
}

export class SnakeBoard {
    dimensions = [9, 9, 5];

    constructor(random = Math.random, planar = false) {
        this.random = random;
        this.planar = planar;
        this.body = [[4, 4, 2]];
        this.direction = null;
        this.requested = null;
        this.score = 0;
        this.ended = false;
        this.food = this.newFood();
    }

    turn(axis) {
        if (this.ended || (this.planar && axis.step[2])) return;
        if (this.direction === null) this.direction = axis.step;
        this.requested = axis.step;
    }

    available(cell) {
        return cell.every((x, i) => x >= 0 && x < this.dimensions[i]) &&
            !this.body.some((part, i) => equal(cell, part) &&
                (i < this.body.length - 1 || equal(cell, this.food ?? [])));
    }

    step() {
        if (!this.direction || this.ended) return;
        if (this.requested && (this.body.length < 2 || dot(this.requested, this.direction) >= 0)) {
            this.direction = this.requested;
            this.requested = null;
        } else if (this.requested) {
            const bend = AXES.find(({ step }) => (!this.planar || !step[2]) &&
                dot(step, this.direction) === 0 && this.available(nextCell(this.body[0], step)));
            if (bend) this.direction = bend.step;
        }
        const next = nextCell(this.body[0], this.direction);
        if (!this.available(next)) {
            this.ended = true;
            return;
        }
        this.body.unshift(next);
        if (equal(next, this.food)) {
            this.score++;
            this.food = this.newFood();
            if (!this.food) this.ended = true;
        } else {
            this.body.pop();
        }
    }

    newFood() {
        const cells = [];
        for (let z = 0; z < this.dimensions[2]; z++) {
            if (this.planar && z !== this.body[0][2]) continue;
            for (let y = 0; y < this.dimensions[1]; y++) {
                for (let x = 0; x < this.dimensions[0]; x++) {
                    const cell = [x, y, z];
                    if (!this.body.some(part => equal(cell, part))) cells.push(cell);
                }
            }
        }
        return cells.length ? cells[Math.floor(this.random() * cells.length)] : null;
    }
}
