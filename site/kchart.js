// ================================================================ K 線圖（TradingView Lightweight Charts）
// KC.mount(容器, {mkt, id, compact, ctx}) → 畫 K 線、均線、布林、成交量、KD、MACD、RSI、疊大盤、系統資訊、自己畫線
// ctx：{row: 篩選資料列, port: portfolio.json, earn: earnings.json, bt: backtest.json}
"use strict";
const KC = (() => {
  const LW = window.LightweightCharts;
  const KEY = "vs_kline_v1", DKEY = "vs_draw_v1";
  const DEF = {
    iv: "d", range: "1y",
    on: { ma: true, vol: true, bb: false, kd: false, macd: false, rsi: false, bench: false, sys: true },
    p: { ma: "5,20,60,120,240", bb: "20,2", kd: "9,3,3", macd: "12,26,9", rsi: "14" },
  };
  const LABEL = { ma: "均線", vol: "成交量", bb: "布林", kd: "KD", macd: "MACD", rsi: "RSI", bench: "比大盤", sys: "系統" };
  const RANGES = { d: [["3m", "3月", 63], ["6m", "6月", 126], ["1y", "1年", 252], ["3y", "3年", 756], ["all", "全部", 1e9]],
                   w: [["1y", "1年", 52], ["3y", "3年", 156], ["5y", "5年", 262], ["all", "全部", 1e9]],
                   m: [["3y", "3年", 36], ["5y", "5年", 60], ["10y", "10年", 120], ["all", "全部", 1e9]] };
  const cache = new Map();
  const ls = {
    get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* 無痕模式等 */ } },
  };
  const prefs = () => { const p = ls.get(KEY, {}); return { ...DEF, ...p, on: { ...DEF.on, ...(p.on || {}) }, p: { ...DEF.p, ...(p.p || {}) } }; };
  const savePrefs = (p) => ls.set(KEY, p);
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const nums = (s) => String(s).split(/[,\s，]+/).map(Number).filter((x) => isFinite(x) && x > 0);
  const e = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fx = (v, d = 2) => (v == null || !isFinite(v) ? "–" : Math.abs(v) >= 1000 ? v.toLocaleString("en-US", { maximumFractionDigits: 1 }) : v.toFixed(Math.abs(v) < 1 ? 4 : d));
  const fv = (v) => (v == null ? "–" : v >= 1e9 ? (v / 1e9).toFixed(2) + "B" : v >= 1e6 ? (v / 1e6).toFixed(2) + "M" : v >= 1e3 ? (v / 1e3).toFixed(1) + "K" : String(v));

  async function load(mkt, id) {
    const k = `${mkt}/${id}`;
    if (cache.has(k)) return cache.get(k);
    let doc = null;
    try { const r = await fetch(`data/kline/${mkt}/${encodeURIComponent(id)}.json`, { cache: "no-cache" }); if (r.ok) doc = await r.json(); } catch (err) { doc = null; }
    cache.set(k, doc);
    return doc;
  }
  function benchOf(mkt, id) {
    if (mkt === "tw" || String(id).endsWith(".TW") || String(id).endsWith(".TWO")) return id === "0050.TW" ? null : "0050.TW";
    return id === "SPY" ? null : "SPY";
  }

  // ---------------------------------------------------------- K 棒（日／週／月）
  function bars(doc, iv) {
    const d = doc.t.map((t, i) => ({ time: t * 86400, open: doc.o[i], high: doc.h[i], low: doc.l[i], close: doc.c[i], volume: doc.v[i] }))
      .filter((b) => b.close != null);
    if (iv === "d") return d;
    const key = (t) => {
      const dt = new Date(t * 1000);
      if (iv === "w") { const day = (dt.getUTCDay() + 6) % 7; return t - day * 86400; }      // 週一
      return Date.UTC(dt.getUTCFullYear(), dt.getUTCMonth(), 1) / 1000;
    };
    const out = [];
    const push = (b, k) => {
      const last = out[out.length - 1];
      if (last && last.time === k) { last.high = Math.max(last.high, b.high); last.low = Math.min(last.low, b.low); last.close = b.close; last.volume += b.volume || 0; }
      else out.push({ time: k, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume || 0 });
    };
    if (iv === "m" && doc.m) doc.m.t.forEach((t, i) => push({ open: doc.m.o[i], high: doc.m.h[i], low: doc.m.l[i], close: doc.m.c[i], volume: doc.m.v[i] }, key(t * 86400)));
    d.forEach((b) => push(b, key(b.time)));
    return out;
  }

  // ---------------------------------------------------------- 指標
  const sma = (a, n) => { const o = Array(a.length).fill(null); let s = 0; for (let i = 0; i < a.length; i++) { s += a[i]; if (i >= n) s -= a[i - n]; if (i >= n - 1) o[i] = s / n; } return o; };
  const ema = (a, n) => { const o = Array(a.length).fill(null), k = 2 / (n + 1); let p = null; for (let i = 0; i < a.length; i++) { p = p == null ? a[i] : a[i] * k + p * (1 - k); if (i >= n - 1) o[i] = p; } return o; };
  function boll(c, n, k) {
    const m = sma(c, n), up = Array(c.length).fill(null), dn = Array(c.length).fill(null);
    for (let i = n - 1; i < c.length; i++) { let s = 0; for (let j = i - n + 1; j <= i; j++) s += (c[j] - m[i]) ** 2; const sd = Math.sqrt(s / n); up[i] = m[i] + k * sd; dn[i] = m[i] - k * sd; }
    return { m, up, dn };
  }
  function kd(b, n, a, d) {      // 台灣常用 KD：RSV 9 日，K、D 各用 1/3 平滑
    const K = Array(b.length).fill(null), D = Array(b.length).fill(null); let k = 50, dd = 50;
    for (let i = 0; i < b.length; i++) {
      if (i < n - 1) continue;
      let hi = -Infinity, lo = Infinity; for (let j = i - n + 1; j <= i; j++) { hi = Math.max(hi, b[j].high); lo = Math.min(lo, b[j].low); }
      const rsv = hi === lo ? 50 : (100 * (b[i].close - lo)) / (hi - lo);
      k = ((a - 1) * k + rsv) / a; dd = ((d - 1) * dd + k) / d; K[i] = k; D[i] = dd;
    }
    return { K, D };
  }
  function macd(c, f, s, g) {
    const ef = ema(c, f), es = ema(c, s), dif = c.map((_, i) => (ef[i] == null || es[i] == null ? null : ef[i] - es[i]));
    const start = dif.findIndex((x) => x != null), sig = Array(c.length).fill(null);
    if (start >= 0) { const e2 = ema(dif.slice(start), g); e2.forEach((v, i) => (sig[start + i] = v)); }
    return { dif, sig, osc: dif.map((x, i) => (x == null || sig[i] == null ? null : x - sig[i])) };
  }
  function rsi(c, n) {
    const o = Array(c.length).fill(null); let g = 0, l = 0;
    for (let i = 1; i < c.length; i++) {
      const ch = c[i] - c[i - 1], up = Math.max(ch, 0), dn = Math.max(-ch, 0);
      if (i <= n) { g += up / n; l += dn / n; if (i === n) o[i] = l === 0 ? 100 : 100 - 100 / (1 + g / l); }
      else { g = (g * (n - 1) + up) / n; l = (l * (n - 1) + dn) / n; o[i] = l === 0 ? 100 : 100 - 100 / (1 + g / l); }
    }
    return o;
  }
  const ser = (b, a) => b.map((x, i) => (a[i] == null || !isFinite(a[i]) ? { time: x.time } : { time: x.time, value: a[i] }));

  // ---------------------------------------------------------- 畫面
  function mount(el, opt) {
    const { mkt, id, compact, ctx = {} } = opt;
    let P = prefs(), chart = null, doc = null, bdoc = null, B = [], drawMode = null, pending = null, ro = null, cur = null, onPick = null;
    const dkey = `${mkt}:${id}`;
    el.innerHTML = `<div class="kc ${compact ? "compact" : ""}">
      <div class="kc-bar"><div class="seg kc-iv">${[["d", "日"], ["w", "週"], ["m", "月"]].map(([k, l]) => `<button data-iv="${k}">${l}</button>`).join("")}</div>
        <div class="kc-range"></div></div>
      <div class="kc-chips">${Object.keys(LABEL).map((k) => `<button class="chip" data-on="${k}">${LABEL[k]}</button>`).join("")}<button class="chip" data-act="draw">✏️ 畫線</button><button class="chip" data-act="set">⚙️</button></div>
      <div class="kc-draw" hidden><button class="chip" data-d="trend">趨勢線</button><button class="chip" data-d="h">水平線</button><button class="chip" data-d="undo">刪除上一條</button><button class="chip" data-d="clear">全部清除</button><button class="chip on" data-d="done">完成</button><span class="kc-tip"></span></div>
      <div class="kc-set" hidden></div>
      <div class="kc-legend"></div>
      <div class="kc-chart"></div>
      <div class="kc-foot"></div></div>`;
    const $ = (s) => el.querySelector(s);
    const box = $(".kc-chart");
    // 畫線：自己判斷點擊（圖表本身會把太快的第二下當成連點而忽略）；拖曳超過 6px 視為平移，不算點
    let down = null;
    box.addEventListener("pointerdown", (ev) => { down = { x: ev.clientX, y: ev.clientY }; });
    box.addEventListener("pointerup", (ev) => {
      if (!drawMode || !down || !chart || !onPick) return;
      const moved = Math.hypot(ev.clientX - down.x, ev.clientY - down.y); down = null;
      if (moved > 6) return;
      const r = box.getBoundingClientRect(), x = ev.clientX - r.left, y = ev.clientY - r.top;
      const t = chart.timeScale().coordinateToTime(x);
      if (t == null) return;
      onPick({ point: { x, y }, time: snap(typeof t === "number" ? t : Date.UTC(t.year, t.month - 1, t.day) / 1000) });
    });

    function chips() {
      el.querySelectorAll(".kc-iv button").forEach((b) => b.classList.toggle("on", b.dataset.iv === P.iv));
      el.querySelectorAll("[data-on]").forEach((b) => b.classList.toggle("on", !!P.on[b.dataset.on]));
      el.querySelector('[data-act="draw"]').classList.toggle("on", !!drawMode || !$(".kc-draw").hidden);
      const rs = RANGES[P.iv]; if (!rs.find((r) => r[0] === P.range)) P.range = rs[Math.min(2, rs.length - 1)][0];
      $(".kc-range").innerHTML = rs.map(([k, l]) => `<button class="chip ${k === P.range ? "on" : ""}" data-r="${k}">${l}</button>`).join("");
      $(".kc-range").querySelectorAll("[data-r]").forEach((b) => (b.onclick = () => { P.range = b.dataset.r; savePrefs(P); chips(); setRange(); }));
    }
    function setRange() {
      if (!chart || !B.length) return;
      const n = (RANGES[P.iv].find((r) => r[0] === P.range) || [0, 0, 1e9])[2];
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, B.length - n), to: B.length + (compact ? 1 : 3) });
    }
    function settings() {
      const s = $(".kc-set");
      s.innerHTML = `<div class="kc-grid">${[["ma", "均線天數（逗號分隔）"], ["bb", "布林：天數, 標準差倍數"], ["kd", "KD：天數, K 平滑, D 平滑"], ["macd", "MACD：快線, 慢線, 訊號"], ["rsi", "RSI 天數"]]
        .map(([k, l]) => `<label>${l}<input data-p="${k}" value="${e(P.p[k])}"></label>`).join("")}</div>
        <div><button class="chip" data-p-reset>恢復預設</button> <button class="chip on" data-p-done>完成</button></div>`;
      s.querySelectorAll("input[data-p]").forEach((i) => (i.onchange = () => { P.p[i.dataset.p] = i.value; savePrefs(P); draw(); }));
      s.querySelector("[data-p-reset]").onclick = () => { P.p = { ...DEF.p }; savePrefs(P); settings(); draw(); };
      s.querySelector("[data-p-done]").onclick = () => { s.hidden = true; };
    }

    // ---- 自己畫的線（存在這台裝置）
    const drawings = () => (ls.get(DKEY, {})[dkey] || []);
    const saveDrawings = (a) => { const all = ls.get(DKEY, {}); all[dkey] = a; ls.set(DKEY, all); };
    function snap(t) {        // 日期對到目前 K 棒
      let lo = 0, hi = B.length - 1; if (!B.length) return t;
      if (t <= B[0].time) return B[0].time; if (t >= B[hi].time) return B[hi].time;
      while (lo < hi) { const m = (lo + hi + 1) >> 1; if (B[m].time <= t) lo = m; else hi = m - 1; }
      return B[lo].time;
    }

    function draw() {
      if (!doc) return;
      const keepRange = chart ? chart.timeScale().getVisibleLogicalRange() : null;
      const keepIv = draw._iv;
      if (ro) { ro.disconnect(); ro = null; }
      if (chart) { chart.remove(); chart = null; }
      B = bars(doc, P.iv);
      if (!B.length) { box.innerHTML = `<p class="hint">沒有資料</p>`; return; }
      const C = B.map((b) => b.close);
      const up = css("--up") || "#16a34a", dn = css("--down") || "#dc2626", txt = css("--text-2"), grid = css("--grid"), bg = css("--surface");
      const ma = [css("--ma1"), css("--ma2"), css("--ma3"), css("--ma4"), css("--ma5")];
      chart = LW.createChart(box, {
        autoSize: true,
        layout: { background: { type: "solid", color: bg }, textColor: txt, fontFamily: css("--font") || undefined, fontSize: compact ? 10 : 11,
                  panes: { separatorColor: grid, separatorHoverColor: grid, enableResize: !compact }, attributionLogo: true },
        grid: { vertLines: { color: grid }, horzLines: { color: grid } },
        rightPriceScale: { borderColor: grid, scaleMargins: { top: 0.08, bottom: 0.06 } },
        timeScale: { borderColor: grid, rightOffset: 3, barSpacing: 6, minBarSpacing: 0.5 },
        crosshair: { mode: LW.CrosshairMode.Normal },
        localization: { locale: "zh-TW", dateFormat: "yyyy/MM/dd" },
        handleScale: { axisPressedMouseMove: { time: true, price: true } },
      });
      const main = chart.addSeries(LW.CandlestickSeries, { upColor: up, downColor: dn, borderUpColor: up, borderDownColor: dn, wickUpColor: up, wickDownColor: dn, priceLineVisible: true });
      main.setData(B.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
      const extra = {};      // 十字線讀數用
      let pane = 0;
      if (P.on.ma) nums(P.p.ma).slice(0, 5).forEach((n, i) => {
        const a = sma(C, n); extra[`MA${n}`] = { a, c: ma[i] };
        chart.addSeries(LW.LineSeries, { color: ma[i], lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }).setData(ser(B, a));
      });
      if (P.on.bb) {
        const [n = 20, k = 2] = nums(P.p.bb), bb = boll(C, n, k), col = css("--accent");
        [["上軌", bb.up], ["中軌", bb.m], ["下軌", bb.dn]].forEach(([l, a], i) => {
          extra[`布林${l}`] = { a, c: col };
          chart.addSeries(LW.LineSeries, { color: col, lineWidth: 1, lineStyle: i === 1 ? LW.LineStyle.Dotted : LW.LineStyle.Solid, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }).setData(ser(B, a));
        });
      }
      // 疊大盤：以畫面最左邊那天為基準，把大盤換算成同一個起點
      let benchSeries = null, benchBars = null;
      if (P.on.bench && bdoc) {
        benchBars = bars(bdoc, P.iv);
        benchSeries = chart.addSeries(LW.LineSeries, { color: css("--muted"), lineWidth: 2, lineStyle: LW.LineStyle.Dashed, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, title: benchOf(mkt, id) === "SPY" ? "SPY" : "0050" });
      }
      if (P.on.vol) {
        pane++;
        const v = chart.addSeries(LW.HistogramSeries, { priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false }, pane);
        v.setData(B.map((b, i) => ({ time: b.time, value: b.volume || 0, color: (i && b.close < B[i - 1].close) || b.close < b.open ? dn + "99" : up + "99" })));
        extra["量"] = { a: B.map((b) => b.volume), c: txt, vol: true };
      }
      if (P.on.kd) {
        pane++;
        const [n = 9, a = 3, d = 3] = nums(P.p.kd), r = kd(B, n, a, d);
        extra.K = { a: r.K, c: ma[0] }; extra.D = { a: r.D, c: ma[1] };
        chart.addSeries(LW.LineSeries, { color: ma[0], lineWidth: 1, priceLineVisible: false, lastValueVisible: false }, pane).setData(ser(B, r.K));
        const dS = chart.addSeries(LW.LineSeries, { color: ma[1], lineWidth: 1, priceLineVisible: false, lastValueVisible: false }, pane); dS.setData(ser(B, r.D));
        [80, 20].forEach((y) => dS.createPriceLine({ price: y, color: grid, lineWidth: 1, lineStyle: LW.LineStyle.Dashed, axisLabelVisible: false }));
      }
      if (P.on.macd) {
        pane++;
        const [f = 12, s = 26, g = 9] = nums(P.p.macd), r = macd(C, f, s, g);
        extra.DIF = { a: r.dif, c: ma[0] }; extra.MACD = { a: r.sig, c: ma[1] }; extra.OSC = { a: r.osc, c: txt };
        chart.addSeries(LW.HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, pane)
          .setData(B.map((b, i) => (r.osc[i] == null ? { time: b.time } : { time: b.time, value: r.osc[i], color: r.osc[i] >= 0 ? up + "aa" : dn + "aa" })));
        chart.addSeries(LW.LineSeries, { color: ma[0], lineWidth: 1, priceLineVisible: false, lastValueVisible: false }, pane).setData(ser(B, r.dif));
        chart.addSeries(LW.LineSeries, { color: ma[1], lineWidth: 1, priceLineVisible: false, lastValueVisible: false }, pane).setData(ser(B, r.sig));
      }
      if (P.on.rsi) {
        pane++;
        const [n = 14] = nums(P.p.rsi), r = rsi(C, n);
        extra[`RSI${n}`] = { a: r, c: ma[2] };
        const s = chart.addSeries(LW.LineSeries, { color: ma[2], lineWidth: 1, priceLineVisible: false, lastValueVisible: false }, pane); s.setData(ser(B, r));
        [70, 30].forEach((y) => s.createPriceLine({ price: y, color: grid, lineWidth: 1, lineStyle: LW.LineStyle.Dashed, axisLabelVisible: false }));
      }
      const panes = chart.panes();
      panes.forEach((p, i) => p.setStretchFactor(i === 0 ? (compact ? 3 : 4) : 1));

      // ---- 系統資訊：合理價、買進價與停損線、財報日、回測買賣點
      const marks = [], foot = [];
      if (P.on.sys) {
        const row = ctx.row || {};
        const pl = (price, title, color, style = LW.LineStyle.Dashed) => price != null && isFinite(price) && main.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title });
        if (row.fair_base != null) {
          pl(row.fair_base, "合理價", css("--accent"));
          pl(row.fair_low, "合理低", css("--muted"), LW.LineStyle.Dotted); pl(row.fair_high, "合理高", css("--muted"), LW.LineStyle.Dotted);
          foot.push("合理價是今天的估值，不是過去每天的");
        }
        const h = ((ctx.port || {}).holdings || []).find((x) => x.id === id && x.kind === "stock");
        if (h && h.entry_date) {
          const t0 = Date.parse(h.entry_date) / 1000, bi = B.findIndex((b) => b.time >= snap(t0));
          if (bi >= 0) {
            const entry = B[bi].close, sl = ((ctx.port || {}).stop_loss || {}).pct || 0.15;
            pl(entry, "買進價", txt); pl(entry * (1 - sl), `停損 −${Math.round(sl * 100)}%`, dn);
            marks.push({ time: B[bi].time, position: "belowBar", color: up, shape: "arrowUp", text: "系統買" });
          }
        }
        const er = ((ctx.earn || {}).recent || []).filter((x) => x.id === id && x.market === mkt && x.type === "earnings");
        er.forEach((x) => { const t = snap(Date.parse(x.date) / 1000); marks.push({ time: t, position: "aboveBar", color: css("--accent"), shape: "circle", text: "財報" }); });
        const tr = ((ctx.bt || {}).trades || []).filter((x) => x.id === id && x.market === mkt);
        tr.forEach((x) => {
          const s0 = Date.parse(x.start + "-01") / 1000 + 31 * 86400; const t1 = snap(Date.UTC(new Date(s0 * 1000).getUTCFullYear(), new Date(s0 * 1000).getUTCMonth(), 0) / 1000);
          if (t1 >= B[0].time) marks.push({ time: t1, position: "belowBar", color: up, shape: "arrowUp", text: "回測買" });
          const t2 = snap(Date.parse(x.end) / 1000);
          if (t2 >= B[0].time && t2 < B[B.length - 1].time) marks.push({ time: t2, position: "aboveBar", color: dn, shape: "arrowDown", text: x.reason === "停損" ? "停損" : "回測賣" });
        });
        if (tr.length) foot.push(`回測（目前版）持有過 ${tr.length} 次`);
      }
      // 自己畫的線
      drawings().forEach((d) => {
        if (d.type === "h") main.createPriceLine({ price: d.p, color: css("--warn"), lineWidth: 1, lineStyle: LW.LineStyle.Solid, axisLabelVisible: true, title: "" });
        else if (d.type === "trend" && !P.on.bench) {     // 比大盤（% 座標）時趨勢線會變形，先不畫
          const t1 = snap(d.t1), t2 = snap(d.t2); if (t1 === t2) return;
          const slope = (d.p2 - d.p1) / (d.t2 - d.t1), tEnd = B[B.length - 1].time;
          const pts = [{ time: t1, value: d.p1 + slope * (t1 - d.t1) }, { time: t2, value: d.p1 + slope * (t2 - d.t1) }];
          if (tEnd > t2) pts.push({ time: tEnd, value: d.p1 + slope * (tEnd - d.t1) });
          chart.addSeries(LW.LineSeries, { color: css("--warn"), lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null }).setData(pts);
        }
      });
      if (marks.length) {
        marks.sort((a, b) => a.time - b.time);
        LW.createSeriesMarkers(main, marks);
      }
      if (P.on.bench && drawings().some((d) => d.type === "trend")) foot.push("比大盤時座標改成漲跌 %，自己畫的趨勢線先隱藏");
      $(".kc-foot").textContent = foot.join("；");

      // ---- 十字線讀數
      const idx = new Map(B.map((b, i) => [b.time, i]));
      const legend = (i) => {
        const b = B[i]; if (!b) return;
        const prev = i ? B[i - 1].close : b.open, ch = b.close / prev - 1, col = ch >= 0 ? up : dn;
        const dt = new Date(b.time * 1000), ds = `${dt.getUTCFullYear()}/${String(dt.getUTCMonth() + 1).padStart(2, "0")}/${String(dt.getUTCDate()).padStart(2, "0")}`;
        let html = `<span class="d">${ds}${P.iv === "w" ? " 週" : P.iv === "m" ? " 月" : ""}</span> 開 <b>${fx(b.open)}</b> 高 <b>${fx(b.high)}</b> 低 <b>${fx(b.low)}</b> 收 <b style="color:${col}">${fx(b.close)}</b> <span style="color:${col}">${ch >= 0 ? "+" : ""}${(ch * 100).toFixed(2)}%</span>`;
        html += Object.entries(extra).map(([k, x]) => `<span><i style="background:${x.c}"></i>${k} ${x.vol ? fv(x.a[i]) : fx(x.a[i])}</span>`).join("");
        if (benchSeries && benchBars && benchBars.length) html += benchInfo();
        $(".kc-legend").innerHTML = html;
      };
      // 疊大盤：依畫面範圍重新換算
      let benchStart = null;
      const bIdx = benchBars ? new Map(benchBars.map((b, i) => [b.time, i])) : null;
      // 比大盤：右邊座標改成「畫面最左邊那天起算的漲跌 %」，個股和大盤從同一個起點比
      if (benchSeries) {
        chart.priceScale("right").applyOptions({ mode: LW.PriceScaleMode.Percentage });
        benchSeries.setData(benchBars.filter((b) => idx.has(b.time)).map((b) => ({ time: b.time, value: b.close })));
      }
      function rebase() {
        if (!benchSeries) return;
        const r = chart.timeScale().getVisibleLogicalRange(); if (!r) return;
        const i0 = Math.min(Math.max(0, Math.ceil(r.from)), B.length - 1), t0 = B[i0].time;
        let j0 = benchBars.findIndex((b) => b.time >= t0); if (j0 < 0) j0 = 0;
        benchStart = { i0, j0 };
      }
      function benchInfo() {
        if (!benchStart) return "";
        const i1 = B.length - 1, j1 = benchBars.length - 1;
        const s = B[i1].close / B[benchStart.i0].close - 1, m = benchBars[j1].close / benchBars[benchStart.j0].close - 1;
        return `<span>畫面期間 個股 ${(s * 100).toFixed(1)}%／${benchOf(mkt, id) === "SPY" ? "SPY" : "0050"} ${(m * 100).toFixed(1)}%</span>`;
      }
      let raf = 0;
      chart.timeScale().subscribeVisibleLogicalRangeChange(() => { if (benchSeries) { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => { rebase(); legend(B.length - 1); }); } });
      chart.subscribeCrosshairMove((p) => legend(p && p.time != null && idx.has(p.time) ? idx.get(p.time) : B.length - 1));
      cur = { main };
      onPick = (p) => {
        if (!drawMode || !p || !p.point || p.time == null) return;
        const price = main.coordinateToPrice(p.point.y); if (price == null) return;
        if (drawMode === "h") { const a = drawings(); a.push({ type: "h", p: price }); saveDrawings(a); drawMode = null; tip(""); draw(); return; }
        if (!pending) { pending = { t: p.time, p: price }; tip("再點第二個點"); return; }
        if (p.time === pending.t) return;
        const [a1, a2] = p.time > pending.t ? [pending, { t: p.time, p: price }] : [{ t: p.time, p: price }, pending];
        const a = drawings(); a.push({ type: "trend", t1: a1.t, p1: a1.p, t2: a2.t, p2: a2.p }); saveDrawings(a);
        pending = null; drawMode = null; tip(""); draw();
      };

      if (keepRange && keepIv === P.iv) chart.timeScale().setVisibleLogicalRange(keepRange); else setRange();
      draw._iv = P.iv;
      rebase();
      legend(B.length - 1);
      chips();
    }
    const tip = (s) => { $(".kc-tip").textContent = s; };

    el.querySelectorAll(".kc-iv button").forEach((b) => (b.onclick = () => { P.iv = b.dataset.iv; savePrefs(P); chips(); draw(); }));
    el.querySelectorAll("[data-on]").forEach((b) => (b.onclick = async () => {
      const k = b.dataset.on; P.on[k] = !P.on[k]; savePrefs(P);
      if (k === "bench" && P.on.bench && !bdoc) { const bt = benchOf(mkt, id); bdoc = bt ? await load("etf", bt) : null; }
      draw();
    }));
    el.querySelector('[data-act="draw"]').onclick = () => { const d = $(".kc-draw"); d.hidden = !d.hidden; drawMode = null; pending = null; tip(d.hidden ? "" : "選「趨勢線」後在圖上點兩個點；「水平線」點一下"); chips(); };
    el.querySelector('[data-act="set"]').onclick = () => { const s = $(".kc-set"); s.hidden = !s.hidden; if (!s.hidden) settings(); };
    el.querySelectorAll("[data-d]").forEach((b) => (b.onclick = () => {
      const k = b.dataset.d;
      if (k === "trend") { drawMode = "trend"; pending = null; tip("在圖上點第一個點"); }
      else if (k === "h") { drawMode = "h"; tip("在圖上點一下要畫水平線的位置"); }
      else if (k === "undo") { const a = drawings(); a.pop(); saveDrawings(a); draw(); }
      else if (k === "clear") { saveDrawings([]); draw(); }
      else { $(".kc-draw").hidden = true; drawMode = null; pending = null; tip(""); chips(); }
    }));

    (async () => {
      $(".kc-legend").innerHTML = `<span class="d">載入中…</span>`;
      doc = await load(mkt, id);
      if (!doc) { box.innerHTML = `<p class="hint" style="padding:24px 4px">這檔還沒有 K 線資料（第一次會分幾天下載完，之後每天更新）。</p>`; $(".kc-legend").innerHTML = ""; chips(); return; }
      if (P.on.bench) { const bt = benchOf(mkt, id); bdoc = bt ? await load("etf", bt) : null; }
      chips();
      draw();
    })();
    return {
      redraw: () => draw(),
      destroy: () => { if (chart) chart.remove(); chart = null; },
      last: () => (doc && doc.c.length ? { close: doc.c[doc.c.length - 1], prev: doc.c[doc.c.length - 2], t: doc.t[doc.t.length - 1] } : null),
    };
  }
  return { mount, load, bars, sma, ema, kd, macd, rsi, boll, benchOf };
})();
