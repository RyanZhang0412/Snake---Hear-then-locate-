// Unit sphere, equator and separate upper/lower meshes follow the layout in
// IntRoLab's ODAS Studio source_sphere.js. The renderer and interaction are local.
class DirectionSphere {
    constructor(canvas) {
        this.canvas = canvas;
        this.ctx = canvas.getContext("2d");
        this.mesh = this.makeMesh();
        this.angles = { azimuth: null, elevation: null };
        this.clapDirections = [];
        this.reset();
        new ResizeObserver(() => this.draw()).observe(canvas);

        canvas.addEventListener("pointerdown", event => {
            this.drag = [event.clientX, event.clientY];
            canvas.setPointerCapture(event.pointerId);
        });
        canvas.addEventListener("pointermove", event => {
            if (!this.drag) return;
            this.rotate((event.clientX - this.drag[0]) * .008, (event.clientY - this.drag[1]) * .008);
            this.drag = [event.clientX, event.clientY];
        });
        for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) {
            canvas.addEventListener(name, () => { this.drag = null; });
        }
        canvas.addEventListener("keydown", event => {
            const turn = { ArrowLeft: [-.12, 0], ArrowRight: [.12, 0], ArrowUp: [0, .12], ArrowDown: [0, -.12] };
            if (turn[event.key]) {
                event.preventDefault();
                this.rotate(...turn[event.key]);
            }
        });
    }

    vector(azimuth, elevation) {
        const az = azimuth * Math.PI / 180, el = elevation * Math.PI / 180;
        return [Math.cos(el) * Math.cos(az), Math.cos(el) * Math.sin(az), Math.sin(el)];
    }

    makeMesh() {
        const curves = [];
        for (const side of [1, -1]) {
            for (let az = 0; az < 360; az += 45) {
                curves.push({ side, points: Array.from({ length: 31 }, (_, i) => this.vector(az, side * i * 3)) });
            }
            for (const el of [30, 60]) {
                curves.push({ side, points: Array.from({ length: 121 }, (_, i) => this.vector(i * 3, side * el)) });
            }
        }
        curves.push({ side: 0, points: Array.from({ length: 121 }, (_, i) => this.vector(i * 3, 0)) });
        return curves;
    }

    reset() {
        this.yaw = .51;
        this.tilt = .27;
        this.draw();
    }

    rotate(yaw, tilt) {
        this.yaw += yaw;
        this.tilt = Math.max(-1.48, Math.min(1.48, this.tilt + tilt));
        this.draw();
    }

    setDirection(angles, clapDirections = []) {
        this.angles = angles;
        this.clapDirections = clapDirections;
        this.draw();
    }

    draw() {
        const { canvas, ctx } = this;
        const w = canvas.clientWidth, h = canvas.clientHeight;
        if (!w || !h) return;
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
            canvas.width = Math.round(w * dpr);
            canvas.height = Math.round(h * dpr);
        }
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        const cx = w / 2, cy = h / 2;
        const r = Math.max(1, Math.min((w - 92) / 2, (h - 54) / 2));
        const cosYaw = Math.cos(this.yaw), sinYaw = Math.sin(this.yaw);
        const cosTilt = Math.cos(this.tilt), sinTilt = Math.sin(this.tilt);
        function project([x, y, z]) {
            const forward = x * cosYaw + y * sinYaw;
            // Viewed from +Z, USB points down and positive azimuth runs clockwise.
            return [cx + r * (x * sinYaw - y * cosYaw),
                    cy - r * (-forward * sinTilt + z * cosTilt), forward * cosTilt + z * sinTilt];
        }
        ctx.clearRect(0, 0, w, h);
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, 2 * Math.PI);
        ctx.strokeStyle = "#777";
        ctx.lineWidth = 1;
        ctx.stroke();

        for (const near of [false, true]) {
            for (const { side, points } of this.mesh) {
                const projected = points.map(project);
                ctx.beginPath();
                for (let i = 1; i < projected.length; i++) {
                    const a = projected[i - 1], b = projected[i];
                    if (((a[2] + b[2]) >= 0) === near) {
                        ctx.moveTo(a[0], a[1]);
                        ctx.lineTo(b[0], b[1]);
                    }
                }
                ctx.strokeStyle = side === 0 ? (near ? "#666" : "#aaa") : (near ? "#aaa" : "#ddd");
                ctx.lineWidth = side === 0 ? 1.2 : .8;
                ctx.stroke();
            }
        }

        ctx.font = "12px system-ui, sans-serif";
        ctx.textAlign = "center";
        for (const el of [90, -90]) {
            const [x, y] = project(this.vector(0, el));
            ctx.fillStyle = "#333";
            ctx.fillText(el > 0 ? "+90°" : "−90°", x, y + (y < cy ? -10 : 18));
        }
        for (const az of [0, 90, 180, 270]) {
            const [x, y, depth] = project(this.vector(az, 0));
            ctx.fillStyle = depth >= 0 ? "#333" : "#666";
            ctx.textAlign = x > cx + 10 ? "left" : x < cx - 10 ? "right" : "center";
            ctx.fillText(az === 0 ? "0° USB" : az + "°",
                x + (x > cx + 10 ? 7 : x < cx - 10 ? -7 : 0), y + (y > cy ? 15 : -9));
        }

        if (this.clapDirections.length) {
            const points = this.clapDirections.map(({ azimuth, elevation, color, number }) => ({
                position: project(this.vector(azimuth ?? 0, elevation ?? 0)), color, number,
            }));
            points.sort((a, b) => a.position[2] - b.position[2]);
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.font = "bold 12px system-ui, sans-serif";
            for (const { position: [x, y, depth], color, number } of points) {
                ctx.beginPath();
                ctx.arc(x, y, 10, 0, 2 * Math.PI);
                ctx.fillStyle = color;
                ctx.globalAlpha = depth < 0 ? .8 : 1;
                ctx.fill();
                ctx.globalAlpha = 1;
                ctx.strokeStyle = "#fff";
                ctx.lineWidth = 2;
                ctx.stroke();
                ctx.fillStyle = "#fff";
                ctx.fillText(number, x, y);
            }
            ctx.textBaseline = "alphabetic";
            return;
        }

        const { azimuth, elevation } = this.angles;
        if (azimuth === null && elevation === null) return;
        const [x, y, depth] = project(this.vector(azimuth ?? 0, elevation ?? 0));
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(x, y);
        ctx.setLineDash(depth < 0 || elevation === null ? [4, 5] : []);
        ctx.strokeStyle = "#222";
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, 2 * Math.PI);
        ctx.fillStyle = elevation !== null && depth >= 0 ? "#222" : "#fff";
        ctx.fill();
        ctx.stroke();
    }
}
