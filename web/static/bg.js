// paltop web - the background, picked with "fx" at the top:
//   abilities  now and then, somewhere at the sides of the page, a Pal
//              ability goes off in retro pixel style, in its element's colour
//   battles    two players' Pals, picked at random, fight it out old-school
//              style at one side of the page (a random one for each fight):
//              little pixel Pals, HP bars, the
//              skills' animations and a text box (the fight is worked out
//              by the server, palbattle.py, from the real stats and skills)
//   off
// The abilities:
// The abilities are real active skills from the game's data table
// (DT_WazaDataTable), the ones that look like something on their own:
// projectiles, beams, rain, tornadoes, eruptions and blasts (claws, bites and
// charges need the Pal itself, so they are left out). Drawn at a fifth
// of the screen's resolution and scaled up without smoothing, so everything
// is square pixels. Rarely more than one at a time, a few seconds apart.
// The interface itself has a solid background, so this only shows at the
// sides and behind the login. Off with the "fx" button, and never on with
// prefers-reduced-motion. paltopFx.play("bolt") shows one on demand;
// paltopFx.names lists them. A click at the sides sets one off right there.
"use strict";

(function () {
  const PIX = 5;                     // screen pixels per canvas pixel
  const MIN_SIDE = 16;               // canvas pixels a side needs for effects
  const MAX_AT_ONCE = 2;
  const GAP = [2.5, 6];              // seconds between effects
  const MAX_CLICKED = 40;            // effects at once when clicking a lot
  const canvas = document.getElementById("fx");
  const select = document.getElementById("fx-mode");
  if (!canvas) return;
  // the canvas everything draws on: the page background's, or while a
  // battle window is being drawn, that window's (see stages below)
  const bgCtx = canvas.getContext("2d");
  let ctx = bgCtx;
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // "off", "abilities" (Pal abilities going off here and there) or
  // "battles" (two players' Pals fighting it out at the sides)
  const MODES = ["off", "abilities", "battles"];
  let mode = "abilities";               // unless the viewer picked another
  try {
    const m = localStorage.getItem("paltop-fx-mode");
    if (MODES.includes(m)) mode = m;
    else if (localStorage.getItem("paltop-fx") === "0") mode = "off";   // the old on/off switch
  } catch (e) {}
  if (reduced) mode = "off";
  let on = mode !== "off";

  // -- colours: the element colours of the page ----------------------------

  const css = getComputedStyle(document.documentElement);
  const cvar = (n, fb) => (css.getPropertyValue(n) || fb).trim();
  const EL = {
    electric: cvar("--el-electricity", "#ffd84a"), fire: cvar("--el-fire", "#ff6a3d"),
    water: cvar("--el-water", "#4da6ff"), ice: cvar("--el-ice", "#9fe8ff"),
    grass: cvar("--el-leaf", "#6fd44f"), ground: cvar("--el-earth", "#c79a5c"),
    dark: cvar("--el-dark", "#a87ce0"), dragon: cvar("--el-dragon", "#e070c8"),
    neutral: cvar("--el-normal", "#d0d0d0"),
  };
  function rgb(hex) {
    const h = hex.replace("#", "");
    const n = parseInt(h.length === 3 ? h.replace(/./g, "$&$&") : h, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  // f > 0 towards white, f < 0 towards black
  function shade(hex, f) {
    const [r, g, b] = rgb(hex);
    const m = (v) => Math.round(f >= 0 ? v + (255 - v) * f : v * (1 + f));
    return `rgb(${m(r)},${m(g)},${m(b)})`;
  }

  // -- drawing helpers, in canvas pixels ------------------------------------

  let W = 0, H = 0;
  const rand = (a, b) => a + Math.random() * (b - a);
  const pick = (list) => list[(Math.random() * list.length) | 0];
  function px(x, y, c, a) {
    ctx.globalAlpha = Math.max(0, Math.min(1, a == null ? 1 : a));
    ctx.fillStyle = c;
    ctx.fillRect(Math.round(x), Math.round(y), 1, 1);
  }
  function rect(x, y, w, h, c, a) {
    ctx.globalAlpha = Math.max(0, Math.min(1, a == null ? 1 : a));
    ctx.fillStyle = c;
    ctx.fillRect(Math.round(x), Math.round(y), Math.round(w), Math.round(h));
  }
  function line(x0, y0, x1, y1, c, a) {
    x0 = Math.round(x0); y0 = Math.round(y0); x1 = Math.round(x1); y1 = Math.round(y1);
    const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0);
    const sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
    let err = dx + dy;
    for (;;) {
      px(x0, y0, c, a);
      if (x0 === x1 && y0 === y1) break;
      const e2 = 2 * err;
      if (e2 >= dy) { err += dy; x0 += sx; }
      if (e2 <= dx) { err += dx; y0 += sy; }
    }
  }
  function ring(cx, cy, r, c, a) {
    if (r < 0.5) { px(cx, cy, c, a); return; }
    const n = Math.max(8, Math.round(r * 6.5));
    for (let i = 0; i < n; i++) {
      const t = (i / n) * Math.PI * 2;
      px(cx + Math.cos(t) * r, cy + Math.sin(t) * r, c, a);
    }
  }
  function disc(cx, cy, r, c, a) {
    for (let y = -r; y <= r; y++)
      for (let x = -r; x <= r; x++)
        if (x * x + y * y <= r * r + r * 0.8) px(cx + x, cy + y, c, a);
  }

  // sparks: small pixels with speed, gravity and a life; they fade out
  function Sparks() {
    const list = [];
    return {
      add(x, y, vx, vy, life, c, g) { list.push({x, y, vx, vy, life, t: 0, c, g: g || 0}); },
      step(dt) {
        for (let i = list.length - 1; i >= 0; i--) {
          const p = list[i];
          p.t += dt;
          if (p.t >= p.life) { list.splice(i, 1); continue; }
          p.vy += p.g * dt; p.x += p.vx * dt; p.y += p.vy * dt;
          px(p.x, p.y, p.c, 1 - p.t / p.life);
        }
        return list.length > 0;
      },
      burst(x, y, n, speed, life, colors, g) {
        for (let i = 0; i < n; i++) {
          const a = Math.random() * Math.PI * 2, s = speed * rand(0.3, 1);
          this.add(x, y, Math.cos(a) * s, Math.sin(a) * s, life * rand(0.5, 1), pick(colors), g);
        }
      },
    };
  }

  // -- the abilities -------------------------------------------------------
  // each takes its area {x0, x1, y0, y1} and returns step(dt) -> still going

  const ABILITIES = {
    // Electric: a jagged bolt from the sky that strikes twice, then sparks
    bolt(r, at) {
      const c = EL.electric, core = shade(c, 0.75);
      const x = at && at.x != null ? at.x : rand(r.x0 + 4, r.x1 - 4), y1 = rand(r.y0 + (r.y1 - r.y0) * 0.45, r.y1 - 6);
      const sparks = Sparks();
      let path, t = 0, restruck = false, hit = false;
      function make() {
        const main = [];
        let bx = x + rand(-12, 12), by = r.y0;
        while (by < y1) { main.push([bx, by]); by += rand(2, 5); bx += rand(-3, 3) + (x - bx) * 0.18; }
        main.push([x, y1]);
        const branches = [];
        for (let k = 0; k < 2; k++) {
          const i = (rand(0.2, 0.7) * main.length) | 0;
          let [qx, qy] = main[i];
          const dir = Math.random() < 0.5 ? -1 : 1, br = [[qx, qy]], n = rand(5, 12);
          for (let s = 0; s < n; s++) { qx += dir * rand(1, 3); qy += rand(1, 4); br.push([qx, qy]); }
          branches.push(br);
        }
        path = {main, branches};
      }
      function draw(pts, a, thick) {
        for (let i = 1; i < pts.length; i++) {
          const [ax, ay] = pts[i - 1], [bx, by] = pts[i];
          if (thick) { line(ax - 1, ay, bx - 1, by, c, a * 0.45); line(ax + 1, ay, bx + 1, by, c, a * 0.45); }
          line(ax, ay, bx, by, thick ? core : c, a);
        }
      }
      make();
      return (dt) => {
        t += dt;
        if (t > 0.16 && !restruck) { make(); restruck = true; }
        if (!hit) { hit = true; sparks.burst(x, y1, 22, 55, 0.7, [c, core, "#ffffff"], 70); }
        const flicker = Math.floor(t * 30) % 4 !== 2;
        if (t < 0.5 && flicker) {
          const a = t < 0.32 ? 1 : 1 - (t - 0.32) / 0.18;
          draw(path.main, a, true);
          for (const b of path.branches) draw(b, a * 0.7, false);
          ring(x, y1, 1 + t * 14, c, a * 0.6);
        }
        const more = sparks.step(dt);
        return t < 0.6 || more;
      };
    },

    // Fire: an ignis blast flying across, leaving embers, then exploding
    ignis(r) {
      const c = EL.fire, hot = shade(EL.electric, 0.3), dark = shade(c, -0.45);
      const fromLeft = Math.random() < 0.5;
      const x0 = fromLeft ? r.x0 + 3 : r.x1 - 3, x1 = fromLeft ? r.x1 - 5 : r.x0 + 5;
      const y0 = rand(r.y0 + (r.y1 - r.y0) * 0.3, r.y1 - 10), y1 = y0 + rand(-25, 25);
      const dur = rand(0.8, 1.3), sparks = Sparks();
      let t = 0, boom = false;
      return (dt) => {
        t += dt;
        if (t < dur) {
          const k = t / dur, bx = x0 + (x1 - x0) * k, by = y0 + (y1 - y0) * k - Math.sin(k * Math.PI) * 10;
          for (let i = 0; i < 3; i++)
            sparks.add(bx + rand(-1, 1), by + rand(-1, 1), rand(-8, 8), rand(-18, -4), rand(0.3, 0.7), pick([hot, c, c, dark]));
          disc(bx, by, 3, c, 0.9);
          disc(bx, by, 1, hot, 1);
        } else if (!boom) {
          boom = true;
          sparks.burst(x1, y1, 34, 60, 0.8, [hot, c, c, dark], 40);
        }
        if (boom && t < dur + 0.35) ring(x1, y1, (t - dur) * 30, c, 1 - (t - dur) / 0.35);
        const more = sparks.step(dt);
        return t < dur || more || t < dur + 0.35;
      };
    },

    // Water: a bubble blast, bubbles wobbling upwards and popping
    bubbles(r) {
      const c = EL.water, hi = shade(c, 0.7);
      const ox = rand(r.x0 + 6, r.x1 - 6), oy = rand(r.y0 + (r.y1 - r.y0) * 0.5, r.y1 - 6);
      const sparks = Sparks(), list = [];
      for (let i = 0; i < 8; i++)
        list.push({x: ox + rand(-4, 4), y: oy, vy: rand(-40, -22), r: pick([2, 2, 3, 3, 4]),
          born: i * 0.09, life: rand(1.0, 1.9), ph: rand(0, 6), popped: false});
      let t = 0;
      return (dt) => {
        t += dt;
        let alive = false;
        for (const b of list) {
          if (t < b.born) { alive = true; continue; }
          if (b.popped) continue;
          const age = t - b.born;
          if (age > b.life) {
            b.popped = true;
            for (let k = 0; k < 6; k++) { const a = (k / 6) * Math.PI * 2; sparks.add(b.x, b.y, Math.cos(a) * 25, Math.sin(a) * 25, 0.3, hi); }
            continue;
          }
          alive = true;
          b.y += b.vy * dt;
          const x = b.x + Math.sin(age * 5 + b.ph) * 1.8;
          ring(x, b.y, b.r, c, 0.9);
          px(x - b.r * 0.5, b.y - b.r * 0.5, hi, 0.9);
        }
        const more = sparks.step(dt);
        return alive || more;
      };
    },

    // Ice: an iceberg, spikes of ice growing from the ground, then shattering
    iceberg(r) {
      const c = EL.ice, hi = "#ffffff", deep = shade(EL.water, -0.1);
      const base = rand(r.y0 + (r.y1 - r.y0) * 0.45, r.y1 - 4);
      const cx = rand(r.x0 + 12, r.x1 - 12), sparks = Sparks();
      const spikes = [];
      for (let i = 0; i < 5; i++)
        spikes.push({x: cx + (i - 2) * rand(3, 5), h: rand(10, 24) * (i === 2 ? 1.3 : 1), w: rand(2.5, 4.5), at: Math.abs(i - 2) * 0.07});
      let t = 0, shattered = false;
      const GROW = 0.22, HOLD = 0.7;
      return (dt) => {
        t += dt;
        if (!shattered && t > GROW + HOLD + 0.15) {
          shattered = true;
          for (const s of spikes)
            for (let y = 0; y < s.h; y += 2)
              sparks.add(s.x + rand(-s.w, s.w), base - y, rand(-20, 20), rand(-30, 0), rand(0.5, 0.9), pick([c, hi, deep]), 90);
        }
        if (!shattered) {
          for (const s of spikes) {
            const k = Math.max(0, Math.min(1, (t - s.at) / GROW)), h = s.h * k;
            for (let y = 0; y < h; y++) {
              const hw = Math.round(Math.max(0, (1 - y / h) * s.w));
              for (let x = -hw; x <= hw; x++)
                px(s.x + x, base - y, x === -hw ? hi : x > 0 ? deep : c, 0.95);
            }
            if (k === 1 && Math.random() < 0.08) px(s.x + rand(-1, 1), base - rand(0, s.h), hi, 1);
          }
        }
        const more = sparks.step(dt);
        return !shattered || more;
      };
    },

    // Grass: wind cutters, green crescents flying across with leaves
    wind(r) {
      const c = EL.grass, hi = shade(c, 0.5), dark = shade(c, -0.4);
      const dir = Math.random() < 0.5 ? 1 : -1, y = rand(r.y0 + 10, r.y1 - 10);
      const sparks = Sparks(), blades = [];
      for (let i = 0; i < 3; i++)
        blades.push({x: dir > 0 ? r.x0 - 4 : r.x1 + 4, y: y + (i - 1) * rand(5, 8), v: rand(70, 100), at: i * 0.14});
      let t = 0;
      return (dt) => {
        t += dt;
        let alive = false;
        for (const b of blades) {
          if (t < b.at) { alive = true; continue; }
          b.x += dir * b.v * dt;
          if (b.x < r.x0 - 8 || b.x > r.x1 + 8) continue;
          alive = true;
          for (let a = -1.1; a <= 1.1; a += 0.22) {
            const rr = 4;
            px(b.x + dir * Math.cos(a) * rr, b.y + Math.sin(a) * rr, Math.abs(a) < 0.5 ? hi : c, 0.95);
            px(b.x + dir * (Math.cos(a) * rr - 1), b.y + Math.sin(a) * rr, dark, 0.6);
          }
          if (Math.random() < 0.5) sparks.add(b.x, b.y + rand(-3, 3), -dir * rand(5, 15), rand(-4, 10), rand(0.4, 0.9), pick([c, hi, dark]), 10);
        }
        const more = sparks.step(dt);
        return alive || more;
      };
    },

    // Ground: rock lances, stone pillars bursting up one after another
    rocks(r) {
      const c = EL.ground, hi = shade(c, 0.35), dark = shade(c, -0.45);
      const base = rand(r.y0 + (r.y1 - r.y0) * 0.45, r.y1 - 3);
      const dir = Math.random() < 0.5 ? 1 : -1, n = 5;
      const span = Math.max(1, r.x1 - r.x0 - 6 - 6 * n);
      const start = dir > 0 ? r.x0 + 3 + rand(0, span) : r.x1 - 3 - rand(0, span);
      const sparks = Sparks(), pillars = [];
      for (let i = 0; i < n; i++)
        pillars.push({x: start + dir * i * 6, w: pick([3, 3, 4]), h: rand(8, 19), at: i * 0.09, gone: false});
      let t = 0;
      const RISE = 0.1, HOLD = 0.55;
      return (dt) => {
        t += dt;
        let alive = false;
        for (const p of pillars) {
          const age = t - p.at;
          if (age < 0) { alive = true; continue; }
          if (age > RISE + HOLD) {
            if (!p.gone) {
              p.gone = true;
              for (let y = 0; y < p.h; y += 2)
                sparks.add(p.x + rand(0, p.w), base - y, rand(-15, 15), rand(-25, -5), rand(0.4, 0.8), pick([c, dark, hi]), 110);
            }
            continue;
          }
          alive = true;
          if (!p.dust) { p.dust = true; for (let k = 0; k < 5; k++) sparks.add(p.x + rand(-2, p.w + 2), base, rand(-12, 12), rand(-14, -3), rand(0.3, 0.6), dark, 20); }
          const h = p.h * Math.min(1, age / RISE);
          rect(p.x, base - h, p.w, h, c, 1);
          rect(p.x + p.w - 1, base - h, 1, h, dark, 1);
          rect(p.x, base - h, p.w, 1, hi, 1);
        }
        const more = sparks.step(dt);
        return alive || more;
      };
    },

    // Dark: a shadow burst, darkness pulled in, then thrown out
    shadow(r) {
      const c = EL.dark, deep = shade(c, -0.6), hi = shade(c, 0.4);
      const x = rand(r.x0 + 10, r.x1 - 10), y = rand(r.y0 + 12, r.y1 - 12);
      const sparks = Sparks();
      let t = 0, burst = false;
      const PULL = 0.7;
      return (dt) => {
        t += dt;
        if (t < PULL) {
          for (let i = 0; i < 2; i++) {
            const a = rand(0, Math.PI * 2), d = rand(10, 16);
            sparks.add(x + Math.cos(a) * d, y + Math.sin(a) * d, -Math.cos(a) * d * 2.2, -Math.sin(a) * d * 2.2, 0.42, pick([c, deep, hi]));
          }
          const rr = Math.round(1 + (t / PULL) * 3.5);
          disc(x, y, rr, deep, 1);
          ring(x, y, rr + 0.6, c, 0.9);
        } else if (!burst) {
          burst = true;
          sparks.burst(x, y, 36, 55, 0.8, [c, c, deep, hi]);
        }
        if (burst && t < PULL + 0.45) {
          const k = (t - PULL) / 0.45;
          ring(x, y, k * 15, c, 1 - k);
          ring(x, y, k * 9, deep, (1 - k) * 0.8);
        }
        const more = sparks.step(dt);
        return t < PULL + 0.45 || more;
      };
    },

    // Dragon: dragon meteors falling from the sky with long trails
    meteor(r) {
      const c = EL.dragon, hi = shade(c, 0.6), deep = shade(c, -0.35);
      const sparks = Sparks(), list = [];
      const dir = Math.random() < 0.5 ? 1 : -1;
      for (let i = 0; i < 3; i++) {
        const tx = rand(r.x0 + 6, r.x1 - 6), ty = rand(r.y0 + (r.y1 - r.y0) * 0.45, r.y1 - 6);
        list.push({tx, ty, sx: tx - dir * rand(15, 30), sy: r.y0 - 5, dur: rand(0.45, 0.7), at: i * rand(0.2, 0.35), hit: false, hitAt: 0});
      }
      let t = 0;
      return (dt) => {
        t += dt;
        let alive = false;
        for (const m of list) {
          const k = (t - m.at) / m.dur;
          if (k < 0) { alive = true; continue; }
          if (k >= 1) {
            if (!m.hit) { m.hit = true; m.hitAt = t; sparks.burst(m.tx, m.ty, 26, 50, 0.7, [c, hi, deep], 50); }
            if (t - m.hitAt < 0.3) { alive = true; ring(m.tx, m.ty, (t - m.hitAt) * 35, c, 1 - (t - m.hitAt) / 0.3); }
            continue;
          }
          alive = true;
          const x = m.sx + (m.tx - m.sx) * k, y = m.sy + (m.ty - m.sy) * k;
          for (let i = 0; i < 2; i++) sparks.add(x + rand(-1, 1), y + rand(-1, 1), rand(-4, 4), rand(-4, 4), rand(0.3, 0.6), pick([c, deep, hi]));
          rect(x - 1, y - 1, 3, 3, c, 0.8);
          rect(x - 1, y - 1, 2, 2, hi, 1);
        }
        const more = sparks.step(dt);
        return alive || more;
      };
    },

    // Neutral: an air cannon, a puff and rings of air rolling out
    air(r) {
      const c = EL.neutral, dim = shade(c, -0.4);
      const x = rand(r.x0 + 12, r.x1 - 12), y = rand(r.y0 + 12, r.y1 - 12);
      const sparks = Sparks();
      sparks.burst(x, y, 14, 25, 0.5, [c, dim]);
      let t = 0;
      return (dt) => {
        t += dt;
        for (let i = 0; i < 3; i++) {
          const k = (t - i * 0.14) / 0.6;
          if (k > 0 && k < 1) ring(x, y, 2 + k * 13, i === 0 ? c : dim, (1 - k) * 0.9);
        }
        const more = sparks.step(dt);
        return t < 0.9 || more;
      };
    },
  };

  // -- more abilities, built from a few shapes ----------------------------

  const side = () => (Math.random() < 0.5 ? 1 : -1);
  const low = (r, f) => rand(r.y0 + (r.y1 - r.y0) * (f || 0.45), r.y1 - 4);   // a spot on "the ground"
  // an element's colours: c the colour, hi the bright core, dark the shadow
  function P(el, over) {
    const c = EL[el];
    return Object.assign({c, hi: shade(c, 0.6), dark: shade(c, -0.45)}, over || {});
  }

  // the moving part of a projectile, pointing where it flies
  function head(shape, x, y, vx, vy, size, o) {
    const sp = Math.hypot(vx, vy) || 1, ux = vx / sp, uy = vy / sp;
    if (shape === "dot") { rect(x, y, size, size, o.c, 1); px(x, y, o.hi, 1); }
    else if (shape === "streak") { line(x, y, x - ux * 5, y - uy * 5, o.c, 0.9); px(x, y, o.hi, 1); }
    else if (shape === "icicle") {
      line(x, y, x - ux * 4, y - uy * 4, o.c, 1);
      line(x - uy, y + ux, x - ux * 3 - uy, y - uy * 3 + ux, o.dark, 0.7);
      px(x, y, o.hi, 1);
    } else if (shape === "diamond") {
      px(x, y - 1, o.c, 1); px(x - 1, y, o.c, 1); px(x + 1, y, o.dark, 1); px(x, y + 1, o.dark, 1); px(x, y, o.hi, 1);
    } else if (shape === "rock") {
      rect(x - size / 2, y - size / 2, size, size, o.c, 1);
      rect(x - size / 2, y + size / 2 - 1, size, 1, o.dark, 1);
      px(x - size / 2, y - size / 2, o.hi, 1);
    } else {                                                             // orb
      disc(x, y, size, o.c, 0.9);
      if (size > 1) disc(x, y, size - 1, o.hi, 1); else px(x, y, o.hi, 1);
    }
  }

  // what happens where a projectile or a drop ends
  function end(o, sparks, x, y, booms, t) {
    const e = o.end || "pop";
    if (e === "pop") sparks.burst(x, y, 6, 20, 0.3, [o.c, o.hi]);
    else if (e === "burst") sparks.burst(x, y, o.burstN || 22, o.burstSpeed || 50, 0.7, [o.c, o.hi, o.dark], o.burstG || 30);
    else if (e === "splash") for (let i = 0; i < 12; i++) sparks.add(x, y, rand(-25, 25), rand(-40, -10), rand(0.4, 0.7), pick([o.c, o.hi]), 110);
    else if (e === "split") sparks.burst(x, y, 40, 70, 0.9, [o.c, o.hi, o.dark], 20);
    if (o.ring && booms) booms.push({x, y, t});
  }

  // projectiles fired sideways: one, a fan, or a stream
  function shot(o) {
    return (r) => {
      const dir = side(), sparks = Sparks(), list = [], booms = [];
      const span = r.x1 - r.x0;
      const sx = dir > 0 ? rand(r.x0 + 3, r.x0 + span * 0.3) : rand(r.x1 - span * 0.3, r.x1 - 3);
      const sy = rand(r.y0 + (r.y1 - r.y0) * 0.3, r.y1 - 12);
      const reach = Math.max(25, span * 0.75), n = o.n || 1;
      for (let i = 0; i < n; i++) {
        const ang = -(o.up || 0) + (n > 1 && o.spread ? (i / (n - 1) - 0.5) * o.spread : 0) + rand(-1, 1) * (o.jitter || 0);
        const v = o.speed * rand(0.85, 1.15);
        list.push({x: sx, y: sy, vx: Math.cos(ang) * v * dir, vy: Math.sin(ang) * v, at: i * (o.every || 0),
          life: (reach / o.speed) * rand(0.8, 1.05), t: 0, ph: rand(0, 6), done: false});
      }
      let t = 0;
      return (dt) => {
        t += dt;
        let alive = false;
        for (const p of list) {
          if (p.done) continue;
          if (t < p.at) { alive = true; continue; }
          p.t += dt;
          p.vy += (o.gravity || 0) * dt;
          p.x += p.vx * dt; p.y += p.vy * dt;
          if (o.wobble) p.y += Math.sin(p.t * 9 + p.ph) * o.wobble * dt * 10;
          if (p.t >= p.life || p.x < r.x0 - 4 || p.x > r.x1 + 4 || p.y > r.y1 + 4) {
            p.done = true; end(o, sparks, p.x, p.y, booms, t); continue;
          }
          alive = true;
          const tr = o.trail || 0, nt = Math.floor(tr) + (Math.random() < tr % 1 ? 1 : 0);
          for (let k = 0; k < nt; k++)
            sparks.add(p.x + rand(-1, 1), p.y + rand(-1, 1), rand(-6, 6) - p.vx * 0.05, rand(-6, 6) + (o.drift || 0),
              rand(0.2, 0.5) * (o.trailLife || 1), pick(o.trailColors || [o.c, o.dark]));
          head(o.shape || "orb", p.x, p.y, p.vx, p.vy, o.size || 1, o);
          if (o.arcs && Math.random() < 0.5) {
            const a = rand(0, 6.3), l = (o.size || 1) + rand(2, 4);
            line(p.x, p.y, p.x + Math.cos(a) * l, p.y + Math.sin(a) * l, o.hi, 0.8);
          }
        }
        for (const b of booms) {
          const k = (t - b.t) / 0.3;
          if (k < 1) { alive = true; ring(b.x, b.y, k * 12, o.c, 1 - k); }
        }
        const more = sparks.step(dt);
        return alive || more;
      };
    };
  }

  // a beam: charged up, then fired sideways, or down from the sky
  function beam(o) {
    return (r) => {
      const sparks = Sparks(), CH = o.charge || 0.35, DUR = o.dur || 0.6, FADE = 0.25;
      let x0, y0, x1, y1;
      if (o.sky) { x0 = x1 = rand(r.x0 + 6, r.x1 - 6); y0 = r.y0; y1 = low(r, 0.5); }
      else {
        const dir = side();
        y0 = y1 = rand(r.y0 + 10, r.y1 - 10);
        x0 = dir > 0 ? r.x0 + rand(3, 10) : r.x1 - rand(3, 10);
        x1 = dir > 0 ? r.x1 + 5 : r.x0 - 5;
      }
      let t = 0;
      return (dt) => {
        t += dt;
        const cx = o.sky ? x1 : x0, cy = o.sky ? y1 : y0;
        if (t < CH) {
          const k = t / CH;
          if (o.sky) ring(x1, y1, 6 * (1 - k) + 1, o.c, 0.3 + 0.6 * k);     // where it will hit
          else { disc(x0, y0, Math.round(1 + k * 2), o.c, 0.9); px(x0, y0, o.hi, 1); }
          const a = rand(0, 6.3), d = rand(5, 9);
          sparks.add(cx + Math.cos(a) * d, cy + Math.sin(a) * d, -Math.cos(a) * d * 3, -Math.sin(a) * d * 3, 0.3, pick([o.c, o.hi]));
        } else if (t < CH + DUR + FADE) {
          const k = t < CH + DUR ? 1 : 1 - (t - CH - DUR) / FADE;
          const w = Math.max(1, Math.round((o.width || 2) * k + Math.sin(t * 40) * 0.6)), hw = Math.floor(w / 2);
          if (o.sky) {
            rect(x1 - w, y0, w * 2 + 1, y1 - y0, o.c, 0.85 * k);
            rect(x1 - hw, y0, hw * 2 + 1, y1 - y0, o.hi, k);
            if (Math.random() < 0.8) sparks.add(x1 + rand(-w * 2, w * 2), y1, rand(-30, 30), rand(-40, -10), 0.5, pick([o.c, o.hi, o.dark]), 90);
          } else {
            const xa = Math.min(x0, x1), xb = Math.max(x0, x1);
            rect(xa, y0 - w, xb - xa, w * 2 + 1, o.c, 0.85 * k);
            rect(xa, y0 - hw, xb - xa, hw * 2 + 1, o.hi, k);
            disc(x0, y0, w + 1, o.c, k);
            if (Math.random() < 0.7) sparks.add(rand(xa, xb), y0 + rand(-w, w), rand(-5, 5), rand(-12, 12), 0.35, pick([o.c, o.hi]));
          }
        }
        const more = sparks.step(dt);
        return t < CH + DUR + FADE || more;
      };
    };
  }

  // things falling from the sky, from a cloud or out of nowhere
  function rain(o) {
    return (r) => {
      const sparks = Sparks(), drops = [];
      const w = Math.max(8, Math.min(r.x1 - r.x0 - 6, rand(18, 32))), xa = rand(r.x0 + 3, Math.max(r.x0 + 4, r.x1 - 3 - w));
      const cy = r.y0 + rand(6, 18), DUR = o.dur || 1.4;
      const blobs = o.cloud ? Array.from({length: 10}, () => ({x: rand(xa, xa + w), y: cy + rand(-2, 2), r: pick([2, 2, 3])})) : [];
      let t = 0, next = 0;
      return (dt) => {
        t += dt;
        const fade = t < 0.3 ? t / 0.3 : t > DUR ? Math.max(0, 1 - (t - DUR) / 0.4) : 1;
        for (const b of blobs) disc(b.x, b.y, b.r, o.cloud, 0.55 * fade);
        next -= dt;
        while (t < DUR && next <= 0) {
          next += 1 / (o.rate || 18);
          drops.push({x: rand(xa, xa + w), y: o.cloud ? cy + 2 : r.y0 - 3, vy: (o.speed || 90) * rand(0.9, 1.1), vx: o.slant || 0, end: low(r, 0.5)});
        }
        for (let i = drops.length - 1; i >= 0; i--) {
          const d = drops[i];
          d.y += d.vy * dt; d.x += d.vx * dt;
          if (d.y >= d.end) { drops.splice(i, 1); end(o, sparks, d.x, d.end); continue; }
          head(o.shape || "streak", d.x, d.y, d.vx, d.vy, o.size || 1, o);
        }
        const more = sparks.step(dt);
        return t < DUR + 0.4 || drops.length > 0 || more;
      };
    };
  }

  // columns bursting up from the ground: geysers, lava, vines
  function eruption(o) {
    return (r) => {
      const sparks = Sparks(), base = low(r, 0.5), n = o.n || 4, spouts = [];
      const cx = rand(r.x0 + 10, r.x1 - 10), hr = o.h || [10, 22];
      for (let i = 0; i < n; i++)
        spouts.push({x: cx + (i - (n - 1) / 2) * rand(4, 7), h: rand(hr[0], hr[1]), at: i * (o.every || 0.12), ph: rand(0, 6), gone: false});
      const RISE = o.rise || 0.2, HOLD = o.hold || 0.5;
      let t = 0;
      return (dt) => {
        t += dt;
        let alive = false;
        for (const s of spouts) {
          const age = t - s.at;
          if (age < 0) { alive = true; continue; }
          if (age > RISE + HOLD) {
            if (!s.gone) {
              s.gone = true;
              for (let y = 0; y < s.h; y += 2)
                sparks.add(s.x + rand(-1, 1), base - y, rand(-15, 15), rand(-20, 0), rand(0.4, 0.8), pick([o.c, o.hi, o.dark]), 100);
            }
            continue;
          }
          alive = true;
          const h = s.h * Math.min(1, age / RISE);
          for (let y = 0; y < h; y++) {
            const sway = o.vine ? Math.sin(y * 0.5 + age * 6 + s.ph) * 1.5 * (y / s.h) : 0;
            const wid = o.vine ? 1 : Math.max(1, Math.round((o.width || 2) * (0.6 + 0.4 * Math.sin(y * 0.8 + age * 20))));
            rect(s.x + sway - Math.floor(wid / 2), base - y, wid, 1, y > h - 2 ? o.hi : y % 3 === 0 ? o.dark : o.c, 1);
            if (o.vine && y % 4 === 2) px(s.x + sway + (y % 8 === 2 ? 1 : -1), base - y, o.hi, 1);   // thorns
          }
          if (!o.vine && Math.random() < 0.9)
            sparks.add(s.x + rand(-1, 1), base - h, rand(-18, 18), rand(-30, -5), rand(0.4, 0.7), pick([o.c, o.hi]), 120);
        }
        const more = sparks.step(dt);
        return alive || more;
      };
    };
  }

  // a spinning funnel moving slowly along the ground
  function tornado(o) {
    return (r) => {
      const sparks = Sparks(), base = low(r, 0.55), H0 = rand(22, 34), dir = side();
      const span = r.x1 - r.x0;
      let x = dir > 0 ? rand(r.x0 + 8, r.x0 + span * 0.4) : rand(r.x1 - span * 0.4, r.x1 - 8);
      const pts = Array.from({length: 60}, () => ({a: rand(0, 6.3), y: rand(0, 1), w: rand(5, 9)}));
      const DUR = o.dur || 2.4;
      let t = 0;
      return (dt) => {
        t += dt;
        x += dir * (o.move || 8) * dt;
        const fade = Math.max(0, Math.min(1, t / 0.3, (DUR - t) / 0.4));
        for (const p of pts) {
          p.a += p.w * dt;
          const rad = 1 + p.y * (o.wide || 7), front = Math.sin(p.a) > 0;
          const xx = x + Math.cos(p.a) * rad + Math.sin(t * 3 + p.y * 4) * p.y * 2;
          px(xx, base - p.y * H0, front ? o.hi : p.y > 0.5 ? o.c : o.dark, fade * (front ? 1 : 0.6));
        }
        if (t < DUR && Math.random() < 0.5) sparks.add(x + rand(-4, 4), base, rand(-20, 20), rand(-15, -5), 0.5, pick([o.c, o.dark]), 30);
        const more = sparks.step(dt);
        return t < DUR || more;
      };
    };
  }

  // a blast: maybe charged up first, then rings and sparks
  function nova(o) {
    return (r) => {
      const sparks = Sparks(), CH = o.charge || 0, RD = o.ringDur || 0.5;
      const x = rand(r.x0 + 12, r.x1 - 12), y = o.ground ? low(r, 0.5) : rand(r.y0 + 12, r.y1 - 12);
      let t = 0, boom = false;
      return (dt) => {
        t += dt;
        if (t < CH) {
          const k = t / CH, orb = o.orb || 3;
          disc(x, y, Math.round(1 + k * orb), o.c, 0.9);
          disc(x, y, Math.max(0, Math.round(k * orb) - 1), o.hi, 1);
          const a = rand(0, 6.3);
          sparks.add(x + Math.cos(a) * 10, y + Math.sin(a) * 10, -Math.cos(a) * 28, -Math.sin(a) * 28, 0.33, pick([o.c, o.hi]));
        } else if (!boom) {
          boom = true;
          if (o.ground)
            for (let i = 0; i < (o.count || 24); i++)
              sparks.add(x + rand(-3, 3), y, rand(-35, 35), rand(-60, -15), rand(0.5, 0.9), pick([o.c, o.hi, o.dark]), 130);
          else sparks.burst(x, y, o.count || 36, o.speed || 60, 0.8, [o.c, o.hi, o.dark], o.g || 0);
        }
        const k = (t - CH) / RD;
        if (boom && k < 1)
          for (let i = 0; i < (o.rings || 2); i++) {
            const kk = k - i * 0.18;
            if (kk <= 0) continue;
            const rad = kk * (o.size || 16), col = i ? o.dark : o.c;
            if (o.ground) {                                           // flat, along the ground
              const n = Math.round(rad * 5) + 6;
              for (let j = 0; j < n; j++) {
                const a = (j / n) * Math.PI * 2;
                px(x + Math.cos(a) * rad, y + Math.sin(a) * rad * 0.3 + (o.zigzag ? (j % 2 ? -1 : 1) : 0), col, 1 - kk);
              }
            } else ring(x, y, rad, col, 1 - kk);
          }
        const more = sparks.step(dt);
        return t < CH + RD + 0.2 || more;
      };
    };
  }

  Object.assign(ABILITIES, {
    // Electric
    triLightning(r) {
      const x = rand(r.x0 + 10, r.x1 - 10);
      const bolts = [-7, 0, 7].map((d, i) => ({at: i * 0.12, x: x + d * rand(0.8, 1.3), step: null, over: false}));
      let t = 0;
      return (dt) => {
        t += dt;
        let alive = false;
        for (const b of bolts) {
          if (t < b.at) { alive = true; continue; }
          if (!b.step) b.step = ABILITIES.bolt(r, {x: b.x});
          if (!b.over) { if (b.step(dt)) alive = true; else b.over = true; }
        }
        return alive;
      };
    },
    sparkBlast: shot(P("electric", {n: 7, every: 0.04, spread: 0.5, jitter: 0.15, speed: 90, shape: "dot", trail: 0.5, arcs: false})),
    electricBall: shot(P("electric", {shape: "orb", size: 2, speed: 35, arcs: true, trail: 1, end: "burst", ring: true})),
    shockwave: nova(P("electric", {ground: true, zigzag: true, count: 18, rings: 3, size: 22, ringDur: 0.6})),
    // Fire
    fireBall: shot(P("fire", {hi: shade(EL.electric, 0.3), shape: "orb", size: 3, speed: 45, up: 0.9, gravity: 50, trail: 2, drift: -10,
      trailColors: [shade(EL.electric, 0.3), EL.fire, shade(EL.fire, -0.45)], end: "burst", burstN: 40, ring: true})),
    flareArrow: shot(P("fire", {hi: shade(EL.electric, 0.3), n: 3, every: 0.12, spread: 0.35, shape: "streak", speed: 110, trail: 1,
      trailColors: [shade(EL.electric, 0.3), EL.fire]})),
    spiritFire: shot(P("fire", {hi: shade(EL.electric, 0.3), shape: "orb", size: 2, speed: 40, trail: 1, end: "split"})),
    flareStorm: tornado(P("fire", {hi: shade(EL.electric, 0.3), wide: 8})),
    volcanicBurst: eruption(P("fire", {hi: shade(EL.electric, 0.3), n: 4, width: 2, h: [10, 20]})),
    // Water
    aquaGun: shot(P("water", {shape: "orb", size: 2, speed: 80, up: 0.3, gravity: 40, trail: 1, drift: 20, end: "splash"})),
    hydroLaser: beam(P("water", {width: 2})),
    acidRain: rain(P("water", {cloud: shade(EL.water, -0.55), shape: "streak", speed: 90, rate: 16})),
    geyserGush: eruption(P("water", {n: 3, width: 3, h: [14, 26], hold: 0.7})),
    // Ice
    iceMissile: shot(P("ice", {hi: "#ffffff", dark: shade(EL.water, -0.1), n: 4, every: 0.1, spread: 0.4, up: 0.5, gravity: 60,
      shape: "icicle", speed: 80, trail: 0.5})),
    blizzardSpike(r) {
      const o = P("ice", {hi: "#ffffff", dark: shade(EL.water, -0.1)});
      const sparks = Sparks(), x = rand(r.x0 + 8, r.x1 - 8), ground = low(r, 0.5), L = rand(10, 16);
      let y = r.y0 - 2, v = 40, t = 0, hit = false, hitAt = 0;
      return (dt) => {
        t += dt;
        if (!hit) {
          v += 260 * dt; y += v * dt;
          for (let i = 0; i < L; i++) {                           // point down, wide at the top
            const hw = Math.round((1 - i / L) * 3);
            for (let k = -hw; k <= hw; k++) px(x + k, y - L + i, k === -hw ? o.hi : k > 0 ? o.dark : o.c, 1);
          }
          if (y >= ground) { hit = true; hitAt = t; sparks.burst(x, ground, 30, 60, 0.8, [o.c, o.hi, o.dark], 120); }
        } else if (t - hitAt < 0.35) {
          const k = (t - hitAt) / 0.35;
          for (let j = 0; j < 20; j++) { const a = (j / 20) * Math.PI * 2; px(x + Math.cos(a) * k * 14, ground + Math.sin(a) * k * 4, o.c, 1 - k); }
        }
        const more = sparks.step(dt);
        return !hit || more || t - hitAt < 0.35;
      };
    },
    diamondRain: rain(P("ice", {hi: "#ffffff", dark: shade(EL.water, -0.1), shape: "diamond", speed: 70, rate: 10,
      end: "burst", burstN: 8, burstSpeed: 30})),
    // Grass
    seedMachineGun: shot(P("grass", {n: 10, every: 0.07, jitter: 0.08, shape: "dot", speed: 120})),
    seedMine(r) {
      const o = P("grass"), sparks = Sparks(), ground = low(r, 0.5), cx = rand(r.x0 + 12, r.x1 - 12);
      const seeds = Array.from({length: 4}, (_, i) => ({x: cx, y: ground - 14, vx: (i - 1.5) * rand(8, 14), vy: rand(-30, -15),
        landed: false, at: 0.8 + i * 0.12, boom: false}));
      let t = 0;
      return (dt) => {
        t += dt;
        let alive = false;
        for (const s of seeds) {
          if (s.boom) continue;
          alive = true;
          if (!s.landed) { s.vy += 120 * dt; s.x += s.vx * dt; s.y += s.vy * dt; if (s.y >= ground) { s.y = ground; s.landed = true; } }
          if (t > s.at) { s.boom = true; sparks.burst(s.x, s.y, 16, 40, 0.6, [o.c, o.hi, o.dark], 60); continue; }
          rect(s.x - 1, s.y - 1, 2, 2, s.landed && Math.floor(t * 10) % 2 ? o.hi : o.dark, 1);   // blinking
        }
        const more = sparks.step(dt);
        return alive || more;
      };
    },
    spineVine: eruption(P("grass", {vine: true, n: 4, h: [12, 22], rise: 0.35, hold: 0.6})),
    grassTornado: tornado(P("grass")),
    solarBlast: beam(P("grass", {hi: shade(EL.electric, 0.55), sky: true, width: 3, charge: 0.5})),
    // Ground
    stoneCannon: shot(P("ground", {shape: "rock", size: 4, speed: 60, up: 0.8, gravity: 90, trail: 0.5,
      trailColors: [shade(EL.ground, -0.45)], end: "burst", burstG: 130})),
    sandTornado: tornado(P("ground", {wide: 8})),
    earthImpact: nova(P("ground", {ground: true, count: 26, rings: 2, size: 22})),
    // Dark
    darkBall: shot(P("dark", {dark: shade(EL.dark, -0.6), shape: "orb", size: 3, speed: 40, trail: 1.5, end: "burst", ring: true})),
    darkLaser: beam(P("dark", {width: 2})),
    poisonFog(r) {
      const o = P("dark", {dark: shade(EL.dark, -0.55)});
      const x = rand(r.x0 + 10, r.x1 - 10), y = low(r, 0.4), sparks = Sparks(), DUR = 2.0;
      const blobs = Array.from({length: 9}, (_, i) => ({x: x + rand(-8, 8), y: y + rand(-3, 3), r: 0, max: rand(2, 5), at: i * 0.08, vx: rand(-4, 4)}));
      let t = 0;
      return (dt) => {
        t += dt;
        const fade = Math.max(0, Math.min(1, (DUR - t) / 0.6));
        for (const b of blobs) {
          if (t < b.at) continue;
          b.r = Math.min(b.max, b.r + dt * 8); b.y -= 3 * dt; b.x += b.vx * dt;
          disc(b.x, b.y, Math.round(b.r), o.dark, 0.5 * fade);
          disc(b.x, b.y, Math.max(0, Math.round(b.r) - 2), o.c, 0.45 * fade);
        }
        if (t < DUR - 0.5 && Math.random() < 0.3) sparks.add(x + rand(-10, 10), y + rand(-4, 2), 0, rand(-10, -4), 0.5, o.hi);
        const more = sparks.step(dt);
        return t < DUR || more;
      };
    },
    spiritFlame: shot(P("dark", {n: 3, every: 0.15, spread: 0.6, wobble: 2, shape: "orb", size: 1, speed: 50, trail: 1})),
    // Dragon
    dragonCannon: shot(P("dragon", {shape: "orb", size: 3, speed: 70, trail: 1.5, end: "burst", ring: true})),
    dragonBurst: nova(P("dragon", {charge: 0.4, count: 40, rings: 3, size: 18})),
    dragonBreath: shot(P("dragon", {n: 14, every: 0.03, spread: 0.5, jitter: 0.1, shape: "dot", size: 2, speed: 70, trail: 0.5})),
    // Neutral
    powerShot: shot(P("neutral", {shape: "orb", size: 2, speed: 90, trail: 0.5, end: "burst", ring: true})),
    powerBomb: nova(P("neutral", {charge: 0.6, orb: 4, count: 44, rings: 3, size: 20})),
  });

  const NAMES = Object.keys(ABILITIES);

  // -- where: the free space at the sides of the interface ----------------

  function areas() {
    const app = document.getElementById("app");
    if (!app || app.classList.contains("hidden"))                       // the login page
      return [{x0: 0, x1: W, y0: 0, y1: H}];
    const b = app.getBoundingClientRect();
    const left = Math.floor(b.left / PIX) - 6, right = Math.ceil(b.right / PIX) + 6;
    const out = [];
    if (left >= MIN_SIDE) out.push({x0: 0, x1: left, y0: 0, y1: H});
    if (W - right >= MIN_SIDE) out.push({x0: right, x1: W, y0: 0, y1: H});
    return out;
  }

  // -- the loop ------------------------------------------------------------

  let running = [], raf = 0, last = 0, timer = 0;
  let focused = document.hasFocus();        // see setFocus below

  function resize() {
    W = Math.ceil(window.innerWidth / PIX); H = Math.ceil(window.innerHeight / PIX);
    canvas.width = W; canvas.height = H;
  }

  // step a list of ability animations, each clipped to its area; the ones
  // still going stay in the list
  function stepAll(list, dt) {
    for (let k = list.length - 1; k >= 0; k--) {
      const f = list[k];
      ctx.save();
      const c = f.clip;
      ctx.beginPath(); ctx.rect(c.x0, c.y0, c.x1 - c.x0, c.y1 - c.y0); ctx.clip();
      const alive = f.step(dt);
      ctx.restore();
      if (!alive) list.splice(k, 1);
    }
    ctx.globalAlpha = 1;
  }

  function frame(now) {
    raf = 0;
    if (!focused) { last = 0; return; }     // nothing moves without focus
    const dt = Math.min(0.05, last ? (now - last) / 1000 : 0.016);
    last = now;
    let more = false;
    // the page background
    if (on) {
      ctx = bgCtx;
      ctx.clearRect(0, 0, W, H);
      more = bgStage.frame(now / 1000, dt) || more;
      stepAll(bgStage.fx, dt);
      stepAll(running, dt);
      more = more || running.length > 0 || bgStage.fx.length > 0;
    }
    // the battles tab's window: its own canvas and size
    if (boxStage && boxStage.visible()) {
      boxStage.fit();
      const saved = [W, H];
      [W, H] = boxStage.dims();
      ctx = boxStage.ctx;
      ctx.clearRect(0, 0, W, H);
      boxStage.frame(now / 1000, dt);
      stepAll(boxStage.fx, boxStage.paused ? 0 : dt);
      [W, H] = saved;
      ctx = bgCtx;
      more = true;
    }
    if (more) raf = requestAnimationFrame(frame);
    else { last = 0; bgCtx.clearRect(0, 0, W, H); }
  }
  const kick = () => { if (!raf && focused) raf = requestAnimationFrame(frame); };

  // play an ability somewhere at the sides, or around a point (canvas
  // pixels) there: then it happens in a small area just above the point,
  // so bolts strike it and things land on it, but may spill over the side
  function play(name, at) {
    const free = areas();
    let clip, area;
    if (at) {
      clip = free.find((a) => at.x >= a.x0 && at.x < a.x1 && at.y >= a.y0 && at.y < a.y1);
      if (!clip) return false;
      const half = 24;
      let x0 = Math.max(clip.x0, at.x - half), x1 = Math.min(clip.x1, at.x + half);
      if (x1 - x0 < 2 * half) { if (x0 === clip.x0) x1 = Math.min(clip.x1, x0 + 2 * half); else x0 = Math.max(clip.x0, x1 - 2 * half); }
      area = {x0, x1, y0: Math.max(0, at.y - 36), y1: Math.min(H, at.y + 14)};
    } else {
      if (!free.length) return false;
      clip = area = pick(free);
    }
    const kind = ABILITIES[name] ? name : pick(NAMES);
    running.push({clip, step: ABILITIES[kind](area)});
    kick();
    return kind;
  }

  // a click at the sides, outside the paltop boxes, sets one off right there
  function clickable(target) {
    if (!(target instanceof Element)) return false;
    if (target.closest("#app > *, .login-box, #notice, button, a, input, select, textarea, label")) return false;
    return true;
  }
  document.addEventListener("pointerdown", (ev) => {
    if (!on || ev.button !== 0 || !clickable(ev.target)) return;
    if (running.length >= MAX_CLICKED) return;
    play(null, {x: ev.clientX / PIX, y: ev.clientY / PIX});
  });

  // random abilities now and then: the "abilities" mode, and the login page
  // in "battles" mode (battles need a login to see the players' Pals)
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const idle = mode === "abilities" || (mode === "battles" && !loggedIn());
      if (on && focused && idle && running.length < MAX_AT_ONCE) play();
      if (on) schedule();
    }, rand(GAP[0], GAP[1]) * 1000);
  }
  const loggedIn = () => { const app = document.getElementById("app"); return app && !app.classList.contains("hidden"); };

  // -- battles: two players' Pals fight it out, one at each side -----------
  // The fight itself comes from the server (palbattle.py): who they are,
  // their HP, attack, defense and skills, and every turn already decided.
  // Here it is only played back: little pixel Pals, HP bars, the skill's
  // animation on the target, and what happened in a text box.

  const TURN = 2.8;                  // seconds a turn takes
  const INTRO = 1.6, OUTRO = 5.5, BETWEEN = 4;
  const ANNOUNCE = 1.0;              // whose turn shows this long before it attacks
  // the animations above that share a name with a real skill, and the
  // ones to use for any other skill of an element
  const BY_NAME = {"Lightning Bolt": "bolt", "Ignis Blast": "ignis", "Bubble Blast": "bubbles",
    "Iceberg": "iceberg", "Wind Cutter": "wind", "Rock Lance": "rocks", "Shadow Burst": "shadow",
    "Dragon Meteor": "meteor", "Air Cannon": "air", "Tri-Lightning": "triLightning",
    "Spark Blast": "sparkBlast", "Electric Ball": "electricBall", "Shockwave": "shockwave",
    "Fire Ball": "fireBall", "Flare Arrow": "flareArrow", "Spirit Fire": "spiritFire",
    "Flare Storm": "flareStorm", "Volcanic Burst": "volcanicBurst", "Aqua Gun": "aquaGun",
    "Hydro Laser": "hydroLaser", "Acid Rain": "acidRain", "Geyser Gush": "geyserGush",
    "Ice Missile": "iceMissile", "Blizzard Spike": "blizzardSpike", "Diamond Rain": "diamondRain",
    "Seed Machine Gun": "seedMachineGun", "Seed Mine": "seedMine", "Spine Vine": "spineVine",
    "Grass Tornado": "grassTornado", "Solar Blast": "solarBlast", "Stone Cannon": "stoneCannon",
    "Sand Tornado": "sandTornado", "Earth Impact": "earthImpact", "Dark Ball": "darkBall",
    "Dark Laser": "darkLaser", "Poison Fog": "poisonFog", "Spirit Flame": "spiritFlame",
    "Dragon Cannon": "dragonCannon", "Dragon Burst": "dragonBurst", "Dragon Breath": "dragonBreath",
    "Power Shot": "powerShot", "Power Bomb": "powerBomb"};
  const BY_ELEMENT = {
    Electricity: ["bolt", "triLightning", "sparkBlast", "electricBall", "shockwave"],
    Fire: ["ignis", "fireBall", "flareArrow", "spiritFire", "volcanicBurst"],
    Water: ["bubbles", "aquaGun", "hydroLaser", "geyserGush"],
    Ice: ["iceberg", "iceMissile", "blizzardSpike", "diamondRain"],
    Leaf: ["wind", "seedMachineGun", "seedMine", "spineVine", "solarBlast"],
    Earth: ["rocks", "stoneCannon", "earthImpact"],
    Dark: ["shadow", "darkBall", "darkLaser", "spiritFlame"],
    Dragon: ["meteor", "dragonCannon", "dragonBurst", "dragonBreath"],
    Normal: ["air", "powerShot", "powerBomb"]};
  const EL_KEY = {Electricity: "electric", Fire: "fire", Water: "water", Ice: "ice", Leaf: "grass",
    Earth: "ground", Dark: "dark", Dragon: "dragon", Normal: "neutral"};
  const EL_CSS = (e) => "el-" + String(e || "Normal").toLowerCase();
  const STATUS_VERB = {Burn: "was burned", Soak: "was soaked", Electrify: "was electrified",
    Freeze: "was frozen", "Ivy-Covered": "was covered in vines", Muddy: "got muddy",
    Blind: "was blinded", Poison: "was poisoned", Stun: "was stunned"};
  const SKIP_TEXT = {Freeze: "is frozen solid!", Electrical: "is paralyzed!", Stun: "is stunned!"};
  const DOT_TEXT = {Burn: "is hurt by its burn", Poison: "is hurt by poison"};
  // status badges on the cards, old-school style, in the colour of the
  // element that causes them
  const BADGE = {Burn: ["BRN", "fire"], Soak: ["SOAK", "water"], Electrify: ["PAR", "electricity"],
    Freeze: ["FRZ", "ice"], "Ivy-Covered": ["VINE", "leaf"], Muddy: ["MUD", "earth"],
    Blind: ["BLND", "dark"], Poison: ["PSN", "dark"], Stun: ["STUN", "normal"]};
  const FOES = {Burn: "Burned", Soak: "Soaked", Electrify: "Electrified", Freeze: "Frozen",
    "Ivy-Covered": "Ivy-Covered", Muddy: "Muddy", Blind: "Blinded", Poison: "Poisoned", Stun: "Stunned"};
  const EL_WORD = {Electricity: "Electric", Leaf: "Grass", Earth: "Ground", Normal: "Neutral"};


  // a little retro monster for a species: a random shape mirrored left to
  // right, the same every time for the same species, in its element colours
  function sprite(species, els) {
    let seed = 2166136261;
    for (const ch of species) seed = Math.imul(seed ^ ch.charCodeAt(0), 16777619);
    const rnd = () => { seed = Math.imul(seed ^ (seed >>> 15), 2246822507); seed ^= seed >>> 13;
      return ((seed >>> 0) % 10000) / 10000; };
    const SW = 14, SH = 14, half = SW / 2, g = [];
    for (let y = 0; y < SH; y++) g.push(new Array(SW).fill(0));
    for (let y = 1; y < SH - 1; y++)
      for (let x = 0; x < half; x++) {
        const d = half - 1 - x, edge = y < 3 || y > SH - 4 ? 0.25 : 0;
        if (rnd() < 0.78 - d * 0.1 - edge) g[y][x] = g[y][SW - 1 - x] = rnd() < 0.22 ? 2 : 1;
      }
    for (let y = 3; y < SH - 2; y++) g[y][half - 1] = g[y][half] = g[y][half - 1] || 1;   // a spine
    const lx = 2 + Math.floor(rnd() * 2);                                            // legs
    g[SH - 1][lx] = g[SH - 1][SW - 1 - lx] = 1;
    const ey = 4 + Math.floor(rnd() * 2), ex = half - 2 - Math.floor(rnd() * 2);     // eyes
    g[ey][ex] = g[ey][SW - 1 - ex] = 3;
    // the outline: empty cells next to the body
    const out = g.map((row) => row.slice());
    for (let y = 0; y < SH; y++)
      for (let x = 0; x < SW; x++)
        if (!g[y][x] && [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => (g[y + dy] || [])[x + dx] > 0))
          out[y][x] = 4;
    const c1 = EL[EL_KEY[els[0]] || "neutral"], c2 = EL[EL_KEY[els[1] || els[0]] || "neutral"];
    return {g: out, w: SW, h: SH, colors: {1: c1, 2: els[1] ? c2 : shade(c1, 0.35), 3: "#ffffff", 4: shade(c1, -0.7)}};
  }

  function drawSprite(sp, x, y, face, opt) {
    for (let r = 0; r < sp.h; r++)
      for (let c = 0; c < sp.w; c++) {
        const v = sp.g[r][c];
        if (!v) continue;
        const cc = face < 0 ? sp.w - 1 - c : c;
        let col = opt.flash && v !== 4 ? "#ffffff" : sp.colors[v];
        if (v === 3 && !opt.flash) {                     // eye: white, pupil towards the foe
          px(x + cc, y + r, "#ffffff", opt.alpha);
          px(x + cc + (face > 0 ? 1 : -1) * 0, y + r + 1, "#101010", opt.alpha);
          continue;
        }
        px(x + cc, y + r, col, opt.alpha);
      }
  }

  // A stage plays battles back: the page background's, at one side of the
  // page, and the battles tab's window in the middle of the screen. Each
  // has its own battle, cards and text box; the drawing helpers above draw
  // on `ctx`, which frame() points at the stage being drawn.
  //   area()    the rect the fight is in (canvas pixels), or null for none
  //   active()  whether it should play at all
  //   layer     the element its cards and text go in
  //   layout    "side": cards over the fighters, for a narrow side of the
  //             page; "box": the left side's cards top left, the right
  //             side's bottom right
  //   log(html) (optional) gets every line the text box says
  const esc = (t) => String(t).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

  function makeStage(opt) {
    const SPX = opt.pix || PIX;    // screen pixels per canvas pixel here
    // the stage's own clock, so a battle can be paused and go on where it was
    let paused = false, pausedAt = 0, offset = 0;
    const clock = () => (paused ? pausedAt : performance.now() / 1000 - offset);
    const bt = {data: null, phase: "idle", t0: 0, turn: -1, hp: [], shown: [],
      lunge: [], flash: [], down: [], sprites: [], fetching: false, retryAt: 0, winner: null,
      side: 0, want: null, size: null, active: null,    // active: whose turn it is
      pending: null, fireAt: 0,                           // the turn shown, not yet played
      manual: false, nextAt: 0, waiting: false};          // a duel: turns as they come
    const fx = [];                 // this stage's ability animations
    const layer = opt.layer;
    const card = (i) => layer.querySelector(`.fxb-card[data-i="${i}"]`);
    const PLACES = {
      side: {0: {1: [[0.3, 0.72]], 2: [[0.2, 0.74], [0.46, 0.7]], 3: [[0.15, 0.8], [0.38, 0.77], [0.61, 0.74]]},
             1: {1: [[0.7, 0.4]], 2: [[0.54, 0.42], [0.8, 0.38]], 3: [[0.4, 0.4], [0.63, 0.37], [0.86, 0.34]]}},
      box: {0: {1: [[0.25, 0.72]], 2: [[0.18, 0.74], [0.34, 0.7]], 3: [[0.12, 0.76], [0.26, 0.73], [0.4, 0.7]]},
            1: {1: [[0.72, 0.38]], 2: [[0.64, 0.4], [0.8, 0.36]], 3: [[0.56, 0.4], [0.7, 0.36], [0.84, 0.32]]}}};

    // where each fighter stands (canvas pixels)
    function spots(a) {
      const w = a.x1 - a.x0, fs = bt.data ? bt.data.fighters : [];
      const count = (sd) => fs.filter((f) => f.side === sd).length;
      const P = PLACES[opt.layout];
      const place = {0: P[0][Math.min(3, count(0))] || P[0][1], 1: P[1][Math.min(3, count(1))] || P[1][1]};
      const used = {0: 0, 1: 0};
      return fs.map((f) => {
        const [fx_, fy] = place[f.side][used[f.side]++] || place[f.side][0];
        return {x: Math.round(a.x0 + w * fx_), y: Math.round(a.y0 + (a.y1 - a.y0) * fy)};
      });
    }

    function build(n) {
      layer.innerHTML = Array.from({length: n}, (_, i) => `<div class="fxb-card" data-i="${i}">
          <div class="fxb-name"></div><div class="fxb-owner"></div>
          <div class="fxb-hp"><i></i></div><div class="fxb-foot"><span class="fxb-status"></span><span class="fxb-num"></span></div></div>`).join("") +
        '<div class="fxb-team" data-side="0"></div><div class="fxb-team" data-side="1"></div><div class="fxb-text"></div>';
    }
    function badges(list) {
      (list || []).forEach((names, i) => {
        const c = card(i);
        if (!c) return;
        c.querySelector(".fxb-status").innerHTML = (names || []).map((n) => {
          const [abbr, el] = BADGE[n] || [n.slice(0, 4).toUpperCase(), "normal"];
          return `<b class="fxb-badge el-${el}" title="${esc(n)}">${esc(abbr)}</b>`;
        }).join("");
      });
    }
    // one text box under the fight, for everyone
    function say(html) {
      const box = layer.querySelector(".fxb-text");
      if (!box) return;
      box.innerHTML = html;
      box.classList.toggle("on", !!html);
      if (html && opt.log) opt.log(html);
    }
    function popNumber(i, text, cls) {
      const a = opt.area(bt.side);
      if (!a) return;
      const sp = spots(a)[i];
      if (!sp) return;
      const el = document.createElement("div");
      el.className = "fxb-pop " + (cls || "");
      el.textContent = text;
      el.style.left = (sp.x * SPX) + "px";
      el.style.top = ((sp.y - 16) * SPX) + "px";
      layer.appendChild(el);
      setTimeout(() => el.remove(), 1400);
    }

    const SILHOUETTE = {1: "#2c2c34", 2: "#23232a", 3: "#5a5a66", 4: "#101014"};
    function hideCard(i) {
      const c = card(i);
      bt.sprites[i] = Object.assign(sprite("hidden", ["Normal"]), {colors: SILHOUETTE});
      c.querySelector(".fxb-name").innerHTML = '<span class=dim>??? </span><span class=fxb-lv>hidden</span>';
      c.querySelector(".fxb-owner").textContent = bt.data.fighters[i].owner + "'s hidden Pal";
    }
    function fillCard(i) {
      const f = bt.data.fighters[i], c = card(i);
      c.querySelector(".fxb-name").innerHTML = `<span class="${EL_CSS(f.els[0])}">${esc(f.name)}</span>${f.alpha ? ' <span class=warn>&alpha;</span>' : ""}${f.lucky ? ' <span class=iv-perfect>&#10022;</span>' : ""}${f.stars ? ` <span class=warn title="${f.stars} condenser star${f.stars > 1 ? "s" : ""}">&#9733;${f.stars}</span>` : ""}${f.awakened ? ' <span class=syn title="awakened: stats x1.1">&#9650;</span>' : ""} <span class=fxb-lv>Lv${f.level}</span>`;
      c.querySelector(".fxb-owner").textContent = f.owner + "'s " + (f.name !== f.kind ? f.kind : "Pal");
    }
    // a duel: begin one, or add the turns and the move the server sent
    function startDuel(d) {
      d.manual = true;
      const n = d.fighters.length;
      bt.data = d; bt.phase = "intro"; bt.t0 = clock(); bt.turn = -1;
      bt.pending = null; bt.active = null; bt.nextAt = 0; bt.waiting = false;
      bt.side = 0;
      bt.hp = d.fighters.map((f) => f.hp || 1); bt.shown = bt.hp.slice(); bt.winner = null;
      bt.lunge = new Array(n).fill(0); bt.flash = new Array(n).fill(0); bt.down = new Array(n).fill(null);
      bt.sprites = d.fighters.map((f) => sprite(f.species || "hidden", f.els));
      fx.length = 0;
      build(n);
      d.fighters.forEach((f, i) => { if (f.hidden) hideCard(i); else fillCard(i); });
      for (const sd of [0, 1]) {
        const fs = d.fighters.filter((f) => f.side === sd);
        const tag = layer.querySelector(`.fxb-team[data-side="${sd}"]`);
        tag.textContent = fs.length > 1 ? `${fs[0].owner}'s team` : "";
        tag.classList.toggle("hidden", fs.length < 2);
      }
      if (opt.log) opt.log(null, d);
      say("");
      layer.classList.add("on");
      kick();
    }
    function moreDuel(answer) {
      const d = bt.data;
      if (!d || !d.manual) return;
      d.turns = d.turns.concat(answer.turns || []);
      d.over = answer.over; d.winner = answer.winner; d.move = answer.move || null;
      bt.waiting = false;
      kick();
    }

    async function fetchBattle() {
      bt.fetching = true;
      const size = bt.want || bt.size;
      try {
        const r = await fetch("/api/battle" + (size ? "?size=" + size : ""), {credentials: "same-origin"});
        bt.want = null;
        const d = r.ok ? await r.json() : null;
        if (d && d.fighters) {
          const n = d.fighters.length;
          bt.data = d; bt.phase = "intro"; bt.t0 = clock(); bt.turn = -1;
          bt.pending = null; bt.active = null;
          bt.side = Math.random() < 0.5 ? 0 : 1;
          bt.hp = d.fighters.map((f) => f.hp); bt.shown = bt.hp.slice(); bt.winner = null;
          bt.lunge = new Array(n).fill(0); bt.flash = new Array(n).fill(0); bt.down = new Array(n).fill(null);
          bt.sprites = d.fighters.map((f) => sprite(f.species, f.els));
          fx.length = 0;
          build(n);
          d.fighters.forEach((f, i) => {
            const c = card(i);
            if (f.hidden) { hideCard(i); return; }
            fillCard(i);
            c.classList.toggle("small", n > 2 && opt.layout === "side");
          });
          // whose Pals: a title over each side's cards when it has more
          // than one (a single Pal's card says it itself)
          for (const sd of [0, 1]) {
            const fs = d.fighters.filter((f) => f.side === sd);
            const tag = layer.querySelector(`.fxb-team[data-side="${sd}"]`);
            tag.textContent = fs.length > 1 ? `${fs[0].owner}'s team` : "";
            tag.classList.toggle("hidden", fs.length < 2);
          }
          if (opt.log) opt.log(null, d);               // a new battle begins
          say("");
          layer.classList.add("on");
          kick();
        } else bt.retryAt = clock() + 30;
      } catch (e) { bt.retryAt = clock() + 30; }
      bt.fetching = false;
    }

    function nameOf(i) { const f = bt.data.fighters[i]; return `<span class="${EL_CSS(f.els[0])}">${esc(f.name)}</span>`; }
    const sideName = (sd) => {
      const fs = bt.data.fighters.filter((f) => f.side === sd);
      return fs.length > 1 ? `${esc(fs[0].owner)}'s team` : `${esc(fs[0].owner)}'s ${nameOf(bt.data.fighters.indexOf(fs[0]))}`;
    };

    // like setTimeout, but only while the same battle is still on: a new
    // one may have other fighters
    function later(fn, ms) {
      const d = bt.data;
      const run = () => {
        if (bt.data !== d) return;
        if (paused) { setTimeout(run, 200); return; }   // waits out a pause
        fn();
      };
      setTimeout(run, ms);
    }

    function playTurn(t) {
      for (const [k, f] of Object.entries(t.reveal || {})) {
        const i = +k;
        bt.data.fighters[i] = f;
        bt.sprites[i] = sprite(f.species, f.els);
        bt.hp[i] = bt.shown[i] = f.hp;
        fillCard(i);
        say(`<span class=warn>It's ${esc(f.owner)}'s</span> <span class="${EL_CSS(f.els[0])}">${esc(f.name)}</span>!`);
      }
      const i = t.who, a = opt.area(bt.side);
      bt.active = i;                 // its turn: a pointer over it, its card lit
      let text;
      if (t.skip) text = `${nameOf(i)} ${t.skip === "Dizzy" ? "is dizzy and can't move!" : esc(SKIP_TEXT[t.skip] || "can't move!")}`;
      else {
        bt.lunge[i] = 0.3;
        text = `${nameOf(i)} used <b class="${EL_CSS(t.element)}">${esc(t.skill)}</b>!`;
        if (t.area) text += (t.targets || []).length > 2 ? " <span class=warn>It hits them all!</span>" : " <span class=warn>It hits both!</span>";
        if (t.miss) { text += " <span class=dim>It missed!</span>"; }
        const key = BY_NAME[t.skill] || pick(BY_ELEMENT[t.element] || BY_ELEMENT.Normal);
        const hits = t.miss ? [] : t.targets || [];
        const sfx = opt.sound && opt.sound();
        if (sfx) {                     // its sound (music.js): its own, or its element's
          sfx.skill(t.skill, t.element, BY_NAME[t.skill]);
          if (t.miss) later(() => sfx.miss(), 300);
          if (hits.length) later(() => sfx.hit(Math.max(...hits.map((h) => h.mult)), hits.some((h) => h.crit)), 350);
          const landed = hits.find((h) => h.applied);
          if (landed) later(() => sfx.status(landed.applied), 650);
        }
        if (t.miss) {
          const foe = bt.data.fighters.findIndex((f, k) => f.side !== bt.data.fighters[i].side && bt.hp[k] > 0);
          if (foe >= 0) popNumber(foe, "miss", "miss");
        }
        const one = hits.length === 1;
        if (one && hits[0].mult > 1.01) text += " <span class=good>It's super effective!</span>";
        else if (one && hits[0].mult < 0.99) text += " <span class=dim>It's not very effective...</span>";
        if (hits.some((h) => h.crit)) text += " <span class=warn>A critical hit!</span>";
        for (const h of hits) {
          if (!one && h.mult > 1.01) text += ` <span class=good>Super effective on ${nameOf(h.to)}!</span>`;
          if (h.combo) text += h.combo.up
            ? ` <span class=good>${esc(FOES[h.combo.status] || h.combo.status)} foes take more ${esc(EL_WORD[h.combo.element] || h.combo.element)} damage!</span>`
            : ` <span class=dim>${esc(FOES[h.combo.status] || h.combo.status)} foes take less ${esc(EL_WORD[h.combo.element] || h.combo.element)} damage.</span>`;
          if (h.applied) text += ` ${nameOf(h.to)} ${esc(STATUS_VERB[h.applied] || "got " + h.applied)}!`;
          if (a && spots(a)[h.to]) {  // the skill's own animation on each target
            const sp = spots(a)[h.to];
            fx.push({clip: a, step: ABILITIES[key]({x0: Math.max(a.x0, sp.x - 16), x1: Math.min(a.x1, sp.x + 16),
              y0: Math.max(a.y0, sp.y - 28), y1: Math.min(a.y1, sp.y + 8)})});
          }
          later(() => { bt.flash[h.to] = 0.25; popNumber(h.to, "-" + h.damage, h.crit ? "crit" : ""); }, 350);
        }
        if (t.self === "drain" && t.heal) { text += ` ${nameOf(i)} drained HP!`; later(() => popNumber(i, "+" + t.heal, "heal"), 500); }
        if (t.self === "dizzy") text += ` ${nameOf(i)} got dizzy!`;
        if (t.self === "ko") text += ` ${nameOf(i)} fainted from the blast!`;
      }
      say(text);
      for (const [etype, d] of t.dots || [])
        later(() => { popNumber(i, "-" + d, "dot"); say(`${nameOf(i)} ${esc(DOT_TEXT[etype] || "is hurt")}.`); }, 1100);
      later(() => {
        bt.hp = t.hp.map((v, k) => (v == null ? bt.hp[k] : v));
        if (t.status) badges(t.status);
        const now = clock();
        bt.hp.forEach((v, k) => {
          if (v <= 0 && bt.down[k] == null) {
            bt.down[k] = now;
            const sfx = opt.sound && opt.sound();
            if (sfx) sfx.faint();
          }
        });
      }, 350);
    }

    function place(el, x, y, w) { el.style.width = w + "px"; el.style.left = x + "px"; el.style.top = y + "px"; }

    // draw one frame; true while it wants more frames
    function frame(_now, dt) {
      if (!opt.active()) { layer.classList.remove("on"); return false; }
      const now = clock();
      if (paused) dt = 0;
      const ar = opt.area(bt.side);
      if (!ar) { layer.classList.remove("on"); return true; }      // wait for room
      if (!bt.data) {
        if (opt.manual && opt.manual()) { layer.classList.remove("on"); return true; }
        if (!bt.fetching && now >= bt.retryAt) fetchBattle();
        return true;
      }
      layer.classList.add("on");
      const d = bt.data, el = now - bt.t0, sp = spots(ar), n = d.fighters.length;
      // what happens when
      if (bt.phase === "intro" && el > INTRO) { bt.phase = "fight"; bt.t0 = now; }
      const finish = () => {
          bt.phase = "end"; bt.t0 = now; bt.winner = d.winner; bt.active = null;
          const w = d.winner;
          const out = d.fighters.map((f, k) => k).filter((k) => d.fighters[k].side !== w);
          say((out.every((k) => bt.hp[k] <= 0)
            ? (out.length > 1 ? `${sideName(1 - w)} is down! ` : `${nameOf(out[0])} fainted! `)
            : (out.length > 1 ? `${sideName(1 - w)} is worn out! ` : `${nameOf(out[0])} is worn out! `)) +
            `${sideName(w)} wins!`);
          d.fighters.forEach((f, k) => card(k).classList.toggle(f.side === w ? "win" : "lose", true));
        };
      if (bt.phase === "fight" && d.manual) {
        if (bt.pending == null && bt.turn < d.turns.length - 1 && now >= bt.nextAt) {
          bt.turn++;
          bt.pending = bt.turn;
          bt.active = d.turns[bt.turn].who;
          bt.fireAt = now + ANNOUNCE;
          bt.nextAt = now + TURN;
        }
        if (bt.pending != null && now >= bt.fireAt) {
          const k = bt.pending;
          bt.pending = null;
          playTurn(d.turns[k]);
        }
        if (bt.pending == null && bt.turn >= d.turns.length - 1 && now >= bt.nextAt) {
          if (d.over) { bt.phase = "end"; bt.t0 = now; bt.winner = d.winner; bt.active = null; finish(); if (opt.onEnd) opt.onEnd(d.winner); }
          else if (d.move && !bt.waiting) {
            bt.waiting = true; bt.active = d.move.who;
            say(`Your <span class="${EL_CSS(d.fighters[d.move.who].els[0])}">${esc(d.fighters[d.move.who].name)}</span>'s move!`);
            if (opt.onMove) opt.onMove(d.move, d);
          }
        }
      } else if (bt.phase === "fight") {
        // each turn: first who is about to act (the pointer, its card lit),
        // then ANNOUNCE seconds later what it does
        const want = Math.floor((now - bt.t0) / TURN);
        while (bt.turn < want && bt.turn < d.turns.length - 1) {
          if (bt.pending != null) playTurn(d.turns[bt.pending]);     // behind: catch up
          bt.turn++;
          bt.pending = bt.turn;
          bt.active = d.turns[bt.turn].who;
          bt.fireAt = bt.t0 + bt.turn * TURN + ANNOUNCE;
        }
        if (bt.pending != null && now >= bt.fireAt) {
          const k = bt.pending;
          bt.pending = null;
          playTurn(d.turns[k]);
        }
        if (bt.pending == null && bt.turn >= d.turns.length - 1 && now - bt.t0 > (bt.turn + 1) * TURN) {
          bt.phase = "end"; bt.t0 = now; bt.winner = d.winner; bt.active = null;
          finish();
        }
      }
      if (bt.phase === "end" && now - bt.t0 > OUTRO && !d.manual) {
        bt.phase = "gone"; bt.t0 = now;
        if (opt.layout === "side") layer.classList.remove("on");
      }
      if (bt.phase === "gone") {
        if (now - bt.t0 > BETWEEN && !bt.fetching) { bt.data = null; bt.retryAt = 0; }
        if (opt.layout === "side") return true;
      }
      // the fighters, back to front: the ones standing higher up first, so
      // in a crowded side the ones in front overlap them
      const aw = (ar.x1 - ar.x0) * SPX;
      const cardTop = {0: Infinity, 1: Infinity};
      const drawOrder = [...Array(n).keys()].sort((x, y) => sp[x].y - sp[y].y);
      for (const i of drawOrder) {
        const f = d.fighters[i], s = sp[i], spr = bt.sprites[i];
        const face = f.side === 0 ? 1 : -1;
        bt.lunge[i] = Math.max(0, bt.lunge[i] - dt);
        bt.flash[i] = Math.max(0, bt.flash[i] - dt);
        const lunge = bt.lunge[i] > 0 ? Math.round(Math.sin((0.3 - bt.lunge[i]) / 0.3 * Math.PI) * 4) * face : 0;
        const bob = Math.round(Math.sin(now * 3 + i * 2) * 0.6);
        let slide = 0, alpha = 1, drop = 0;
        if (bt.phase === "intro") slide = Math.round((1 - Math.min(1, el / INTRO)) * 20) * -face;
        if (bt.down[i] != null) {                   // fainted: sinks and fades
          const k = Math.min(1, (now - bt.down[i]) / 1.2); drop = Math.round(k * 8); alpha = 1 - k;
        }
        const flash = bt.flash[i] > 0 && Math.floor(bt.flash[i] * 20) % 2 === 0;
        if (alpha > 0 && bt.active === i && bt.phase === "fight") {
          // whose turn: a little pointer over its head
          const tx = s.x + lunge + slide, ty = s.y - spr.h - 3 + bob;
          const col = shade(EL.electric, 0.3);
          rect(tx - 2, ty - 2, 5, 1, col, 1); rect(tx - 1, ty - 1, 3, 1, col, 1); px(tx, ty, col, 1);
        }
        if (alpha > 0) {
          rect(s.x - 6 + slide, s.y + 1, 12, 1, "#222", 0.6 * alpha);
          drawSprite(spr, s.x - spr.w / 2 + lunge + slide, s.y - spr.h + bob + drop, face, {flash, alpha});
          if (f.lucky && Math.random() < 0.15) px(s.x + rand(-9, 9), s.y - rand(2, 16), "#ffffff", 1);
        }
        cardTop[f.side] = Math.min(cardTop[f.side], (s.y - spr.h - 6) * SPX);
        // the HP shown slides towards the real HP
        bt.shown[i] += (bt.hp[i] - bt.shown[i]) * Math.min(1, dt * 5);
        const c = card(i);
        const frac = f.hidden ? 1 : Math.max(0, bt.shown[i] / f.hp);
        const bar = c.querySelector(".fxb-hp i");
        bar.style.width = (frac * 100).toFixed(1) + "%";
        bar.className = frac > 0.5 ? "hi" : frac > 0.2 ? "mid" : "lo";
        c.querySelector(".fxb-num").textContent = f.hidden ? "? / ?" : `${Math.max(0, Math.round(bt.shown[i]))} / ${f.hp}`;
        c.classList.toggle("down", bt.down[i] != null);
        c.classList.toggle("turn", bt.active === i && bt.phase === "fight");
      }
      const box = layer.querySelector(".fxb-text");
      if (opt.layout === "side") {
        // a side's cards stacked above its fighters, one per Pal
        const cw = Math.min(190, aw - 16);
        for (const sd of [0, 1]) {
          const mine = d.fighters.map((f, k) => k).filter((k) => d.fighters[k].side === sd);
          let y = cardTop[sd];
          for (const k of mine.slice().reverse()) {
            const c = card(k);
            y -= c.offsetHeight + 4;
            place(c, ar.x0 * SPX + (aw - cw) / 2, y, cw);
          }
          const tag = layer.querySelector(`.fxb-team[data-side="${sd}"]`);
          place(tag, ar.x0 * SPX + (aw - cw) / 2, y - tag.offsetHeight - 3, cw);
        }
        // the text box under the lower fighters, across the side
        const low = Math.max(...sp.filter((_, k) => d.fighters[k].side === 0).map((q) => q.y));
        const bw = Math.min(260, aw - 16);
        place(box, ar.x0 * SPX + (aw - bw) / 2, (low + 4) * SPX, bw);
      } else {
        // the left (lower) side's cards at the top left, the right (upper)
        // side's at the bottom right above the text box, which runs along
        // the bottom
        const ah = (ar.y1 - ar.y0) * SPX, cw = Math.min(230, aw * 0.36), m = 12;
        place(box, m, 0, aw - 2 * m);
        box.style.top = (ah - box.offsetHeight - m) + "px";
        const tag0 = layer.querySelector('.fxb-team[data-side="0"]');
        let y = m;
        place(tag0, m, y, cw);
        if (!tag0.classList.contains("hidden")) y += tag0.offsetHeight + 3;
        for (const k of d.fighters.map((f, q) => q).filter((q) => d.fighters[q].side === 0)) {
          place(card(k), m, y, cw);
          y += card(k).offsetHeight + 4;
        }
        const theirs = d.fighters.map((f, q) => q).filter((q) => d.fighters[q].side === 1);
        y = ah - box.offsetHeight - 2 * m;
        for (const k of theirs.slice().reverse()) {
          y -= card(k).offsetHeight + 4;
          place(card(k), aw - cw - m, y, cw);
        }
        const tag1 = layer.querySelector('.fxb-team[data-side="1"]');
        place(tag1, aw - cw - m, y - tag1.offsetHeight - 3, cw);
      }
      return true;
    }

    return {
      frame, fx, startDuel, moreDuel,
      get paused() { return paused; },
      pause(want) {
        if (want && !paused) { pausedAt = clock(); paused = true; }
        else if (!want && paused) { offset = performance.now() / 1000 - pausedAt; paused = false; }
        return paused;
      },
      // start the next battle now, of `size` a side (or keep the stage's
      // own choice); `keep` makes it the stage's choice from now on
      next(size, keep) {
        if (paused) this.pause(false);
        if (keep) bt.size = size || null; else bt.want = size || null;
        bt.data = null; bt.retryAt = 0; fx.length = 0; kick();
      },
      reset() { bt.active = null; bt.pending = null; bt.data = null; bt.phase = "idle"; bt.retryAt = 0; fx.length = 0; layer.classList.remove("on"); },
    };
  }

  function makeLayer(parent, id) {
    const el = document.createElement("div");
    el.className = "fxb-layer";
    if (id) el.id = id;
    el.setAttribute("aria-hidden", "true");
    parent.appendChild(el);
    return el;
  }

  // the page background's stage: one side of the page, only in "battles"
  // mode and not while the battles tab shows one in the middle
  const bgStage = makeStage({
    layer: makeLayer(document.body, "fx-battle"), layout: "side",
    active: () => mode === "battles" && on && loggedIn() && !(boxStage && boxStage.visible()),
    area: (side) => {
      const free = areas();
      const app = document.getElementById("app");
      if (!app || !free.length) return null;
      free.sort((a, b) => a.x0 - b.x0);
      const room = free.filter((a) => (a.x1 - a.x0) * PIX >= 150);
      if (!room.length) return null;
      const want = free.length === 2 ? free[side || 0] : free[0];
      return room.includes(want) ? want : room[0];
    }});

  // the battles tab: a window in the middle of the screen with its own
  // canvas, battle and log
  let boxStage = null;
  (function battleWindow() {
    const holder = document.getElementById("battle-stage");
    if (!holder) return;
    const BPX = 8;                 // bigger pixels than the background's
    const cv = document.createElement("canvas");
    cv.className = "battle-canvas";
    holder.appendChild(cv);
    const bctx = cv.getContext("2d");
    const logEl = document.getElementById("battle-log");
    let bw = 0, bh = 0, count = 0;
    const visible = () => holder.offsetParent !== null && loggedIn();
    function fit() {
      const w = Math.max(40, Math.floor(holder.clientWidth / BPX)), h = Math.max(30, Math.floor(holder.clientHeight / BPX));
      if (w !== bw || h !== bh) {
        bw = w; bh = h; cv.width = w; cv.height = h;
        cv.style.width = (w * BPX) + "px"; cv.style.height = (h * BPX) + "px";   // whole pixels
      }
    }
    let manual = false;
    const stage = makeStage({
      layer: makeLayer(holder), layout: "box", active: visible, pix: BPX,
      manual: () => manual,
      sound: () => (window.paltopMusic && window.paltopMusic.sfx.enabled ? window.paltopMusic.sfx : null),
      onMove: (move, d) => showMove(move, d),
      onEnd: (winner) => endDuel(winner),
      area: () => (bw ? {x0: 0, x1: bw, y0: 0, y1: bh} : null),
      log: (html, d) => {
        if (!logEl) return;
        if (d) {                                    // a new battle
          count++;
          const sides = [0, 1].map((sd) => {
            const fs = d.fighters.filter((f) => f.side === sd);
            return `${esc(fs[0].owner)}: ${fs.map((f) => f.hidden ? '<span class=dim>??? (hidden)</span>' : `<span class="${EL_CSS(f.els[0])}">${esc(f.name)}</span> <span class=dim>Lv${f.level}</span>`).join(", ")}`;
          });
          logEl.insertAdjacentHTML("afterbegin", `<div class="log-head">${d.manual ? "duel" : "battle"} ${count} &middot; ${d.size} v ${d.size} &middot; ${sides.join(" <span class=dim>vs</span> ")}</div>`);
        } else logEl.insertAdjacentHTML("afterbegin", `<div>${html}</div>`);
        while (logEl.children.length > 120) logEl.lastChild.remove();
      }});
    boxStage = Object.assign(stage, {visible, fit, ctx: bctx, dims: () => [bw, bh]});
    // the window's buttons: pause, new battle, and how many a side
    const again = document.getElementById("battle-next");
    const hold = document.getElementById("battle-pause");
    const showHold = () => {
      if (hold) { hold.textContent = stage.paused ? "resume" : "pause"; hold.classList.toggle("active", stage.paused); }
      holder.classList.toggle("paused", stage.paused);
    };
    for (const b of document.querySelectorAll("[data-battle-size]"))
      b.addEventListener("click", () => {
        if (manual) return;          // a duel's size: see below
        const v = b.dataset.battleSize;
        for (const o of document.querySelectorAll("[data-battle-size]")) o.classList.toggle("active", o === b);
        stage.next(v === "any" ? null : +v, true);
        showHold();
      });
    if (again) again.addEventListener("click", () => { if (manual) showPick(); else stage.next(); showHold(); });

    // -- manual: a duel you fight yourself (palduel on the server) -----------
    const pickEl = document.getElementById("duel-pick");
    const moveEl = document.getElementById("duel-move");
    let size = 3, pool = null, chosen = [];
    const post = async (path, body) => {
      const r = await fetch(path, {method: "POST", credentials: "same-origin",
        headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || "something went wrong");
      return d;
    };
    const skillBtn = (s, i) => `<button class="btn duel-skill ${EL_CSS(s.element)}-edge" data-skill="${i}" ${s.ready ? "" : "disabled"}
        title="${esc(s.element)} &middot; power ${s.power}${s.area ? " &middot; hits all foes" : ""}${s.effects.length ? " &middot; can inflict " + esc(s.effects.join(", ")) : ""}${s.ready ? "" : " &middot; cooling down"}">
        <b class="${EL_CSS(s.element)}">${esc(s.name)}</b> <span class=dim>${s.power}</span>${s.area ? ' <span class="tag">all</span>' : ""}${s.effects.length ? ` <span class="tag">${esc(s.effects[0])}</span>` : ""}${s.ready ? "" : ' <span class=dim>(cooling)</span>'}</button>`;
    function palChip(p) {
      const on = chosen.includes(p.id);
      return `<button class="btn duel-pal ${on ? "active" : ""}" data-pal="${esc(p.id)}" title="${esc(p.skills.map((s) => s[0]).join(", "))} &middot; HP ${p.hp}, attack ${p.atk}, defense ${p.def} &middot; IVs ${p.iv.join("/")}">
        <b class="${EL_CSS(p.els[0])}">${esc(p.name)}</b> <span class=dim>Lv${p.level}</span>${p.stars ? ` <span class=warn>&#9733;${p.stars}</span>` : ""}${p.awakened ? ' <span class=syn>&#9650;</span>' : ""}</button>`;
    }
    function renderPick() {
      const q = (pickEl.querySelector("#duel-search") || {}).value || "";
      const list = (pool || []).filter((p) => !q || `${p.name} ${p.kind} ${p.els.join(" ")}`.toLowerCase().includes(q.toLowerCase()));
      pickEl.querySelector(".duel-pals").innerHTML = list.map(palChip).join("") || '<span class=dim>no Pal matches</span>';
      pickEl.querySelector("#duel-go").disabled = chosen.length !== size;
      pickEl.querySelector("#duel-count").textContent = `${chosen.length} of ${size} picked`;
      for (const b of pickEl.querySelectorAll("[data-pal]"))
        b.onclick = () => {
          const id = b.dataset.pal;
          if (chosen.includes(id)) chosen = chosen.filter((x) => x !== id);
          else if (chosen.length < size) chosen.push(id);
          renderPick();
        };
    }
    async function showPick() {
      moveEl.classList.add("hidden");
      stage.reset();
      pickEl.classList.remove("hidden");
      pickEl.innerHTML = '<p class=dim>loading your Pals...</p>';
      if (!pool) {
        try {
          const r = await fetch("/api/duel/pals", {credentials: "same-origin"});
          const d = await r.json();
          pool = d.pals || [];
          if (!d.linked) { pickEl.innerHTML = '<p class=warn>your user is not linked to a player on the server</p>'; return; }
        } catch (e) { pickEl.innerHTML = '<p class=warn>could not load your Pals</p>'; return; }
      }
      chosen = chosen.filter((id) => pool.some((p) => p.id === id)).slice(0, size);
      pickEl.innerHTML = `<div class="controls"><b>pick ${size} of your Pals</b> <span class=dim id="duel-count"></span>
          <label class="field grow"><input id="duel-search" class="search" type="search" placeholder="search your Pals..." autocomplete="off"></label>
          <button class="btn primary" id="duel-go">fight!</button></div>
        <p class=dim>You will see ${size === 3 ? "two of their three Pals" : size === 2 ? "one of their two Pals" : "none of their Pals"} before the fight; a hidden one shows itself when it acts or is hit.</p>
        <div class="duel-pals"></div><p class="warn" id="duel-err"></p>`;
      pickEl.querySelector("#duel-search").addEventListener("input", renderPick);
      pickEl.querySelector("#duel-go").onclick = async () => {
        try {
          const d = await post("/api/duel", {pals: chosen});
          pickEl.classList.add("hidden");
          stage.startDuel(d);
        } catch (e) { pickEl.querySelector("#duel-err").textContent = e.message; }
      };
      renderPick();
    }
    function showMove(move, d) {
      const me = d.fighters[move.who];
      moveEl.classList.remove("hidden");
      moveEl.innerHTML = `<div class="duel-who">your <b class="${EL_CSS(me.els[0])}">${esc(me.name)}</b>'s move</div>
        <div class="duel-skills">${move.skills.map(skillBtn).join("")}</div>
        <div class="duel-targets hidden"></div>
        <div class="controls"><span class="spacer"></span><button class="btn small" id="duel-quit">give up</button></div>`;
      const act = async (skill, target) => {
        moveEl.innerHTML = '<p class=dim>...</p>';
        try { stage.moreDuel(await post("/api/duel/act", {skill, target})); moveEl.classList.add("hidden"); }
        catch (e) { moveEl.innerHTML = `<p class=warn>${esc(e.message)}</p>`; }
      };
      for (const b of moveEl.querySelectorAll("[data-skill]"))
        b.onclick = () => {
          const s = move.skills[+b.dataset.skill];
          if (s.area || move.targets.length < 2) return act(s.name, move.targets[0]);
          const box = moveEl.querySelector(".duel-targets");
          box.classList.remove("hidden");
          box.innerHTML = `<span class=label>use <b class="${EL_CSS(s.element)}">${esc(s.name)}</b> on</span> ` +
            move.targets.map((t) => { const f = d.fighters[t];
              return `<button class="btn" data-target="${t}"><b class="${EL_CSS(f.els[0])}">${esc(f.name)}</b></button>`; }).join(" ");
          for (const tb of box.querySelectorAll("[data-target]")) tb.onclick = () => act(s.name, +tb.dataset.target);
        };
      moveEl.querySelector("#duel-quit").onclick = async () => { try { await post("/api/duel/quit"); } catch (e) {} endDuel(null); };
    }
    function endDuel(winner) {
      if (window.paltopMusic) { if (winner === 0) window.paltopMusic.victory(); else if (winner === 1) window.paltopMusic.defeat(); }
      moveEl.classList.remove("hidden");
      moveEl.innerHTML = `<div class="duel-who">${winner === 0 ? '<span class=good>You won!</span>' : winner === 1 ? '<span class=warn>You lost.</span>' : "You gave up."}</div>
        <div class="controls"><button class="btn primary" id="duel-again">new duel</button></div>`;
      moveEl.querySelector("#duel-again").onclick = () => showPick();
    }
    function setMode(m) {
      manual = m === "manual";
      for (const b of document.querySelectorAll("[data-battle-mode]")) b.classList.toggle("active", b.dataset.battleMode === m);
      const anyBtn = document.querySelector('[data-battle-size="any"]');
      if (anyBtn) anyBtn.classList.toggle("hidden", manual);
      if (hold) hold.classList.toggle("hidden", manual);
      if (again) again.textContent = manual ? "new duel" : "new battle";
      for (const o of document.querySelectorAll("[data-battle-size]"))
        o.classList.toggle("active", manual ? o.dataset.battleSize === String(size) : o.dataset.battleSize === "any");
      if (manual) showPick();
      else { pickEl.classList.add("hidden"); moveEl.classList.add("hidden"); stage.next(null, true); }
      showHold();
    }
    for (const b of document.querySelectorAll("[data-battle-mode]"))
      b.addEventListener("click", () => setMode(b.dataset.battleMode));
    // in a duel the size buttons pick how many you take
    for (const b of document.querySelectorAll("[data-battle-size]"))
      b.addEventListener("click", () => {
        if (!manual || b.dataset.battleSize === "any") return;
        size = +b.dataset.battleSize;
        for (const o of document.querySelectorAll("[data-battle-size]")) o.classList.toggle("active", o === b);
        showPick();
      });
    if (hold) hold.addEventListener("click", () => { stage.pause(!stage.paused); showHold(); });
  })();

  // -- modes -----------------------------------------------------------------

  function apply() {
    on = mode !== "off";
    canvas.classList.toggle("hidden", !on);
    document.documentElement.classList.toggle("fx-on", on);
    if (select) {
      select.value = mode;
      select.title = {off: "background effects are off", abilities: "Pal abilities go off here and there",
        battles: "two players' Pals fight it out at the sides"}[mode];
    }
    running = [];
    clearTimeout(timer);
    bgStage.reset();
    if (on) { resize(); schedule(); kick(); }
    else bgCtx.clearRect(0, 0, W, H);
    kick();                       // the battles tab draws even with fx off
  }

  let resizeTimer = null;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (on) { running = []; resize(); kick(); } }, 150); });
  if (select) {
    if (reduced) select.classList.add("hidden");
    select.addEventListener("change", () => {
      mode = MODES.includes(select.value) ? select.value : "abilities";
      try { localStorage.setItem("paltop-fx-mode", mode); } catch (e) {}
      apply();
    });
  }
  // only while the window has focus: without it, battles stand still where
  // they are (on their own clocks, so they go on from there) and no
  // abilities go off; a battle the viewer paused stays paused
  const held = new Set();
  function setFocus(f) {
    if (f === focused) return;
    focused = f;
    for (const st of [bgStage, boxStage]) {
      if (!st) continue;
      if (!f && !st.paused) { st.pause(true); held.add(st); }
      else if (f && held.has(st)) { st.pause(false); held.delete(st); }
    }
    if (f) kick();
  }
  window.addEventListener("blur", () => setFocus(false));
  window.addEventListener("focus", () => setFocus(true));
  document.addEventListener("visibilitychange", () => setFocus(!document.hidden && document.hasFocus()));

  // the page tells us when someone logs in or out, so battles can start
  document.addEventListener("paltop-login", () => { if (mode === "battles") bgStage.next(); kick(); });
  // the page tells us when the battles tab opens, to start drawing it
  document.addEventListener("paltop-tab", () => kick());
  window.paltopFx = {play: (name) => (on ? play(name) : false), names: NAMES,
    battle: (size) => ((boxStage && boxStage.visible()) ? boxStage : bgStage).next(size)};   // 1, 2 or 3 a side
  apply();
})();
