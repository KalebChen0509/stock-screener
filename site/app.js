/* 價值選股系統 前端（純 JS，資料來自 data/*.json） */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const S = { mkt: "tw", wlMkt: "tw", secMkt: "tw", sort: { key: "score", dir: -1 }, charts: {}, btPeriod: "full" };

const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* ignore */ } },
};

// ---------------------------------------------------------------- 指標定義
const METRICS = {
  cash_ratio: ["現金/總資產", "pct"], intangible_ratio: ["商譽+無形/總資產", "pct"],
  lt_invest_ratio: ["長期投資/總資產", "pct"], inventory_ratio: ["存貨/總資產", "pct"],
  equity_ratio: ["權益比率", "pct"], current_ratio: ["流動比率", "pct"], quick_ratio: ["速動比率", "pct"],
  gross_margin: ["毛利率", "pct"], op_margin: ["營益率", "pct"], op_to_gross: ["營益/毛利", "pct"],
  nonop_share: ["業外/稅前", "pct"], cfo: ["營業現金流", "money"], fcf: ["自由現金流", "money"],
  cff: ["籌資現金流", "money"], net_income: ["稅後淨利", "money"], revenue: ["營收", "money"],
  cfo_to_ni: ["營業CF/淨利", "pct"], fcf_to_ni: ["FCF/淨利", "pct"], roe: ["ROE", "pct"],
  pe: ["本益比", "x"], pb: ["股價淨值比", "x"], share_growth: ["股數年增", "pct"],
  market_cap: ["市值", "money"], avg_traded_value: ["日均成交值", "money"], n_quarters: ["財報季數", "int"],
  earnings_yield: ["EBIT/EV", "pct"], fcf_yield: ["FCF殖利率", "pct"], book_to_price: ["淨值/市值", "pct"],
  is_financial: ["金融業", "bool"], profitable_3y: ["近3年皆獲利", "bool"],
  cfo_positive_3y: ["近3年營業CF皆正", "bool"],
  gross_margin_min3y: ["毛利率（近3年最低）", "pct"], gross_margin_avg3y: ["毛利率（近3年平均）", "pct"],
  op_margin_min3y: ["營益率（近3年最低）", "pct"], op_margin_avg3y: ["營益率（近3年平均）", "pct"],
  rd_to_revenue: ["研發費用率", "pct"], revenue_cagr_3y: ["營收3年年化成長", "pct"],
  mom_12m_pct: ["12月動能百分位", "pctile"], ret_12m: ["近12月報酬", "pct"], pledge_ratio: ["董監質押比", "pct"],
  event_veto: ["重大負面事件", "bool"], cf_type_ok: ["現金流類型甲/乙", "bool"], cf_type: ["現金流類型", "text"],
  expense_ratio: ["費用率", "pct2"], aum_usd: ["規模(USD)", "usd"], avg_traded_value_usd: ["日均成交值(USD)", "usd"],
  age_years: ["成立年數", "x"], top10_weight: ["前十大占比", "pct"], is_value: ["價值型指數", "bool"],
  tracking_vol: ["追蹤波動", "pct"], value_tilt: ["價值傾向", "pctile"], div_yield_ttm: ["近12月殖利率", "pct"],
};
const mlabel = (m) => (METRICS[m] ? METRICS[m][0] : m);
const GROUP_SHORT = { rd_heavy: "研發密集", cyclicals: "循環股", discretionary: "非必需消費", staples: "必需消費", healthcare: "醫療", utilities_telecom: "公用/電信" };
const groupTags = (r) => (r.groups || []).map((g) => `<span class="pill" title="${esc((S.cfg.groups[g] || {}).label || g)}">${GROUP_SHORT[g] || g}</span>`).join(" ");

const fmt = {
  pct: (v, d = 1) => (v == null || !isFinite(v) ? "–" : (v * 100).toFixed(d) + "%"),
  x: (v, d = 1) => (v == null ? "–" : !isFinite(v) || Math.abs(v) >= 1e11 ? "∞" : (+v).toFixed(d)),
  int: (v) => (v == null ? "–" : Math.round(v).toLocaleString()),
  money(v, mkt) {
    if (v == null || !isFinite(v)) return "–";
    if (mkt === "tw") return (v / 1e8).toLocaleString(undefined, { maximumFractionDigits: v / 1e8 >= 100 ? 0 : 1 }) + " 億";
    const a = Math.abs(v);
    if (a >= 1e9) return "$" + (v / 1e9).toFixed(1) + "B";
    if (a >= 1e6) return "$" + (v / 1e6).toFixed(0) + "M";
    return "$" + Math.round(v).toLocaleString();
  },
  usd(v) { return fmt.money(v, "us"); },
  ntd(v) { return v == null ? "–" : "NT$ " + Math.round(v).toLocaleString(); },
  price: (v) => (v == null ? "–" : v >= 100 ? v.toFixed(1) : v.toFixed(2)),
};
function fmtMetric(m, v, mkt) {
  const t = METRICS[m] ? METRICS[m][1] : "x";
  if (t === "pct") return fmt.pct(v);
  if (t === "pct2") return fmt.pct(v, 2);
  if (t === "pctile") return v == null ? "–" : Math.round(v * 100) + "";
  if (t === "money") return fmt.money(v, mkt);
  if (t === "usd") return fmt.usd(v);
  if (t === "int") return fmt.int(v);
  if (t === "bool") return v == null ? "–" : v ? "是" : "否";
  if (t === "text") return v == null ? "–" : esc(v);
  return fmt.x(v);
}
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

// ---------------------------------------------------------------- 載入
async function getJSON(p, opt = false) {
  try {
    const r = await fetch(p, { cache: "no-cache" });
    if (!r.ok) throw new Error(r.status);
    return await r.json();
  } catch (e) {
    if (opt) return null;
    throw e;
  }
}
const toObjs = (t) => (t ? t.rows.map((r) => Object.fromEntries(t.cols.map((c, i) => [c, r[i]]))) : []);

async function init() {
  initTheme();
  try {
    const [cfg, meta, tw, us, etf, port] = await Promise.all([
      getJSON("data/config.json"), getJSON("data/meta.json"),
      getJSON("data/stocks_tw.json", true), getJSON("data/stocks_us.json", true),
      getJSON("data/etf.json", true), getJSON("data/portfolio.json", true),
    ]);
    Object.assign(S, { cfg, meta, port, stocks: { tw: toObjs(tw), us: toObjs(us) }, etf: toObjs(etf) });
  } catch (e) {
    $("#updated").textContent = "資料尚未產生（等待第一次自動更新）";
    return;
  }
  S.bt = await getJSON("data/backtest.json", true);
  S.news = await getJSON("data/news.json", true);
  S.settings = store.get("vs_settings_v1", null) || defaultSettings();
  $("#updated").textContent = `資料更新：${S.meta.updated}（台北時間）　USD/TWD ${(+S.meta.fx_usdtwd).toFixed(2)}`;
  bindUI();
  route();
  window.addEventListener("hashchange", route);
}

function defaultSettings() {
  const f = {};
  for (const x of S.cfg.hard_filters) f[x.id] = { enabled: x.enabled !== false, value: x.value };
  const w = {};
  for (const grp of Object.values(S.cfg.ranking.bottom_up)) Object.assign(w, grp);
  w.top_down = S.cfg.ranking.top_down;
  return { filters: f, weights: w };
}

// ---------------------------------------------------------------- 路由
function route() {
  const v = (location.hash || "#overview").slice(1);
  $$(".view").forEach((el) => el.classList.toggle("on", el.id === "view-" + v));
  $$("#tabs a").forEach((a) => a.classList.toggle("on", a.getAttribute("href") === "#" + v));
  ({ overview: renderOverview, screener: renderScreener, etf: renderEtf, backtest: renderBacktest,
     watchlist: renderWatchlist, rules: renderRules }[v] || renderOverview)();
}

function initTheme() {
  const t = store.get("vs_theme", null);
  if (t) document.documentElement.dataset.theme = t;
  $("#themeBtn").onclick = () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    store.set("vs_theme", document.documentElement.dataset.theme);
    route();
  };
}

// ---------------------------------------------------------------- 篩選（瀏覽器端重算）
function thr(v, mkt) { return v && typeof v === "object" ? v[mkt] : v; }
const OPS = { ">=": (a, b) => a >= b, "<=": (a, b) => a <= b, ">": (a, b) => a > b, "<": (a, b) => a < b, "==": (a, b) => a === b };

function median(a) { const s = [...a].sort((x, y) => x - y); const n = s.length; return n ? (n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2) : NaN; }
function stdev(a) { const n = a.length; if (n < 2) return 0; const m = a.reduce((x, y) => x + y, 0) / n; return Math.sqrt(a.reduce((s, x) => s + (x - m) ** 2, 0) / (n - 1)); }
function pctRank(vals) {
  // 平均名次百分位；null → 0.5
  const idx = vals.map((v, i) => [v, i]).filter(([v]) => v != null && isFinite(v)).sort((a, b) => a[0] - b[0]);
  const out = vals.map(() => 0.5);
  const n = idx.length;
  let i = 0;
  while (i < n) {
    let j = i;
    while (j + 1 < n && idx[j + 1][0] === idx[i][0]) j++;
    const r = (i + j) / 2 + 1;
    for (let k = i; k <= j; k++) out[idx[k][1]] = r / n;
    i = j + 1;
  }
  return out;
}

function resolveRule(f, r, mkt) {
  // 預設門檻（可在網站調整）＋景氣循環但書覆寫（依群組，後者優先）
  const st = S.settings.filters[f.id] || { enabled: f.enabled !== false, value: f.value };
  let metric = f.metric, t = thr(st.value, mkt), enabled = !!st.enabled;
  if (S.settings.useOverrides !== false && r.groups && r.groups.length) {
    for (const [g, ov] of Object.entries(S.cfg.overrides || {})) {
      if (!r.groups.includes(g) || !ov[f.id]) continue;
      const o = ov[f.id];
      if ("metric" in o) metric = o.metric;
      if ("value" in o) t = thr(o.value, mkt);
      if ("enabled" in o) enabled = !!o.enabled && !!st.enabled;
    }
  }
  return { metric, t, enabled };
}

function runScreen(mkt) {
  const rows = S.stocks[mkt].map((r) => ({ ...r }));
  const fs = S.cfg.hard_filters.filter((f) => S.settings.filters[f.id]?.enabled);
  for (const f of fs) {
    if (f.op === "<=peer") {
      const k = f.peer_k_std ?? 1, minG = f.peer_min_group ?? 5;
      const by = {};
      for (const r of rows) if (r[f.metric] != null) (by[r.industry] ||= []).push(r[f.metric]);
      const all = rows.map((r) => r[f.metric]).filter((x) => x != null);
      const allLim = median(all) + k * stdev(all);
      const lim = {};
      for (const [g, a] of Object.entries(by)) lim[g] = a.length >= minG ? median(a) + k * stdev(a) : allLim;
      for (const r of rows) {
        const L = lim[r.industry] ?? allLim;
        r._peer = L;
        r["f_" + f.id] = r[f.metric] != null && r[f.metric] <= L;
      }
    } else {
      for (const r of rows) {
        const { metric, t, enabled } = resolveRule(f, r, mkt);
        r["_m_" + f.id] = metric; r["_t_" + f.id] = t;
        if (!enabled || t == null) { r["f_" + f.id] = true; r["_off_" + f.id] = !enabled; continue; }
        const x = r[metric];
        if (x == null) { r["f_" + f.id] = !!f.na_pass; continue; }
        r["f_" + f.id] = typeof t === "boolean" ? !!x === t : OPS[f.op](+x, t);
      }
    }
  }
  for (const r of rows) {
    r.failed = fs.filter((f) => !r["f_" + f.id]).map((f) => f.id);
    r.pass = r.failed.length === 0;
  }
  // 排名（通過者內部百分位）
  const passed = rows.filter((r) => r.pass);
  const W = S.settings.weights;
  const keys = Object.keys(W).filter((k) => k !== "top_down");
  const tot = keys.reduce((s, k) => s + (+W[k] || 0), 0) + (+W.top_down || 0);
  const pcts = {};
  for (const k of keys) pcts[k] = pctRank(passed.map((r) => r[k]));
  const bon = S.cfg.ranking.bonuses || {};
  passed.forEach((r, i) => {
    let s = 0;
    for (const k of keys) s += (+W[k] || 0) * pcts[k][i] * 100;
    s += (+W.top_down || 0) * (r.top_down_score ?? 50);
    r.bu = keys.length ? (s - (+W.top_down || 0) * (r.top_down_score ?? 50)) / Math.max(tot - (+W.top_down || 0), 1e-9) : 0;
    r.score = tot ? s / tot : 0;
    if (r.cf_type === "甲") r.score += bon.cf_type_a || 0;
    if (r.cfo_to_ni != null && r.cfo_to_ni >= 1) r.score += bon.cfo_to_ni_100 || 0;
  });
  const cc = S.cfg.top_down.cross_check;
  const buR = pctRank(passed.map((r) => -r.bu));
  passed.forEach((r, i) => { r.cross = buR[i] <= cc.bottom_up_top && (r.top_down_score ?? 0) >= 100 * (1 - cc.sector_top); });
  for (const r of rows) if (!r.pass) r.score = null;
  return rows;
}

// ---------------------------------------------------------------- 總覽
function renderOverview() {
  const m = S.meta, c = m.counts || {};
  const bt = S.bt && S.bt.stats_full ? S.bt.stats_full["策略（扣費後）"] : null;
  const tiles = [
    ["台股通過", c.tw ? c.tw.passed + " 檔" : "–", c.tw ? `資料 ${c.tw.with_data} / 清單 ${c.tw.universe}` : ""],
    ["美股通過", c.us ? c.us.passed + " 檔" : "–", c.us ? `資料 ${c.us.with_data} / 清單 ${c.us.universe}` : ""],
    ["ETF 通過", c.etf ? c.etf.passed + " 檔" : "–", c.etf ? `候選 ${c.etf.universe} 檔` : ""],
    ["回測年化（扣費後）", bt ? fmt.pct(bt.cagr) : "–", S.bt ? `${S.bt.start} ~ ${S.bt.end}` : "尚未回測"],
  ];
  if (m.tw_backfill && m.tw_backfill.have < m.tw_backfill.total)
    tiles.push(["台股財報回填中", `${Math.round((100 * m.tw_backfill.have) / m.tw_backfill.total)}%`, `${m.tw_backfill.have} / ${m.tw_backfill.total} 家`]);
  $("#ovTiles").innerHTML = tiles.map(([k, v, d]) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="d">${d}</div></div>`).join("");

  const H = (S.port && S.port.holdings) || [];
  const names = { tw: "台股", us: "美股", etf: "ETF" };
  let html = "";
  for (const sl of ["tw", "us", "etf"]) {
    const hs = H.filter((h) => h.sleeve === sl);
    const w = S.cfg.portfolio.weights[sl === "etf" ? "etf" : sl + "_stock"];
    html += `<div class="sleeve-h"><span>${names[sl]}　${fmt.pct(w, 0)}</span><span class="hint">${hs.length} 檔${hs.length ? "，每檔 " + fmt.pct(hs[0].weight, 2) : ""}</span></div>`;
    if (!hs.length) { html += `<p class="hint">目前沒有符合全部條件的標的 → 這部分持有現金</p>`; continue; }
    html += `<div class="table-wrap"><table class="data compact"><tbody>${hs.map((h) => `
      <tr class="clickable" data-m="${sl}" data-id="${esc(h.id)}"><td>${h.cross ? "⭐ " : ""}<b>${esc(h.id)}</b></td><td>${esc(h.name)}</td><td class="hint">${esc(h.industry || "")}</td><td class="num">${h.score != null ? h.score.toFixed(1) : ""}</td></tr>`).join("")}</tbody></table></div>`;
  }
  $("#ovPortfolio").innerHTML = html;
  $$("#ovPortfolio tr.clickable").forEach((tr) => (tr.onclick = () => openDetail(tr.dataset.m, tr.dataset.id)));

  const rg = m.regime || {};
  const rtxt = (k, label) => {
    const r = rg[k];
    if (!r || r.bull == null) return `<div>${label}：資料不足</div>`;
    return `<div>${label}：<b class="${r.bull ? "ok" : "ng"}">${r.bull ? "▲ 多頭（指數在 10 個月均線之上）" : "▼ 偏空（指數跌破 10 個月均線）"}</b> <span class="hint">指數 ${r.last.toFixed(0)}／均線 ${r.ma.toFixed(0)}</span></div>`;
  };
  $("#ovRegime").innerHTML = rtxt("tw", "台股加權") + rtxt("us", "美股 S&P 500") + `<p class="hint">產業分數 = 6 個月相對強弱 50% + 產業估值（中位數 EBIT/EV）50%，占個股綜合分數 ${fmt.pct(S.cfg.ranking.top_down, 0)}。</p>`;
  renderSectors();
  const N = S.news || { news: [], events: [] };
  const ev = (N.events || []).map((e) => `<li>${e.veto ? "⛔" : "⚠️"} <b>${esc(e.id)}</b> ${esc(e.name || "")}：${esc(e.text)}</li>`).join("");
  const nw = (N.news || []).slice().sort((a, b) => (b.negative - a.negative) || String(b.date).localeCompare(String(a.date))).slice(0, 40)
    .map((n) => `<li>${n.negative ? "⚠️ " : ""}<b>${esc(n.id)}</b> <span class="hint">${esc(n.date)}</span> ${n.url ? `<a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a>` : esc(n.title)}</li>`).join("");
  $("#ovNews").innerHTML = (ev ? `<h3>重大事件</h3><ul>${ev}</ul>` : "") + (nw ? `<h3>近 3 天新聞</h3><ul>${nw}</ul>` : "") || `<p class="hint">目前沒有資料</p>`;
}
function renderSectors() {
  const sec = (S.meta.sectors || {})[S.secMkt] || [];
  if (!sec.length) { $("#ovSectors").innerHTML = `<p class="hint">尚無資料</p>`; return; }
  const max = 100;
  $("#ovSectors").innerHTML = `<div class="table-wrap" style="max-height:360px;overflow:auto"><table class="data compact"><thead><tr><th>產業</th><th class="num">家數</th><th class="num">6月報酬</th><th class="num">EBIT/EV</th><th>分數</th></tr></thead><tbody>${sec.map((s) => `
    <tr><td>${esc(s.industry)}</td><td class="num">${s.n}</td><td class="num ${s.mom >= 0 ? "pos" : "neg"}">${fmt.pct(s.mom)}</td><td class="num">${fmt.pct(s.val)}</td>
    <td><span class="bar" style="width:${(70 * s.score) / max}px"></span> <span class="hint">${s.score.toFixed(0)}</span></td></tr>`).join("")}</tbody></table></div>`;
}

// ---------------------------------------------------------------- 個股篩選
const STOCK_COLS = [
  ["rank", "#", (r, i) => i + 1, "num"], ["id", "代號", (r) => `<b>${esc(r.id)}</b>`],
  ["name", "名稱", (r) => esc(r.name)], ["industry", "產業", (r) => `<span class="hint">${esc(r.industry)}</span> ${groupTags(r)}`],
  ["score", "綜合分數", (r) => (r.score == null ? "–" : (r.cross ? "⭐ " : "") + r.score.toFixed(1)), "num"],
  ["close", "收盤", (r) => fmt.price(r.close), "num"], ["market_cap", "市值", (r, i, m) => fmt.money(r.market_cap, m), "num"],
  ["pe", "本益比", (r) => fmt.x(r.pe), "num"], ["pb", "P/B", (r) => fmt.x(r.pb, 2), "num"],
  ["earnings_yield", "EBIT/EV", (r) => fmt.pct(r.earnings_yield), "num"], ["fcf_yield", "FCF殖利率", (r) => fmt.pct(r.fcf_yield), "num"],
  ["cash_ratio", "現金/資產", (r) => fmt.pct(r.cash_ratio), "num"], ["equity_ratio", "權益比", (r) => fmt.pct(r.equity_ratio), "num"],
  ["op_margin", "營益率", (r) => fmt.pct(r.op_margin), "num"], ["fcf_to_ni", "FCF/淨利", (r) => fmt.pct(r.fcf_to_ni, 0), "num"],
  ["roe", "ROE", (r) => fmt.pct(r.roe), "num"], ["cf_type", "現金流", (r) => esc(r.cf_type || "–")], ["top_down_score", "產業分數", (r) => (r.top_down_score == null ? "–" : r.top_down_score.toFixed(0)), "num"],
  ["failed", "狀態", (r) => (r.pass ? `<span class="ok">✔ 通過</span>` : `<span class="ng" title="${esc(r.failed.map(fname).join("、"))}">✘ ${r.failed.length} 條</span> <span class="hint">${esc(r.failed.slice(0, 2).map(fname).join("、"))}${r.failed.length > 2 ? "…" : ""}</span>`)],
];
function fname(id) { const f = S.cfg.hard_filters.find((x) => x.id === id) || (S.cfg.etf.hard_filters || []).find((x) => x.id === id); return f ? f.label.replace(/^[\d]+[ab]?\.\s*/, "").split("：")[0] : id; }

function renderScreener() {
  buildFilterPanel();
  const rows = runScreen(S.mkt);
  S.lastRows = rows;
  const q = $("#search").value.trim().toLowerCase();
  const near = $("#showNear").checked, all = $("#showAll").checked;
  let list = rows.filter((r) => all || r.pass || (near && r.failed.length <= 2));
  if (q) list = list.filter((r) => `${r.id} ${r.name} ${r.industry}`.toLowerCase().includes(q));
  const { key, dir } = S.sort;
  list.sort((a, b) => {
    if (key === "failed") return dir * (a.failed.length - b.failed.length);
    let x = a[key], y = b[key];
    if (key === "score") { x = x ?? -1e9; y = y ?? -1e9; if (x === y) return a.failed.length - b.failed.length; }
    if (x == null) return 1; if (y == null) return -1;
    return typeof x === "string" ? dir * x.localeCompare(y) : dir * (x - y);
  });
  const passN = rows.filter((r) => r.pass).length;
  $("#screenSummary").textContent = `${S.mkt === "tw" ? "台股" : "美股"} ${rows.length} 檔有完整資料，${passN} 檔通過全部條件；部位取排名前 ${S.cfg.portfolio.max_holdings_per_stock_sleeve} 檔。點任一列看詳情。`;
  const shown = list.slice(0, 600);
  $("#stockTable").innerHTML = `<thead><tr>${STOCK_COLS.map(([k, l, , c]) => `<th data-k="${k}" class="${c || ""}">${l}${S.sort.key === k ? (S.sort.dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr></thead>
  <tbody>${shown.map((r, i) => `<tr class="clickable ${r.pass ? "" : "dim"}" data-id="${esc(r.id)}">${STOCK_COLS.map(([, , f, c]) => `<td class="${c || ""}">${f(r, i, S.mkt)}</td>`).join("")}</tr>`).join("")}</tbody>`;
  if (!shown.length) $("#stockTable").innerHTML += `<tbody><tr><td colspan="${STOCK_COLS.length}" class="hint" style="padding:20px">沒有股票符合目前條件。可勾選「顯示差 1–2 條的股票」看看是哪幾條卡住。</td></tr></tbody>`;
  $$("#stockTable th").forEach((th) => (th.onclick = () => {
    const k = th.dataset.k; if (k === "rank") return;
    S.sort = { key: k, dir: S.sort.key === k ? -S.sort.dir : (["id", "name", "industry", "pe", "pb", "failed"].includes(k) ? 1 : -1) };
    renderScreener();
  }));
  $$("#stockTable tr.clickable").forEach((tr) => (tr.onclick = () => openDetail(S.mkt, tr.dataset.id)));
}

function unitOf(f) {
  const t = METRICS[f.metric] ? METRICS[f.metric][1] : "x";
  if (t === "money") return S.mkt === "tw" ? ["億", 1e8] : ["百萬美元", 1e6];
  if (t === "pct") return ["%", 0.01];
  return ["", 1];
}
function buildFilterPanel() {
  const fl = S.cfg.hard_filters.map((f) => {
    const st = S.settings.filters[f.id] || { enabled: true, value: f.value };
    let input = "";
    if (f.op === "<=peer") input = `<span class="hint">中位數 + ${f.peer_k_std}σ</span>`;
    else if (typeof thr(st.value, S.mkt) === "boolean") input = `<span class="hint">${thr(st.value, S.mkt) ? "是" : "否"}</span>`;
    else {
      const [u, s] = unitOf(f);
      const v = thr(st.value, S.mkt);
      input = `<span class="hint">${f.op}</span><input type="number" step="any" data-f="${f.id}" value="${+(v / s).toFixed(4)}"><span class="hint">${u}</span>`;
    }
    return `<div class="frow"><input type="checkbox" data-en="${f.id}" ${st.enabled ? "checked" : ""}><span class="lbl">${esc(f.label)}</span><span class="tag ${f.source}">${f.source === "user" ? "你的" : "補充"}</span>${input}</div>`;
  }).join("");
  $("#filterList").innerHTML = `<div class="frow" style="grid-column:1/-1"><input type="checkbox" id="useOv" ${S.settings.useOverrides !== false ? "checked" : ""}><span class="lbl"><b>套用景氣循環但書</b>（循環股、非必需消費、必需消費、醫療、公用／電信、研發密集公司改用調整後門檻，詳見「條件說明」）</span></div>` + fl;
  $("#useOv").onchange = (e) => { S.settings.useOverrides = e.target.checked; saveSettings(); };
  const W = S.settings.weights;
  $("#weightList").innerHTML = Object.keys(W).map((k) => `<div class="frow"><span class="lbl">${k === "top_down" ? "由上而下（產業分數）" : mlabel(k)}</span><input type="number" step="0.01" min="0" data-w="${k}" value="${W[k]}"></div>`).join("");
  $$("#filterList [data-en]").forEach((el) => (el.onchange = () => { S.settings.filters[el.dataset.en].enabled = el.checked; saveSettings(); }));
  $$("#filterList [data-f]").forEach((el) => (el.onchange = () => {
    const f = S.cfg.hard_filters.find((x) => x.id === el.dataset.f);
    const [, s] = unitOf(f);
    const st = S.settings.filters[f.id];
    const nv = +el.value * s;
    if (st.value && typeof st.value === "object") st.value = { ...st.value, [S.mkt]: nv }; else st.value = nv;
    saveSettings();
  }));
  $$("#weightList [data-w]").forEach((el) => (el.onchange = () => { S.settings.weights[el.dataset.w] = +el.value || 0; saveSettings(); }));
}
function saveSettings() { store.set("vs_settings_v1", S.settings); renderScreener(); }

function exportCsv() {
  const rows = (S.lastRows || []).filter((r) => r.pass || $("#showAll").checked || ($("#showNear").checked && r.failed.length <= 2));
  const cols = ["id", "name", "industry", "score", "close", "market_cap", "pe", "pb", "earnings_yield", "fcf_yield", "cash_ratio", "equity_ratio", "current_ratio", "quick_ratio", "gross_margin", "op_margin", "op_to_gross", "nonop_share", "cfo_to_ni", "fcf_to_ni", "roe", "top_down_score", "failed"];
  const lines = [cols.join(",")].concat(rows.map((r) => cols.map((c) => { const v = c === "failed" ? r.failed.join("|") : r[c]; return typeof v === "string" ? `"${v.replace(/"/g, '""')}"` : v ?? ""; }).join(",")));
  const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = `screen_${S.mkt}_${(S.meta.updated || "").slice(0, 10)}.csv`; a.click();
}

// ---------------------------------------------------------------- ETF
function renderEtf() {
  const cols = [["ticker", "代號"], ["name", "名稱"], ["style", "類型"], ["score", "分數", "num"], ["expense_ratio", "費用率", "num"], ["aum_usd", "規模", "num"],
    ["avg_traded_value_usd", "日均成交值", "num"], ["age_years", "成立(年)", "num"], ["top10_weight", "前十大", "num"], ["div_yield_ttm", "近12月殖利率", "num"],
    ["pe", "本益比", "num"], ["pb", "P/B", "num"], ["failed", "狀態"]];
  const top = S.cfg.portfolio.etf_holdings;
  let rank = 0;
  $("#etfTable").innerHTML = `<thead><tr>${cols.map(([, l, c]) => `<th class="${c || ""}">${l}</th>`).join("")}</tr></thead><tbody>${S.etf.map((r) => {
    if (r.pass_all) rank++;
    const pick = r.pass_all && rank <= top;
    return `<tr class="clickable ${r.pass_all ? "" : "dim"}" data-id="${esc(r.ticker)}">${cols.map(([k, , c]) => {
      let v;
      if (k === "ticker") v = `${pick ? "⭐ " : ""}<b>${esc(r.ticker)}</b>`;
      else if (k === "name" || k === "style") v = esc(r[k]);
      else if (k === "score") v = r.score == null ? "–" : r.score.toFixed(1);
      else if (k === "failed") v = r.pass_all ? `<span class="ok">✔ ${pick ? "入選" : "通過"}</span>` : `<span class="ng">✘</span> <span class="hint">${esc((r.failed || []).map(fname).join("、"))}</span>`;
      else if (k === "pe" || k === "pb" || k === "age_years") v = fmt.x(r[k], 1);
      else v = fmtMetric(k, r[k], "us");
      return `<td class="${c || ""}">${v}</td>`;
    }).join("")}</tr>`;
  }).join("")}</tbody>`;
  $$("#etfTable tr.clickable").forEach((tr) => (tr.onclick = () => openDetail("etf", tr.dataset.id)));
}

// ---------------------------------------------------------------- 詳情
async function openDetail(mkt, id) {
  const dlg = $("#detail");
  const row = mkt === "etf" ? S.etf.find((r) => r.ticker === id) : (S.lastRows && S.mkt === mkt ? S.lastRows : runScreen(mkt)).find((r) => r.id === id);
  $("#dTitle").textContent = `${id}　${row ? row.name : ""}`;
  $("#dBody").innerHTML = `<p class="hint">載入中…</p>`;
  dlg.showModal();
  const d = await getJSON(`data/detail/${mkt}/${encodeURIComponent(id)}.json`, true);
  let html = "";
  if (row && mkt !== "etf") {
    html += `<div class="tiles">${[["綜合分數", row.score != null ? row.score.toFixed(1) : "未通過"], ["本益比", fmt.x(row.pe)], ["P/B", fmt.x(row.pb, 2)], ["FCF 殖利率", fmt.pct(row.fcf_yield)], ["市值", fmt.money(row.market_cap, mkt)], ["產業", esc(row.industry)]].map(([k, v]) => `<div class="tile"><div class="k">${k}</div><div class="v" style="font-size:18px">${v}</div></div>`).join("")}</div>`;
    html += `<div class="card"><h2>條件檢查</h2><div class="checklist">${S.cfg.hard_filters.filter((f) => S.settings.filters[f.id]?.enabled).map((f) => {
      const ok = row["f_" + f.id];
      const m = row["_m_" + f.id] || f.metric;
      let val = fmtMetric(m, row[m], mkt);
      if (f.op === "<=peer") val += ` ≤ ${fmt.pct(row._peer)}`;
      const t = row["_t_" + f.id], dflt = thr((S.settings.filters[f.id] || {}).value, mkt);
      let note = "";
      if (row["_off_" + f.id]) note = `<span class="hint">（但書：不適用）</span>`;
      else if (f.op !== "<=peer" && (t !== dflt || m !== f.metric)) note = `<span class="hint">（但書：${esc(mlabel(m))} ${f.op} ${fmtMetric(m, t, mkt)}）</span>`;
      return `<div><span class="${ok ? "ok" : "ng"}">${ok ? "✔" : "✘"}</span><span>${esc(f.label)} ${note}</span><span class="val">${val}</span></div>`;
    }).join("")}</div><p class="hint">財報期別：${esc(row.period_end || "")}（流量為近四季合計）${row.groups && row.groups.length ? "　但書群組：" + groupTags(row) : ""}${row.event_flags ? "<br>⚠️ 事件：" + esc(row.event_flags) : ""}</p></div>`;
  } else if (row) {
    html += `<div class="card"><h2>ETF 條件檢查</h2><div class="checklist">${S.cfg.etf.hard_filters.map((f) => {
      const ok = !(row.failed || []).includes(f.id);
      return `<div><span class="${ok ? "ok" : "ng"}">${ok ? "✔" : "✘"}</span><span>${esc(f.label)}</span><span class="val">${fmtMetric(f.metric, row[f.metric], "us")}</span></div>`;
    }).join("")}</div></div>`;
  }
  if (!d) html += `<p class="hint">此標的沒有詳細資料（只有通過或差 1–2 條的股票、ETF 與追蹤清單會產生詳情）。</p>`;
  else {
    html += `<div class="card"><h2>近兩年股價</h2><div class="chart-box"><canvas id="dPrice"></canvas></div></div>`;
    if (d.quarters && d.quarters.length) {
      html += `<div class="grid2"><div class="card"><h2>獲利與現金流（單季）</h2><div class="legend" id="dCfLeg"></div><div class="chart-box"><canvas id="dCf"></canvas></div></div>
               <div class="card"><h2>毛利率與營益率（近四季）</h2><div class="legend" id="dMgLeg"></div><div class="chart-box"><canvas id="dMg"></canvas></div></div></div>`;
    }
  }
  $("#dBody").innerHTML = html;
  if (!d) return;
  destroyChart("dPrice"); destroyChart("dCf"); destroyChart("dMg");
  if (d.prices && d.prices.length) {
    S.charts.dPrice = lineChart($("#dPrice"), d.prices.map((p) => p[0]), [{ label: "收盤價", data: d.prices.map((p) => p[1]), color: cssVar("--s1") }], { yfmt: (v) => fmt.price(v) });
  }
  if (d.quarters && d.quarters.length) {
    const L = d.quarters.map((q) => q.q);
    const ser = [["稅後淨利", "net_income", "--s1"], ["營業現金流", "cfo", "--s2"], ["投資現金流", "cfi", "--s3"], ["籌資現金流", "cff", "--s4"]];
    legend($("#dCfLeg"), ser.map(([l, , c]) => [l, cssVar(c)]));
    S.charts.dCf = new Chart($("#dCf"), {
      type: "bar",
      data: { labels: L, datasets: ser.map(([l, k, c]) => ({ label: l, data: d.quarters.map((q) => q[k]), backgroundColor: cssVar(c), borderRadius: 4, borderSkipped: "start", borderWidth: 0 })) },
      options: baseOpts({ yfmt: (v) => fmt.money(v, mkt === "tw" ? "tw" : "us"), legend: false }),
    });
    const mg = [["毛利率", "gm", "--s1"], ["營益率", "om", "--s2"]];
    legend($("#dMgLeg"), mg.map(([l, , c]) => [l, cssVar(c)]));
    S.charts.dMg = lineChart($("#dMg"), L, mg.map(([l, k, c]) => ({ label: l, data: d.quarters.map((q) => q[k]), color: cssVar(c) })), { yfmt: (v) => fmt.pct(v, 0), points: true });
  }
}

// ---------------------------------------------------------------- 圖表工具
function destroyChart(k) { if (S.charts[k]) { S.charts[k].destroy(); delete S.charts[k]; } }
function baseOpts({ yfmt = (v) => v, legend = false, log = false } = {}) {
  const grid = cssVar("--grid"), txt = cssVar("--text-2");
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { display: legend },
      tooltip: { backgroundColor: cssVar("--surface"), titleColor: cssVar("--text"), bodyColor: cssVar("--text-2"), borderColor: cssVar("--border"), borderWidth: 1, padding: 8, boxPadding: 4,
        callbacks: { label: (c) => ` ${c.dataset.label}：${yfmt(c.parsed.y)}` } },
    },
    scales: {
      x: { grid: { display: false }, ticks: { color: txt, maxTicksLimit: 8, maxRotation: 0 }, border: { color: grid } },
      y: { type: log ? "logarithmic" : "linear", grid: { color: grid }, ticks: { color: txt, callback: (v) => yfmt(v), maxTicksLimit: 6 }, border: { display: false } },
    },
  };
}
function lineChart(canvas, labels, series, opt = {}) {
  return new Chart(canvas, {
    type: "line",
    data: { labels, datasets: series.map((s) => ({ label: s.label, data: s.data, borderColor: s.color, backgroundColor: s.color, borderWidth: s.width || 2, pointRadius: opt.points ? 3 : 0, pointHoverRadius: 4, tension: 0.15, hidden: s.hidden, borderDash: s.dash || [] })) },
    options: baseOpts(opt),
  });
}
function legend(el, items, onToggle) {
  el.innerHTML = items.map(([l, c, on = true], i) => `<label>${onToggle ? `<input type="checkbox" data-i="${i}" ${on ? "checked" : ""}>` : ""}<span class="sw" style="background:${c}"></span>${esc(l)}</label>`).join("");
  if (onToggle) $$("input", el).forEach((x) => (x.onchange = () => onToggle(+x.dataset.i, x.checked)));
}

// ---------------------------------------------------------------- 回測
function renderBacktest() {
  const bt = S.bt;
  if (!bt) { $("#btTiles").innerHTML = `<div class="card">尚未執行回測。</div>`; return; }
  const st = S.btPeriod === "5y" ? bt.stats_5y : bt.stats_full;
  const net = st["策略（扣費後）"], gross = st["策略（未扣費）"];
  $("#btMeta").textContent = `${bt.start} ~ ${bt.end}　起始 ${fmt.ntd(bt.initial_capital)}　產生於 ${bt.generated}`;
  const tiles = [
    ["年化報酬（扣費後）", fmt.pct(net.cagr), `未扣費 ${fmt.pct(gross.cagr)}`],
    ["累積報酬（扣費後）", fmt.pct(net.total_return, 0), `未扣費 ${fmt.pct(gross.total_return, 0)}`],
    ["期末金額（扣費後）", fmt.ntd(net.final_amount), `未扣費 ${fmt.ntd(gross.final_amount)}`],
    ["最大回撤", fmt.pct(net.max_drawdown), `波動度 ${fmt.pct(net.vol)}`],
    ["夏普值", fmt.x(net.sharpe, 2), `無風險利率 ${fmt.pct(S.cfg.backtest.risk_free)}`],
  ];
  $("#btTiles").innerHTML = tiles.map(([k, v, d]) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="d">${d}</div></div>`).join("");

  let months = bt.months, start = 0;
  if (S.btPeriod === "5y") start = Math.max(0, months.length - 60);
  months = months.slice(start);
  const order = Object.keys(bt.nav);
  const colors = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7"];
  const prefer = ["策略（扣費後）", "策略（未扣費）"];
  const keys = [...prefer.filter((k) => order.includes(k)), ...order.filter((k) => !prefer.includes(k))];
  S.btVis = S.btVis || Object.fromEntries(keys.map((k) => [k, prefer.includes(k) || k.startsWith("混合")]));
  const ser = keys.map((k, i) => {
    const raw = bt.nav[k].slice(start);
    const base = start > 0 ? bt.nav[k][start - 1] : bt.initial_capital;
    return { label: k, data: raw.map((v) => (v / base) * bt.initial_capital), color: cssVar(colors[i % colors.length]), hidden: !S.btVis[k], dash: k === "策略（未扣費）" ? [5, 4] : [] };
  });
  destroyChart("bt");
  S.charts.bt = lineChart($("#btChart"), months, ser, { yfmt: (v) => fmt.ntd(v), log: $("#btLog").checked });
  legend($("#btLegend"), ser.map((s) => [s.label, s.color, !s.hidden]), (i, on) => { S.btVis[keys[i]] = on; S.charts.bt.setDatasetVisibility(i, on); S.charts.bt.update(); });

  // 統計表
  const cols = [["cagr", "年化"], ["total_return", "累積"], ["final_amount", "期末金額"], ["vol", "波動"], ["max_drawdown", "最大回撤"], ["sharpe", "夏普"]];
  $("#btStats").innerHTML = `<thead><tr><th></th>${cols.map(([, l]) => `<th class="num">${l}</th>`).join("")}</tr></thead><tbody>${keys.map((k) => `<tr><td>${esc(k)}</td>${cols.map(([c]) => {
    const v = st[k][c];
    const t = c === "final_amount" ? fmt.ntd(v) : c === "sharpe" ? fmt.x(v, 2) : fmt.pct(v, c === "total_return" ? 0 : 1);
    return `<td class="num">${t}</td>`;
  }).join("")}</tr>`).join("")}</tbody>`;
  // 各部位
  const sl = (S.btPeriod === "5y" ? bt.sleeves_5y : bt.sleeves_full) || {};
  $("#btSleeves").innerHTML = `<thead><tr><th>部位</th><th class="num">年化</th><th class="num">對照</th><th class="num">對照年化</th><th class="num">差距</th><th class="num">最大回撤</th></tr></thead><tbody>${Object.entries(sl).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${fmt.pct(v.cagr)}</td><td class="num">${esc(v.bench || "")}</td><td class="num">${fmt.pct(v.bench_cagr)}</td><td class="num ${v.cagr - v.bench_cagr >= 0 ? "pos" : "neg"}">${v.bench_cagr != null ? fmt.pct(v.cagr - v.bench_cagr) : "–"}</td><td class="num">${fmt.pct(v.max_drawdown)}</td></tr>`).join("")}</tbody>`;
  const rg = (S.btPeriod === "5y" ? bt.regimes_5y : bt.regimes_full) || {};
  if (rg.up_capture != null) {
    const per = Object.entries(rg.periods || {}).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${fmt.pct(v.strategy_net)}</td><td class="num">${fmt.pct(v.bench)}</td><td class="num ${v.strategy_net >= v.bench ? "pos" : "neg"}">${fmt.pct(v.strategy_net - v.bench)}</td></tr>`).join("");
    $("#btRegime").innerHTML = `<div class="tiles" style="grid-template-columns:repeat(auto-fit,minmax(130px,1fr))">
      <div class="tile"><div class="k">上漲月參與率</div><div class="v">${fmt.pct(rg.up_capture, 0)}</div><div class="d">${rg.up_months} 個月</div></div>
      <div class="tile"><div class="k">下跌月參與率</div><div class="v">${fmt.pct(rg.down_capture, 0)}</div><div class="d">越低越抗跌</div></div>
      <div class="tile"><div class="k">滾動一年勝率</div><div class="v">${fmt.pct(rg.rolling12_beat_rate, 0)}</div><div class="d">空頭年 ${fmt.pct(rg.rolling12_beat_rate_bear, 0)}／多頭年 ${fmt.pct(rg.rolling12_beat_rate_bull, 0)}</div></div></div>
      <div class="table-wrap"><table class="data compact"><thead><tr><th>期間</th><th class="num">策略</th><th class="num">混合基準</th><th class="num">差距</th></tr></thead><tbody>${per}</tbody></table></div>
      <p class="hint">滾動一年勝率 = 任選一個 12 個月區間，策略報酬 ≥ 基準的比例。你的目標「任何景氣下都持平或擊敗大盤」對應的是空頭年與多頭年兩個勝率都要高。</p>`;
  } else $("#btRegime").innerHTML = `<p class="hint">無資料</p>`;
  // 逐年
  const years = [...new Set(keys.flatMap((k) => Object.keys(st[k].yearly || {})))].sort();
  $("#btYearly").innerHTML = `<thead><tr><th>年度</th>${keys.map((k) => `<th class="num">${esc(k.replace("策略", "").replace(/[（）]/g, "") || k)}</th>`).join("")}</tr></thead><tbody>${years.map((y) => `<tr><td>${y}</td>${keys.map((k) => { const v = (st[k].yearly || {})[y]; return `<td class="num ${v >= 0 ? "pos" : "neg"}">${fmt.pct(v)}</td>`; }).join("")}</tr>`).join("")}</tbody>`;
  // 回撤
  const nav = ser[0].data;
  let peak = -Infinity;
  const dd = nav.map((v) => { peak = Math.max(peak, v); return v / peak - 1; });
  destroyChart("dd");
  S.charts.dd = new Chart($("#btDD"), { type: "line", data: { labels: months, datasets: [{ label: "回撤", data: dd, borderColor: cssVar("--s8"), backgroundColor: cssVar("--s8") + "33", fill: true, borderWidth: 2, pointRadius: 0 }] }, options: baseOpts({ yfmt: (v) => fmt.pct(v, 0) }) });
  // 檔數
  const cser = [["台股", "tw", "--s1"], ["美股", "us", "--s2"], ["ETF", "etf", "--s3"]];
  legend($("#cntLegend"), cser.map(([l, , c]) => [l, cssVar(c)]));
  destroyChart("cnt");
  S.charts.cnt = new Chart($("#btCount"), {
    type: "bar",
    data: { labels: months, datasets: cser.map(([l, k, c]) => ({ label: l, data: bt.counts[k].slice(start), backgroundColor: cssVar(c), borderWidth: 0, borderRadius: 2, stack: "s" })) },
    options: (() => { const o = baseOpts({ yfmt: (v) => Math.round(v) + "" }); o.scales.x.stacked = true; o.scales.y.stacked = true; return o; })(),
  });
  // 持股
  const sel = $("#btMonth");
  sel.innerHTML = bt.holdings.slice().reverse().map((h) => `<option>${h.month}</option>`).join("");
  const showH = () => {
    const h = bt.holdings.find((x) => x.month === sel.value);
    const blk = (t, a) => `<div><b>${t}（${a.length}）</b><ul>${a.map(([i, n]) => `<li>${esc(i)} ${esc(n)}</li>`).join("") || "<li class='hint'>無 → 現金</li>"}</ul></div>`;
    $("#btHoldings").innerHTML = blk("台股", h.tw) + blk("美股", h.us) + blk("ETF", h.etf);
  };
  sel.onchange = showH; showH();
  const c = S.cfg.costs;
  $("#btNotes").innerHTML = `<h2>回測假設與限制</h2><ul>
    <li>每月底以「當時已公布」的財報（台股依法定公告期限、美股依 SEC 實際申報日）與月底股價選股，下個月持有；各部位等權重，台股 ${fmt.pct(bt.weights.tw_stock, 0)}／美股 ${fmt.pct(bt.weights.us_stock, 0)}／ETF ${fmt.pct(bt.weights.etf, 0)}，每月再平衡。</li>
    <li>扣費後情境：台股手續費 ${fmt.pct(c.tw_stock.buy, 4)}、賣出證交稅 0.3%（ETF 0.1%）；美股複委託手續費 ${fmt.pct(c.us.buy, 2)}＋換匯價差 ${fmt.pct(c.fx_spread, 2)}；美股股利預扣 ${fmt.pct(c.us_dividend_withholding, 0)}。全期累計交易成本約占淨值 ${fmt.pct(bt.cost_total_pct, 1)}。未計台股股利所得稅與二代健保補充保費（依個人稅率而定）。</li>
    <li>以新台幣計價，美股與美股 ETF 依每月匯率換算；股價含息還原（Yahoo Finance）。某市場無股票通過時該部位持有現金（報酬 0）。</li>
    <li><b>倖存者偏差</b>：資料來源只包含目前仍掛牌的公司，已下市股票不在股票池中，回測結果通常會偏樂觀。</li>
    <li>ETF 的費用率與規模用目前數值（歷史數值不易取得）；成立年數、流動性、殖利率則用當時數值。</li>
    <li>過去績效不代表未來報酬，本系統僅供研究參考，不構成投資建議。</li></ul>`;
}

// ---------------------------------------------------------------- 追蹤清單
function renderWatchlist() {
  const wl = store.get("vs_watch", { tw: [], us: [] });
  const repo = S.meta.repo || "";
  $("#wlEdit").href = repo ? `https://github.com/${repo}/edit/main/config/watchlist.yaml` : "#";
  const rowsAll = { tw: runScreen("tw"), us: runScreen("us") };
  const out = [];
  for (const m of ["tw", "us"]) for (const id of wl[m] || []) {
    const r = rowsAll[m].find((x) => x.id === id);
    out.push({ m, id, r });
  }
  $("#wlTable").innerHTML = `<thead><tr><th>市場</th><th>代號</th><th>名稱</th><th class="num">分數</th><th class="num">本益比</th><th>狀態</th><th></th></tr></thead><tbody>${out.map(({ m, id, r }) => `<tr class="clickable" data-m="${m}" data-id="${esc(id)}">
    <td>${m === "tw" ? "台股" : "美股"}</td><td><b>${esc(id)}</b></td><td>${esc(r ? r.name : "（查無資料）")}</td><td class="num">${r && r.score != null ? r.score.toFixed(1) : "–"}</td><td class="num">${r ? fmt.x(r.pe) : "–"}</td>
    <td>${!r ? "" : r.pass ? `<span class="ok">✔ 通過</span>` : `<span class="ng">✘</span> <span class="hint">${esc(r.failed.map(fname).join("、"))}</span>`}</td>
    <td><button class="ghost small" data-del="${m}:${esc(id)}">移除</button></td></tr>`).join("") || `<tr><td colspan="7" class="hint" style="padding:16px">還沒有追蹤的股票</td></tr>`}</tbody>`;
  $$("#wlTable tr.clickable").forEach((tr) => (tr.onclick = (e) => { if (e.target.dataset.del) return; openDetail(tr.dataset.m, tr.dataset.id); }));
  $$("#wlTable [data-del]").forEach((b) => (b.onclick = () => {
    const [m, id] = b.dataset.del.split(":");
    wl[m] = wl[m].filter((x) => x !== id); store.set("vs_watch", wl); renderWatchlist();
  }));
}

// ---------------------------------------------------------------- 條件說明
function renderRules() {
  const c = S.cfg;
  const fr = (f) => {
    let v = f.op === "<=peer" ? `同產業中位數 + ${f.peer_k_std}σ` : "";
    if (!v) { const t = f.value; v = typeof t === "object" ? `台股 ${fmtMetric(f.metric, t.tw, "tw")}／美股 ${fmtMetric(f.metric, t.us, "us")}` : typeof t === "boolean" ? (t ? "是" : "否") : `${f.op} ${fmtMetric(f.metric, t, "tw")}`; }
    return `<tr><td>${esc(f.label)}</td><td>${f.source === "user" ? "你的條件" : "補充"}</td><td class="hint">${esc(mlabel(f.metric))}：${esc(v)}</td></tr>`;
  };
  const w = c.ranking.bottom_up;
  const wrow = (g, name) => Object.entries(w[g]).map(([k, v]) => `<li>${mlabel(k)}：${fmt.pct(v, 0)}</li>`).join("");
  $("#rulesBody").innerHTML = `
  <h2>選股流程</h2>
  <ol>
    <li><b>由下而上（Bottom-up）硬性條件</b>：個股要通過下表所有條件才進入候選池。流量項目（營收、利潤、現金流）用近四季合計，資產負債用最新一季。</li>
    <li><b>由上而下（Top-down）</b>：每個產業用「6 個月相對強弱 50% + 產業估值（中位數 EBIT/EV）50%」打分；大盤以 10 個月均線判斷多空（目前僅顯示，可在設定檔開啟「跌破均線股票部位減半」）。</li>
    <li><b>綜合排名</b>：候選池內以百分位計分，價值 40%＋品質 30%＋安全 10%＋產業分數 20%，另有加分。由下而上分數前 1/3 且產業分數前 1/2 的股票標為 ⭐「上下皆優」。</li>
    <li><b>組合</b>：台股、美股各取前 ${c.portfolio.max_holdings_per_stock_sleeve} 名等權重；ETF 不分市場取前 ${c.portfolio.etf_holdings} 名；每月初調整。</li>
  </ol>
  <h2>一、個股硬性條件</h2>
  <div class="table-wrap"><table class="data compact"><thead><tr><th>條件</th><th>來源</th><th>計算方式／門檻</th></tr></thead><tbody>${c.hard_filters.map(fr).join("")}</tbody></table></div>
  <p class="hint">說明：第 2 條「資產太多太雜」量化為商譽＋無形資產 ≤ 15% 與長期投資 ≤ 20%；第 4 條「股東出資佔比」解讀為股東權益占總資產；第 5 條「短期負債太多」量化為流動比率 ≥ 150% 與速動比率 ≥ 100%；第 6、7 條改為近 3 年每年都要達標；第 9 條由 5% 放寬到 10%，避免誤殺利息收入高的現金充裕公司。</p>
  <h2>現金流量與公司類型（近 3 年合計）</h2>
  <div class="table-wrap"><table class="data compact"><thead><tr><th>類型</th><th>營業</th><th>投資</th><th>籌資</th><th>處理</th></tr></thead><tbody>
  <tr><td>甲．營運成長及財務穩健</td><td>＋</td><td>－</td><td>－</td><td class="ok">首選（加分）</td></tr>
  <tr><td>乙．營運成長及積極投資</td><td>＋</td><td>－</td><td>＋</td><td>次選</td></tr>
  <tr><td>丙．本業趨緩及積極投資</td><td>－</td><td>－</td><td>＋</td><td class="ng">不要碰</td></tr>
  <tr><td>丁．本業趨緩及投資保守</td><td>－</td><td>＋</td><td>平</td><td class="ng">不要碰</td></tr>
  <tr><td>戊．本業惡化及投資停頓</td><td>－</td><td>＋</td><td>＋</td><td class="ng">不要碰</td></tr></tbody></table></div>
  <p class="hint">另外要求近 3 年「每年」營業現金流都是正數。營業為正但投資也為正（處分資產）的公司不屬於甲、乙，一併排除。</p>
  <h2>景氣循環檢查與但書</h2>
  <p>你的目標是「任何景氣下都持平或擊敗大盤」。下表列出各景氣階段通常領先的產業，以及原始條件會不會把它們刷掉。被刷掉的，改用調整後門檻（網站「調整條件」可以關掉但書做比較）。</p>
  <div class="table-wrap"><table class="data compact"><thead><tr><th>景氣階段</th><th>常見強勢產業</th><th>原始條件的問題</th><th>但書</th></tr></thead><tbody>
  <tr><td>復甦期</td><td>景氣循環（鋼鐵、塑化、航運、化工、機械、汽車）、非必需消費、金融</td><td>谷底那年利潤率低、甚至虧損 → 「近 3 年每年」條件全滅；谷底時本益比偏高</td><td>循環股：毛利率、營益率改看 3 年平均，營益率 ≥ 10%；不要求每年獲利；改用股價淨值比 ≤ 1.5 取代本益比</td></tr>
  <tr><td>擴張期</td><td>科技、半導體、軟體、工業</td><td>研發費用高 → 營益／毛利常低於 65%</td><td>研發費用 ≥ 營收 5%：營益／毛利 ≥ 55%</td></tr>
  <tr><td>過熱期</td><td>能源、原物料</td><td>同循環股</td><td>同循環股</td></tr>
  <tr><td>衰退期</td><td>必需消費（食品、日用品）、醫療保健、公用事業、電信</td><td>品牌行銷費用高 → 營益／毛利低；藥廠併購的商譽多；公用事業負債高、流動比低、自由現金流常為負</td><td>必需消費：營益率 ≥ 10%、營益／毛利 ≥ 40%、現金比 ≥ 8%。醫療：無形資產 ≤ 40%、營益／毛利 ≥ 55%。公用／電信：權益比 ≥ 25%、流動比 ≥ 60%、現金比 ≥ 2%，不要求自由現金流與營收成長</td></tr>
  <tr><td>全階段</td><td>金融（銀行、保險）</td><td>財報結構不同，所有比率都不適用</td><td>維持排除；60% ETF 部位裡的價值型 ETF 已含約 2 成金融股</td></tr>
  </tbody></table></div>
  <p class="hint">提醒：任何篩選都無法保證每個景氣都贏大盤。回測頁的「滾動一年勝率」會告訴你實際上做到幾成。</p>
  <h2>換股緩衝區與事件否決</h2>
  <ul><li>新買進要通過全部條件；已持有的股票只要門檻放寬 ${fmt.pct(c.buffer.tolerance, 0)} 仍通過、而且排名在前 ${c.buffer.rank_multiple} 倍內就續抱，避免在門檻邊緣來回買賣。</li>
  <li>重大事件否決：美股 8-K 4.02（財報不可信賴，12 個月）、1.03（破產，24 個月）、3.01（下市警告，12 個月）、4.01（更換會計師，6 個月）；台股重大訊息出現「財報重編、會計師保留意見、跳票、重整、掏空…」等字眼排除 12 個月。台股重大訊息從系統上線後每天累積，過去 10 年無法回測。</li>
  <li>台股董監質押比 ≤ 30%（只有最新資料，回測時不套用）。</li></ul>
  <h2>二、排名權重</h2>
  <ul><li><b>價值 40%</b><ul>${wrow("value")}</ul></li><li><b>品質 30%</b><ul>${wrow("quality")}</ul></li><li><b>安全 10%</b><ul>${wrow("safety")}</ul></li>
  <li><b>由上而下（產業分數）${fmt.pct(c.ranking.top_down, 0)}</b></li>
  <li><b>加分</b>：現金流甲型 +${c.ranking.bonuses.cf_type_a}（甲優先於乙，含你的第 14 條「籌資為負」）；營業現金流 ≥ 稅後淨利 +${c.ranking.bonuses.cfo_to_ni_100}（你的第 12 條「100% 以上更好」）</li></ul>
  <h2>三、ETF 條件</h2>
  <div class="table-wrap"><table class="data compact"><thead><tr><th>條件</th><th>門檻</th></tr></thead><tbody>${c.etf.hard_filters.map((f) => `<tr><td>${esc(f.label)}</td><td class="hint">${esc(mlabel(f.metric))} ${f.op} ${typeof f.value === "boolean" ? (f.value ? "是" : "否") : fmtMetric(f.metric, f.value, "us")}</td></tr>`).join("")}</tbody></table></div>
  <p>排名：費用率（越低越好）30%、價值傾向（ETF 持股的盈餘殖利率、淨值市價比、近 12 月殖利率）30%、規模 10%、流動性 10%、追蹤波動（越低越好）10%、前十大集中度（越低越好）10%。</p>
  <p class="hint">補充給 ETF 新手：(1) 費用率是每年從淨值裡扣的成本，長期複利影響很大；(2) 規模太小的 ETF 可能下市清算；(3) 成交量低、買賣價差大時，買賣一次就先虧價差；(4) 追蹤誤差大代表 ETF 沒有好好複製指數；(5) 前十大持股占比太高時，其實接近押少數幾檔股票；(6) 名稱有「價值」「高股息」不代表真的便宜，要看指數怎麼選股（本益比、股價淨值比、殖利率、獲利品質）。台股高股息 ETF 多半半年或一季換股一次，周轉率高、內扣成本常比標示的經理費＋保管費高。</p>
  <h2>四、交易成本與稅（回測扣費後情境）</h2>
  <ul><li>台股：手續費 0.1425%（買賣各一次）＋賣出證交稅 0.3%（ETF 0.1%）</li><li>美股：複委託手續費假設 0.1%（買賣各一次）＋換匯價差 0.05%；股利預扣稅 30%</li></ul>
  <h2>五、資料來源</h2>
  <ul><li>台股財報：FinMind（整理自公開資訊觀測站）；台股清單與產業別：FinMind</li><li>美股財報：SEC EDGAR XBRL（含申報日期）；產業：SEC SIC 代碼</li><li>股價、ETF 資訊、匯率：Yahoo Finance</li></ul>
  <p class="hint">本系統僅供研究參考，不構成投資建議。</p>`;
}

// ---------------------------------------------------------------- 事件
function bindUI() {
  $$("#mktSeg button").forEach((b) => (b.onclick = () => { S.mkt = b.dataset.m; $$("#mktSeg button").forEach((x) => x.classList.toggle("on", x === b)); renderScreener(); }));
  $$("#ovSecSeg button").forEach((b) => (b.onclick = () => { S.secMkt = b.dataset.m; $$("#ovSecSeg button").forEach((x) => x.classList.toggle("on", x === b)); renderSectors(); }));
  $$("#wlSeg button").forEach((b) => (b.onclick = () => { S.wlMkt = b.dataset.m; $$("#wlSeg button").forEach((x) => x.classList.toggle("on", x === b)); }));
  $$("#btPeriod button").forEach((b) => (b.onclick = () => { S.btPeriod = b.dataset.p; $$("#btPeriod button").forEach((x) => x.classList.toggle("on", x === b)); renderBacktest(); }));
  $("#btLog").onchange = renderBacktest;
  let tm; $("#search").oninput = () => { clearTimeout(tm); tm = setTimeout(renderScreener, 150); };
  $("#showNear").onchange = renderScreener; $("#showAll").onchange = renderScreener;
  $("#toggleFilters").onclick = () => { const p = $("#filterPanel"); p.hidden = !p.hidden; $("#toggleFilters").textContent = p.hidden ? "調整條件 ▾" : "收起條件 ▴"; };
  $("#resetFilters").onclick = () => { S.settings = defaultSettings(); saveSettings(); };
  $("#exportCsv").onclick = exportCsv;
  $("#dClose").onclick = () => $("#detail").close();
  $("#detail").addEventListener("click", (e) => { if (e.target.id === "detail") $("#detail").close(); });
  $("#wlAdd").onclick = () => {
    const v = $("#wlInput").value.trim().toUpperCase(); if (!v) return;
    const wl = store.get("vs_watch", { tw: [], us: [] });
    (wl[S.wlMkt] ||= []); if (!wl[S.wlMkt].includes(v)) wl[S.wlMkt].push(v);
    store.set("vs_watch", wl); $("#wlInput").value = ""; renderWatchlist();
  };
}

init();
