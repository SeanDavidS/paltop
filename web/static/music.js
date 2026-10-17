// paltop battle music - an original chiptune loop in the style of handheld
// battle themes, played with the Web Audio API: no sound files.
//
// Four channels, like the old handhelds: two pulse waves (lead at 25% duty,
// harmony at 12.5%), a triangle bass and noise drums. 160 BPM in A minor: a
// short intro, then six parts of 8 bars that loop (see the score). Short
// jingles for winning and losing a duel.
//
// window.paltopMusic = {enabled, setEnabled(on), volume, setVolume(v),
//                       want(playing), victory(), defeat()}
// The page decides when it should play (want). It is on unless the viewer
// turns it off; browsers only allow sound after a click on the page, so it
// starts with the first one.
"use strict";

(function () {
  const BPM = 160;
  const FX_LEVEL = 1.5;                   // the ability sounds against the music
  const STEP = 60 / BPM / 4;              // one 16th note, in seconds
  const AHEAD = 0.12, TICK = 25;          // scheduling: look ahead, how often

  // -- the score: [note, length in 16ths] per bar -----------------------------
  // The melody moves mostly by step, in 8ths, quarters and longer notes, so
  // it sings rather than runs. The loop is six parts of 8 bars (about 72 s):
  //   A   the theme, everything playing                      Am F G E
  //   B   tension, busier drums                              Dm Am Bb E
  //   C   the calm: long notes, a held chord, only hats      Am Em F C Dm Am F E
  //   D   the build-up: a figure that climbs, drums growing  Am F G E
  //   A'  the theme again at full strength, doubled          Am F G E
  //   E   a brighter bridge in C major, lighter drums        C G Am Em F C Dm E
  const INTRO = [
    [["E4", 2], ["G#4", 2], ["B4", 2], ["E5", 2], ["G#4", 2], ["B4", 2], ["E5", 2], ["G#5", 2]],
    [["B5", 8], ["G#5", 4], ["E5", 4]],
  ];
  const A = [
    [["E5", 4], ["D5", 2], ["C5", 2], ["D5", 4], ["E5", 4]],
    [["F5", 4], ["E5", 2], ["D5", 2], ["C5", 6], ["A4", 2]],
    [["B4", 2], ["C5", 2], ["D5", 4], ["G5", 4], ["F5", 2], ["E5", 2]],
    [["E5", 6], ["D5", 2], ["C5", 4], ["B4", 4]],
    [["E5", 4], ["D5", 2], ["C5", 2], ["D5", 4], ["E5", 4]],
    [["F5", 4], ["G5", 2], ["A5", 2], ["G5", 4], ["F5", 4]],
    [["E5", 4], ["F5", 2], ["G5", 2], ["B5", 4], ["A5", 2], ["G5", 2]],
    [["G#5", 6], ["A5", 2], ["B5", 8]],
  ];
  const B = [
    [["D5", 2], ["E5", 2], ["F5", 4], ["E5", 2], ["D5", 2], ["F5", 4]],
    [["E5", 2], ["D5", 2], ["C5", 4], ["D5", 2], ["E5", 2], ["A4", 4]],
    [["F5", 2], ["G5", 2], ["A5", 4], ["G5", 2], ["F5", 2], ["D5", 4]],
    [["E5", 4], ["F5", 2], ["E5", 2], ["D5", 4], ["B4", 4]],
    [["A5", 4], ["G5", 2], ["F5", 2], ["G5", 4], ["A5", 4]],
    [["C6", 4], ["B5", 2], ["A5", 2], ["G5", 4], ["E5", 4]],
    [["F5", 4], ["G5", 2], ["A5", 2], ["D6", 4], ["C6", 2], ["A#5", 2]],
    [["B5", 4], ["A5", 2], ["G#5", 2], ["E5", 8]],
  ];
  const C = [
    [["A4", 8], ["C5", 8]],
    [["B4", 12], ["G4", 4]],
    [["A4", 8], ["C5", 4], ["A4", 4]],
    [["G4", 16]],
    [["F4", 8], ["A4", 8]],
    [["E5", 12], ["C5", 4]],
    [["D5", 8], ["C5", 8]],
    [["B4", 16]],
  ];
  const D = [
    [["A4", 4], ["C5", 4], ["E5", 4], ["C5", 4]],
    [["A4", 4], ["C5", 4], ["E5", 4], ["A5", 4]],
    [["A4", 4], ["C5", 4], ["F5", 4], ["C5", 4]],
    [["A4", 4], ["C5", 4], ["F5", 4], ["A5", 4]],
    [["B4", 4], ["D5", 4], ["G5", 4], ["D5", 4]],
    [["B4", 4], ["D5", 4], ["G5", 4], ["B5", 4]],
    [["B4", 2], ["D5", 2], ["E5", 2], ["G#5", 2], ["B5", 4], ["G#5", 4]],
    [["B5", 16]],
  ];
  const E = [
    [["E5", 4], ["F5", 2], ["G5", 2], ["A5", 4], ["G5", 4]],
    [["D5", 4], ["E5", 2], ["F5", 2], ["G5", 8]],
    [["C5", 4], ["D5", 2], ["E5", 2], ["A5", 4], ["G5", 4]],
    [["G5", 4], ["F5", 2], ["E5", 2], ["B4", 8]],
    [["A5", 4], ["G5", 2], ["F5", 2], ["C6", 4], ["A5", 4]],
    [["G5", 4], ["E5", 2], ["G5", 2], ["C6", 8]],
    [["F5", 4], ["E5", 2], ["D5", 2], ["A5", 4], ["F5", 4]],
    [["G#5", 4], ["A5", 2], ["B5", 2], ["B5", 8]],
  ];
  // each chord: root for the bass, triad for the harmony
  const CHORDS = {Am: ["A", ["A3", "C4", "E4"]], F: ["F", ["F3", "A3", "C4"]], G: ["G", ["G3", "B3", "D4"]],
    E: ["E", ["E3", "G#3", "B3"]], Dm: ["D", ["D3", "F3", "A3"]], Bb: ["A#", ["A#2", "D3", "F3"]],
    Em: ["E", ["E3", "G3", "B3"]], C: ["C", ["C3", "E3", "G3"]]};

  // drums, 16 steps: k kick, s snare, h hat, . nothing
  const DRUMS = {
    intro:  "h.h.h.h.h.h.h.h.",
    roll:   "k.h.s.h.ssssssss",
    beat:   "k.h.s.hkk.h.s.h.",
    beat2:  "k.hks.h.k.hks.hh",
    fill:   "k.h.s.hks.s.ssss",
    calm:   "k...h...h...h...",
    calm2:  "k...h...h...h.h.",
    build1: "k...k...k...k...",
    build2: "k.h.s.h.k.h.s.hs",
    half:   "k.h.s.h.k.h.s.h.",
    none:   "................",
  };

  // the song: each bar is {lead, chord, drums, harm, bass, vol}
  //   harm  arp (the triad in 8ths), pad (held), double (the lead an octave
  //         down), none;  bass  octave (jumps in 8ths), long (one note),
  //         pulse (8ths on the root)
  const BARS = [];
  const part = (lead, chords, drums, harm, bass, vol) => lead.forEach((l, i) =>
    BARS.push({lead: l, chord: chords[i % chords.length],
               drums: typeof drums === "function" ? drums(i) : drums, harm, bass, vol}));
  part(INTRO, ["E"], (i) => (i ? "roll" : "intro"), "arp", "pulse", 0.15);
  const LOOP = BARS.length;
  const AM = ["Am", "F", "G", "E"];
  part(A, AM, (i) => (i === 7 ? "fill" : "beat"), "arp", "octave", 0.16);
  part(B, ["Dm", "Am", "Bb", "E"], (i) => (i === 7 ? "roll" : "beat2"), "arp", "octave", 0.16);
  part(C, ["Am", "Em", "F", "C", "Dm", "Am", "F", "E"], (i) => (i === 7 ? "calm2" : "calm"), "pad", "long", 0.12);
  part(D, ["Am", "Am", "F", "F", "G", "G", "E", "E"],
       (i) => ["build1", "build1", "build2", "build2", "beat", "beat", "beat2", "roll"][i], "arp", "pulse", 0.15);
  part(A, AM, (i) => (i === 7 ? "fill" : "beat2"), "double", "octave", 0.17);
  part(E, ["C", "G", "Am", "Em", "F", "C", "Dm", "E"], (i) => (i === 7 ? "fill" : "half"), "arp", "octave", 0.15);

  const NOTE = {C: 0, "C#": 1, D: 2, "D#": 3, E: 4, F: 5, "F#": 6, G: 7, "G#": 8, A: 9, "A#": 10, B: 11};
  function freq(n) {
    const m = /^([A-G]#?)(\d)$/.exec(n);
    return 440 * Math.pow(2, (NOTE[m[1]] + (+m[2] + 1) * 12 - 69) / 12);
  }

  // -- the sound ---------------------------------------------------------------
  let ac = null, master = null, fxOut = null, pulse25 = null, pulse12 = null, pulse50 = null, noise = null;
  let sounds = true;                       // ability sounds, on unless turned off
  try { sounds = localStorage.getItem("paltop-sounds") !== "0"; } catch (e) {}
  let enabled = false, volume = 0.5, wanted = false, timer = null;
  let bar = 0, step = 0, nextAt = 0;
  // on unless the viewer has turned it off
  enabled = true;
  try { enabled = localStorage.getItem("paltop-music") !== "0"; } catch (e) {}
  try { const v = parseFloat(localStorage.getItem("paltop-music-volume")); if (v >= 0 && v <= 1) volume = v; } catch (e) {}

  function pulseWave(duty) {               // a pulse wave from its harmonics
    const n = 32, re = new Float32Array(n), im = new Float32Array(n);
    for (let k = 1; k < n; k++) im[k] = (2 / (k * Math.PI)) * Math.sin(k * Math.PI * duty);
    return ac.createPeriodicWave(re, im);
  }
  function setup() {
    if (ac) return true;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return false;
    ac = new AC();
    master = ac.createGain();
    master.gain.value = 0;
    master.connect(ac.destination);
    fxOut = ac.createGain();
    fxOut.gain.value = volume * FX_LEVEL;
    fxOut.connect(ac.destination);
    pulse25 = pulseWave(0.25); pulse12 = pulseWave(0.125); pulse50 = pulseWave(0.5);
    noise = ac.createBuffer(1, ac.sampleRate, ac.sampleRate);
    const d = noise.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
    return true;
  }

  // one note on a channel: wave, frequency, start, length, loudness
  function tone(wave, f, t, len, vol, glide) {
    const o = ac.createOscillator(), g = ac.createGain();
    if (wave === "triangle") o.type = "triangle"; else o.setPeriodicWave(wave);
    o.frequency.setValueAtTime(f, t);
    if (glide) o.frequency.exponentialRampToValueAtTime(glide, t + len);
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(vol, t + 0.005);
    g.gain.setValueAtTime(vol, t + Math.max(0.01, len - 0.03));
    g.gain.linearRampToValueAtTime(0, t + len);
    o.connect(g); g.connect(master);
    o.start(t); o.stop(t + len + 0.02);
  }
  function hit(kind, t) {
    const src = ac.createBufferSource(), g = ac.createGain(), fl = ac.createBiquadFilter();
    src.buffer = noise;
    const len = kind === "k" ? 0.12 : kind === "s" ? 0.1 : 0.03;
    fl.type = kind === "k" ? "lowpass" : "highpass";
    fl.frequency.value = kind === "k" ? 300 : kind === "s" ? 1500 : 7000;
    g.gain.setValueAtTime(kind === "h" ? 0.08 : kind === "s" ? 0.22 : 0.3, t);
    g.gain.exponentialRampToValueAtTime(0.001, t + len);
    src.connect(fl); fl.connect(g); g.connect(master);
    src.start(t, Math.random() * 0.5, len);
    if (kind === "k") tone("triangle", 150, t, 0.1, 0.5, 45);       // the thump
  }

  // the notes of one bar, starting at time t
  function scheduleBar(i, t) {
    const b = BARS[i];
    let at = 0;
    for (const [n, len] of b.lead) {
      tone(pulse25, freq(n), t + at * STEP, len * STEP * 0.92, b.vol);
      if (b.harm === "double")             // the lead an octave down
        tone(pulse12, freq(n) / 2, t + at * STEP, len * STEP * 0.9, 0.08);
      at += len;
    }
    const [root, triad] = CHORDS[b.chord];
    if (b.harm === "arp") {
      [0, 1, 2, 1, 0, 1, 2, 1].forEach((k, j) =>
        tone(pulse12, freq(triad[k]), t + j * 2 * STEP, 2 * STEP * 0.8, 0.07));
    } else if (b.harm === "pad") {
      for (const n of triad) tone(pulse12, freq(n), t, 16 * STEP * 0.97, 0.035);
    }
    if (b.bass === "long") tone("triangle", freq(root + "2"), t, 16 * STEP * 0.95, 0.3);
    else
      for (let j = 0; j < 8; j++)
        tone("triangle", freq(root + (b.bass === "octave" && j % 2 ? "3" : "2")),
             t + j * 2 * STEP, 2 * STEP * 0.85, 0.32);
    [...DRUMS[b.drums]].forEach((c, j) => { if (c !== ".") hit(c, t + j * STEP); });
  }

  function pump() {
    while (nextAt < ac.currentTime + AHEAD) {
      scheduleBar(bar, nextAt);
      nextAt += 16 * STEP;
      bar = bar + 1 >= BARS.length ? LOOP : bar + 1;
    }
  }

  function fade(to, secs) {
    const now = ac.currentTime;
    master.gain.cancelScheduledValues(now);
    master.gain.setValueAtTime(master.gain.value, now);
    master.gain.linearRampToValueAtTime(to, now + secs);
  }

  // browsers keep sound off until the viewer clicks or types on the page:
  // after a reload with the music on, it starts on the first click
  let waitingForClick = false;
  function resumeOnClick() {
    if (waitingForClick) return;
    waitingForClick = true;
    const EVENTS = ["pointerdown", "pointerup", "click", "touchend", "keydown"];
    const go = () => {
      if (!ac || ac.state !== "suspended") return;
      ac.resume().then(() => {
        if (ac.state !== "running") return;
        waitingForClick = false;
        for (const e of EVENTS) document.removeEventListener(e, go, true);
      });
    };
    for (const e of EVENTS) document.addEventListener(e, go, true);
  }

  function play() {
    if (!setup()) return;
    if (ac.state === "suspended") {
      resumeOnClick();               // the browser may hold resume() until then
      ac.resume();
    }
    if (timer) return;
    bar = 0; nextAt = ac.currentTime + 0.05;
    fade(volume * 0.6, 0.3);
    pump();
    timer = setInterval(pump, TICK);
  }
  function stop() {
    if (!ac || !timer) return;
    clearInterval(timer); timer = null;
    fade(0, 0.25);
  }
  function update() { if (enabled && wanted) play(); else stop(); }

  // short tunes for the end of a duel, over the loop
  function jingle(notes, bassNotes) {
    if (!enabled || !setup()) return;
    stop();
    const t = ac.currentTime + 0.3;
    fade(volume * 0.6, 0.05);
    let at = 0;
    for (const [n, len] of notes) { tone(pulse25, freq(n), t + at * STEP, len * STEP * 0.95, 0.18); at += len; }
    at = 0;
    for (const [n, len] of bassNotes) { tone("triangle", freq(n), t + at * STEP, len * STEP * 0.95, 0.3); at += len; }
    setTimeout(update, 5000);              // then the loop again, if still wanted
  }

  // -- ability sounds ----------------------------------------------------------
  // Each sound is a few short layers made from four building blocks:
  //   blip    a wave gliding from one pitch to another
  //   hiss    a burst of noise through a filter that sweeps
  //   series  a run of blips (bubbles, pings, shots)
  //   crackle little pops at random times
  // 42 abilities have a sound of their own (the ones the battles window has an
  // animation for); every other skill gets its element's sound, varied by its
  // name - pitch, length, how many and an extra layer - so most skills sound
  // different, and the same skill always the same.
  function blip(wave, f0, f1, t, len, vol, curve) {
    const o = ac.createOscillator(), g = ac.createGain();
    if (typeof wave === "string") o.type = wave; else o.setPeriodicWave(wave);
    o.frequency.setValueAtTime(Math.max(20, f0), t);
    if (f1 && f1 !== f0) {
      if (curve === "lin") o.frequency.linearRampToValueAtTime(Math.max(20, f1), t + len);
      else o.frequency.exponentialRampToValueAtTime(Math.max(20, f1), t + len);
    }
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(vol, t + Math.min(0.01, len / 4));
    g.gain.exponentialRampToValueAtTime(0.0001, t + len);
    o.connect(g); g.connect(fxOut);
    o.start(t); o.stop(t + len + 0.02);
  }
  function hiss(t, len, vol, type, f0, f1, q) {
    const src = ac.createBufferSource(), fl = ac.createBiquadFilter(), g = ac.createGain();
    // a narrow band of noise is much quieter than the whole of it
    vol *= (type || "bandpass") === "bandpass" ? 2.2 + (q || 1) * 0.4 : 1.2;
    src.buffer = noise;
    fl.type = type || "bandpass";
    fl.Q.value = q || 1;
    fl.frequency.setValueAtTime(f0, t);
    if (f1 && f1 !== f0) fl.frequency.exponentialRampToValueAtTime(f1, t + len);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(vol, t + Math.min(0.03, len / 3));
    g.gain.exponentialRampToValueAtTime(0.0001, t + len);
    src.connect(fl); fl.connect(g); g.connect(fxOut);
    src.start(t, Math.random() * 0.4, len + 0.05);
  }
  function series(wave, n, gap, f0, f1, len, vol, t, spread) {
    for (let k = 0; k < n; k++) {
      const f = f0 * Math.pow(f1 / f0, n > 1 ? k / (n - 1) : 0) * (1 + (spread || 0) * (Math.random() - 0.5));
      blip(wave, f, f * 0.8, t + k * gap, len, vol);
    }
  }
  function crackle(t, len, n, vol) {
    for (let k = 0; k < n; k++) hiss(t + Math.random() * len, 0.02 + Math.random() * 0.03, vol, "highpass", 2500 + Math.random() * 3000, 0, 0.7);
  }
  const zap = (t, f0, f1, len, vol) => {    // a buzzing bolt: a pulse that jitters
    const o = ac.createOscillator(), g = ac.createGain();
    o.setPeriodicWave(pulse12);
    for (let k = 0; k * 0.02 < len; k++)
      o.frequency.setValueAtTime(f0 * Math.pow(f1 / f0, k * 0.02 / len) * (0.7 + Math.random() * 0.6), t + k * 0.02);
    g.gain.setValueAtTime(vol, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + len);
    o.connect(g); g.connect(fxOut);
    o.start(t); o.stop(t + len + 0.02);
  };
  const boom = (t, vol, len) => { blip("sine", 160, 35, t, len || 0.45, vol); hiss(t, len || 0.45, vol * 0.8, "lowpass", 900, 120, 0.7); };
  const whoosh = (t, len, vol, f0, f1) => hiss(t, len, vol, "bandpass", f0 || 400, f1 || 2500, 1.2);

  // the 42 with a sound of their own: (t, p) where p varies it a little
  const SIGNATURE = {
    bolt: (t) => { zap(t, 2400, 180, 0.35, 0.2); boom(t + 0.25, 0.25, 0.3); },
    triLightning: (t) => { for (let k = 0; k < 3; k++) zap(t + k * 0.13, 2000 + k * 300, 200, 0.18, 0.16); },
    sparkBlast: (t) => { crackle(t, 0.4, 14, 0.12); zap(t, 900, 1800, 0.3, 0.12); },
    electricBall: (t) => { zap(t, 300, 600, 0.5, 0.14); blip(pulse25, 600, 1200, t + 0.45, 0.15, 0.12); },
    shockwave: (t) => { zap(t, 140, 70, 0.6, 0.2); hiss(t, 0.6, 0.15, "lowpass", 2000, 200); },
    ignis: (t) => { whoosh(t, 0.35, 0.2, 300, 2000); crackle(t + 0.1, 0.5, 10, 0.1); boom(t + 0.3, 0.2); },
    fireBall: (t) => { whoosh(t, 0.4, 0.2, 500, 1500); boom(t + 0.35, 0.3); crackle(t + 0.4, 0.4, 8, 0.1); },
    flareArrow: (t) => { blip(pulse25, 1800, 500, t, 0.18, 0.14); whoosh(t, 0.2, 0.12, 2000, 800); crackle(t + 0.15, 0.2, 5, 0.1); },
    spiritFire: (t) => { blip("triangle", 300, 700, t, 0.5, 0.15); crackle(t, 0.6, 12, 0.08); },
    flareStorm: (t) => { for (let k = 0; k < 5; k++) whoosh(t + k * 0.12, 0.3, 0.12, 300, 1800); crackle(t, 0.9, 20, 0.1); },
    volcanicBurst: (t) => { boom(t, 0.35, 0.7); for (let k = 0; k < 4; k++) boom(t + 0.25 + k * 0.12, 0.15, 0.25); crackle(t, 1, 16, 0.1); },
    bubbles: (t) => series("sine", 7, 0.06, 500, 1300, 0.07, 0.14, t, 0.4),
    aquaGun: (t) => { hiss(t, 0.4, 0.18, "bandpass", 1200, 600, 2); series("sine", 4, 0.05, 900, 600, 0.06, 0.1, t + 0.1, 0.3); },
    hydroLaser: (t) => { blip(pulse25, 900, 1400, t, 0.6, 0.12, "lin"); hiss(t, 0.6, 0.14, "bandpass", 2500, 1500, 3); },
    acidRain: (t) => { for (let k = 0; k < 12; k++) blip("sine", 1400 + Math.random() * 900, 500, t + Math.random() * 0.8, 0.05, 0.09); hiss(t, 0.9, 0.06, "highpass", 3000, 3000); },
    geyserGush: (t) => { hiss(t, 0.7, 0.22, "bandpass", 300, 2500, 1); series("sine", 5, 0.07, 400, 1200, 0.08, 0.1, t + 0.2, 0.3); },
    iceberg: (t) => { blip("triangle", 2600, 2400, t, 0.4, 0.12); blip("triangle", 3100, 2900, t + 0.05, 0.4, 0.08); boom(t + 0.2, 0.2); hiss(t + 0.2, 0.15, 0.12, "highpass", 5000, 5000); },
    iceMissile: (t) => { blip(pulse25, 2400, 900, t, 0.25, 0.12); hiss(t + 0.22, 0.12, 0.15, "highpass", 6000, 4000); },
    blizzardSpike: (t) => { whoosh(t, 0.5, 0.14, 3000, 1500); series("triangle", 4, 0.08, 2800, 2000, 0.12, 0.09, t + 0.1); },
    diamondRain: (t) => series("triangle", 10, 0.07, 3200, 2000, 0.15, 0.14, t, 0.5),
    wind: (t) => { whoosh(t, 0.6, 0.2, 300, 3000); whoosh(t + 0.2, 0.4, 0.12, 2500, 600); },
    seedMachineGun: (t) => { for (let k = 0; k < 10; k++) blip(pulse25, 900, 400, t + k * 0.055, 0.03, 0.18); },
    seedMine: (t) => { blip("triangle", 700, 1000, t, 0.12, 0.12); boom(t + 0.35, 0.25); },
    spineVine: (t) => { whoosh(t, 0.3, 0.14, 1500, 400); blip(pulse12, 200, 120, t + 0.1, 0.3, 0.14); hiss(t + 0.25, 0.08, 0.15, "highpass", 3000, 3000); },
    grassTornado: (t) => { for (let k = 0; k < 4; k++) whoosh(t + k * 0.15, 0.35, 0.12, 400 + k * 300, 2500); },
    solarBlast: (t) => { blip("triangle", 400, 1600, t, 0.5, 0.12); blip(pulse12, 800, 2400, t + 0.1, 0.5, 0.08); boom(t + 0.55, 0.25); },
    rocks: (t) => { for (let k = 0; k < 4; k++) blip("sine", 220 - k * 20, 60, t + k * 0.1, 0.15, 0.2); },
    stoneCannon: (t) => { whoosh(t, 0.25, 0.12, 800, 300); boom(t + 0.22, 0.3, 0.35); for (let k = 0; k < 5; k++) hiss(t + 0.25 + Math.random() * 0.3, 0.03, 0.1, "bandpass", 1500, 1500, 3); },
    sandTornado: (t) => { hiss(t, 0.9, 0.18, "bandpass", 600, 1800, 0.8); hiss(t + 0.3, 0.6, 0.12, "highpass", 3000, 2000); },
    earthImpact: (t) => { boom(t, 0.28, 0.8); blip("sine", 60, 30, t, 0.8, 0.15); },
    shadow: (t) => { blip(pulse25, 500, 120, t, 0.6, 0.12); blip(pulse25, 507, 118, t, 0.6, 0.1); },
    darkBall: (t) => { blip(pulse12, 180, 260, t, 0.4, 0.14); blip(pulse12, 182, 90, t + 0.4, 0.3, 0.14); hiss(t + 0.4, 0.3, 0.1, "lowpass", 600, 100); },
    darkLaser: (t) => { blip(pulse25, 300, 260, t, 0.6, 0.12, "lin"); blip(pulse25, 303, 262, t, 0.6, 0.1, "lin"); hiss(t, 0.6, 0.08, "bandpass", 700, 400, 4); },
    poisonFog: (t) => { hiss(t, 0.9, 0.2, "lowpass", 500, 300); series("sine", 5, 0.12, 250, 180, 0.12, 0.16, t + 0.1, 0.5); },
    spiritFlame: (t) => { blip("triangle", 900, 300, t, 0.5, 0.12); blip("triangle", 905, 306, t + 0.15, 0.5, 0.08); crackle(t, 0.5, 6, 0.07); },
    meteor: (t) => { blip("sine", 2200, 300, t, 0.6, 0.12); whoosh(t, 0.6, 0.12, 2500, 400); boom(t + 0.6, 0.4, 0.7); },
    dragonCannon: (t) => { blip(pulse25, 160, 90, t, 0.5, 0.16); hiss(t, 0.5, 0.18, "bandpass", 500, 2000, 1); boom(t + 0.45, 0.3); },
    dragonBurst: (t) => { blip(pulse25, 220, 110, t, 0.7, 0.15); blip(pulse12, 330, 160, t, 0.7, 0.1); hiss(t, 0.7, 0.14, "lowpass", 1500, 300); },
    dragonBreath: (t) => { hiss(t, 0.8, 0.2, "bandpass", 300, 1500, 0.8); blip(pulse25, 140, 100, t, 0.8, 0.12); },
    air: (t) => { whoosh(t, 0.3, 0.16, 600, 3000); blip("sine", 200, 80, t + 0.25, 0.15, 0.2); },
    powerShot: (t) => { blip(pulse25, 400, 900, t, 0.3, 0.12); blip("sine", 220, 60, t + 0.28, 0.2, 0.25); },
    powerBomb: (t) => { blip("triangle", 300, 900, t, 0.4, 0.1); boom(t + 0.4, 0.35, 0.6); },
  };

  // every other skill: its element's sound, varied by its name
  function hashOf(text) {
    let h = 2166136261;
    for (const ch of text) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
    return () => { h = Math.imul(h ^ (h >>> 15), 2246822507); h ^= h >>> 13; return ((h >>> 0) % 10000) / 10000; };
  }
  const ELEMENT_SOUND = {
    Fire: (t, r, p) => { whoosh(t, 0.3 + r() * 0.3, 0.17, 300 * p, 1500 + r() * 1500); crackle(t + 0.1, 0.4 + r() * 0.4, 6 + Math.floor(r() * 10), 0.09); if (r() < 0.6) boom(t + 0.3 + r() * 0.2, 0.2 + r() * 0.1); },
    Water: (t, r, p) => { hiss(t, 0.3 + r() * 0.4, 0.15, "bandpass", 500 * p, 1500 * p, 1 + r() * 2); series("sine", 3 + Math.floor(r() * 6), 0.04 + r() * 0.05, 450 * p, 1200 * p, 0.06, 0.15, t + 0.1, 0.4); },
    Electricity: (t, r, p) => { const n = 1 + Math.floor(r() * 3); for (let k = 0; k < n; k++) zap(t + k * (0.1 + r() * 0.1), (1200 + r() * 1400) * p, 150 + r() * 300, 0.18 + r() * 0.25, 0.15); if (r() < 0.5) crackle(t, 0.4, 8, 0.08); },
    Ice: (t, r, p) => { series("triangle", 2 + Math.floor(r() * 7), 0.05 + r() * 0.07, (2400 + r() * 900) * p, (1700 + r() * 800) * p, 0.14, 0.09, t, 0.4); hiss(t + 0.2, 0.12 + r() * 0.1, 0.12, "highpass", 5000, 4000); },
    Leaf: (t, r, p) => { whoosh(t, 0.3 + r() * 0.4, 0.15, 400 * p, (1800 + r() * 1500)); const n = 2 + Math.floor(r() * 4); for (let k = 0; k < n; k++) blip("triangle", [523, 587, 659, 784, 880][Math.floor(r() * 5)] * p, 0, t + 0.1 + k * 0.08, 0.1, 0.15); },
    Earth: (t, r, p) => { boom(t + r() * 0.1, 0.3, 0.3 + r() * 0.4); hiss(t, 0.4 + r() * 0.4, 0.14, "lowpass", 700 * p, 150); const n = Math.floor(r() * 5); for (let k = 0; k < n; k++) blip("sine", (200 - k * 25) * p, 60, t + 0.2 + k * 0.08, 0.12, 0.15); },
    Dark: (t, r, p) => { const f = (250 + r() * 300) * p; blip(pulse25, f, f * (0.25 + r() * 0.3), t, 0.4 + r() * 0.4, 0.12); blip(pulse25, f * 1.015, f * 0.3, t, 0.4 + r() * 0.4, 0.09); if (r() < 0.5) hiss(t + 0.3, 0.3, 0.1, "lowpass", 600, 100); },
    Dragon: (t, r, p) => { const f = (130 + r() * 120) * p; blip(pulse25, f, f * 0.55, t, 0.5 + r() * 0.3, 0.15); hiss(t, 0.5 + r() * 0.3, 0.16, "bandpass", 400, 1800, 0.8); if (r() < 0.6) boom(t + 0.45, 0.25); },
    Normal: (t, r, p) => { whoosh(t, 0.2 + r() * 0.25, 0.15, 500 * p, 2500 * p); blip("sine", 220 * p, 60, t + 0.2, 0.15, 0.22); if (r() < 0.4) blip(pulse12, 600 * p, 300 * p, t, 0.12, 0.08); },
  };

  // status ailments, as they land
  const STATUS_SOUND = {
    Burn: (t) => crackle(t, 0.3, 8, 0.12), Soak: (t) => series("sine", 3, 0.05, 600, 1000, 0.06, 0.12, t),
    Electrify: (t) => zap(t, 1500, 800, 0.15, 0.12), Freeze: (t) => series("triangle", 3, 0.06, 2800, 3400, 0.18, 0.1, t),
    "Ivy-Covered": (t) => whoosh(t, 0.2, 0.12, 2000, 600), Muddy: (t) => hiss(t, 0.2, 0.15, "lowpass", 400, 150),
    Blind: (t) => blip(pulse25, 700, 200, t, 0.3, 0.1), Poison: (t) => series("sine", 3, 0.08, 300, 200, 0.08, 0.12, t),
    Stun: (t) => { blip("triangle", 1800, 1750, t, 0.3, 0.1); blip("triangle", 1350, 1300, t + 0.1, 0.3, 0.08); },
  };

  function fxReady() { return sounds && ac && ac.state === "running"; }
  function fxStart() {                     // the sounds may start the audio too
    if (!sounds || !setup()) return false;
    if (ac.state === "suspended") { resumeOnClick(); ac.resume(); }
    return ac.state === "running";
  }

  const SFX = {
    get enabled() { return sounds; },
    setEnabled(on) { sounds = !!on; try { localStorage.setItem("paltop-sounds", sounds ? "1" : "0"); } catch (e) {} if (on) fxStart(); },
    // a skill being used: its own sound if it has one, else its element's
    skill(name, element, key) {
      if (!fxStart()) return;
      const t = ac.currentTime + 0.02;
      if (key && SIGNATURE[key]) { SIGNATURE[key](t); return; }
      const r = hashOf(String(name || element || "?"));
      const p = 0.75 + r() * 0.6;          // its own pitch
      (ELEMENT_SOUND[element] || ELEMENT_SOUND.Normal)(t, r, p);
    },
    // a hit landing: harder for a critical hit, brighter when super effective
    hit(mult, crit) {
      if (!fxReady()) return;
      const t = ac.currentTime + 0.01;
      hiss(t, 0.08, crit ? 0.3 : 0.2, "bandpass", 1500, 600, 0.8);
      blip("sine", crit ? 260 : 200, 50, t, 0.12, crit ? 0.32 : 0.24);
      if (mult > 1.01) { blip(pulse25, 880, 880, t + 0.05, 0.07, 0.1); blip(pulse25, 1320, 1320, t + 0.12, 0.1, 0.1); }
      else if (mult < 0.99) blip("triangle", 180, 120, t + 0.04, 0.15, 0.12);
      if (crit) blip("triangle", 2000, 2600, t, 0.12, 0.1);
    },
    miss() { if (fxReady()) whoosh(ac.currentTime, 0.25, 0.12, 2500, 900); },
    faint() {
      if (!fxReady()) return;
      const t = ac.currentTime;
      blip(pulse25, 700, 90, t, 0.7, 0.12, "lin");
      blip(pulse12, 350, 45, t + 0.05, 0.7, 0.08, "lin");
    },
    status(name) { if (fxReady() && STATUS_SOUND[name]) STATUS_SOUND[name](ac.currentTime + 0.05); },
    // a new chat message: two quick notes, a little different for a message
    // from the game and one from the page (it has its own mute, in the chat)
    chime(from) {
      if (!setup()) return;
      if (ac.state === "suspended") { resumeOnClick(); ac.resume(); return; }
      const t = ac.currentTime + 0.02;
      const [a, b] = from === "web" ? [1047, 1319] : [1319, 1760];   // C6 E6 / E6 A6
      blip("triangle", a, a, t, 0.09, 0.22);
      blip("triangle", b, b, t + 0.09, 0.16, 0.2);
      blip(pulse12, b * 2, b * 2, t + 0.09, 0.08, 0.04);
    },
  };

  window.paltopMusic = {
    get enabled() { return enabled; },
    setEnabled(on) {
      enabled = !!on;
      try { localStorage.setItem("paltop-music", enabled ? "1" : "0"); } catch (e) {}
      update();
    },
    get volume() { return volume; },
    setVolume(v) {
      volume = Math.max(0, Math.min(1, v));
      try { localStorage.setItem("paltop-music-volume", String(volume)); } catch (e) {}
      if (ac && timer) fade(volume * 0.6, 0.1);
      if (fxOut) fxOut.gain.setValueAtTime(volume * FX_LEVEL, ac.currentTime);
    },
    sfx: SFX,
    want(on) { wanted = !!on; update(); },
    get playing() { return !!(ac && timer && ac.state === "running"); },
    get state() { return {audio: ac ? ac.state : "none", loop: !!timer, wanted, enabled}; },
    victory() {
      jingle([["C5", 2], ["E5", 2], ["G5", 2], ["C6", 2], ["G5", 2], ["C6", 6], ["D6", 2], ["E6", 12]],
             [["C3", 4], ["G2", 4], ["C3", 4], ["F2", 4], ["G2", 4], ["C3", 10]]);
    },
    defeat() {
      jingle([["E5", 4], ["D#5", 4], ["D5", 4], ["C#5", 4], ["A4", 16]],
             [["A2", 8], ["G#2", 8], ["A2", 16]]);
    },
  };
})();
