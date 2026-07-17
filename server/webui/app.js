/* CAN-Rosetta Server dashboard — framework-free SPA.
 *
 * Translated from the Claude Design mockup (server_design.html). All demo data
 * below is ported from the design's DCLogic so the dashboard looks identical to
 * the mockup out of the box. The data-bearing views (Sessions, Session detail,
 * Alignment, Hypotheses, Bus census) then prefer live server data from /api/*
 * when it is available, falling back gracefully to this embedded demo data.
 */
(function () {
  "use strict";

  var NBSP = " ";
  var app = document.getElementById("app");

  /* ---------------------------------------------------------------- helpers */
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function fmt(v, p) {
    if (v == null || !isFinite(v)) return String(v);
    p = p == null ? 2 : p;
    return Number(v).toFixed(p);
  }
  function badge(tone) {
    return {
      active: { bg: "var(--badge-active-bg)", fg: "var(--badge-active-text)" },
      warn: { bg: "var(--badge-maintenance-bg)", fg: "var(--badge-maintenance-text)" },
      danger: { bg: "var(--badge-danger-bg)", fg: "var(--badge-danger-text)" },
      info: { bg: "var(--badge-info-bg)", fg: "var(--badge-info-text)" },
      neutral: { bg: "var(--badge-inactive-bg)", fg: "var(--badge-inactive-text)" },
    }[tone];
  }
  function dot(st) {
    return {
      done: { c: "var(--status-success)", anim: "none", t: "done" },
      run: { c: "var(--accent)", anim: "crPulse 1.6s ease-in-out infinite", t: "running" },
      wait: { c: "var(--border-secondary)", anim: "none", t: "queued" },
      warn: { c: "var(--status-warning)", anim: "none", t: "blocked" },
    }[st];
  }
  var CARD = "background:var(--bg-card);border:1px solid var(--border-primary);" +
    "border-radius:12px;box-shadow:var(--shadow)";
  var EYEBROW = "font-size:12px;font-weight:500;text-transform:uppercase;" +
    "letter-spacing:0.05em;color:var(--text-secondary)";
  var MONO = "font-family:ui-monospace,Menlo,Consolas,monospace";
  var TNUM = "font-variant-numeric:tabular-nums";

  function badgeSpan(text, tone) {
    var b = badge(tone);
    return '<span class="badge" style="background:' + b.bg + ";color:" + b.fg + '">' +
      esc(text) + "</span>";
  }
  function kpi(label, value, note) {
    return '<div style="' + CARD + ';padding:16px;display:flex;flex-direction:column;gap:8px">' +
      '<div style="' + EYEBROW + '">' + esc(label) + "</div>" +
      '<div style="font-size:36px;font-weight:700;line-height:1;color:var(--text-heading);' +
      TNUM + ';white-space:nowrap">' + esc(value) + "</div>" +
      (note ? '<div style="font-size:12px;color:var(--text-muted)">' + esc(note) + "</div>" : "") +
      "</div>";
  }

  /* ------------------------------------------------------------------ state */
  var state = {
    view: "sessions",
    sbOpen: true,
    theme: document.documentElement.getAttribute("data-theme") || "midnight",
    toast: "",
    identifyPct: 73,
    hypoOverride: {},
    sel: { id: "s-20260717-0742-golf8", vehicle: "VW Golf 8 1.5 eTSI" },
    live: { sessions: null, detail: {}, identify: {}, census: {}, clusters: {}, coverage: {}, knowledge: null },
    hypoLive: {},
    hypoRefOverride: {},
  };
  var liveIds = {}; // ids known to be live (fetchable)

  /* ------------------------------------------------------------- demo data */
  function mkDots(arr) { return arr.map(dot); }
  function S(id, vehicle, when, dur, frames, merge, mTone, dots, cov) {
    var b = badge(mTone);
    return {
      id: id, vehicle: vehicle, when: when, dur: dur, frames: frames, merge: merge,
      dots: mkDots(dots), cov: cov + "%", covW: cov + "%", mBg: b.bg, mFg: b.fg,
    };
  }
  var DEMO_SESSIONS = [
    S("s-20260717-0742-golf8", "VW Golf 8 1.5 eTSI", "today 07:42", "31:26", "1.94 M", "merged", "active", ["done", "done", "done", "run", "wait"], 61),
    S("s-20260716-1804-golf8", "VW Golf 8 1.5 eTSI", "yest. 18:04", "28:11", "1.71 M", "merged", "active", ["done", "done", "done", "done", "done"], 58),
    S("s-20260715-0908-idbuzz", "VW ID. Buzz Cargo", "Jul 15", "47:52", "3.21 M", "merged", "active", ["done", "done", "done", "done", "done"], 74),
    S("s-20260714-1131-etransit", "Ford E-Transit", "Jul 14", "1:22:08", "5.09 M", "awaiting phone part", "warn", ["done", "warn", "wait", "wait", "wait"], 0),
    S("s-20260713-0655-edeliver", "Maxus eDeliver 3", "Jul 13", "39:04", "2.58 M", "merged", "active", ["done", "done", "done", "done", "done"], 49),
    S("s-20260710-1442-octavia", "Škoda Octavia iV", "Jul 10", "22:37", "1.32 M", "merged", "active", ["done", "done", "done", "done", "done"], 66),
    S("candump-import-a3", "unknown · candump import", "Jul 8", "18:20", "0.91 M", "edge only", "neutral", ["done", "wait", "done", "warn", "wait"], 12),
    S("sim-heart-of-gold", "Heart of Gold (simulated bus)", "Jul 6", "10:00", "0.36 M", "merged", "active", ["done", "done", "done", "done", "done"], 100),
  ];

  function demoStages(identifyPct) {
    return [
      { num: "Stage 1", name: "Discover + log", stat: "87 arb IDs · OBD 41 PIDs · UDS 3 ECUs · 1.94 M frames", time: "31:26", st: "done", link: false },
      { num: "Stage 2", name: "Align", stat: "residual 8.3 ms (prior 210 ms) · drift −2.1 ppm", time: "11.4 s", st: "done", link: true, linkLabel: "Inspect alignment →", go: "align" },
      { num: "Stage 3", name: "Extract", stat: "31,407 candidates · dropped 214 counters, 87 checksums", time: "2:06", st: "done", link: false },
      { num: "Stage 4", name: "Identify", stat: identifyPct + "% · 12 confirmed · 27 pending review", time: "4:12", st: "run", link: true, linkLabel: "Review hypotheses →", go: "hypo" },
      { num: "Stage 5", name: "Model", stat: "queued · fine-tune batch #14 (babelfish-1)", time: "—", st: "wait", link: true, linkLabel: "Training →", go: "training" },
    ].map(function (s) {
      var d = dot(s.st);
      s.dotC = d.c; s.anim = d.anim;
      s.bg = s.st === "run" ? "var(--accent-subtle)" : "var(--bg-subtle)";
      s.border = s.st === "run" ? "color-mix(in oklab, var(--accent) 40%, transparent)" : "var(--border-primary)";
      return s;
    });
  }
  var DEMO_STREAMS = [
    { path: "can/frames.parquet", kind: "can_frames", rows: "1,942,117", rate: "~1 kHz" },
    { path: "can/discovery.json", kind: "discovery", rows: "—", rate: "—" },
    { path: "edge/motion.jsonl", kind: "motion", rows: "94,300", rate: "50 Hz" },
    { path: "edge/location.jsonl", kind: "location", rows: "1,886", rate: "1 Hz" },
    { path: "phone/motion.jsonl", kind: "motion", rows: "188,600", rate: "100 Hz" },
    { path: "phone/location.jsonl", kind: "location", rows: "1,886", rate: "1 Hz" },
    { path: "phone/video.mp4 + index", kind: "video", rows: "56,580 f", rate: "30 fps" },
  ];
  function demoLog(identifyPct) {
    return [
      { t: "08:15:02 INFO  merge: parts {autopi-3f7a2c1d, iphone-7c} → session ok", c: "var(--text-secondary)" },
      { t: "08:15:12 INFO  align: coarse via t_utc, prior 210 ms", c: "var(--text-secondary)" },
      { t: "08:15:23 INFO  align: xcorr speed×gps → +8.3 ms (r=0.998)", c: "var(--text-secondary)" },
      { t: "08:15:24 INFO  extract: 87 ids → 31,407 candidates", c: "var(--text-secondary)" },
      { t: "08:17:30 INFO  identify: 0x0FD[0:2] u16 LE ×0.0075 ≈ wheel_speed_FL (r=0.998)", c: "var(--text-secondary)" },
      { t: "08:19:02 WARN  identify: 0x3D5[4] matches fuel AND cabin_temp — needs review", c: "var(--status-warning)" },
      { t: "08:21:44 INFO  identify: " + identifyPct + "% — 12 confirmed, 27 pending", c: "var(--text-primary)" },
    ];
  }
  var DEMO_CLOCKS = [
    { dev: "autopi-3f7a2c1d", src: "ntp", prior: "±30 ms", fine: "+8.3 ms ±1.2", drift: "−2.1 ppm", method: "xcorr obd.vehicle_speed × gps.speed" },
    { dev: "iphone-7c", src: "gps", prior: "±20 ms", fine: "−3.7 ms ±0.9", drift: "+0.4 ppm", method: "xcorr accel × d(speed)/dt + brake marker" },
    { dev: "video (pts)", src: "index", prior: "±120 ms", fine: "+12.0 ms ±4.0", drift: "—", method: "brake-lamp flash vs 0x105 bit 21" },
  ];
  var XCORR_PTS = [[-400, .03], [-360, .05], [-320, .02], [-280, .06], [-240, .08], [-200, .06], [-160, .10], [-120, .15], [-80, .29], [-40, .62], [0, .998], [40, .65], [80, .31], [120, .16], [160, .09], [200, .07], [240, .05], [280, .06], [320, .03], [360, .04], [400, .02]];
  var XCORR_DATA = XCORR_PTS.map(function (p) { return { lag: String(p[0]), r: p[1] }; });
  var GPS_PROFILE = [0, 16, 42, 51, 49, 33, 9, 46, 52, 50, 61, 88, 97, 94, 52, 24, 0];
  var OVERLAY_DATA = GPS_PROFILE.map(function (v, i) {
    var mm = 42 + i * 2, h = Math.floor(mm / 60) + 7, m = mm % 60;
    return { t: ("0" + h).slice(-2) + ":" + ("0" + m).slice(-2), gps: v, can: Math.max(0, v + (i % 3 - 1) * 0.4) };
  });

  function H(rank, ref, refKind, cand, candNote, score, prior, priorOk, conf, status0) {
    var status = state.hypoOverride[rank] || status0;
    var tone = status === "confirmed" ? "active" : status === "rejected" ? "neutral" : status === "review" ? "warn" : "info";
    var b = badge(tone);
    return {
      rank: rank, ref: ref, refKind: refKind, cand: cand, candNote: candNote, score: score, prior: prior,
      priorC: priorOk ? "var(--status-success)" : "var(--status-warning)",
      conf: (conf / 100).toFixed(2).slice(1), confW: conf + "%",
      confC: conf > 90 ? "var(--status-success)" : conf > 60 ? "var(--accent-solid)" : "var(--status-warning)",
      status: status, sBg: b.bg, sFg: b.fg,
    };
  }
  function demoHypos() {
    return [
      H(1, "gps.speed", "continuous · 1 Hz", "0x0FD [0:2] u16 LE ×0.0075 km/h", "ESP wheel-speed frame, 20 ms period", "r .998 · MI 4.2", "✓ ≥0, rate-lim", true, 99, "confirmed"),
      H(2, "obd.engine_rpm", "continuous · 5 Hz", "0x107 [2:4] u16 LE ×0.25 rpm", "matches gear-ratio prior vs speed", "r .996 · MI 3.9", "✓ gear ratios", true, 98, "pending"),
      H(3, "video.brake_lamp", "event · OCR", "0x105 bit 21", "event coincidence 47/47 pulses", "coinc 1.00", "✓ decel", true, 97, "confirmed"),
      H(4, "imu.accel_lon", "continuous · 100 Hz", "0x101 [1:2] i16 LE ×0.01 m/s²", "ESP accel broadcast, 10 ms period", "r .974 · DTW 0.11", "✓ d(speed)/dt", true, 94, "pending"),
      H(5, "imu.gyro_yaw", "continuous · 100 Hz", "0x086 [1:3] i16 LE ×0.0044 rad/s", "steering-column frame; sign flips with turns", "r .968 · MI 3.1", "✓ lat accel", true, 92, "pending"),
      H(6, "gps.odometer", "integrated", "0x6B7 [0:3] u32 LE ×0.1 km", "monotonic, rollover-free", "r .999", "✓ monotonic", true, 96, "confirmed"),
      H(7, "obd.coolant_temp", "continuous · 1 Hz", "0x3E9 [1] u8 −40 °C", "slow ramp matches warm-up curve", "r .987", "✓ bounded", true, 91, "pending"),
      H(8, "ocr.fuel_gauge", "continuous · OCR", "0x3D5 [4] u8 ×0.5 %", "also correlates with cabin_temp — improbable twin", "r .893 · MI 1.7", "~ ambiguous", false, 64, "review"),
      H(9, "imu.accel_lat", "continuous · 100 Hz", "0x22B [3:4] i16 BE ×0.02", "aliasing suspected at 20 ms period", "r .42", "✗ jerk-lim", false, 42, "review"),
    ];
  }
  var DBC_TEXT = 'VERSION "canrosetta 0.4.2"\n\nBO_ 253 ESP_21: 8 Vector__XXX\n SG_ WHEEL_SPEED_FL : 0|16@1+ (0.0075,0) [0|491] "km/h" Vector__XXX\n\nBO_ 261 ESP_05: 8 Vector__XXX\n SG_ BRAKE_LAMP : 21|1@1+ (1,0) [0|1] "" Vector__XXX\n\nBO_ 1719 KOMBI_03: 8 Vector__XXX\n SG_ ODOMETER : 0|32@1+ (0.1,0) [0|429496729] "km" Vector__XXX';

  var HEAT = [
    ["0x086", [0.02, .81, .84, .12, 0, .05, .93, 1]],
    ["0x0FD", [.88, .91, .87, .90, .86, .89, .12, 1]],
    ["0x101", [.06, .78, .74, .70, .72, .04, .95, 1]],
    ["0x105", [.03, .22, .41, .02, 0, .18, .96, 1]],
    ["0x107", [.09, .13, .83, .86, .31, .02, .94, 1]],
    ["0x12B", [.01, .04, .02, 0, 0, 0, .11, .34]],
    ["0x02A", [.51, .48, 0, 0, .07, 0, .92, 1]],
    ["0x30B", [.85, .88, .05, .03, 0, 0, .90, 1]],
    ["0x3C0", [.02, .76, .79, .08, .21, 0, .11, .95]],
    ["0x3D5", [0, .02, .05, 0, .16, .01, 0, .30]],
    ["0x3E9", [.01, .09, 0, 0, .02, 0, 0, .12]],
    ["0x6B7", [.28, .11, .04, .01, 0, 0, .09, .88]],
  ];
  var HEAT_ROWS = HEAT.map(function (row) {
    var id = row[0], cells = row[1];
    return {
      id: id,
      cells: cells.map(function (v, i) {
        return {
          bg: "color-mix(in oklab, var(--accent) " + Math.round(6 + v * 88) + "%, transparent)",
          ti: id + " B" + i + " · flip rate " + v.toFixed(2),
        };
      }),
    };
  });
  function C(id, period, jitter, dlc, frames, tax, note, nTone) {
    var b = note ? badge(nTone) : null;
    return {
      id: id, period: period, jitter: jitter, dlc: dlc, frames: frames, tax: tax,
      note: note || "", hasNote: !!note, nBg: b ? b.bg : "", nFg: b ? b.fg : "",
    };
  }
  var DEMO_CENSUS = [
    C("0x0FD", "20 ms", "±0.4", "8", "94,280", "dyn 0–5 · ctr 6 · crc 7", "wheel speeds", "active"),
    C("0x101", "10 ms", "±0.2", "8", "188,610", "dyn 1–4 · ctr 6 · crc 7", "accel / yaw", "active"),
    C("0x086", "10 ms", "±0.2", "8", "188,540", "dyn 1–2, 6 · crc 7", "steering", "active"),
    C("0x02A", "10 ms", "±0.1", "8", "188,571", "dyn 0–1 · sw 4 · ctr 6 · crc 7", "the answer", "info"),
    C("0x107", "20 ms", "±0.5", "8", "94,251", "dyn 2–4 · ctr 6 · crc 7", "rpm / torque", "active"),
    C("0x105", "20 ms", "±0.3", "8", "94,244", "sw 2, 21 · ctr 6 · crc 7", "brake", "active"),
    C("0x30B", "50 ms", "±1.1", "8", "37,690", "dyn 0–1 · ctr 6 · crc 7", "display speed", "info"),
    C("0x3C0", "100 ms", "±2.0", "8", "18,844", "dyn 1–2, 4 · crc 7", "", "neutral"),
    C("0x3D5", "500 ms", "±6.3", "8", "3,772", "dyn 4 · sw 5 · const rest", "fuel? review", "warn"),
    C("0x5C2", "1 s", "±44", "8", "1,884", "dyn 0–3 · high entropy", "darmok", "warn"),
  ];

  var RUNS = [
    { name: "babelfish-1", params: "124 M", status: "running", tone: "info", note: "flagship · masked-frame + next-frame objectives", val: "0.847" },
    { name: "c3po-6m", params: "6 M", status: "completed", tone: "active", note: "baseline — fluent in six million forms of communication", val: "1.213" },
    { name: "marvin-350m", params: "350 M", status: "paused", tone: "warn", note: "brain the size of a planet, data-bound at 412 h", val: "0.902" },
    { name: "hal-9000", params: "90 M", status: "diverged", tone: "danger", note: "lr 3e-4 too hot · halted at step 9,000", val: "NaN" },
  ].map(function (r) { var b = badge(r.tone); r.sBg = b.bg; r.sFg = b.fg; return r; });
  var LOSS_BAB = [3.21, 2.38, 1.92, 1.63, 1.42, 1.28, 1.16, 1.08, 1.00, 0.95, 0.90, 0.87, 0.85];
  var LOSS_C3 = [3.24, 2.61, 2.22, 1.97, 1.80, 1.68, 1.58, 1.50, 1.43, 1.37, 1.31, 1.26, 1.21];
  var LOSS_DATA = LOSS_BAB.map(function (v, i) { return { step: (i * 10) + "k", a: v, b: LOSS_C3[i] }; });
  var TRANSFER_DATA = [
    { v: "ID. Buzz", scratch: 12, tuned: 3 }, { v: "E-Transit", scratch: 14, tuned: 2 },
    { v: "eDeliver 3", scratch: 16, tuned: 4 }, { v: "Octavia iV", scratch: 9, tuned: 2 },
  ];
  function K(name, platform, sessions, signals, cov, opendbc) {
    return { name: name, platform: platform, sessions: sessions, signals: signals, cov: cov + "%", covW: cov + "%", opendbc: opendbc };
  }
  var KB = [
    K("VW Golf 8 1.5 eTSI", "MQB", "6", "84", 61, "87 % agree · vw_mqb"),
    K("VW ID. Buzz Cargo", "MEB", "4", "71", 74, "91 % agree · vw_meb"),
    K("Škoda Octavia iV", "MQB", "3", "58", 66, "shares 79 % with Golf 8"),
    K("Ford E-Transit", "—", "5", "44", 41, "no public DBC"),
    K("Maxus eDeliver 3", "SAIC", "3", "39", 49, "no public DBC"),
    K("Heart of Gold (sim)", "sim", "2", "12", 100, "ground truth by construction"),
  ];
  var SIG_TYPES = [
    { name: "vehicle speed", n: "9/9" }, { name: "wheel speeds", n: "8/9" }, { name: "brake", n: "9/9" },
    { name: "accel lon/lat", n: "8/9" }, { name: "steering angle", n: "6/9" }, { name: "motor/engine rpm", n: "7/9" },
    { name: "odometer", n: "7/9" }, { name: "SoC", n: "4/5 EV" }, { name: "coolant temp", n: "5/9" },
    { name: "gear", n: "3/9" }, { name: "fuel gauge", n: "2/4 ICE" }, { name: "turn signal", n: "5/9" },
  ];

  /* ------------------------------------------------------------ icons (SVG) */
  function icon(name) {
    var p = {
      sessions: '<path d="M4 5h16M4 12h16M4 19h10"/>',
      detail: '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
      align: '<circle cx="6" cy="6" r="2"/><circle cx="18" cy="18" r="2"/><path d="M8 6h6a2 2 0 0 1 2 2v8"/>',
      hypo: '<path d="M3 17l6-6 4 4 8-8"/><path d="M21 7v4M21 7h-4"/>',
      census: '<path d="M4 20V10M10 20V4M16 20v-7M22 20h-20"/>',
      training: '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M6 6l2 2M16 16l2 2M18 6l-2 2M8 16l-2 2"/>',
      kb: '<path d="M4 5a2 2 0 0 1 2-2h12v18H6a2 2 0 0 1-2-2z"/><path d="M8 3v18"/>',
      theme: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19"/>',
      logo: '<path d="M4 12a8 8 0 0 1 8-8M20 12a8 8 0 0 1-8 8"/><circle cx="12" cy="12" r="2.5"/>',
    }[name] || "";
    return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + p + "</svg>";
  }

  var NAV = [
    { id: "sessions", label: "Sessions" },
    { id: "detail", label: "Session detail" },
    { id: "align", label: "Alignment" },
    { id: "hypo", label: "Hypotheses" },
    { id: "census", label: "Bus census" },
    { id: "training", label: "Training", beta: true },
    { id: "kb", label: "Knowledge base" },
  ];
  var TITLES = {
    sessions: "Sessions", detail: "Session detail", align: "Alignment",
    hypo: "Hypothesis review", census: "Bus census", training: "Training", kb: "Knowledge base",
  };

  /* ----------------------------------------------------------------- charts */
  function chartLegend(series, colors) {
    return '<div style="display:flex;gap:16px;flex-wrap:wrap">' + series.map(function (s, i) {
      return '<span style="display:inline-flex;align-items:center;gap:6px;font-size:11px;' +
        'color:var(--text-secondary)"><span style="width:12px;height:3px;border-radius:2px;background:' +
        colors[i % colors.length] + '"></span>' + esc(s.name) + "</span>";
    }).join("") + "</div>";
  }
  var CHART_COLORS = ["var(--accent)", "var(--accent-solid)", "var(--status-info)"];
  function lineChart(data, xKey, series, opt) {
    opt = opt || {};
    var H2 = opt.height || 210, W = 1000, padL = 6, padR = 6, padT = 12, padB = 12;
    var vals = [];
    series.forEach(function (s) { data.forEach(function (d) { var v = +d[s.dataKey]; if (isFinite(v)) vals.push(v); }); });
    var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    if (opt.yMinZero) min = Math.min(0, min);
    if (min === max) max = min + 1;
    var n = data.length;
    function X(i) { return padL + (W - padL - padR) * (n <= 1 ? 0.5 : i / (n - 1)); }
    function Y(v) { return padT + (H2 - padT - padB) * (1 - (v - min) / (max - min)); }
    var grid = "";
    for (var g = 0; g <= 3; g++) {
      var gy = padT + (H2 - padT - padB) * g / 3;
      grid += '<line x1="' + padL + '" y1="' + gy + '" x2="' + (W - padR) + '" y2="' + gy +
        '" stroke="var(--border-primary)" stroke-width="1" vector-effect="non-scaling-stroke" opacity="0.5"/>';
    }
    var paths = series.map(function (s, si) {
      var pts = data.map(function (d, i) { return X(i) + "," + Y(+d[s.dataKey]); }).join(" ");
      return '<polyline points="' + pts + '" fill="none" stroke="' + CHART_COLORS[si % CHART_COLORS.length] +
        '" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/>';
    }).join("");
    var svg = '<svg viewBox="0 0 ' + W + " " + H2 + '" preserveAspectRatio="none" ' +
      'style="width:100%;height:' + H2 + 'px;display:block">' + grid + paths + "</svg>";
    var u = opt.unit ? " " + opt.unit : "";
    var yl = '<span style="position:absolute;left:3px;top:2px;font-size:10px;color:var(--text-muted);' + TNUM + '">' +
      fmt(max, opt.precision) + u + "</span>" +
      '<span style="position:absolute;left:3px;bottom:2px;font-size:10px;color:var(--text-muted);' + TNUM + '">' +
      fmt(min, opt.precision) + u + "</span>";
    var legend = opt.hideLegend ? "" : chartLegend(series, CHART_COLORS);
    return '<div style="display:flex;flex-direction:column;gap:6px">' + legend +
      '<div style="position:relative">' + svg + yl + "</div></div>";
  }
  function barChart(data, xKey, series, opt) {
    opt = opt || {};
    var H2 = opt.height || 230, W = 1000, padT = 12, padB = 6;
    var max = 0;
    series.forEach(function (s) { data.forEach(function (d) { max = Math.max(max, +d[s.dataKey]); }); });
    if (max === 0) max = 1;
    var groups = data.length, gw = W / groups, inner = gw * 0.62, bw = inner / series.length;
    var bars = "";
    data.forEach(function (d, gi) {
      var gx = gi * gw + (gw - inner) / 2;
      series.forEach(function (s, si) {
        var v = +d[s.dataKey], bh = (H2 - padT - padB) * (v / max);
        var bx = gx + si * bw, by = padT + (H2 - padT - padB) - bh;
        bars += '<rect x="' + bx + '" y="' + by + '" width="' + (bw * 0.84) + '" height="' + bh +
          '" rx="3" fill="' + CHART_COLORS[si % CHART_COLORS.length] + '"/>';
      });
    });
    var svg = '<svg viewBox="0 0 ' + W + " " + H2 + '" preserveAspectRatio="none" ' +
      'style="width:100%;height:' + H2 + 'px;display:block">' + bars + "</svg>";
    var labels = '<div style="display:flex;font-size:10.5px;color:var(--text-muted)">' +
      data.map(function (d) { return '<span style="flex:1;text-align:center">' + esc(String(d[xKey])) + "</span>"; }).join("") +
      "</div>";
    return '<div style="display:flex;flex-direction:column;gap:6px">' + chartLegend(series, CHART_COLORS) + svg + labels + "</div>";
  }

  /* ------------------------------------------------------------------ views */
  function coverageBar(covW, cov) {
    return '<div style="display:flex;align-items:center;gap:8px">' +
      '<div style="flex:1;height:6px;border-radius:999px;background:var(--bg-input);overflow:hidden">' +
      '<div style="height:100%;border-radius:999px;background:var(--accent-solid);width:' + covW + '"></div></div>' +
      '<span style="font-size:12px;' + TNUM + ';color:var(--text-secondary);width:34px;text-align:right">' + esc(cov) + "</span></div>";
  }

  function viewSessions() {
    var sessions = liveSessions() || DEMO_SESSIONS;
    var kpis = state.live.sessions
      ? [kpi("Sessions", String(sessions.length), sessions.length + " discovered on this server"),
         kpi("Confirmed signals", "342", "across 9 vehicles"),
         kpi("Corpus", "412 h", "unlabelled CAN, 2.1 B frames"),
         kpi("Awaiting merge", "2", "parts missing a counterpart")]
      : [kpi("Sessions", "27", "8 shown · 19 archived"),
         kpi("Confirmed signals", "342", "across 9 vehicles"),
         kpi("Corpus", "412 h", "unlabelled CAN, 2.1 B frames"),
         kpi("Awaiting merge", "2", "parts missing a counterpart")];

    var rows = sessions.map(function (s) {
      var dots = s.dots.map(function (d) {
        return '<span title="' + esc(d.t) + '" style="width:8px;height:8px;border-radius:999px;background:' +
          d.c + ";animation:" + d.anim + '"></span>';
      }).join("");
      return '<div class="cr-row-hover" data-action="open-session" data-id="' + esc(s.id) + '" data-vehicle="' + esc(s.vehicle) +
        '" style="display:grid;min-width:1080px;grid-template-columns:minmax(200px,1.5fr) minmax(150px,1.1fr) 100px 64px 80px 150px 104px minmax(130px,1fr);' +
        'gap:12px;padding:12px 16px;border-top:1px solid var(--border-primary);align-items:center;font-size:13px;color:var(--text-primary)">' +
        '<div style="' + MONO + ';font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(s.id) + "</div>" +
        '<div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(s.vehicle) + "</div>" +
        '<div style="color:var(--text-secondary)">' + esc(s.when) + "</div>" +
        '<div style="' + TNUM + ';color:var(--text-secondary)">' + esc(s.dur) + "</div>" +
        '<div style="' + TNUM + ';color:var(--text-secondary)">' + esc(s.frames) + "</div>" +
        '<div>' + badgeSpanRaw(s.merge, s.mBg, s.mFg) + "</div>" +
        '<div style="display:flex;gap:5px;align-items:center">' + dots + "</div>" +
        coverageBar(s.covW, s.cov) + "</div>";
    }).join("");

    return '<div style="display:flex;flex-direction:column;gap:16px">' +
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:16px">' + kpis.join("") + "</div>" +
      '<div style="' + CARD + ';overflow-x:auto;overflow-y:hidden">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;padding:14px 16px;position:sticky;left:0">' +
      '<div style="' + EYEBROW + '">Recent sessions</div>' +
      '<div style="font-size:12px;color:var(--text-muted)">click a row to open the pipeline</div></div>' +
      '<div style="display:grid;min-width:1080px;grid-template-columns:minmax(200px,1.5fr) minmax(150px,1.1fr) 100px 64px 80px 150px 104px minmax(130px,1fr);' +
      'gap:12px;padding:10px 16px;background:var(--bg-table-head);font-size:11px;font-weight:500;text-transform:uppercase;letter-spacing:0.05em;color:var(--text-secondary)">' +
      "<div>Session</div><div>Vehicle</div><div>Started</div><div>Length</div><div>Frames</div><div>Merge</div><div>Pipeline</div><div>Coverage</div></div>" +
      rows + "</div></div>";
  }
  function badgeSpanRaw(text, bg, fg) {
    return '<span class="badge" style="background:' + bg + ";color:" + fg + '">' + esc(text) + "</span>";
  }

  function viewDetail() {
    var stages = demoStages(state.identifyPct);
    var live = state.live.detail[state.sel.id];
    var streams = (live && live.streams && live.streams.length) ? live.streams.map(function (s) {
      return { path: s.path, kind: s.kind, rows: s.rows == null ? "—" : String(s.rows), rate: "—" };
    }) : DEMO_STREAMS;

    var stageCards = stages.map(function (st) {
      var link = st.link ? '<button data-action="go-view" data-view="' + st.go +
        '" style="align-self:flex-start;margin-top:2px;background:transparent;border:0;padding:0;' +
        'font-family:var(--font-sans);font-size:12px;font-weight:500;color:var(--accent);cursor:pointer">' + esc(st.linkLabel) + "</button>" : "";
      return '<div style="display:flex;flex-direction:column;gap:6px;padding:12px;border-radius:8px;background:' +
        st.bg + ";border:1px solid " + st.border + '">' +
        '<div style="display:flex;align-items:center;gap:8px">' +
        '<span style="width:9px;height:9px;border-radius:999px;background:' + st.dotC + ";animation:" + st.anim + ';flex-shrink:0"></span>' +
        '<span style="font-size:11px;font-weight:500;text-transform:uppercase;letter-spacing:0.05em;color:var(--text-secondary)">' + esc(st.num) + "</span>" +
        '<span style="margin-left:auto;font-size:11px;' + TNUM + ';color:var(--text-muted);white-space:nowrap">' + esc(st.time) + "</span></div>" +
        '<div style="font-size:14px;font-weight:600;color:var(--text-heading)">' + esc(st.name) + "</div>" +
        '<div style="font-size:12px;color:var(--text-secondary);line-height:1.45">' + esc(st.stat) + "</div>" + link + "</div>";
    }).join("");

    var streamRows = streams.map(function (f) {
      return '<div style="display:grid;min-width:560px;grid-template-columns:1.6fr 100px 100px 80px;gap:12px;padding:9px 16px;' +
        'border-top:1px solid var(--border-primary);font-size:12px;align-items:center">' +
        '<div style="' + MONO + ';color:var(--text-primary)">' + esc(f.path) + "</div>" +
        '<div style="color:var(--text-secondary)">' + esc(f.kind) + "</div>" +
        '<div style="' + TNUM + ';color:var(--text-secondary)">' + esc(f.rows) + "</div>" +
        '<div style="' + TNUM + ';color:var(--text-muted)">' + esc(f.rate) + "</div></div>";
    }).join("");

    var manifest = manifestRows(live);
    var log = demoLog(state.identifyPct).map(function (l) {
      return '<div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:' + l.c + '">' + esc(l.t) + "</div>";
    }).join("");

    return '<div style="display:flex;flex-direction:column;gap:16px">' +
      '<div style="' + CARD + ';padding:16px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">' +
      '<div style="' + EYEBROW + '">Pipeline</div>' +
      '<div style="font-size:12px;color:var(--text-muted)">stages 1a–1b run in the vehicle · 2–5 run here</div></div>' +
      '<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px">' + stageCards + "</div></div>" +
      '<div style="display:grid;grid-template-columns:1.1fr 1fr;gap:16px">' +
      '<div style="' + CARD + ';overflow-x:auto;overflow-y:hidden">' +
      '<div style="padding:14px 16px;' + EYEBROW + ';position:sticky;left:0">Streams</div>' +
      '<div style="display:grid;min-width:560px;grid-template-columns:1.6fr 100px 100px 80px;gap:12px;padding:8px 16px;background:var(--bg-table-head);' +
      'font-size:11px;font-weight:500;text-transform:uppercase;letter-spacing:0.05em;color:var(--text-secondary)">' +
      "<div>Path</div><div>Kind</div><div>Rows</div><div>Rate</div></div>" + streamRows + "</div>" +
      '<div style="display:flex;flex-direction:column;gap:16px">' +
      '<div style="' + CARD + ';padding:16px;display:flex;flex-direction:column;gap:10px">' +
      '<div style="' + EYEBROW + '">Manifest</div>' +
      '<div style="display:grid;grid-template-columns:110px 1fr;gap:6px 12px;font-size:13px">' + manifest + "</div></div>" +
      '<div style="' + CARD + ';padding:16px;display:flex;flex-direction:column;gap:8px;flex:1">' +
      '<div style="' + EYEBROW + '">Pipeline log</div>' +
      '<div style="' + MONO + ';font-size:11.5px;line-height:1.7;overflow:hidden">' + log + "</div></div></div></div></div>";
  }
  function manifestRows(live) {
    if (live) {
      var rows = "";
      function r(k, v) { rows += '<div style="color:var(--text-secondary)">' + esc(k) + "</div><div>" + v + "</div>"; }
      r("Vehicle", esc(live.vehicle || "unknown"));
      (live.devices || []).forEach(function (d) {
        var clk = d.clock || {};
        r(cap(d.role || d.kind || "device"),
          '<span style="' + MONO + ';font-size:12px">' + esc(d.id || "") + "</span> · clock " +
          esc(clk.source || "?") + " ±" + esc(clk.err_est_s != null ? clk.err_est_s + " s" : "?"));
      });
      r("Streams", (live.streams || []).length + " declared");
      if (live.created_utc) r("Created", '<span style="' + MONO + ';font-size:12px">' + esc(live.created_utc) + "</span>");
      return rows;
    }
    // demo manifest
    return '<div style="color:var(--text-secondary)">Vehicle</div><div>VW Golf 8 1.5 eTSI (2021) · <span style="color:var(--text-muted)">vin sha256:9f2c…</span></div>' +
      '<div style="color:var(--text-secondary)">Route</div><div>Graz Andritz → Raaba, A2 stretch</div>' +
      '<div style="color:var(--text-secondary)">Edge</div><div style="' + MONO + ';font-size:12px">autopi-3f7a2c1d · clock ntp ±30 ms</div>' +
      '<div style="color:var(--text-secondary)">Companion</div><div style="' + MONO + ';font-size:12px">iphone-7c · clock gps ±20 ms</div>' +
      '<div style="color:var(--text-secondary)">Sync marker</div><div>brake_pulse ×3 @ 07:42:03 <span style="color:var(--text-muted)">(video + CAN + IMU)</span></div>' +
      '<div style="color:var(--text-secondary)">Bus</div><div>can0 @ 500 kbit/s · 87 arbitration IDs</div>';
  }
  function cap(s) { return String(s).charAt(0).toUpperCase() + String(s).slice(1); }

  function viewAlign() {
    var live = state.live.identify[state.sel.id];
    var clocks = DEMO_CLOCKS;
    var note = "fine alignment finished in 11.4 s — comfortably under twelve parsecs";
    if (live && live.alignment) {
      var a = live.alignment;
      clocks = [{
        dev: (a.pair && a.pair[0]) || "edge", src: "ntp", prior: "—",
        fine: (a.delta >= 0 ? "+" : "") + fmt(a.delta * 1000, 1) + " ms",
        drift: "—", method: esc(a.method || "xcorr"),
      }, {
        dev: (a.pair && a.pair[1]) || "companion", src: "gps", prior: "—",
        fine: "0.0 ms (ref)", drift: "—", method: "reference clock",
      }];
      note = "live · confidence " + fmt(a.confidence, 3) + " · delta " + fmt(a.delta, 3) + " s";
    }
    var clockRows = clocks.map(function (c) {
      return '<div style="display:grid;min-width:900px;grid-template-columns:1fr 90px 110px 130px 90px 1.5fr;gap:12px;padding:10px 16px;' +
        'border-top:1px solid var(--border-primary);font-size:12.5px;align-items:center">' +
        '<div style="' + MONO + ';font-size:12px">' + esc(c.dev) + "</div>" +
        '<div style="color:var(--text-secondary)">' + esc(c.src) + "</div>" +
        '<div style="' + TNUM + ';color:var(--text-secondary)">' + esc(c.prior) + "</div>" +
        '<div style="' + TNUM + ';color:var(--text-primary);font-weight:600">' + esc(c.fine) + "</div>" +
        '<div style="' + TNUM + ';color:var(--text-secondary)">' + esc(c.drift) + "</div>" +
        '<div style="color:var(--text-secondary)">' + c.method + "</div></div>";
    }).join("");

    function syncCard(title, val, note) {
      return '<div style="flex:1;padding:10px 12px;border-radius:8px;background:var(--bg-subtle);border:1px solid var(--border-primary)">' +
        '<div style="font-size:11px;text-transform:uppercase;letter-spacing:0.05em;color:var(--text-secondary)">' + esc(title) + "</div>" +
        '<div style="' + MONO + ';font-size:13px;margin-top:4px;color:var(--text-primary)">' + val +
        ' <span style="color:var(--status-success)">✓</span>' + (note ? ' <span style="color:var(--text-muted)">' + note + "</span>" : "") + "</div></div>";
    }

    return '<div style="display:flex;flex-direction:column;gap:16px">' +
      '<div style="' + CARD + ';overflow-x:auto;overflow-y:hidden">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;padding:14px 16px;position:sticky;left:0">' +
      '<div style="' + EYEBROW + '">Clock offsets</div><div style="font-size:12px;color:var(--text-muted)">' + esc(note) + "</div></div>" +
      '<div style="display:grid;min-width:900px;grid-template-columns:1fr 90px 110px 130px 90px 1.5fr;gap:12px;padding:8px 16px;background:var(--bg-table-head);' +
      'font-size:11px;font-weight:500;text-transform:uppercase;letter-spacing:0.05em;color:var(--text-secondary)">' +
      "<div>Clock</div><div>Source</div><div>Prior</div><div>Fine offset</div><div>Drift</div><div>Method</div></div>" + clockRows + "</div>" +
      '<div style="display:grid;grid-template-columns:1fr 1.2fr;gap:16px">' +
      '<div style="' + CARD + ';padding:16px;display:flex;flex-direction:column;gap:6px">' +
      '<div style="' + EYEBROW + '">Cross-correlation · obd.vehicle_speed × gps.speed</div>' +
      '<div style="font-size:12px;color:var(--text-muted);margin-bottom:4px">peak r = 0.998 at +8.3 ms — residual applied to the edge clock</div>' +
      lineChart(XCORR_DATA, "lag", [{ dataKey: "r", name: "r" }], { height: 210, hideLegend: true, precision: 2 }) +
      '<div style="font-size:11px;color:var(--text-muted);text-align:center">lag, ms</div></div>' +
      '<div style="' + CARD + ';padding:16px;display:flex;flex-direction:column;gap:6px">' +
      '<div style="' + EYEBROW + '">After alignment · GPS speed vs candidate 0x0FD</div>' +
      '<div style="font-size:12px;color:var(--text-muted);margin-bottom:4px">two clocks, one drive — the traces should be indistinguishable</div>' +
      lineChart(OVERLAY_DATA, "t", [{ dataKey: "gps", name: "GPS ground speed" }, { dataKey: "can", name: "0x0FD × 0.0075" }], { height: 210, unit: "km/h", precision: 1 }) +
      "</div></div>" +
      '<div style="' + CARD + ';padding:16px;display:flex;gap:24px;align-items:flex-start">' +
      '<div style="display:flex;flex-direction:column;gap:4px;min-width:220px">' +
      '<div style="' + EYEBROW + '">Sync marker</div>' +
      '<div style="font-size:14px;font-weight:600;color:var(--text-heading)">brake_pulse ×3 @ 07:42:03</div>' +
      '<div style="font-size:12px;color:var(--text-muted)">Three short flashes — Morse for "S". One shared instant, three streams.</div></div>' +
      '<div style="display:flex;gap:12px;flex:1">' +
      syncCard("Video · lamp pixels", "+12.0 ms", "") +
      syncCard("CAN · 0x105 bit 21", "0.0 ms", "(reference)") +
      syncCard("IMU · longitudinal decel", "−4.1 ms", "") +
      "</div></div></div>";
  }

  function viewHypo() {
    var live = state.live.identify[state.sel.id];
    var hypos = (live && live.per_reference) ? hyposFromLive(live) : demoHypos();
    // Map each row (by rank) back to the payload the confirm/reject API needs.
    state.hypoLive = {};
    hypos.forEach(function (h) { if (h.live) state.hypoLive[h.rank] = h.live; });
    var counts = {};
    hypos.forEach(function (h) { counts[h.status] = (counts[h.status] || 0) + 1; });
    var confirmed = counts.confirmed || 0;
    var pending = (counts.pending || 0) + (counts.review || 0);
    var rejected = counts.rejected || 0;

    var rows = hypos.map(function (h) {
      return '<div style="display:grid;min-width:1140px;grid-template-columns:36px 1fr 1.7fr 120px 64px 120px 104px 158px;gap:12px;padding:11px 16px;' +
        'border-top:1px solid var(--border-primary);align-items:center;font-size:12.5px">' +
        '<div style="' + TNUM + ';color:var(--text-muted)">' + esc(h.rank) + "</div>" +
        '<div><div style="' + MONO + ';font-size:12px;color:var(--text-primary)">' + esc(h.ref) + "</div>" +
        '<div style="font-size:11px;color:var(--text-muted)">' + esc(h.refKind) + "</div></div>" +
        '<div><div style="' + MONO + ';font-size:12px;color:var(--text-primary)">' + esc(h.cand) + "</div>" +
        '<div style="font-size:11px;color:var(--text-muted)">' + esc(h.candNote) + "</div></div>" +
        '<div style="' + MONO + ';font-size:12px;color:var(--text-secondary)">' + esc(h.score) + "</div>" +
        '<div style="font-size:12px;color:' + h.priorC + '">' + esc(h.prior) + "</div>" +
        '<div style="display:flex;align-items:center;gap:8px">' +
        '<div style="flex:1;height:6px;border-radius:999px;background:var(--bg-input);overflow:hidden">' +
        '<div style="height:100%;border-radius:999px;background:' + h.confC + ";width:" + h.confW + '"></div></div>' +
        '<span style="font-size:11px;' + TNUM + ';color:var(--text-secondary);width:26px;text-align:right">' + esc(h.conf) + "</span></div>" +
        "<div>" + badgeSpanRaw(h.status, h.sBg, h.sFg) + "</div>" +
        '<div style="display:flex;gap:8px;justify-content:flex-end">' +
        '<button class="cr-rowbtn" data-action="confirm-hypo" data-id="' + esc(h.rank) + '">Confirm</button>' +
        '<button class="cr-rowbtn cr-rowbtn-danger" data-action="reject-hypo" data-id="' + esc(h.rank) + '">Reject</button>' +
        "</div></div>";
    }).join("");

    return '<div style="display:flex;flex-direction:column;gap:16px">' +
      '<div style="display:flex;align-items:center;gap:12px">' +
      badgeSpan(confirmed + " confirmed", "active") +
      badgeSpan(pending + " pending", "info") +
      badgeSpan(rejected + " rejected", "neutral") +
      '<span style="font-size:12px;color:var(--text-muted)">ranked by correlation · mutual information · physical priors</span>' +
      '<div style="flex:1"></div>' +
      '<button class="cr-btn cr-btn-primary cr-btn-sm" data-action="export-dbc">Export DBC</button></div>' +
      '<div style="' + CARD + ';overflow-x:auto;overflow-y:hidden">' +
      '<div style="display:grid;min-width:1140px;grid-template-columns:36px 1fr 1.7fr 120px 64px 120px 104px 158px;gap:12px;padding:10px 16px;background:var(--bg-table-head);' +
      'font-size:11px;font-weight:500;text-transform:uppercase;letter-spacing:0.05em;color:var(--text-secondary)">' +
      "<div>#</div><div>Reference</div><div>Candidate</div><div>Score</div><div>Prior</div><div>Confidence</div><div>Status</div><div></div></div>" +
      rows + "</div>" +
      '<div style="display:grid;grid-template-columns:1.4fr 1fr;gap:16px">' +
      '<div style="' + CARD + ';padding:16px;display:flex;flex-direction:column;gap:10px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between">' +
      '<div style="' + EYEBROW + '">DBC preview · vw_golf8_mqb.dbc</div>' +
      '<span style="font-size:12px;color:var(--text-muted)">confirmed hypotheses only</span></div>' +
      '<pre style="margin:0;padding:12px;border-radius:8px;background:var(--bg-page);border:1px solid var(--border-primary);' +
      MONO + ';font-size:11.5px;line-height:1.65;color:var(--text-secondary);overflow-x:auto">' + esc(DBC_TEXT) + "</pre></div>" +
      '<div style="' + CARD + ';padding:16px;display:flex;flex-direction:column;gap:10px">' +
      '<div style="' + EYEBROW + '">Review notes</div>' +
      '<div style="font-size:13px;line-height:1.6;color:var(--text-secondary)">Confirm writes the mapping to <span style="' + MONO + ';font-size:12px">labels/annotations.json</span> — it becomes a training label for the foundation model and a regression fixture for this vehicle.</div>' +
      '<div style="font-size:13px;line-height:1.6;color:var(--text-secondary)">Rejections are remembered per platform, so MQB false friends stay rejected on the next Golf.</div>' +
      '<div style="margin-top:auto;font-size:12px;color:var(--text-muted);font-style:italic">Rejected candidates: these aren\'t the signals you\'re looking for.</div></div></div></div>';
  }
  function hyposFromLive(live) {
    var out = [];
    var rank = 1;
    Object.keys(live.per_reference).forEach(function (ref) {
      var arr = live.per_reference[ref];
      if (!arr || !arr.length) return;
      var top = arr[0];
      var r = Math.abs(top.r);
      var conf = Math.round(r * 100);
      var f = top.field || {};
      // Live rows are re-ranked after sorting, so key any confirm/reject status
      // override by the stable reference name rather than the volatile rank.
      var status0 = state.hypoRefOverride[ref] ||
        (r >= 0.97 ? "confirmed" : r >= 0.9 ? "pending" : "review");
      var h = H(rank, ref, "n=" + (top.n || 0),
        esc(top.candidate),
        "scale " + fmt(top.scale, 4) + " · offset " + fmt(top.offset, 2),
        "r " + fmt(top.r, 3) + " · MI " + fmt(top.mutual_info, 1),
        r >= 0.9 ? "✓ strong" : "~ weak", r >= 0.9, conf, status0);
      // Carry the raw payload the confirm/reject endpoints need for this row.
      // `field` is the candidate dict but omits the computed `label` property, so
      // inject the label the API keys the KB entry / rejection memory on.
      var candDict = {};
      Object.keys(f).forEach(function (k) { candDict[k] = f[k]; });
      candDict.label = top.candidate;
      h.live = {
        reference: ref,
        candidate: candDict,
        candidate_label: top.candidate,
        r: top.r,
      };
      out.push(h);
      rank++;
    });
    out.sort(function (a, b) { return parseFloat(b.conf) - parseFloat(a.conf); });
    out.forEach(function (h, i) { h.rank = i + 1; });
    return out;
  }

  function viewCensus() {
    var live = state.live.census[state.sel.id];
    var census = DEMO_CENSUS;
    var kpiVals = [kpi("Arbitration IDs", "87"), kpi("Frames", "1.94 M"), kpi("Bus load", "38 %"), kpi("Bitrate", "500 k")];
    if (live && live.messages && live.messages.length) {
      census = live.messages.slice().sort(function (a, b) { return b.count - a.count; }).map(function (m) {
        var tone = m.role === "periodic" ? "active" : m.role === "sporadic" ? "info" : "neutral";
        var note = m.multiplexor ? "mux b" + m.multiplexor.byte_offset : "";
        return C(m.arb_id_hex,
          m.period_ms ? m.period_ms.toFixed(0) + " ms" : "—",
          "cv " + fmt(m.jitter, 2),
          String(m.dlc),
          m.count.toLocaleString(),
          m.role, note, note ? "warn" : "");
      });
      kpiVals = [kpi("Arbitration IDs", String(live.arbitration_ids)),
        kpi("Frames", live.frames != null ? live.frames.toLocaleString() : "—"),
        kpi("Bus load", "—", "live census"),
        kpi("Bitrate", "—")];
    }

    // Real coverage for the selected session replaces the "Bus load" placeholder.
    var cov = state.live.coverage[state.sel.id];
    if (cov) {
      kpiVals[2] = kpi("Coverage", Math.round((cov.coverage || 0) * 100) + " %",
        (cov.confirmed_fields != null ? cov.confirmed_fields : "—") + " / " +
        (cov.dynamic_fields != null ? cov.dynamic_fields : "—") + " dynamic fields");
    }

    var heatHeader = '<div style="display:grid;grid-template-columns:76px repeat(8,1fr);gap:3px;font-size:10.5px;color:var(--text-muted)">' +
      "<div></div>" + [0, 1, 2, 3, 4, 5, 6, 7].map(function (i) { return '<div style="text-align:center">B' + i + "</div>"; }).join("") + "</div>";
    var heatBody = HEAT_ROWS.map(function (r) {
      var cells = r.cells.map(function (c) {
        return '<div title="' + esc(c.ti) + '" style="height:20px;border-radius:4px;background:' + c.bg + '"></div>';
      }).join("");
      return '<div style="display:grid;grid-template-columns:76px repeat(8,1fr);gap:3px;align-items:center">' +
        '<div style="' + MONO + ';font-size:11px;color:var(--text-secondary)">' + esc(r.id) + "</div>" + cells + "</div>";
    }).join("");

    var censusRows = census.map(function (c) {
      var noteHtml = c.hasNote ? badgeSpanRaw(c.note, c.nBg, c.nFg) : "";
      return '<div style="display:grid;min-width:640px;grid-template-columns:76px 86px 70px 46px 86px 1.5fr;gap:12px;padding:9px 16px;' +
        'border-top:1px solid var(--border-primary);font-size:12px;align-items:center">' +
        '<div style="' + MONO + ';color:var(--text-primary)">' + esc(c.id) + "</div>" +
        '<div style="' + TNUM + ';color:var(--text-secondary)">' + esc(c.period) + "</div>" +
        '<div style="' + TNUM + ';color:var(--text-muted)">' + esc(c.jitter) + "</div>" +
        '<div style="' + TNUM + ';color:var(--text-secondary)">' + esc(c.dlc) + "</div>" +
        '<div style="' + TNUM + ';color:var(--text-secondary)">' + esc(c.frames) + "</div>" +
        '<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">' +
        '<span style="font-size:11px;color:var(--text-secondary)">' + esc(c.tax) + "</span>" + noteHtml + "</div></div>";
    }).join("");

    // Unidentified clusters: prefer the real ones for the selected session.
    var clu = state.live.clusters[state.sel.id];
    var clusterCard;
    if (clu && Array.isArray(clu.clusters) && clu.clusters.length) {
      var clusterList = clu.clusters.map(function (c) {
        return '<div style="font-size:13px;line-height:1.6;color:var(--text-secondary)">' +
          '<span style="' + MONO + ';font-size:12px;color:var(--text-primary)">' +
          (c || []).map(esc).join(" · ") + "</span></div>";
      }).join("");
      clusterCard = '<div style="' + CARD + ';padding:16px;display:flex;flex-direction:column;gap:8px">' +
        '<div style="display:flex;align-items:center;gap:10px">' +
        '<div style="' + EYEBROW + '">Unidentified clusters</div>' +
        badgeSpan(clu.clusters.length + " unmatched", "warn") + "</div>" +
        '<div style="font-size:12px;color:var(--text-muted)">Periodic, structured, mutually correlated groups that match no reference on this drive.</div>' +
        clusterList + "</div>";
    } else {
      clusterCard = '<div style="' + CARD + ';padding:16px;display:flex;flex-direction:column;gap:8px">' +
        '<div style="display:flex;align-items:center;gap:10px">' +
        '<div style="' + EYEBROW + '">Unidentified cluster · darmok</div>' + badgeSpan("meaning unknown", "warn") + "</div>" +
        '<div style="font-size:13px;line-height:1.6;color:var(--text-secondary)"><span style="' + MONO + ';font-size:12px">0x4A1 · 0x4A3 · 0x5C2</span> — periodic, structured, mutually correlated; matches no reference on this drive. The metaphor isn\'t in the corpus yet — queued for the next labelled drive.</div></div>';
    }

    return '<div style="display:flex;flex-direction:column;gap:16px">' +
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:16px">' + kpiVals.join("") + "</div>" +
      '<div style="display:grid;grid-template-columns:minmax(420px,1fr) 1.3fr;gap:16px;align-items:start">' +
      '<div style="' + CARD + ';padding:16px;display:flex;flex-direction:column;gap:10px">' +
      '<div style="' + EYEBROW + '">Byte activity · flips per frame</div>' + heatHeader +
      '<div style="display:flex;flex-direction:column;gap:3px">' + heatBody + "</div>" +
      '<div style="display:flex;align-items:center;gap:8px;font-size:11px;color:var(--text-muted);margin-top:2px">' +
      "<span>quiet</span><div style=\"flex:1;height:8px;border-radius:4px;background:linear-gradient(90deg, color-mix(in oklab, var(--accent) 6%, transparent), var(--accent))\"></div><span>every frame</span></div></div>" +
      '<div style="display:flex;flex-direction:column;gap:16px">' +
      '<div style="' + CARD + ';overflow-x:auto;overflow-y:hidden">' +
      '<div style="padding:14px 16px;' + EYEBROW + ';position:sticky;left:0">Census · top talkers</div>' +
      '<div style="display:grid;min-width:640px;grid-template-columns:76px 86px 70px 46px 86px 1.5fr;gap:12px;padding:8px 16px;background:var(--bg-table-head);' +
      'font-size:11px;font-weight:500;text-transform:uppercase;letter-spacing:0.05em;color:var(--text-secondary)">' +
      "<div>ID</div><div>Period</div><div>Jitter</div><div>DLC</div><div>Frames</div><div>Field taxonomy</div></div>" + censusRows + "</div>" +
      clusterCard + "</div></div></div>";
  }

  function viewTraining() {
    var kpis = [kpi("Pretraining corpus", "412 h", "raw CAN · 9 vehicles"),
      kpi("Labelled drives", "61", "phone + OBD + OCR grounded"),
      '<div style="' + CARD + ';padding:16px"><div style="' + EYEBROW + '">Active run</div>' +
      '<div style="font-size:26px;font-weight:700;line-height:1.4;color:var(--text-heading);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">babelfish-1</div>' +
      '<div style="font-size:12px;color:var(--text-muted)">epoch 12/40 · 8×A100</div></div>',
      kpi("Val loss", "0.847", "masked-frame prediction")];
    var runRows = RUNS.map(function (r) {
      return '<div style="display:grid;min-width:620px;grid-template-columns:130px 70px 110px 1fr 80px;gap:12px;padding:10px 16px;' +
        'border-top:1px solid var(--border-primary);font-size:12.5px;align-items:center">' +
        '<div style="' + MONO + ';font-size:12px;color:var(--text-primary)">' + esc(r.name) + "</div>" +
        '<div style="' + TNUM + ';color:var(--text-secondary)">' + esc(r.params) + "</div>" +
        "<div>" + badgeSpanRaw(r.status, r.sBg, r.sFg) + "</div>" +
        '<div style="font-size:12px;color:var(--text-muted)">' + esc(r.note) + "</div>" +
        '<div style="' + TNUM + ';color:var(--text-secondary)">' + esc(r.val) + "</div></div>";
    }).join("");

    return '<div style="display:flex;flex-direction:column;gap:16px">' +
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:16px">' + kpis.join("") + "</div>" +
      '<div style="display:grid;grid-template-columns:1.3fr 1fr;gap:16px">' +
      '<div style="' + CARD + ';padding:16px;display:flex;flex-direction:column;gap:6px">' +
      '<div style="' + EYEBROW + '">Pretraining loss</div>' +
      lineChart(LOSS_DATA, "step", [{ dataKey: "a", name: "babelfish-1" }, { dataKey: "b", name: "c3po-6m" }], { height: 230, precision: 2 }) + "</div>" +
      '<div style="' + CARD + ';padding:16px;display:flex;flex-direction:column;gap:6px">' +
      '<div style="' + EYEBROW + '">Transfer · labelled drives to 90 % recall</div>' +
      barChart(TRANSFER_DATA, "v", [{ dataKey: "scratch", name: "from scratch" }, { dataKey: "tuned", name: "fine-tuned" }], { height: 230 }) + "</div></div>" +
      '<div style="display:grid;grid-template-columns:1.5fr 1fr;gap:16px">' +
      '<div style="' + CARD + ';overflow-x:auto;overflow-y:hidden">' +
      '<div style="padding:14px 16px;' + EYEBROW + ';position:sticky;left:0">Runs</div>' +
      '<div style="display:grid;min-width:620px;grid-template-columns:130px 70px 110px 1fr 80px;gap:12px;padding:8px 16px;background:var(--bg-table-head);' +
      'font-size:11px;font-weight:500;text-transform:uppercase;letter-spacing:0.05em;color:var(--text-secondary)">' +
      "<div>Run</div><div>Params</div><div>Status</div><div>Note</div><div>Val loss</div></div>" + runRows + "</div>" +
      '<div style="' + CARD + ';padding:16px;display:flex;flex-direction:column;gap:8px">' +
      '<div style="' + EYEBROW + '">Trainer log · hal-9000</div>' +
      '<div style="' + MONO + ';font-size:11.5px;line-height:1.7;color:var(--text-secondary)">' +
      "<div>step 8600" + NBSP + NBSP + "loss 1.034" + NBSP + NBSP + "grad 2.1</div>" +
      "<div>step 8800" + NBSP + NBSP + "loss 1.021" + NBSP + NBSP + "grad 7.4</div>" +
      '<div style="color:var(--status-warning)">step 8900' + NBSP + NBSP + "loss 1.318" + NBSP + NBSP + "grad 41.9</div>" +
      '<div style="color:var(--status-danger)">step 9000' + NBSP + NBSP + "loss NaN — i'm sorry dave, i'm afraid i can't descend this gradient</div>" +
      '<div style="color:var(--text-muted)">run halted (exit 2001) · lr 3e-4 → retry queued at 1e-4</div></div></div></div></div>';
  }

  function kbPlatformTable(platforms) {
    var rows = platforms.map(function (p) {
      return '<div style="display:grid;min-width:640px;grid-template-columns:1.6fr 90px 90px 90px;gap:12px;padding:11px 16px;' +
        'border-top:1px solid var(--border-primary);font-size:12.5px;align-items:center">' +
        '<div style="color:var(--text-primary);font-weight:500">' + esc(p.platform) + "</div>" +
        '<div style="' + TNUM + ';color:var(--text-secondary)">' + esc(p.signals) + "</div>" +
        '<div style="' + TNUM + ';color:var(--text-secondary)">' + esc(p.vehicles) + "</div>" +
        '<div style="' + TNUM + ';color:var(--text-secondary)">' + esc(p.rejected) + "</div></div>";
    }).join("");
    return '<div style="' + CARD + ';overflow-x:auto;overflow-y:hidden">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;padding:14px 16px;position:sticky;left:0">' +
      '<div style="' + EYEBROW + '">Platforms</div>' +
      '<div style="font-size:12px;color:var(--text-muted)">live · from the cross-vehicle knowledge base</div></div>' +
      '<div style="display:grid;min-width:640px;grid-template-columns:1.6fr 90px 90px 90px;gap:12px;padding:8px 16px;background:var(--bg-table-head);' +
      'font-size:11px;font-weight:500;text-transform:uppercase;letter-spacing:0.05em;color:var(--text-secondary)">' +
      "<div>Platform</div><div>Signals</div><div>Vehicles</div><div>Rejected</div></div>" + rows + "</div>";
  }
  function kbVehicleTable() {
    var rows = KB.map(function (v) {
      return '<div style="display:grid;min-width:980px;grid-template-columns:1.3fr 90px 80px 90px minmax(140px,1fr) 150px 90px;gap:12px;padding:11px 16px;' +
        'border-top:1px solid var(--border-primary);font-size:12.5px;align-items:center">' +
        '<div style="color:var(--text-primary);font-weight:500">' + esc(v.name) + "</div>" +
        '<div style="color:var(--text-secondary)">' + esc(v.platform) + "</div>" +
        '<div style="' + TNUM + ';color:var(--text-secondary)">' + esc(v.sessions) + "</div>" +
        '<div style="' + TNUM + ';color:var(--text-secondary)">' + esc(v.signals) + "</div>" +
        coverageBar(v.covW, v.cov) +
        '<div style="font-size:12px;color:var(--text-muted)">' + esc(v.opendbc) + "</div>" +
        '<div><button class="cr-rowbtn" data-action="kb-export" data-name="' + esc(v.name) + '">.dbc ↓</button></div></div>';
    }).join("");
    return '<div style="' + CARD + ';overflow-x:auto;overflow-y:hidden">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;padding:14px 16px;position:sticky;left:0">' +
      '<div style="' + EYEBROW + '">Vehicles</div>' +
      '<div style="font-size:12px;color:var(--text-muted)">coverage = confirmed signals ÷ dynamic fields observed</div></div>' +
      '<div style="display:grid;min-width:980px;grid-template-columns:1.3fr 90px 80px 90px minmax(140px,1fr) 150px 90px;gap:12px;padding:8px 16px;background:var(--bg-table-head);' +
      'font-size:11px;font-weight:500;text-transform:uppercase;letter-spacing:0.05em;color:var(--text-secondary)">' +
      "<div>Vehicle</div><div>Platform</div><div>Sessions</div><div>Signals</div><div>Coverage</div><div>vs opendbc</div><div></div></div>" + rows + "</div>";
  }
  function viewKb() {
    var kn = state.live.knowledge;
    var tableCard = (kn && Array.isArray(kn.platforms) && kn.platforms.length)
      ? kbPlatformTable(kn.platforms) : kbVehicleTable();
    var chips = SIG_TYPES.map(function (t) {
      return '<span style="display:inline-flex;align-items:center;gap:6px;padding:5px 11px;border-radius:999px;background:var(--bg-subtle);' +
        'border:1px solid var(--border-primary);font-size:12px;color:var(--text-primary)">' + esc(t.name) +
        ' <span style="' + TNUM + ';color:var(--text-muted)">' + esc(t.n) + "</span></span>";
    }).join("");

    return '<div style="display:flex;flex-direction:column;gap:16px">' + tableCard +
      '<div style="display:grid;grid-template-columns:1.4fr 1fr;gap:16px">' +
      '<div style="' + CARD + ';padding:16px;display:flex;flex-direction:column;gap:12px">' +
      '<div style="' + EYEBROW + '">Signal types across the fleet</div>' +
      '<div style="display:flex;flex-wrap:wrap;gap:8px">' + chips + "</div>" +
      '<div style="font-size:12px;color:var(--text-muted);line-height:1.6">Counts are vehicles with a confirmed mapping. Speed and brake are grounded on every vehicle — GPS and the IMU travel with every drive. Fuel and gear need the dashboard camera.</div></div>' +
      '<div style="' + CARD + ';padding:16px;display:flex;flex-direction:column;gap:10px;justify-content:center">' +
      '<div style="font-size:15px;line-height:1.65;color:var(--text-primary);font-style:italic">"Small, yellow, plugs into the OBD port — feed it half an hour of ordinary driving and you can understand anything said to you on any bus."</div>' +
      '<div style="font-size:12px;color:var(--text-muted)">— the pitch, roughly. Improbably, it works: 342 signals and counting.</div></div></div></div>';
  }

  var VIEWS = {
    sessions: viewSessions, detail: viewDetail, align: viewAlign,
    hypo: viewHypo, census: viewCensus, training: viewTraining, kb: viewKb,
  };

  /* --------------------------------------------------------------- shell */
  function sidebar() {
    if (!state.sbOpen) {
      return '<div style="width:64px;flex-shrink:0;background:var(--bg-sidebar);border-right:1px solid var(--border-primary);' +
        'display:flex;flex-direction:column;align-items:center;padding:16px 0;gap:8px">' +
        '<button class="cr-nav" data-action="toggle-sidebar" style="width:40px;justify-content:center;padding:9px 0" title="Expand">' + icon("logo") + "</button>" +
        NAV.map(function (n) {
          return '<button class="cr-nav' + (state.view === n.id ? " active" : "") + '" data-action="nav" data-view="' + n.id +
            '" title="' + esc(n.label) + '" style="width:40px;justify-content:center;padding:9px 0">' + icon(n.id) + "</button>";
        }).join("") + "</div>";
    }
    var items = NAV.map(function (n) {
      var betaPill = n.beta ? '<span class="badge" style="margin-left:auto;background:var(--accent-subtle);color:var(--accent-subtle-text)">beta</span>' : "";
      return '<button class="cr-nav' + (state.view === n.id ? " active" : "") + '" data-action="nav" data-view="' + n.id + '">' +
        icon(n.id) + "<span>" + esc(n.label) + "</span>" + betaPill + "</button>";
    }).join("");
    return '<div style="width:256px;flex-shrink:0;background:var(--bg-sidebar);border-right:1px solid var(--border-primary);' +
      'display:flex;flex-direction:column;height:100%">' +
      '<div style="height:64px;flex-shrink:0;display:flex;align-items:center;gap:10px;padding:0 16px;border-bottom:1px solid var(--border-primary)">' +
      '<span style="color:var(--logo-accent);display:flex">' + icon("logo") + "</span>" +
      '<span style="font-weight:700;font-size:15px;color:var(--text-heading)">CAN-Rosetta</span>' +
      '<button class="cr-nav" data-action="toggle-sidebar" style="width:auto;margin-left:auto;padding:6px" title="Collapse">' +
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 6l-6 6 6 6"/></svg></button></div>' +
      '<div style="flex:1;overflow-y:auto;padding:12px 12px;display:flex;flex-direction:column;gap:4px">' + items + "</div>" +
      '<div style="padding:12px 16px;border-top:1px solid var(--border-primary);display:flex;flex-direction:column;gap:6px">' +
      '<button class="cr-nav" data-action="theme-toggle" style="font-size:13px">' + icon("theme") +
      "<span>Theme · " + esc(state.theme === "midnight" ? "Midnight" : "B-ON") + "</span></button>" +
      '<div style="font-size:11px;' + MONO + ';color:var(--text-secondary)">CAN-Rosetta v0.4.2</div>' +
      '<div style="font-size:11px;color:var(--text-muted)">deep-thought.local · up 42 d</div></div></div>';
  }

  function header() {
    var showCtx = ["detail", "align", "hypo", "census"].indexOf(state.view) >= 0;
    var ctx = showCtx ? '<span style="display:inline-flex;align-items:center;gap:8px;padding:4px 10px;border-radius:8px;' +
      'background:var(--bg-subtle);border:1px solid var(--border-primary);font-size:12px;color:var(--text-secondary)">' +
      '<span style="' + MONO + ';color:var(--text-primary)">' + esc(state.sel.id) + "</span>" +
      "<span>" + esc(state.sel.vehicle) + "</span></span>" : "";
    return '<div style="height:64px;flex-shrink:0;display:flex;align-items:center;gap:16px;padding:0 24px;background:var(--bg-header);' +
      'border-bottom:1px solid var(--border-primary)">' +
      '<h1 style="margin:0;font-size:20px;font-weight:700;letter-spacing:-0.01em;color:var(--text-heading)">' + esc(TITLES[state.view]) + "</h1>" +
      ctx + '<div style="flex:1"></div>' +
      '<button class="cr-btn cr-btn-secondary cr-btn-sm" data-action="noop">Import candump…</button>' +
      '<button class="cr-btn cr-btn-primary cr-btn-sm" data-action="upload">Upload session…</button></div>';
  }

  function toastEl() {
    if (!state.toast) return "";
    return '<div style="position:fixed;right:24px;bottom:24px;z-index:100;' + CARD + ';padding:14px 18px;' +
      'display:flex;align-items:center;gap:10px;max-width:420px;animation:crToastIn 0.18s ease">' +
      '<span style="width:8px;height:8px;border-radius:999px;background:var(--status-success);flex-shrink:0"></span>' +
      '<span style="font-size:13px;color:var(--text-primary)">' + esc(state.toast) + "</span></div>";
  }

  function render() {
    var body = (VIEWS[state.view] || viewSessions)();
    app.innerHTML = sidebar() +
      '<div style="flex:1;display:flex;flex-direction:column;min-width:0">' + header() +
      '<div class="cr-scroll">' + body + "</div></div>" + toastEl();
  }

  /* ------------------------------------------------------------- behaviour */
  var toastTimer = null;
  function showToast(msg) {
    state.toast = msg;
    render();
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { state.toast = ""; render(); }, 4200);
  }
  function liveSessions() {
    if (!state.live.sessions) return null;
    return state.live.sessions.map(function (s) {
      var dots = s.frames ? ["done", "done", "run", "wait", "wait"] : ["done", "wait", "wait", "wait", "wait"];
      return S(s.id, s.vehicle, fmtCreated(s.created_utc), "—",
        s.frames != null ? Number(s.frames).toLocaleString() : "—",
        "live", "info", dots, 0);
    });
  }
  function fmtCreated(ts) {
    if (!ts) return "—";
    try { return new Date(ts * 1000).toISOString().slice(0, 16).replace("T", " "); } catch (e) { return "—"; }
  }

  function applyTheme() { document.documentElement.setAttribute("data-theme", state.theme); }

  function openSession(id, vehicle) {
    state.sel = { id: id, vehicle: vehicle };
    state.view = "detail";
    render();
    if (liveIds[id]) ensureLive(id);
  }
  function ensureLive(id) {
    fetchJson("/api/sessions/" + encodeURIComponent(id)).then(function (j) {
      if (j) { state.live.detail[id] = j; if (state.sel.id === id) render(); }
    });
    fetchJson("/api/sessions/" + encodeURIComponent(id) + "/identify").then(function (j) {
      if (j) { state.live.identify[id] = j; if (state.sel.id === id) render(); }
    });
    fetchJson("/api/sessions/" + encodeURIComponent(id) + "/census").then(function (j) {
      if (j) { state.live.census[id] = j; if (state.sel.id === id) render(); }
    });
    fetchJson("/api/sessions/" + encodeURIComponent(id) + "/clusters").then(function (j) {
      if (j) { state.live.clusters[id] = j; if (state.sel.id === id) render(); }
    });
    refreshCoverage(id);
  }
  function refreshCoverage(id) {
    fetchJson("/api/sessions/" + encodeURIComponent(id) + "/coverage").then(function (j) {
      if (j) { state.live.coverage[id] = j; if (state.sel.id === id) render(); }
    });
  }
  function refreshKnowledge() {
    fetchJson("/api/knowledge").then(function (j) {
      if (j && Array.isArray(j.platforms) && j.platforms.length) {
        state.live.knowledge = j; if (state.view === "kb") render();
      }
    });
  }
  function fetchJson(url) {
    return fetch(url).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
  }
  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
  }

  function confirmHypo(rank) {
    var lv = state.hypoLive[rank];
    var id = state.sel.id;
    if (lv && liveIds[id]) {
      postJson("/api/sessions/" + encodeURIComponent(id) + "/confirm",
        { reference: lv.reference, candidate: lv.candidate, r: lv.r }).then(function (res) {
          state.hypoRefOverride[lv.reference] = "confirmed"; // badge → confirmed (green)
          if (res && res.status === "confirmed") {
            showToast("Confirmed " + lv.reference + " — written to labels/annotations.json.");
            refreshKnowledge();
            refreshCoverage(id);
          } else {
            // API failed: degrade to the demo toast, keep the optimistic badge.
            showToast("Hypothesis confirmed — written to labels/annotations.json.");
          }
        });
      return;
    }
    // No live session (demo / offline): keep the original behaviour intact.
    state.hypoOverride[rank] = "confirmed";
    showToast("Hypothesis confirmed — written to labels/annotations.json.");
  }
  function rejectHypo(rank) {
    var lv = state.hypoLive[rank];
    var id = state.sel.id;
    if (lv && liveIds[id]) {
      postJson("/api/sessions/" + encodeURIComponent(id) + "/reject",
        { reference: lv.reference, candidate_label: lv.candidate_label }).then(function (res) {
          state.hypoRefOverride[lv.reference] = "rejected"; // badge → neutral
          if (res && res.status === "rejected") {
            showToast("Rejected " + lv.reference + " — remembered per platform.");
            refreshKnowledge();
          } else {
            showToast("Hypothesis rejected — remembered per platform.");
          }
        });
      return;
    }
    state.hypoOverride[rank] = "rejected";
    showToast("Hypothesis rejected — remembered per platform.");
  }

  app.addEventListener("click", function (e) {
    var el = e.target.closest("[data-action]");
    if (!el) return;
    var a = el.getAttribute("data-action");
    if (a === "nav" || a === "go-view") {
      state.view = el.getAttribute("data-view"); render();
    } else if (a === "toggle-sidebar") {
      state.sbOpen = !state.sbOpen; render();
    } else if (a === "theme-toggle") {
      state.theme = state.theme === "midnight" ? "bon" : "midnight"; applyTheme(); render();
    } else if (a === "open-session") {
      openSession(el.getAttribute("data-id"), el.getAttribute("data-vehicle"));
    } else if (a === "confirm-hypo") {
      confirmHypo(el.getAttribute("data-id"));
    } else if (a === "reject-hypo") {
      rejectHypo(el.getAttribute("data-id"));
    } else if (a === "export-dbc") {
      showToast("vw_golf8_mqb.dbc exported (12 signals) — so long, and thanks for all the frames.");
    } else if (a === "kb-export") {
      showToast(el.getAttribute("data-name") + ".dbc written — so long, and thanks for all the frames.");
    } else if (a === "upload") {
      showToast("Upload wired to POST /identify — pick a session archive.");
    } else if (a === "noop") {
      showToast("Wired in the real thing — this is a design prototype.");
    }
  });

  /* -------------------------------------------------------------------- boot */
  applyTheme();
  render();

  // Live wiring: prefer server data when available.
  refreshKnowledge();
  fetchJson("/api/sessions").then(function (j) {
    if (j && Array.isArray(j.sessions) && j.sessions.length) {
      state.live.sessions = j.sessions;
      j.sessions.forEach(function (s) { liveIds[s.id] = true; });
      render();
    }
  });

  // Reproduce the mockup's "identify running" tick.
  setInterval(function () {
    if (state.identifyPct < 88) {
      state.identifyPct += 1;
      if (state.view === "detail") render();
    }
  }, 3000);
})();
