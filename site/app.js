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
  sue: ["財報驚喜", "x"], rev_yoy_3m: ["近3月營收年增", "pct"], mos: ["安全邊際", "pct"], ep: ["盈餘殖利率(E/P)", "pct"],
  roe_norm: ["正常化 ROE", "pct"], roe_5y: ["ROE（5年平均）", "pct"], roe_avg5y: ["ROE（5年平均）", "pct"], roe_avg10y: ["ROE（10年平均）", "pct"],
  pb_hist_pct: ["P/B 在自己10年歷史的位置", "pctile"], gm_peak_ratio: ["毛利率÷長期平均", "x"],
  buyable: ["模板可買進", "bool"], fin_allowed: ["金融股開放", "bool"], cfo_3y: ["近3年營業CF合計", "money"],
  ev_ebitda: ["EV/EBITDA", "x"], shareholder_yield: ["股東回饋率（股利+買回）", "pct"], int_cover: ["利息保障倍數", "x"],
  sbc: ["股票酬勞", "money"], fcf_after_sbc: ["扣股票酬勞的FCF", "money"],
};
const mlabel = (m) => (METRICS[m] ? METRICS[m][0] : m);
const GROUP_SHORT = { financials: "金融", rd_heavy: "研發密集", cyclicals: "循環股", discretionary: "非必需消費", staples: "必需消費", healthcare: "醫療", utilities_telecom: "公用/電信" };
const TEMPLATE_LABEL = { general: "一般", cyclical: "循環", financial: "金融", construction: "營建", utility: "公用", defensive: "防禦", growth_watch: "成長觀察", biotech_research: "新藥研究", exclude: "排除" };
const TEMPLATE_NOTE = {
  general: "一般公司：三種估值模型（RIM、DCF、同業）", cyclical: "循環股：看循環平均、股價淨值比、高峰警示；ROE 用 10 年平均",
  financial: "金融股：看 ROE、股價淨值比、本益比；不用現金流條件", construction: "營建：完工才認列，看 5 年平均 ROE、歷史股價淨值比、近 3 年現金流合計",
  utility: "公用：負債高、流動比低是常態；自由現金流條件不適用", defensive: "防禦：品牌與專利多來自併購，無形資產門檻放寬",
  growth_watch: "成長觀察：只估值觀察，不自動買進", biotech_research: "新藥研究：還沒獲利，不自動篩，用研究工具估", exclude: "排除（創新板、空殼公司）",
};
// 規則版本：old = 原本 31 條；new = 市場 × 產業規則（industry_rules）；cycle = 依景氣階段切換（cycle.phase_rules）
const liveMode = () => ((S.cfg.cycle || {}).enabled ? "cycle" : (S.cfg.industry_rules || {}).enabled ? "new" : "old");
const ruleMode = () => (S.cfg.industry_rules ? (S.settings.ruleSet || liveMode()) : "old");
function phaseRule(mkt) {
  const ph = ((S.meta.cycle || {})[mkt] || {}).phase;
  return ((S.cfg.cycle || {}).phase_rules || {})[ph] || null;
}
const ruleSet = (mkt = S.curMkt || S.mkt) => {
  const md = ruleMode();
  if (md !== "cycle") return md;
  const r = phaseRule(mkt);
  return r && r.rule_set === "new" ? "new" : "old";
};
const finAllowed = (mkt = S.curMkt || S.mkt) => (ruleMode() === "cycle" ? !!(phaseRule(mkt) || {}).financials : !!(S.cfg.industry_rules || {}).financials);
const activeFilters = () => (ruleSet() === "new" ? S.cfg.industry_rules.filters : S.cfg.hard_filters);
const allFilters = () => [...S.cfg.hard_filters, ...((S.cfg.industry_rules || {}).filters || [])];
function activeOverrides(mkt) {
  if (ruleSet() !== "new") return S.cfg.overrides || {};
  const ir = S.cfg.industry_rules, o = { ...(ir.template_overrides || {}) };
  for (const [k, v] of Object.entries((ir.industry_overrides || {})[mkt] || {})) o["ind:" + k] = v;
  return o;
}
const rowGroups = (r) => (ruleSet() === "new" ? [r.template, "ind:" + r.industry_norm] : (r.groups_legacy || r.groups || []));
const tplTag = (r) => (r.template ? `<span class="pill tpl-${esc(r.template)}" title="${esc(TEMPLATE_NOTE[r.template] || "")}${r.peer_group ? "；同業比較：" + esc(r.peer_group) + "（" + (r.peer_n ?? "") + " 家）" : ""}">${TEMPLATE_LABEL[r.template] || esc(r.template)}</span>` : "");
const groupTags = (r) => (ruleSet() === "new" ? tplTag(r) : rowGroups(r).map((g) => `<span class="pill" title="${esc((S.cfg.groups[g] || {}).label || g)}">${GROUP_SHORT[g] || g}</span>`).join(" "));

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

// ---------------------------------------------------------------- 公司中文名稱與一句話說明（data/names.json）
const mktOf = (id) => (/^\d/.test(String(id)) ? "tw" : "us");
function zhInfo(m, id, name) {
  const N = S.names || {}, k = String(id ?? "");
  const x = (m === "etf" ? null : (N[m] || {})[k]) || (N.etf || {})[k] || (m === "tw" ? (N.tw || {})[k.replace(/\.TWO?$/, "")] : null);
  return x ? { n: x[0] || name || k, d: x[1] || "" } : { n: name || "", d: "" };
}
// 表格名稱欄：中文名＋小字說明（滑鼠移上顯示原本名稱）
function nameCell(m, id, name) {
  const z = zhInfo(m, id, name);
  return `<span title="${esc(name || "")}">${esc(z.n)}</span>${z.d ? `<br><span class="hint">${esc(z.d)}</span>` : ""}`;
}
// 清單裡的名稱：「中文名 說明」同一行
function nameInline(m, id, name) {
  const z = zhInfo(m, id, name);
  return `${esc(z.n)}${z.d ? ` <span class="hint">${esc(z.d)}</span>` : ""}`;
}
const zhText = (m, id) => { const z = zhInfo(m, id, ""); return `${z.n} ${z.d}`; };

async function init() {
  initTheme();
  try {
    const [cfg, meta, tw, us, etf, port, names] = await Promise.all([
      getJSON("data/config.json"), getJSON("data/meta.json"),
      getJSON("data/stocks_tw.json", true), getJSON("data/stocks_us.json", true),
      getJSON("data/etf.json", true), getJSON("data/portfolio.json", true), getJSON("data/names.json", true),
    ]);
    Object.assign(S, { cfg, meta, port, names: names || {}, stocks: { tw: toObjs(tw), us: toObjs(us) }, etf: toObjs(etf) });
  } catch (e) {
    $("#updated").textContent = "資料尚未產生（等待第一次自動更新）";
    return;
  }
  S.bt = await getJSON("data/backtest.json", true);
  S.news = await getJSON("data/news.json", true);
  S.orders = await getJSON("data/orders.json", true);
  S.paper = await getJSON("data/paper.json", true);
  S.earn = await getJSON("data/earnings.json", true);
  S.today = await getJSON("data/today.json", true);
  [S.chips, S.est, S.quality, S.derivs, S.events] = await Promise.all([getJSON("data/chips_tw.json", true), getJSON("data/estimates_us.json", true), getJSON("data/quality.json", true), getJSON("data/derivs.json", true), getJSON(eventsURL(), true)]);
  S.settings = store.get("vs_settings_v1", null) || defaultSettings();
  // 舊的瀏覽器設定沒有新規則的條件：補上預設值
  for (const x of allFilters()) if (!S.settings.filters[x.id]) S.settings.filters[x.id] = { enabled: x.enabled !== false, value: x.value };
  // 排名指標跟著設定檔更新（例如 roe → roe_5y）；保留你改過、而且仍存在的權重
  { const dw = defaultSettings().weights, old = S.settings.weights || {};
    S.settings.weights = Object.fromEntries(Object.keys(dw).map((k) => [k, k in old ? old[k] : dw[k]])); }
  $("#updated").textContent = `資料更新：${S.meta.updated}（台北時間）　USD/TWD ${(+S.meta.fx_usdtwd).toFixed(2)}`;
  bindUI();
  route();
  window.addEventListener("hashchange", route);
}

function defaultSettings() {
  const f = {};
  for (const x of [...((S.cfg.industry_rules || {}).filters || []), ...S.cfg.hard_filters]) f[x.id] = { enabled: x.enabled !== false, value: x.value };
  const w = {};
  for (const grp of Object.values(S.cfg.ranking.bottom_up)) Object.assign(w, grp);
  w.top_down = S.cfg.ranking.top_down;
  return { filters: f, weights: w };
}

// ---------------------------------------------------------------- 路由
function route() {
  const [v0, ...args] = (location.hash || "#overview").slice(1).split("/");
  const views = { overview: renderOverview, screener: renderScreener, chart: () => renderChartView(args), etf: renderEtf, backtest: renderBacktest,
                  paper: renderPaper, signals: renderSignals, earnings: renderEarnings, watchlist: renderWatchlist, rules: renderRules };
  const v = views[v0] ? v0 : "overview";
  $$(".view").forEach((el) => el.classList.toggle("on", el.id === "view-" + v));
  $$("#tabs a").forEach((a) => a.classList.toggle("on", a.getAttribute("href") === "#" + v));
  const inMore = ["etf", "backtest", "paper", "signals", "watchlist", "rules"].includes(v);
  $$("#bnav a").forEach((a) => a.classList.toggle("on", a.getAttribute("href") === "#" + v));
  $("#moreBtn") && $("#moreBtn").classList.toggle("on", inMore);
  $("#moreSheet") && ($("#moreSheet").hidden = true);
  if (S.kc && v !== "chart") { S.kc.destroy(); S.kc = null; }
  views[v]();
}

// ---------------------------------------------------------------- 外觀（三種主題＋自動）
const SKINS = [["auto", "自動", "手機深色時用 A、淺色時用 C"], ["A", "A 深色看盤", "深色底、數字密集"], ["B", "B 清爽卡片", "白底、大數字"], ["C", "C 柔和儀表板", "米色底、深藍重點卡片"]];
const darkMQ = matchMedia("(prefers-color-scheme: dark)");
function applySkin(rerender = true) {
  const p = store.get("vs_skin", "auto");
  document.documentElement.dataset.skin = p === "auto" ? (darkMQ.matches ? "A" : "C") : p;
  const meta = document.querySelector('meta[name="theme-color"]'); if (meta) meta.setAttribute("content", cssVar("--bg"));
  if (rerender && S.cfg) { if (S.kc) S.kc.redraw(); route(); }
}
function skinMenu() {
  const p = store.get("vs_skin", "auto"), pop = $("#skinPop");
  pop.innerHTML = `<h4>外觀（這台裝置）</h4>${SKINS.map(([k, l, d]) => `<button class="skin-opt ${k === p ? "on" : ""}" data-skin="${k}"><span class="sw sw-${k}"><i></i><i></i></span><span><b>${l}</b><small>${d}</small></span></button>`).join("")}`;
  $$("[data-skin]", pop).forEach((b) => (b.onclick = () => { store.set("vs_skin", b.dataset.skin); pop.hidden = true; applySkin(); }));
}
function initTheme() {
  applySkin(false);
  darkMQ.addEventListener("change", () => { if (store.get("vs_skin", "auto") === "auto") applySkin(); });
  $("#themeBtn").onclick = (e) => { e.stopPropagation(); const pop = $("#skinPop"); if (pop.hidden) skinMenu(); pop.hidden = !pop.hidden; };
  document.addEventListener("click", (e) => { const pop = $("#skinPop"); if (!pop.hidden && !pop.contains(e.target)) pop.hidden = true; });
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
  const gs = rowGroups(r);
  if (S.settings.useOverrides !== false && gs.length) {
    for (const [g, ov] of Object.entries(activeOverrides(mkt))) {
      if (!gs.includes(g) || !ov[f.id]) continue;
      const o = ov[f.id];
      if ("metric" in o) metric = o.metric;
      if ("value" in o) t = thr(o.value, mkt);
      if ("enabled" in o) enabled = !!o.enabled && !!st.enabled;
    }
  }
  return { metric, t, enabled };
}

function pctRankNull(vals) {
  // 平均名次百分位（同 pandas rank(pct=True)）；null 保持 null
  const p = pctRank(vals);
  return vals.map((v, i) => (v == null || !isFinite(v) ? null : p[i]));
}
function peerPct(rows, metric, col) {
  const by = {};
  rows.forEach((r, i) => (by[r[col] ?? "all"] ||= []).push(i));
  const out = rows.map(() => null);
  for (const idx of Object.values(by)) {
    const pr = pctRankNull(idx.map((i) => rows[i][metric]));
    idx.forEach((i, k) => (out[i] = pr[k]));
  }
  return out;
}

function runScreen(mkt) {
  S.curMkt = mkt;
  const rows = S.stocks[mkt].map((r) => ({ ...r }));
  const isNew = ruleSet() === "new";
  const fin = finAllowed(mkt);
  for (const r of rows) if (r.template) r.fin_allowed = r.template !== "financial" || fin;
  const pcol = isNew ? "peer_group" : "industry";
  const fs = activeFilters().filter((f) => S.settings.filters[f.id]?.enabled);
  for (const f of fs) {
    if (f.op.startsWith("peer_pct")) {
      // 同業百分位：在同市場、同產業別（家數不足時同模板）裡排第幾，0 = 最低、1 = 最高
      const op = f.op.slice(8);
      const res = rows.map((r) => resolveRule(f, r, mkt));
      const cache = {};
      rows.forEach((r, i) => {
        const { metric, t, enabled } = res[i];
        r["_m_" + f.id] = metric; r["_t_" + f.id] = t;
        if (!enabled || t == null) { r["f_" + f.id] = true; r["_off_" + f.id] = !enabled; return; }
        const pc = (cache[metric] ||= peerPct(rows, metric, pcol));
        const x = pc[i];
        r["_pct_" + f.id] = x;
        r["f_" + f.id] = x == null ? !!f.na_pass : OPS[op](x, t);
      });
    } else if (f.op === "<=peer") {
      const k = f.peer_k_std ?? 1, minG = f.peer_min_group ?? 5;
      const by = {};
      for (const r of rows) if (r[f.metric] != null) (by[r[pcol]] ||= []).push(r[f.metric]);
      const all = rows.map((r) => r[f.metric]).filter((x) => x != null);
      const allLim = median(all) + k * stdev(all);
      const lim = {};
      for (const [g, a] of Object.entries(by)) lim[g] = a.length >= minG ? median(a) + k * stdev(a) : allLim;
      for (const r of rows) {
        const { enabled } = resolveRule(f, r, mkt);
        const L = lim[r[pcol]] ?? allLim;
        r._peer = L;
        if (!enabled) { r["f_" + f.id] = true; r["_off_" + f.id] = true; continue; }
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
  // 排名：每個指標在全市場（新規則：同業群組）的百分位加權 → 分數代表贏過全市場百分之幾
  const passed = rows.filter((r) => r.pass);
  const W = S.settings.weights;
  const keys = Object.keys(W).filter((k) => k !== "top_down");
  const tot = keys.reduce((s, k) => s + (+W[k] || 0), 0) + (+W.top_down || 0);
  const pcts = {};
  {
    const gcol = isNew ? pcol : "__all__";
    const pk = {};
    for (const k of keys) pk[k] = peerPct(rows, k, gcol);
    const pep = peerPct(rows, "ep", gcol);
    const pos = new Map(rows.map((r, i) => [r, i]));
    const isFin = (r) => (isNew ? r.template === "financial" : !!r.is_financial);
    for (const k of keys) pcts[k] = passed.map((r) => {
      const i = pos.get(r);
      let v = pk[k][i];
      if (isFin(r)) v = k === "earnings_yield" ? pep[i] : (k === "roe" || k === "roe_5y" || k === "book_to_price") ? v : null;
      return v == null ? 0.5 : v;
    });
  }
  const bon = S.cfg.ranking.bonuses || {};
  passed.forEach((r, i) => {
    let s = 0;
    for (const k of keys) s += (+W[k] || 0) * pcts[k][i] * 100;
    s += (+W.top_down || 0) * (r.top_down_score ?? 50);
    r.bu = keys.length ? (s - (+W.top_down || 0) * (r.top_down_score ?? 50)) / Math.max(tot - (+W.top_down || 0), 1e-9) : 0;
    r.score = tot ? s / tot : 0;
    if (r.cf_type === "甲" && !(isNew && r.template === "financial")) r.score += bon.cf_type_a || 0;
    if (r.cfo_to_ni != null && r.cfo_to_ni >= 1) r.score += bon.cfo_to_ni_100 || 0;
  });
  const cc = S.cfg.top_down.cross_check;
  const buR = pctRank(passed.map((r) => -r.bu));
  passed.forEach((r, i) => { r.cross = buR[i] <= cc.bottom_up_top && (r.top_down_score ?? 0) >= 100 * (1 - cc.sector_top); });
  for (const r of rows) if (!r.pass) r.score = null;
  return rows;
}

// ---------------------------------------------------------------- 總覽
function renderToday() {
  const T = S.today;
  const md = (d) => (d ? `${+d.slice(5, 7)}/${+d.slice(8, 10)}` : "");
  if (!T) { $("#ovToday").innerHTML = ""; return; }
  const items = T.items || [];
  const big = items.length === 1 ? items[0].head : T.headline === "今天不用動作" ? "✅ 今天不用動作" : T.headline === "本月下單日" ? "🧾 本月下單日" : "🛑 有停損要賣";
  const list = items.length === 1 && items[0].head.startsWith("✅") ? "" : `<ul>${items.map((x) => `<li>${esc(x.head)}<span class="why">為什麼：${esc(x.why)}</span></li>`).join("")}</ul>`;
  $("#ovToday").innerHTML = `<div class="hero"><div class="k"><span>今天要做的事（${md(T.date)}）</span><span>下次下單 ${md(T.next_order)}</span></div>
    <div class="v">${esc(big)}</div>${items.length === 1 ? `<div class="note">${esc(items[0].why)}</div>` : list}
    ${T.investing ? "" : `<div class="note" style="margin-top:6px">還沒開始投入真錢：停損和下單先當練習。</div>`}</div>`;
}
// 市場重大事件：每 30 分鐘由「市場事件偵測」寫到 events 分支，網站直接讀（不用等重新部署）
function eventsURL() {
  if (location.hostname.endsWith("github.io")) {
    const owner = location.hostname.split(".")[0], repo = location.pathname.split("/")[1] || "stock-screener";
    return `https://raw.githubusercontent.com/${owner}/${repo}/events/market_events.json`;
  }
  return "data/market_events.json";          // 本機預覽
}
function evMore(e) {
  try { return Array.isArray(e.more) ? e.more : (e.more ? JSON.parse(String(e.more).replace(/'/g, '"')) : []); } catch (_) { return []; }
}
function evItem(e) {
  const more = evMore(e);
  return `<div class="ev ${e.level === "重大" ? "major" : ""}"><div class="ev-top"><span class="pill ${e.level === "重大" ? "neg" : ""}">${esc(e.level)}</span> <b>${esc(e.label)}</b> <span class="hint">${esc(e.time)}${e.category ? "｜" + esc(e.category) : ""}</span></div>
    <div>${e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title)}</a>` : esc(e.title)}${e.source ? ` <span class="hint">（${esc(e.source)}）</span>` : ""}</div>
    ${more.length ? `<div class="hint">${more.slice(0, 3).map((m) => esc(m.title) + "（" + esc(m.source || "") + "）").join("<br>")}</div>` : ""}</div>`;
}
function renderEventsBanner() {
  const el = $("#ovEvents");
  if (!el) return;
  const E = (S.events && S.events.events) || [];
  const cut = Date.now() - 48 * 3600e3;
  const seen = new Set();   // 同一類事件（例如多家媒體報導國安基金）只算一件
  const recent = E.filter((e) => e.level === "重大" && Date.parse(String(e.time).replace(" ", "T") + ":00+08:00") >= cut && !seen.has(e.label) && seen.add(e.label));
  el.innerHTML = recent.length ? `<div class="card ev-banner"><b>⚠️ 過去 48 小時有 ${recent.length} 件重大市場事件</b>
    <div>${recent.slice(0, 3).map((e) => `${esc(e.label)}：${esc(e.title)}`).join("<br>")}</div>
    <a href="#signals">看全部 ›</a></div>` : "";
}
function renderEvents() {
  const V = S.events, list = $("#evList");
  if (!list) return;
  if (!V) { $("#evMeta").textContent = "事件資料還沒產生（「市場事件偵測」第一次執行後就會出現）。"; list.innerHTML = ""; $("#evRead").innerHTML = ""; return; }
  $("#evMeta").textContent = `每 30 分鐘自動檢查（含晚上、週末），更新：${V.updated}。「重大」＝確定發生（立刻 Email／LINE 通知）；「注意」＝傳聞、評論或數字警報（每天早上彙整一封）。分級靠標題用字判斷，可能有誤。`;
  const R = V.reading || {}, t = R["^TWII"], u = R["^GSPC"], v = R["^VIX"], f = R.flows || {};
  const tile = (k, val, d, bad) => `<div class="tile"><div class="k">${k}</div><div class="v ${bad ? "neg" : ""}" style="font-size:18px">${val}</div><div class="d">${d || ""}</div></div>`;
  $("#evRead").innerHTML = `<div class="tiles">${[
    t && tile("台股單日", fmt.pct(t.day_chg), `${t.date}　距一年高點 ${fmt.pct(t.from_1y_high)}`, t.day_chg <= -0.03 || t.from_1y_high <= -0.2),
    u && tile("美股 S&P 500 單日", fmt.pct(u.day_chg), `${u.date}　距一年高點 ${fmt.pct(u.from_1y_high)}`, u.day_chg <= -0.03 || u.from_1y_high <= -0.2),
    v && tile("VIX 恐慌指數", (+v.price).toFixed(1), v.date, v.price >= 30),
    f.foreign_net != null && tile("外資買賣超", `${Math.round(f.foreign_net).toLocaleString()} 億`, f.date, f.foreign_net <= -800),
    f.margin_chg != null && tile("融資餘額變化", `${Math.round(f.margin_chg).toLocaleString()} 億`, `剩 ${Math.round(f.margin || 0).toLocaleString()} 億`, f.margin_chg <= -200),
  ].filter(Boolean).join("")}</div>`;
  const lv = S.evLevel || "all";
  $$("#evSeg button").forEach((b) => { b.classList.toggle("on", b.dataset.l === lv); b.onclick = () => { S.evLevel = b.dataset.l; renderEvents(); }; });
  const E = (V.events || []).filter((e) => lv === "all" || e.level === lv).slice(0, 60);
  list.innerHTML = E.length ? E.map(evItem).join("") : `<p class="hint">最近 60 天沒有${lv === "all" ? "" : "「" + lv + "」"}事件。</p>`;
}

function renderOverview() {
  renderEventsBanner();
  renderToday();
  const m = S.meta, c = m.counts || {};
  const bt = S.bt && S.bt.stats_full ? S.bt.stats_full["策略（扣費後）"] : null;
  const tiles = [
    ["台股通過", c.tw ? c.tw.passed + " 檔" : "–", c.tw ? `資料 ${c.tw.with_data} / 清單 ${c.tw.universe}` : ""],
    ["美股通過", c.us ? c.us.passed + " 檔" : "–", c.us ? `資料 ${c.us.with_data} / 清單 ${c.us.universe}` : ""],
    ["ETF 通過", c.etf ? c.etf.passed + " 檔" : "–", c.etf ? `候選 ${c.etf.universe} 檔` : ""],
    ["回測年化（扣費後）", bt ? fmt.pct(bt.cagr) : "–", S.bt ? `${S.bt.start} ~ ${S.bt.end}` : "尚未回測"],
  ];
  if (m.tw_month_rev && m.tw_month_rev.have < m.tw_month_rev.total)
    tiles.push(["台股月營收回填中", `${Math.round((100 * m.tw_month_rev.have) / m.tw_month_rev.total)}%`, `${m.tw_month_rev.have} / ${m.tw_month_rev.total} 家（每天用剩餘額度補）`]);
  if (m.tw_backfill && m.tw_backfill.have < m.tw_backfill.total)
    tiles.push(["台股財報回填中", `${Math.round((100 * m.tw_backfill.have) / m.tw_backfill.total)}%`, `${m.tw_backfill.have} / ${m.tw_backfill.total} 家`]);
  $("#ovTiles").innerHTML = tiles.map(([k, v, d]) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="d">${d}</div></div>`).join("");

  const H = (S.port && S.port.holdings) || [];
  const names = { core: "核心（全球大盤）", tw: "台股個股", us: "美股個股", etf: "ETF" };
  let html = "";
  for (const sl of ["core", "tw", "us", "etf"]) {
    const hs = H.filter((h) => h.sleeve === sl);
    const w = hs.reduce((a, h) => a + (h.weight || 0), 0);
    if (!hs.length && (sl === "core" || sl === "etf")) continue;
    const nStock = hs.filter((h) => h.kind !== "fill").length;
    const fillH = hs.find((h) => h.kind === "fill");
    html += `<div class="sleeve-h"><span>${names[sl]}　${fmt.pct(w, 0)}</span><span class="hint">${sl === "core" ? "每月定期定額" : `${nStock} 檔${nStock ? "，每檔 " + fmt.pct(hs.find((h) => h.kind !== "fill").weight, 2) : ""}${fillH ? `；大盤補位 ${fmt.pct(fillH.weight, 1)}` : ""}`}</span></div>`;
    if (!hs.length) { html += `<p class="hint">目前沒有符合全部條件的標的 → 這部分持有現金</p>`; continue; }
    html += `<div class="table-wrap"><table class="data compact"><tbody>${hs.map((h) => {
      const click = h.kind === "stock" && h.score != null;
      const stopCells = (sl === "tw" || sl === "us") && h.kind === "stock" ? `<td class="num ${h.since_entry == null ? "" : h.since_entry >= 0 ? "pos" : "neg"}" title="自買進（含息）">${h.since_entry != null ? fmt.pct(h.since_entry) : ""}</td><td class="num hint" title="距離停損">${h.stop_dist != null ? (h.stop_dist < 0.03 ? "⚠️ " : "") + "距停損 " + fmt.pct(h.stop_dist) : ""}</td>` : `<td></td><td></td>`;
      return `<tr class="${click ? "clickable" : ""}" data-m="${sl}" data-id="${esc(h.id)}"><td>${h.cross_ok ? "⭐ " : ""}<b>${esc(h.id)}</b></td><td>${h.kind === "fill" ? esc(h.name) : nameCell(h.kind === "stock" ? h.market : "etf", h.id, h.name)}</td><td class="hint">${esc(h.industry || "")}</td><td class="num">${fmt.pct(h.weight, 1)}</td><td class="num">${h.score != null ? h.score.toFixed(1) : ""}</td>${stopCells}</tr>`;
    }).join("")}</tbody></table></div>`;
  }
  const P = S.port || {};
  const slc = P.stop_loss || {};
  if (slc.enabled) {
    const st = (P.stopped || []).map((x) => `<li>🛑 <b>${esc(x.id)}</b> ${nameInline(x.market, x.id, x.name)}：${fmt.pct(x.ret)} → 請賣出</li>`).join("");
    const cd = (P.cooldown || []).map((x) => `${esc(x.id)}（至 ${esc(x.until)}）`).join("、");
    html += `<p class="hint">停損：${slc.basis === "vol" ? `個股自持有期間最高點回檔超過「月波動度 × ${slc.vol_mult}」（${fmt.pct(slc.vol_min, 0)}～${fmt.pct(slc.vol_max, 0)}）即賣出` : `個股自${slc.basis === "trailing" ? "持有期間最高點" : "買進價"}下跌 ${fmt.pct(slc.pct, 0)} 即賣出`}，含息計算；ETF 不設停損。實際成本可填在 config/positions.yaml。</p>` +
      (st ? `<ul>${st}</ul>` : "") + (cd ? `<p class="hint">冷卻期（暫不買回）：${cd}</p>` : "");
  }
  $("#ovPortfolio").innerHTML = html;
  $$("#ovPortfolio tr.clickable").forEach((tr) => (tr.onclick = () => openDetail(tr.dataset.m, tr.dataset.id)));

  const rg = m.regime || {};
  const rtxt = (k, label) => {
    const r = rg[k];
    if (!r || r.bull == null) return `<div>${label}：資料不足</div>`;
    return `<div>${label}：<b class="${r.bull ? "ok" : "ng"}">${r.bull ? "▲ 多頭（指數在 10 個月均線之上）" : "▼ 偏空（指數跌破 10 個月均線）"}</b> <span class="hint">指數 ${r.last.toFixed(0)}／均線 ${r.ma.toFixed(0)}</span></div>`;
  };
  const cr = m.credit;
  const ctxt = cr && cr.available !== undefined ? `<div>企業信用：<b class="${cr.risk_off ? "ng" : "ok"}">${cr.risk_off ? "▼ 偏空（信用轉差）" : "▲ 正常"}</b> <span class="hint">${esc(cr.text || "")}${cr.enabled ? "；已啟用減碼" : "；目前只提示、不調整部位"}</span></div>` : "";
  const cy = m.cycle || {};
  const PH = { recovery: "復甦", expansion: "擴張", slowdown: "趨緩", recession: "衰退" };
  const PHCLS = { recovery: "ok", expansion: "ok", slowdown: "ng", recession: "ng" };
  const ptxt = (k, label, src) => {
    const c = cy[k];
    if (!c || !c.phase) return "";
    const rule = (cy.rules || {})[c.phase] || {};
    const ruleTxt = rule.rule_set === "new" ? `市場 × 產業規則${rule.financials ? "＋金融股" : ""}` : "原本 31 條規則";
    const strip = (c.history || []).slice(-24).map((h) => `<span class="phs ph-${h.phase || "na"}" title="${esc(h.m)}：${esc(PH[h.phase] || "–")}（${h.level}）"></span>`).join("");
    return `<div>${label}景氣階段：<b class="${PHCLS[c.phase] || ""}">${PH[c.phase]}</b> <span class="hint">${src} ${(+c.level).toFixed(2)}（${c.data_month}，近 3 月 ${c.chg3 >= 0 ? "↑" : "↓"} ${Math.abs(c.chg3).toFixed(2)}）${c.signal ? "；" + esc(c.signal) : ""}；此階段用${ruleTxt}${cy.enabled ? "" : "（模組尚未啟用）"}</span><div class="phstrip">${strip}</div></div>`;
  };
  let cytxt = ptxt("tw", "台股", "國發會領先指標") + ptxt("us", "美股", "OECD 領先指標");
  if (cytxt) cytxt += `<p class="hint">色條為近 24 個月：<span class="phs ph-recovery"></span> 復甦　<span class="phs ph-expansion"></span> 擴張　<span class="phs ph-slowdown"></span> 趨緩　<span class="phs ph-recession"></span> 衰退</p>`;
  $("#ovRegime").innerHTML = rtxt("tw", "台股加權") + rtxt("us", "美股 S&P 500") + ctxt + cytxt + `<p class="hint">產業分數 = 6 個月相對強弱 50% + 產業估值（中位數 EBIT/EV）50%，占個股綜合分數 ${fmt.pct(S.cfg.ranking.top_down, 0)}。</p>`;
  renderSectors();
  renderOrders();
  const N = S.news || { news: [], events: [] };
  const ev = (N.events || []).map((e) => `<li>${e.veto ? "⛔" : "⚠️"} <b>${esc(e.id)}</b> ${nameInline(e.market, e.id, e.name)}：${esc(e.text)}</li>`).join("");
  const nw = (N.news || []).slice().sort((a, b) => (b.negative - a.negative) || String(b.date).localeCompare(String(a.date))).slice(0, 40)
    .map((n) => `<li>${n.negative ? "⚠️ " : ""}<b>${esc(n.id)}</b> ${esc(zhInfo(n.market === "etf" ? "etf" : n.market || mktOf(n.id), n.id, "").n)} ${n.source ? `<span class="pill">${esc(n.source)}</span> ` : ""}<span class="hint">${esc(n.date)}</span> ${n.url ? `<a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a>` : esc(n.title)}</li>`).join("");
  const wsj = (N.wsj_headlines || []).slice(0, 15).map((n) => `<li>${n.negative ? "⚠️ " : ""}<span class="pill">${esc(n.category)}</span> <a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a></li>`).join("");
  $("#ovNews").innerHTML = ((ev ? `<h3>重大事件</h3><ul>${ev}</ul>` : "") + (nw ? `<h3>近 3 天新聞（含華爾街日報提及持股的報導）</h3><ul>${nw}</ul>` : "") + (wsj ? `<h3>華爾街日報頭條（由上而下參考）</h3><ul>${wsj}</ul><p class="hint">標題與連結來自 WSJ 公開 RSS；點開後用你的訂閱帳號閱讀全文。</p>` : "")) || `<p class="hint">目前沒有資料</p>`;
}
function renderSectors() {
  const sec = (S.meta.sectors || {})[S.secMkt] || [];
  if (!sec.length) { $("#ovSectors").innerHTML = `<p class="hint">尚無資料</p>`; return; }
  const max = 100;
  $("#ovSectors").innerHTML = `<div class="table-wrap" style="max-height:360px;overflow:auto"><table class="data compact"><thead><tr><th>產業</th><th class="num">家數</th><th class="num">6月報酬</th><th class="num">EBIT/EV</th><th>分數</th></tr></thead><tbody>${sec.map((s) => `
    <tr><td>${esc(s.industry)}</td><td class="num">${s.n}</td><td class="num ${s.mom >= 0 ? "pos" : "neg"}">${fmt.pct(s.mom)}</td><td class="num">${fmt.pct(s.val)}</td>
    <td><span class="bar" style="width:${(70 * s.score) / max}px"></span> <span class="hint">${s.score.toFixed(0)}</span></td></tr>`).join("")}</tbody></table></div>`;
}

function renderOrders() {
  const O = S.orders;
  if (!O || !O.orders) { $("#ordNote").textContent = "尚無資料"; return; }
  const fx = +O.fx_usdtwd;
  $("#ordNote").textContent = `${O.month} 本月投入 ${fmt.ntd(O.monthly_contribution_twd || 0)}（在 config/strategy.yaml 的 monthly_contribution_twd 修改）＋上月剩餘 ${fmt.ntd(O.cash_before_twd || 0)}${O.proceeds_twd ? `＋賣出所得 ${fmt.ntd(O.proceeds_twd)}` : ""}；用來補足低於目標比例的部位，原則上不賣股。USD/TWD ${fx.toFixed(2)}。${O.note}`;
  const names = { core: "核心", tw: "台股", us: "美股", etf: "ETF" };
  const act = (o) => {
    if (o.action === "sell") return `<b class="neg">賣出全部</b><br><span class="hint">${esc(o.reason || "")}</span>`;
    if (o.action === "buy") {
      const sh = Number.isInteger(o.shares) ? o.shares.toLocaleString() : o.shares;
      const amt = o.market === "us" ? `US$${(+o.amount_local).toFixed(2)}` : fmt.ntd(o.amount_twd);
      return `<b class="pos">買 ${sh} 股</b>（約 ${amt}）<br><span class="hint">${esc(o.reason || "")}</span>`;
    }
    return `<span class="hint">${esc(o.reason || "不動")}</span>`;
  };
  const order = { sell: 0, buy: 1, hold: 2 };
  const rows = O.orders.slice().sort((a, b) => order[a.action] - order[b.action] || (a.sleeve > b.sleeve ? 1 : -1));
  $("#ordTable").innerHTML = `<thead><tr><th>部位</th><th>代號</th><th>名稱</th><th class="num">參考價</th><th class="num">目標比重</th><th class="num">下單後比重</th><th>本月動作</th></tr></thead><tbody>${rows.map((o) => `
    <tr><td>${names[o.sleeve] || o.sleeve}${o.kind === "fill" ? "<br><span class=\"hint\">大盤補位</span>" : ""}</td><td><b>${esc(o.id)}</b></td><td>${o.kind === "fill" ? esc(o.name || "") : nameCell(o.kind === "stock" ? o.market : "etf", o.id, o.name)}</td><td class="num">${o.price == null ? "–" : o.price + " " + (o.currency || "")}</td>
    <td class="num">${fmt.pct(o.target_weight, 1)}</td><td class="num">${o.weight_after == null ? "–" : fmt.pct(o.weight_after, 1)}</td><td>${act(o)}</td></tr>`).join("")}
    <tr class="dim"><td colspan="6">下單後剩餘現金（留到下個月）</td><td>${fmt.ntd(O.cash_after_twd || 0)}</td></tr></tbody>`;
  $("#ordCsv").onclick = () => {
    rows.forEach((o) => { const z = zhInfo(o.kind === "stock" ? o.market : "etf", o.id, o.name); o.name_zh = z.n; o.desc_zh = z.d; });
    const cols = ["sleeve", "kind", "id", "name", "name_zh", "desc_zh", "currency", "price", "action", "shares", "amount_local", "amount_twd", "target_weight", "weight_after", "reason"];
    const csv = [cols.join(",")].concat(rows.map((o) => cols.map((c) => (typeof o[c] === "string" ? `"${o[c]}"` : o[c] ?? "")).join(","))).join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv" }));
    a.download = `orders_${O.month}.csv`; a.click();
  };
}

// ---------------------------------------------------------------- 個股篩選
const STOCK_COLS = [
  ["rank", "#", (r, i) => i + 1, "num"], ["id", "代號", (r) => `<b>${esc(r.id)}</b>`],
  ["name", "名稱", (r, i, m) => nameCell(m, r.id, r.name)], ["industry", "產業", (r) => `<span class="hint">${esc(r.industry)}</span> ${groupTags(r)}`],
  ["score", "綜合分數", (r) => (r.score == null ? "–" : `<span title="各指標在全市場${ruleSet() === "new" ? "（同業群組）" : ""}的百分位加權：約等於贏過百分之幾的公司；另有現金流加分">${r.cross ? "⭐ " : ""}${r.score.toFixed(1)}</span>`), "num"],
  ["close", "收盤", (r) => fmt.price(r.close), "num"], ["market_cap", "市值", (r, i, m) => fmt.money(r.market_cap, m), "num"],
  ["pe", "本益比", (r) => fmt.x(r.pe), "num"], ["pb", "P/B", (r) => fmt.x(r.pb, 2), "num"],
  ["fair_base", "合理價", (r) => (r.fair_base == null ? "–" : fmt.price(r.fair_base)), "num"],
  ["mos", "安全邊際", (r) => (r.mos == null ? "–" : `<span class="${r.mos >= 0.3 ? "pos" : r.mos <= -0.2 ? "neg" : ""}" title="${esc(r.val_label || "")}（可信度 ${esc(r.val_conf || "")}）">${fmt.pct(r.mos, 0)}</span>`), "num"],
  ["earnings_yield", "EBIT/EV", (r) => fmt.pct(r.earnings_yield), "num"], ["fcf_yield", "FCF殖利率", (r) => fmt.pct(r.fcf_yield), "num"],
  ["cash_ratio", "現金/資產", (r) => fmt.pct(r.cash_ratio), "num"], ["equity_ratio", "權益比", (r) => fmt.pct(r.equity_ratio), "num"],
  ["op_margin", "營益率", (r) => fmt.pct(r.op_margin), "num"], ["fcf_to_ni", "FCF/淨利", (r) => fmt.pct(r.fcf_to_ni, 0), "num"],
  ["roe", "ROE", (r) => fmt.pct(r.roe), "num"], ["cf_type", "現金流", (r) => esc(r.cf_type || "–")], ["top_down_score", "產業分數", (r) => (r.top_down_score == null ? "–" : r.top_down_score.toFixed(0)), "num"],
  ["failed", "狀態", (r) => (r.pass ? `<span class="ok">✔ 通過</span>` : `<span class="ng" title="${esc(r.failed.map(fname).join("、"))}">✘ ${r.failed.length} 條</span> <span class="hint">${esc(r.failed.slice(0, 2).map(fname).join("、"))}${r.failed.length > 2 ? "…" : ""}</span>`)],
];
function fname(id) { const f = activeFilters().find((x) => x.id === id) || allFilters().find((x) => x.id === id) || (S.cfg.etf.hard_filters || []).find((x) => x.id === id); return f ? f.label.replace(/^[\d]+[ab']?\.\s*/, "").split("：")[0] : id; }

// 外部連結：系統沒有的資料（例如即時股價、新聞、完整財報）一鍵查；美股可直接在 Mac／iPhone 的「股市」App 打開
function extLinks(mkt, id) {
  const raw = String(id || "").trim().toUpperCase(), tw = mkt === "tw" || /\.TWO?$/.test(raw);
  const code = raw.replace(/\.TWO?$/, "");
  if (!code) return "";
  const L = tw
    ? [["Yahoo 股市", `https://tw.stock.yahoo.com/quote/${encodeURIComponent(code)}`], ["Goodinfo", `https://goodinfo.tw/tw/StockDetail.asp?STOCK_ID=${encodeURIComponent(code)}`]]
    : [["在「股市」App 開啟", `stocks://?symbol=${encodeURIComponent(raw)}`], ["Yahoo Finance", `https://finance.yahoo.com/quote/${encodeURIComponent(raw)}`]];
  return `<p class="ext-links">${L.map(([t, u]) => `<a href="${u}" target="_blank" rel="noopener">${t} ↗</a>`).join("")}</p>`;
}

function renderScreener() {
  buildFilterPanel();
  const rows = runScreen(S.mkt);
  S.lastRows = rows;
  const q = $("#search").value.trim().toLowerCase();
  const near = $("#showNear").checked, all = $("#showAll").checked;
  // 有輸入搜尋字時在全部股票裡找（不只通過條件的），才找得到例如波克夏這種沒通過篩選的公司
  let list = q ? rows.slice() : rows.filter((r) => all || r.pass || (near && r.failed.length <= 2));
  if (q) list = list.filter((r) => `${r.id} ${r.name} ${r.industry} ${zhText(S.mkt, r.id)}`.toLowerCase().includes(q));
  const { key, dir } = S.sort;
  list.sort((a, b) => {
    if (key === "failed") return dir * (a.failed.length - b.failed.length);
    let x = a[key], y = b[key];
    if (key === "score") { x = x ?? -1e9; y = y ?? -1e9; if (x === y) return a.failed.length - b.failed.length; }
    if (x == null) return 1; if (y == null) return -1;
    return typeof x === "string" ? dir * x.localeCompare(y) : dir * (x - y);
  });
  const passN = rows.filter((r) => r.pass).length;
  const rsNote = (ruleMode() === "cycle" ? "依景氣階段：" : "") + (ruleSet() === "new" ? "市場 × 產業規則" : "原本 31 條規則");
  $("#screenSummary").textContent = `${S.mkt === "tw" ? "台股" : "美股"} ${rows.length} 檔有完整資料，${passN} 檔通過全部條件（${rsNote}${ruleMode() !== liveMode() ? "，預覽中，實際下單用另一種設定" : ""}）；部位取排名前 ${S.cfg.portfolio.max_holdings_per_stock_sleeve} 檔。點任一列看詳情。`;
  const shown = list.slice(0, 600);
  $("#stockTable").innerHTML = `<thead><tr>${STOCK_COLS.map(([k, l, , c]) => `<th data-k="${k}" class="${c || ""}">${l}${S.sort.key === k ? (S.sort.dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr></thead>
  <tbody>${shown.map((r, i) => `<tr class="clickable ${r.pass ? "" : "dim"}" data-id="${esc(r.id)}">${STOCK_COLS.map(([, , f, c]) => `<td class="${c || ""}">${f(r, i, S.mkt)}</td>`).join("")}</tr>`).join("")}</tbody>`;
  if (!shown.length) $("#stockTable").innerHTML += q
    ? `<tbody><tr><td colspan="${STOCK_COLS.length}" class="hint" style="padding:20px">系統的股票池裡沒有「${esc(q.toUpperCase())}」（可能是太新、財報資料還沒抓到、不在台美主要交易所，或是 ETF：ETF 請到「ETF」頁）。可以直接查：${extLinks(S.mkt, q)}</td></tr></tbody>`
    : `<tbody><tr><td colspan="${STOCK_COLS.length}" class="hint" style="padding:20px">沒有股票符合目前條件。可勾選「顯示差 1–2 條的股票」看看是哪幾條卡住。</td></tr></tbody>`;
  $$("#stockTable th").forEach((th) => (th.onclick = () => {
    const k = th.dataset.k; if (k === "rank") return;
    S.sort = { key: k, dir: S.sort.key === k ? -S.sort.dir : (["id", "name", "industry", "pe", "pb", "failed"].includes(k) ? 1 : -1) };
    renderScreener();
  }));
  $$("#stockTable tr.clickable").forEach((tr) => (tr.onclick = () => openDetail(S.mkt, tr.dataset.id)));
}

function unitOf(f) {
  if ((f.op || "").startsWith("peer_pct")) return ["%（同業百分位）", 0.01];
  const t = METRICS[f.metric] ? METRICS[f.metric][1] : "x";
  if (t === "pctile") return ["%（百分位）", 0.01];
  if (t === "money") return S.mkt === "tw" ? ["億", 1e8] : ["百萬美元", 1e6];
  if (t === "pct") return ["%", 0.01];
  return ["", 1];
}
function buildFilterPanel() {
  S.curMkt = S.mkt;
  const fl = activeFilters().map((f) => {
    const st = S.settings.filters[f.id] || { enabled: true, value: f.value };
    let input = "";
    if (f.op === "<=peer") input = `<span class="hint">中位數 + ${f.peer_k_std}σ</span>`;
    else if (typeof thr(st.value, S.mkt) === "boolean") input = `<span class="hint">${thr(st.value, S.mkt) ? "是" : "否"}</span>`;
    else {
      const [u, s] = unitOf(f);
      const v = thr(st.value, S.mkt);
      input = `<span class="hint">${f.op.replace("peer_pct", "")}</span><input type="number" step="any" data-f="${f.id}" value="${+(v / s).toFixed(4)}"><span class="hint">${u}</span>`;
    }
    const tag = f.source ? `<span class="tag ${f.source}">${f.source === "user" ? "你的" : "補充"}</span>` : "";
    return `<div class="frow"><input type="checkbox" data-en="${f.id}" ${st.enabled ? "checked" : ""}><span class="lbl">${esc(f.label)}</span>${tag}${input}</div>`;
  }).join("");
  const live = liveMode(), md = ruleMode();
  const PHN = { recovery: "復甦", expansion: "擴張", slowdown: "趨緩", recession: "衰退" };
  const phNow = ((S.meta.cycle || {})[S.mkt] || {}).phase;
  const tag = (v) => (live === v ? "（目前下單用）" : "（預覽）");
  const rs = S.cfg.industry_rules ? `<div class="frow" style="grid-column:1/-1"><span class="lbl"><b>規則版本</b>　
    <label><input type="radio" name="ruleSet" value="old" ${md === "old" ? "checked" : ""}> 原本 31 條${tag("old")}</label>　
    <label><input type="radio" name="ruleSet" value="new" ${md === "new" ? "checked" : ""}> 市場 × 產業規則${tag("new")}</label>
    ${S.cfg.cycle ? `　<label><input type="radio" name="ruleSet" value="cycle" ${md === "cycle" ? "checked" : ""}> 依景氣階段${phNow ? `（目前${PHN[phNow]} → ${ruleSet() === "new" ? "產業規則" : "原本 31 條"}${finAllowed() ? "＋金融股" : ""}）` : ""}${tag("cycle")}</label>` : ""}</span></div>` : "";
  const ovText = ruleSet() === "new" ? "<b>套用產業模板覆寫</b>（循環、營建、金融、公用、防禦改用各自的條件，詳見「條件說明」）"
    : "<b>套用景氣循環但書</b>（循環股、非必需消費、必需消費、醫療、公用／電信、研發密集公司改用調整後門檻，詳見「條件說明」）";
  $("#filterList").innerHTML = rs + `<div class="frow" style="grid-column:1/-1"><input type="checkbox" id="useOv" ${S.settings.useOverrides !== false ? "checked" : ""}><span class="lbl">${ovText}</span></div>` + fl;
  $("#useOv").onchange = (e) => { S.settings.useOverrides = e.target.checked; saveSettings(); };
  $$("#filterList [name=ruleSet]").forEach((el) => (el.onchange = () => { S.settings.ruleSet = el.value; saveSettings(); }));
  const W = S.settings.weights;
  $("#weightList").innerHTML = Object.keys(W).map((k) => `<div class="frow"><span class="lbl">${k === "top_down" ? "由上而下（產業分數）" : mlabel(k)}</span><input type="number" step="0.01" min="0" data-w="${k}" value="${W[k]}"></div>`).join("");
  $$("#filterList [data-en]").forEach((el) => (el.onchange = () => { S.settings.filters[el.dataset.en].enabled = el.checked; saveSettings(); }));
  $$("#filterList [data-f]").forEach((el) => (el.onchange = () => {
    const f = activeFilters().find((x) => x.id === el.dataset.f);
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
  const cols = ["id", "name", "industry", "template_label", "peer_group", "score", "close", "market_cap", "fair_base", "mos", "pe", "pb", "earnings_yield", "fcf_yield", "cash_ratio", "equity_ratio", "current_ratio", "quick_ratio", "gross_margin", "op_margin", "op_to_gross", "nonop_share", "cfo_to_ni", "fcf_to_ni", "roe", "top_down_score", "failed"];
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
  if (mkt !== "etf") S.curMkt = mkt;
  const dlg = $("#detail");
  const row = mkt === "etf" ? S.etf.find((r) => r.ticker === id) : (S.lastRows && S.mkt === mkt ? S.lastRows : runScreen(mkt)).find((r) => r.id === id);
  { const z = zhInfo(mkt, id, row ? row.name : ""); $("#dTitle").textContent = `${id}　${z.n}${z.d ? "｜" + z.d : ""}`; }
  $("#dBody").innerHTML = `<p class="hint">載入中…</p>`;
  dlg.showModal();
  const d = await getJSON(`data/detail/${mkt}/${encodeURIComponent(id)}.json`, true);
  let html = extLinks(mkt, id);
  if (row && mkt !== "etf") {
    html += `<div class="tiles">${[["綜合分數", row.score != null ? row.score.toFixed(1) : "未通過"], ["本益比", fmt.x(row.pe)], ["P/B", fmt.x(row.pb, 2)], ["FCF 殖利率", fmt.pct(row.fcf_yield)], ["市值", fmt.money(row.market_cap, mkt)], ["產業", esc(row.industry)]].map(([k, v]) => `<div class="tile"><div class="k">${k}</div><div class="v" style="font-size:18px">${v}</div></div>`).join("")}</div>`;
    html += qualityNote(mkt, id);
    if (d && d.valuation) html += valuationCard(d.valuation, row, mkt);
    else if (row.fair_base != null) html += valuationCard(row, row, mkt);
    html += mkt === "tw" ? chipsCard(id) : estCard(id, row);
    const isNew = ruleSet() === "new";
    html += `<div class="card"><h2>條件檢查${isNew ? "（市場 × 產業規則）" : ""}</h2>${isNew && row.template ? `<p class="hint">產業模板：${tplTag(row)} ${esc(TEMPLATE_NOTE[row.template] || "")}；同業比較群組：${esc(row.peer_group || "")}（${row.peer_n ?? "–"} 家）</p>` : ""}<div class="checklist">${activeFilters().filter((f) => S.settings.filters[f.id]?.enabled).map((f) => {
      const ok = row["f_" + f.id];
      const m = row["_m_" + f.id] || f.metric;
      const peerOp = f.op.startsWith("peer_pct");
      let val = fmtMetric(m, row[m], mkt);
      if (f.op === "<=peer") val += ` ≤ ${fmt.pct(row._peer)}`;
      if (peerOp) val += `<span class="hint">（同業第 ${row["_pct_" + f.id] == null ? "–" : Math.round(100 * row["_pct_" + f.id])} 百分位）</span>`;
      const t = row["_t_" + f.id], dflt = thr((S.settings.filters[f.id] || {}).value, mkt);
      const word = isNew ? "產業覆寫" : "但書";
      let note = "";
      if (row["_off_" + f.id]) note = `<span class="hint">（${word}：不適用）</span>`;
      else if (f.op !== "<=peer" && (t !== dflt || m !== f.metric)) note = `<span class="hint">（${word}：${esc(mlabel(m))} ${f.op.replace("peer_pct", "同業百分位 ")} ${peerOp ? Math.round(100 * t) + "%" : fmtMetric(m, t, mkt)}）</span>`;
      return `<div><span class="${ok ? "ok" : "ng"}">${ok ? "✔" : "✘"}</span><span>${esc(f.label)} ${note}</span><span class="val">${val}</span></div>`;
    }).join("")}</div><p class="hint">財報期別：${esc(row.period_end || "")}（流量為近四季合計）${!isNew && rowGroups(row).length ? "　但書群組：" + groupTags(row) : ""}${row.event_flags ? "<br>⚠️ 事件：" + esc(row.event_flags) : ""}</p></div>`;
  } else if (row) {
    html += `<div class="card"><h2>ETF 條件檢查</h2><div class="checklist">${S.cfg.etf.hard_filters.map((f) => {
      const ok = !(row.failed || []).includes(f.id);
      return `<div><span class="${ok ? "ok" : "ng"}">${ok ? "✔" : "✘"}</span><span>${esc(f.label)}</span><span class="val">${fmtMetric(f.metric, row[f.metric], "us")}</span></div>`;
    }).join("")}</div></div>`;
  }
  if (!row && mkt !== "etf") html += qualityNote(mkt, id) + (mkt === "tw" ? chipsCard(id) : estCard(id, null));
  html += `<div class="card"><div class="fp-head"><h2>K 線</h2><a href="#chart/${mkt}/${encodeURIComponent(id)}" class="small" onclick="document.getElementById('detail').close()">在看盤頁開啟 ›</a></div><div id="dK"></div><div class="chart-box" id="dPriceBox" hidden><canvas id="dPrice"></canvas></div></div>`;
  if (!d) html += `<p class="hint">此標的沒有財報圖表（只有通過或差 1–2 條的股票、ETF 與追蹤清單會產生）。</p>`;
  else {
    if (d.quarters && d.quarters.length) {
      html += `<div class="grid2"><div class="card"><h2>獲利與現金流（單季）</h2><div class="legend" id="dCfLeg"></div><div class="chart-box"><canvas id="dCf"></canvas></div></div>
               <div class="card"><h2>毛利率與營益率（近四季）</h2><div class="legend" id="dMgLeg"></div><div class="chart-box"><canvas id="dMg"></canvas></div></div></div>`;
    }
  }
  $("#dBody").innerHTML = html;
  destroyChart("dPrice"); destroyChart("dCf"); destroyChart("dMg");
  const kdoc = await KC.load(mkt, id);
  if (S.dkc) { S.dkc.destroy(); S.dkc = null; }
  if (kdoc) S.dkc = KC.mount($("#dK"), { mkt, id, compact: true, ctx: kctx(mkt, id) });
  else if (!d) $("#dK").innerHTML = `<p class="hint">這檔還沒有 K 線資料（第一次會分幾天下載完）。</p>`;
  if (!d) return;
  if (!kdoc && d.prices && d.prices.length) {
    $("#dPriceBox").hidden = false;
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
  // 方案比較
  const V = bt.variants || {};
  if (Object.keys(V).length) {
    $("#varCard").hidden = false;
    const bf = bt.stats_full["混合基準(20/20/60)"], b5 = bt.stats_5y["混合基準(20/20/60)"];
    const gname = { stop: "停損規則（目前配置）", macro: "信用利差減碼（目前配置）", factor: "新因子與金融股（目前配置）", rules: "市場 × 產業規則（目前配置）", style: "選股風格與持股比重（同樣 VT 50%＋個股 50%）", alloc: "資產配置比較（買進價停損 15%）", other: "其他" };
    let lastG = null;
    const rows = Object.entries(V).map(([k, v]) => {
      const h = v.halves || {};
      const g = v.group || "other";
      const sep = g !== lastG ? `<tr class="dim"><td colspan="12"><b>${esc(gname[g] || g)}</b></td></tr>` : "";
      lastG = g;
      return sep + `<tr><td><b>${esc(k)}</b></td><td class="num">${fmt.pct(v.full.cagr)}</td><td class="num">${fmt.pct(v.y5.cagr)}</td><td class="num">${fmt.ntd(v.full.final_amount)}</td><td class="num">${fmt.pct(v.full.max_drawdown)}</td><td class="num">${fmt.x(v.full.sharpe, 2)}</td><td class="num">${fmt.pct(v.regimes.rolling12_beat_rate_bear, 0)}</td><td class="num">${fmt.pct(v.regimes.rolling12_beat_rate_bull, 0)}</td><td class="num">${h.h1 ? fmt.pct(h.h1.cagr) : "–"}</td><td class="num">${h.h2 ? fmt.pct(h.h2.cagr) : "–"}</td><td class="num">${v.avg_tw.toFixed(1)}／${v.avg_us.toFixed(1)}</td><td class="num">${v.n_stops != null ? v.n_stops : "–"}</td></tr>`;
    }).join("");
    const h0 = (Object.values(V)[0] || {}).halves || {};
    $("#varTable").innerHTML = `<thead><tr><th>方案</th><th class="num">10年年化</th><th class="num">近5年年化</th><th class="num">期末金額</th><th class="num">最大回撤</th><th class="num">夏普</th><th class="num">空頭年勝率</th><th class="num">多頭年勝率</th><th class="num">前半${h0.h1 ? `<br><span class="hint">${h0.h1.from}～${h0.h1.to}</span>` : ""}</th><th class="num">後半${h0.h2 ? `<br><span class="hint">${h0.h2.from}～${h0.h2.to}</span>` : ""}</th><th class="num">平均檔數 台／美</th><th class="num">停損次數</th></tr></thead><tbody>${rows}
      <tr class="dim"><td>混合基準</td><td class="num">${fmt.pct(bf.cagr)}</td><td class="num">${fmt.pct(b5.cagr)}</td><td class="num">${fmt.ntd(bf.final_amount)}</td><td class="num">${fmt.pct(bf.max_drawdown)}</td><td class="num">${fmt.x(bf.sharpe, 2)}</td><td></td><td></td><td class="num">${h0.h1 && h0.h1.bench_cagr != null ? fmt.pct(h0.h1.bench_cagr) : ""}</td><td class="num">${h0.h2 && h0.h2.bench_cagr != null ? fmt.pct(h0.h2.bench_cagr) : ""}</td><td></td><td></td></tr></tbody>`;
  }
  renderSleeveCmp(bt);
  renderCrisis(bt);
  renderStress(bt);
  renderFrontier(bt);
  renderTrades(bt);
  // 各部位
  const sl = (S.btPeriod === "5y" ? bt.sleeves_5y : bt.sleeves_full) || {};
  $("#btSleeves").innerHTML = `<thead><tr><th>部位</th><th class="num">年化</th><th class="num">對照</th><th class="num">對照年化</th><th class="num">差距</th><th class="num">最大回撤</th><th class="num">3 年勝率</th><th class="num">最差 3 年</th><th class="num">實際買股</th></tr></thead><tbody>${Object.entries(sl).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${fmt.pct(v.cagr)}</td><td class="num">${esc(v.bench || "")}</td><td class="num">${fmt.pct(v.bench_cagr)}</td><td class="num ${v.cagr - v.bench_cagr >= 0 ? "pos" : "neg"}">${v.bench_cagr != null ? fmt.pct(v.cagr - v.bench_cagr) : "–"}</td><td class="num">${fmt.pct(v.max_drawdown)}</td><td class="num">${v.roll36_beat != null ? fmt.pct(v.roll36_beat, 0) : "–"}</td><td class="num ${v.roll36_worst_excess >= 0 ? "pos" : "neg"}">${v.roll36_worst_excess != null ? fmt.pct(v.roll36_worst_excess) : "–"}</td><td class="num">${v.stock_share != null ? fmt.pct(v.stock_share, 0) : "–"}</td></tr>`).join("")}</tbody>`;
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
  // 各景氣階段：個股部位 vs 對應大盤（目前配置；階段由領先指標判斷）
  if (bt.cycle && (bt.cycle.tw || bt.cycle.us)) {
    const PH = { recovery: "復甦", expansion: "擴張", slowdown: "趨緩", recession: "衰退" };
    const order = ["recovery", "expansion", "slowdown", "recession"];
    const tbl = (mk, label, bk) => {
      const c = bt.cycle[mk];
      if (!c) return "";
      const rows = order.map((p) => c.stats.find((x) => x.phase === p)).filter(Boolean).map((x) =>
        `<tr><td>${PH[x.phase]}</td><td class="num">${x.months}</td><td class="num">${fmt.pct(x.sleeve_avg, 2)}</td><td class="num">${fmt.pct(x.bench_avg, 2)}</td><td class="num ${x.sleeve_avg >= x.bench_avg ? "pos" : "neg"}">${fmt.pct(x.sleeve_avg - x.bench_avg, 2)}</td><td class="num">${fmt.pct(x.beat_rate, 0)}</td></tr>`).join("");
      return `<h3 style="margin-top:14px">${label}：各景氣階段的月平均報酬</h3><div class="table-wrap"><table class="data compact"><thead><tr><th>階段</th><th class="num">月數</th><th class="num">個股部位</th><th class="num">${bk}</th><th class="num">差距</th><th class="num">贏的月份</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    };
    $("#btRegime").innerHTML += tbl("tw", "台股", "0050") + tbl("us", "美股", "SPY") + `<p class="hint">階段用領先指標判斷（台股國發會、美股 OECD），延後 1 個月、連續 2 個月才切換。這裡是目前配置在各階段的表現，用來看選股在哪個階段贏、哪個階段輸。</p>`;
  }
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
    const blk = (t, a, m) => `<div><b>${t}（${a.length}）</b><ul>${a.map(([i, n]) => `<li>${esc(i)} ${nameInline(m, i, n)}</li>`).join("") || "<li class='hint'>無 → 現金</li>"}</ul></div>`;
    $("#btHoldings").innerHTML = blk("台股", h.tw, "tw") + blk("美股", h.us, "us") + blk("ETF", h.etf, "etf");
  };
  sel.onchange = showH; showH();
  const c = S.cfg.costs;
  $("#btNotes").innerHTML = `<h2>回測假設與限制</h2><ul>
    <li>每月底以「當時已公布」的財報（台股依法定公告期限、美股依 SEC 實際申報日）與月底股價選股，下個月持有；各部位等權重，台股 ${fmt.pct(bt.weights.tw_stock, 0)}／美股 ${fmt.pct(bt.weights.us_stock, 0)}／ETF ${fmt.pct(bt.weights.etf, 0)}，每月再平衡。</li>
    <li>扣費後情境：台股手續費 ${fmt.pct(c.tw_stock.buy, 4)}、賣出證交稅 0.3%（ETF 0.1%）；美股複委託手續費 ${fmt.pct(c.us.buy, 2)}＋換匯價差 ${fmt.pct(c.fx_spread, 2)}；美股股利預扣 ${fmt.pct(c.us_dividend_withholding, 0)}。全期累計交易成本約占淨值 ${fmt.pct(bt.cost_total_pct, 1)}。未計台股股利所得稅與二代健保補充保費（依個人稅率而定）。</li>
    <li>以新台幣計價，美股與美股 ETF 依每月匯率換算；股價含息還原（Yahoo Finance）。某市場無股票通過時該部位持有現金（報酬 0）。</li>
    <li><b>倖存者偏差</b>：${bt.delisted && bt.delisted.n_universe ? `台股已加入 2016 年後下市（櫃）的 ${bt.delisted.n_universe} 家公司（股價來自 FinMind，下市股沒有還原股利，偏保守）；持有中遇到下市以最後成交價出場，停止交易一段時間後才下市的，最後成交價再扣掉 ${fmt.pct(bt.delisted.haircut, 0)}，回測期間共 ${(bt.delisted.exits || []).length} 次。部分下市公司 FinMind 查不到財報，無法放進回測。` : "台股下市公司資料尚未補齊。"}美股只包含目前仍掛牌的公司（免費資料沒有下市股票的股價），結果可能偏樂觀。</li>
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
    <td>${m === "tw" ? "台股" : "美股"}</td><td><b>${esc(id)}</b></td><td>${r || zhInfo(m, id, "").n ? nameCell(m, id, r ? r.name : "") : "（查無資料）"}</td><td class="num">${r && r.score != null ? r.score.toFixed(1) : "–"}</td><td class="num">${r ? fmt.x(r.pe) : "–"}</td>
    <td>${!r ? "" : r.pass ? `<span class="ok">✔ 通過</span>` : `<span class="ng">✘</span> <span class="hint">${esc(r.failed.map(fname).join("、"))}</span>`}</td>
    <td><button class="ghost small" data-del="${m}:${esc(id)}">移除</button></td></tr>`).join("") || `<tr><td colspan="7" class="hint" style="padding:16px">還沒有追蹤的股票</td></tr>`}</tbody>`;
  $$("#wlTable tr.clickable").forEach((tr) => (tr.onclick = (e) => { if (e.target.dataset.del) return; openDetail(tr.dataset.m, tr.dataset.id); }));
  $$("#wlTable [data-del]").forEach((b) => (b.onclick = () => {
    const [m, id] = b.dataset.del.split(":");
    wl[m] = wl[m].filter((x) => x !== id); store.set("vs_watch", wl); renderWatchlist();
  }));
}

// ---------------------------------------------------------------- 條件說明
function cycleRulesHtml(c) {
  const cy = c.cycle, PH = { recovery: "復甦", expansion: "擴張", slowdown: "趨緩", recession: "衰退" };
  const desc = { recovery: "低於 100、往上", expansion: "高於 100、往上", slowdown: "高於 100、往下", recession: "低於 100、往下" };
  const rows = ["recovery", "expansion", "slowdown", "recession"].map((p) => {
    const r = (cy.phase_rules || {})[p] || {};
    return `<tr><td><b>${PH[p]}</b></td><td class="hint">${desc[p]}</td><td>${r.rule_set === "new" ? "市場 × 產業規則" : "原本 31 條規則"}</td><td>${r.financials ? "可買" : "不買"}</td></tr>`;
  }).join("");
  const ov = cy.us_overlays || {};
  return `<h2>景氣階段模組${cy.enabled ? "（目前使用中）" : "（回測驗證後才會用來下單）"}</h2>
  <p>每月底用領先指標判斷景氣階段：<b>位置</b>（領先指標去除長期趨勢後高於或低於 100）× <b>方向</b>（近 3 個月往上或往下）。台股用國發會領先指標不含趨勢指數，美股用 OECD 綜合領先指標${ov.curve_inversion ? "；美股另外在殖利率曲線倒掛（10 年 − 3 個月 < 0）時把擴張改判趨緩" : ""}${ov.credit_jump_pp ? `，信用利差 3 個月內擴大 ≥ ${ov.credit_jump_pp} 個百分點時往後推一個階段` : ""}。領先指標延後 ${cy.lag_months} 個月使用（公布時間差），連續 ${cy.confirm_months} 個月同一階段才切換。各階段只切換「篩選條件」，不調整台美比重。</p>
  <div class="table-wrap"><table class="data compact"><thead><tr><th>階段</th><th>領先指標</th><th>篩選規則</th><th>金融股</th></tr></thead><tbody>${rows}</tbody></table></div>
  <p class="hint">注意：國發會領先指標每月會回溯修正，沒有當時公布的版本，回測會稍微樂觀；美股有 FRED 金鑰時，回測用 OECD 領先指標的「第一次公布值」。</p>`;
}

function industryRulesHtml(c) {
  const ir = c.industry_rules;
  const fv = (f) => {
    if (f.op === "<=peer") return `同業中位數 + ${f.peer_k_std}σ`;
    if (f.op.startsWith("peer_pct")) return `同業百分位 ${f.op.slice(8)} ${Math.round(100 * f.value)}%`;
    const t = f.value;
    return typeof t === "object" ? `台股 ${fmtMetric(f.metric, t.tw, "tw")}／美股 ${fmtMetric(f.metric, t.us, "us")}` : typeof t === "boolean" ? (t ? "是" : "否") : `${f.op} ${fmtMetric(f.metric, t, "tw")}`;
  };
  const ovText = (o) => Object.entries(o).map(([id, x]) => {
    const f = ir.filters.find((y) => y.id === id);
    const nm = f ? f.label.replace(/^[\d]+[ab']?\.\s*/, "") : id;
    if (x.enabled === false) return `不檢查「${esc(nm)}」`;
    if (x.enabled === true && !("value" in x) && !("metric" in x)) return `加上「${esc(nm)}」`;
    const parts = [];
    if ("metric" in x) parts.push(`改看${esc(mlabel(x.metric))}`);
    if ("value" in x) parts.push(f && f.op.startsWith("peer_pct") ? `門檻 ${Math.round(100 * x.value)}%` : `門檻 ${fmtMetric(x.metric || (f || {}).metric, x.value, "tw")}`);
    return `「${esc(nm)}」${parts.join("、")}`;
  }).join("；");
  const models = { general: "RIM、DCF、同業", defensive: "RIM、DCF、同業", utility: "RIM、DCF、同業", cyclical: "RIM（10 年 ROE）、歷史 P/B、同業 P/B", construction: "RIM（5 年 ROE）、歷史 P/B、同業 P/B", financial: "RIM、同業 P/B 與本益比", growth_watch: "DCF、EV/營收（只觀察）", biotech_research: "不估值（研究工具）", exclude: "—" };
  const tpls = Object.keys(TEMPLATE_LABEL);
  const tov = ir.template_overrides || {};
  const iov = ir.industry_overrides || {};
  return `<h2>市場 × 產業規則${ir.enabled ? "（目前使用中）" : "（新版，回測驗證後才會用來下單）"}</h2>
  <p>每檔股票依「市場 × 產業別」歸入一個產業模板。估值與獲利條件改用<b>同業百分位</b>：在同市場、同產業別裡排第幾（0% 最低、100% 最高）；同產業別不到 ${ir.min_peers} 家時改跟同模板的公司比，模板也不足時跟全市場比。財務安全條件維持絕對門檻、依模板調整；最後要求估值安全邊際 ≥ 0。綜合排名也改用同業百分位，避免整體便宜的產業占便宜。金融股目前${ir.financials ? "開放" : "不開放"}買進（之後由景氣階段決定）。網站「個股篩選」可切換規則版本預覽。</p>
  <div class="table-wrap"><table class="data compact"><thead><tr><th>模板</th><th>適用</th><th>估值模型</th><th>跟基準條件不同的地方</th></tr></thead><tbody>${tpls.map((t) =>
    `<tr><td><span class="pill">${TEMPLATE_LABEL[t]}</span></td><td class="hint">${esc(TEMPLATE_NOTE[t] || "")}</td><td class="hint">${models[t] || ""}</td><td class="hint">${tov[t] ? ovText(tov[t]) : (t === "growth_watch" || t === "biotech_research" || t === "exclude") ? "不自動買進" : "—"}</td></tr>`).join("")}</tbody></table></div>
  ${Object.entries(iov).some(([, v]) => Object.keys(v || {}).length) ? `<p class="hint">個別產業別：${Object.entries(iov).flatMap(([m, v]) => Object.entries(v || {}).map(([ind, o]) => `${m === "tw" ? "台股" : "美股"}「${esc(ind)}」${ovText(o)}`)).join("；")}</p>` : ""}
  <div class="table-wrap"><table class="data compact"><thead><tr><th>條件（基準）</th><th>門檻</th></tr></thead><tbody>${ir.filters.map((f) => `<tr class="${f.enabled === false ? "dim" : ""}"><td>${esc(f.label)}${f.enabled === false ? ` <span class="hint">（預設關閉，由模板或產業別開啟）</span>` : ""}</td><td class="hint">${esc(mlabel(f.metric))}：${esc(fv(f))}</td></tr>`).join("")}</tbody></table></div>`;
}

function renderRules() {
  const c = S.cfg;
  const fr = (f) => {
    let v = f.op === "<=peer" ? `同產業中位數 + ${f.peer_k_std}σ` : "";
    if (!v) { const t = f.value; v = typeof t === "object" ? `台股 ${fmtMetric(f.metric, t.tw, "tw")}／美股 ${fmtMetric(f.metric, t.us, "us")}` : typeof t === "boolean" ? (t ? "是" : "否") : `${f.op} ${fmtMetric(f.metric, t, "tw")}`; }
    return `<tr><td>${esc(f.label)}</td><td>${f.source === "user" ? "你的條件" : "補充"}</td><td class="hint">${esc(mlabel(f.metric))}：${esc(v)}</td></tr>`;
  };
  const w = c.ranking.bottom_up;
  const wrow = (g, name) => Object.entries(w[g]).map(([k, v]) => `<li>${mlabel(k)}：${fmt.pct(v, 0)}</li>`).join("");
  $("#rulesBody").innerHTML = qualityCard() + `
  <h2>選股流程</h2>
  <ol>
    <li><b>由下而上（Bottom-up）硬性條件</b>：個股要通過下表所有條件才進入候選池。流量項目（營收、利潤、現金流）用近四季合計，資產負債用最新一季。</li>
    <li><b>由上而下（Top-down）</b>：每個產業用「6 個月相對強弱 50% + 產業估值（中位數 EBIT/EV）50%」打分；大盤以 10 個月均線判斷多空（目前僅顯示，可在設定檔開啟「跌破均線股票部位減半」）。</li>
    <li><b>綜合排名</b>：通過條件的股票才排名。每個指標用「在全市場排第幾」的百分位（市場 × 產業規則：在同業群組排第幾），價值 40%＋品質 30%（ROE 用近 5 年平均）＋安全 10%＋產業分數 20%，另有加分。分數約等於「贏過全市場百分之幾的公司」，不會因為通過的股票少就被壓低。由下而上分數在通過者前 1/3 且產業分數前 1/2 的股票標為 ⭐「上下皆優」。</li>
    <li><b>組合</b>：${(c.portfolio.fixed_sleeves || []).map((f) => `${esc(f.name)} ${fmt.pct(f.weight, 0)}（${Object.keys(f.holdings).join("、")}）`).join("＋")}${(c.portfolio.fixed_sleeves || []).length ? "＋" : ""}台股個股 ${fmt.pct(c.portfolio.weights.tw_stock, 0)}＋美股個股 ${fmt.pct(c.portfolio.weights.us_stock, 0)}${c.portfolio.weights.etf ? `＋ETF ${fmt.pct(c.portfolio.weights.etf, 0)}` : ""}。個股各取前 ${c.portfolio.max_holdings_per_stock_sleeve} 名等權重${c.portfolio.fill_with_index && c.portfolio.fill_with_index.enabled ? `；通過的股票少於 ${c.portfolio.fill_with_index.min_holdings} 檔時，空名額改買大盤（台股 ${esc(c.portfolio.fill_with_index.tw)}、美股 ${esc(c.portfolio.fill_with_index.us)}）` : ""}。每月用新投入的錢補足低於目標的部位，原則上不賣股。</li>
    ${c.risk && c.risk.stop_loss && c.risk.stop_loss.enabled ? `<li><b>停損</b>：${c.risk.stop_loss.basis === "vol" ? `個股自持有期間最高點回檔超過「月波動度 × ${c.risk.stop_loss.vol_mult}」` : `個股自${c.risk.stop_loss.basis === "trailing" ? "持有期間最高點" : "買進價"}下跌 ${fmt.pct(c.risk.stop_loss.pct, 0)}`}（含息）就賣出，賣出的錢放現金到下次調倉；${c.risk.stop_loss.reentry === "ma" ? "至少 1 個月後、且股價站上 10 個月均線才買回" : c.risk.stop_loss.cooldown_months + " 個月內不買回同一檔"}。每日更新時以日收盤檢查並通知；回測以日線逐日模擬，觸發後隔一個交易日才以收盤價賣出。ETF 不設停損。</li>` : ""}
  </ol>
  <h2>估值方法（合理價區間）</h2>
  <ul>
    <li><b>要求報酬率（CAPM）</b>：無風險利率＋β × 市場風險溢酬。β 用近 60 個月月報酬對大盤迴歸並做 Blume 調整；美股無風險利率自動取 FRED 10 年期美債，台股用設定值 ${fmt.pct((c.valuation || {}).rf ? c.valuation.rf.tw : 0.016, 1)}；市場風險溢酬 台股 ${fmt.pct(((c.valuation || {}).erp || {}).tw || 0.055, 1)}、美股 ${fmt.pct(((c.valuation || {}).erp || {}).us || 0.045, 1)}。</li>
    <li><b>剩餘收益模型</b>：每股淨值＋未來超額報酬折現，ROE 在 ${(c.valuation || {}).fade_years || 10} 年內回到要求報酬率（保守）。ROE 用「正常化 ROE」：一般公司近 5 年平均、循環股近 10 年平均，避免拿景氣高峰的獲利往後推。</li>
    <li><b>自由現金流折現</b>：近 3 年平均自由現金流，前 5 年以過去營收成長率成長（限制 −3%～12%），再降到永續成長率。</li>
    <li><b>同業相對估值</b>：同業比較群組（同市場、同產業別；家數不足時同模板）的倍數中位數。</li>
    <li><b>依產業模板選模型</b>：一般／防禦／公用用 RIM、DCF、同業（本益比、股價淨值比、EV/EBIT）；循環用 RIM、歷史股價淨值比、同業股價淨值比（不用本益比，高峰時會假性便宜）；營建用 RIM、歷史股價淨值比；金融用 RIM（等同合理股價淨值比）與同業股價淨值比、本益比；成長觀察用 DCF 與 EV/營收；新藥研究不估值。美股股東權益為負或很薄（大量買回）時不用淨值類模型，DCF 的自由現金流再扣股票酬勞。</li>
    <li><b>合理價</b>＝各模型中位數；<b>安全邊際</b>＝合理價 ÷ 股價 − 1，≥ 30% 標為低估、≤ −20% 標為高估。另外反推市場隱含成長率，跟公司過去成長比較。</li>
    <li>缺少的資料：分析師預估（付費）、股利明細（以保留率代替）、台股利息費用與折舊。舊規則下估值只供參考；市場 × 產業規則要求安全邊際 ≥ 0。</li>
  </ul>
  ${c.cycle ? cycleRulesHtml(c) : ""}
  ${c.industry_rules ? industryRulesHtml(c) : ""}
  <h2>一、個股硬性條件${c.industry_rules && c.industry_rules.enabled ? "（舊規則，比較用）" : ""}</h2>
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
  <ul><li>台股財報：FinMind（整理自公開資訊觀測站）；台股清單與產業別：FinMind</li><li>美股財報：SEC EDGAR XBRL（含申報日期）；產業：SEC SIC 代碼</li><li>股價、ETF 資訊、匯率：Yahoo Finance</li><li>台股三大法人、融資融券：臺灣證券交易所、櫃買中心（只用在網站，不影響選股）</li><li>美股分析師預估：Yahoo Finance（只用在網站與財報快報）</li><li>新聞：台股 FinMind、美股 Yahoo Finance，抓不到時改用 Google 新聞</li></ul>
  <p class="hint">本系統僅供研究參考，不構成投資建議。</p>`;
  $$("[data-q]", $("#rulesBody")).forEach((a) => (a.onclick = () => { const [m, i] = a.dataset.q.split("|"); openDetail(m, i); }));
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
  $("#moreBtn").onclick = () => { $("#moreSheet").hidden = false; };
  $("#moreSheet").onclick = (e) => { if (e.target.id === "moreSheet" || e.target.closest("a")) $("#moreSheet").hidden = true; };
  bindChartSearch();
  $("#detail").addEventListener("click", (e) => { if (e.target.id === "detail") $("#detail").close(); });
  $("#wlAdd").onclick = () => {
    const v = $("#wlInput").value.trim().toUpperCase(); if (!v) return;
    const wl = store.get("vs_watch", { tw: [], us: [] });
    (wl[S.wlMkt] ||= []); if (!wl[S.wlMkt].includes(v)) wl[S.wlMkt].push(v);
    store.set("vs_watch", wl); $("#wlInput").value = ""; renderWatchlist();
  };
}

init();

// ---------------------------------------------------------------- 財報快報
function renderEarnings() {
  const E = S.earn;
  if (!E) { $("#erMeta").textContent = "財報資料還沒產生，每日更新跑過一次之後就會出現。"; $("#erTable").innerHTML = ""; $("#erUpcoming").innerHTML = ""; return; }
  $("#erMeta").textContent = `追蹤：台股 ${E.coverage.tw} 家、美股 ${E.coverage.us} 家（持股、追蹤清單、模擬持股、市值大型股）；更新：${E.as_of}。${E.tw_deadlines || ""}`;
  const g = S.erGroup || "all";
  $$("#erSeg button").forEach((b) => { b.classList.toggle("on", b.dataset.g === g); b.onclick = () => { S.erGroup = b.dataset.g; renderEarnings(); }; });
  const pc = (x) => (x == null ? "–" : fmt.pct(x, 1));
  const cls = (x) => (x == null ? "" : x >= 0 ? "pos" : "neg");
  const rows = (E.recent || []).filter((e) => g === "all" || (g === "us" && e.market === "us") || (g === "twq" && e.market === "tw" && e.type === "earnings") || (g === "twm" && e.type === "month_rev"));
  const when = { before: "盤前", during: "盤中", after: "盤後" };
  $("#erTable").innerHTML = `<thead><tr><th>日期</th><th>公司</th><th>類型</th><th class="num">財報後</th><th class="num">大盤</th><th class="num">相對</th><th class="num">營收年增</th><th class="num">淨利年增</th><th class="num">單季 EPS</th><th>vs 分析師預估</th><th></th></tr></thead><tbody>${rows.map((e) => {
    const r = e.reaction || {}, n = e.numbers || {};
    const dt = e.type === "month_rev" ? (e.period || "").slice(0, 7) + " 月" : e.market === "tw" ? `${e.first_seen || e.date || ""} 抓到` : (e.date || "") + (when[e.timing] ? ` ${when[e.timing]}` : "");
    const typ = e.type === "month_rev" ? "月營收" : e.market === "us" ? "財報" : `季報 ${(e.period || "").slice(0, 7)}`;
    const pend = e.type === "earnings" && !e.reaction ? `<span class="hint">等收盤</span>` : "";
    return `<tr><td>${esc(dt)}</td><td><b>${esc(e.id)}</b> ${nameCell(e.market, e.id, e.name)}</td><td>${typ}</td><td class="num ${cls(r.ret)}">${e.type === "month_rev" ? "" : r.ret != null ? pc(r.ret) : pend}</td><td class="num">${r.bench != null ? pc(r.bench) : ""}</td><td class="num ${cls(r.excess)}">${r.excess != null ? pc(r.excess) : ""}</td><td class="num ${cls(n.rev_yoy)}">${pc(n.rev_yoy)}${e.type === "month_rev" && n.rev_yoy_3m != null ? `<br><span class="hint">近 3 月 ${pc(n.rev_yoy_3m)}</span>` : ""}</td><td class="num ${cls(n.ni_yoy)}">${pc(n.ni_yoy)}</td><td class="num">${n.eps != null ? fmt.x(n.eps, 2) : "–"}</td><td class="${/超乎/.test(e.surprise || "") ? "pos" : /低於/.test(e.surprise || "") ? "neg" : ""}">${esc(e.surprise || "")}</td><td>${e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">財報</a>` : ""}${e.index ? `<br><a href="${esc(e.index)}" target="_blank" rel="noopener" title="SEC 申報索引頁：新聞稿（Exhibit 99.1）、簡報都在這裡">新聞稿</a>` : ""}</td></tr>`;
  }).join("") || `<tr><td colspan="11" class="hint">這段期間沒有財報</td></tr>`}</tbody>`;
  const U = E.upcoming || [];
  $("#erUpcoming").innerHTML = U.length ? `<div class="table-wrap"><table class="data compact"><thead><tr><th>預定日期</th><th>公司</th></tr></thead><tbody>${U.slice(0, 60).map((u) => `<tr><td>${esc(u.date)}</td><td><b>${esc(u.id)}</b> ${nameInline("us", u.id, u.name)}</td></tr>`).join("")}</tbody></table></div><p class="hint">預定日期來自 Yahoo Finance，公司可能更改；台股看上方的法定期限。</p>` : `<p class="hint">還沒有資料（大約每週更新一次）。</p>`;
}

// ---------------------------------------------------------------- 市場訊號（衍生性商品資料＋大盤趨勢）
function renderSignals() {
  const D = S.derivs, meta = $("#sgMeta"), box = $("#sgTrend"), grid = $("#sgGrid"), note = $("#sgNote");
  Object.keys(S.charts).filter((k) => k.startsWith("sg_")).forEach(destroyChart);
  renderEvents();
  if (!D) {
    meta.textContent = "";
    box.innerHTML = `<p class="hint">市場訊號資料還沒產生，每日更新跑過一次之後就會出現。</p>`;
    grid.innerHTML = ""; note.innerHTML = "";
    return;
  }
  meta.textContent = `資料到 ${D.as_of || "–"}。這些只是「市場現在的狀態」，不是買賣建議，也不會影響選股。`;
  const T = D.trend || {};
  const gap = (x) => (x == null ? "–" : (x >= 0 ? "+" : "") + fmt.pct(x, 1));
  const row = (mk, name) => {
    const t = T[mk];
    if (!t) return "";
    const nowTxt = t.below_now ? "目前在均線<b class=\"neg\">下方</b>" : "目前在均線<b class=\"pos\">上方</b>";
    const offTxt = t.below_official ? `<span class="pill neg">${esc(t.official_month)} 月底：已跌破</span>` : `<span class="pill">${esc(t.official_month)} 月底：還在均線上</span>`;
    return `<div class="sg-trend"><div><b>${name}</b> ${offTxt}</div>
      <div class="sg-big ${t.below_now ? "neg" : "pos"}">${gap(t.gap_now)}</div>
      <div class="hint">${nowTxt}（現價 ${fmt.int(t.price)}，近 10 個月月收盤均價 ${fmt.int(t.ma)}）</div></div>`;
  };
  box.innerHTML = `<h3 class="sg-h">大盤趨勢：現價 vs 近 10 個月均價</h3><div class="sg-trends">${row("tw", "台股（加權指數）")}${row("us", "美股（SPY）")}</div>
    <p class="hint">回測用的避險規則是「月底收盤跌破 10 個月均線，下個月才降低風險」，所以<b>月底的狀態才算數</b>；上面的「目前」只是預告。
    回測結果（2007～2026）：這個規則只對 2008、2022 這種<b>慢慢跌的長空頭</b>有幫助，碰到 2020、2024、2025 這種急跌又急漲來不及反應（只會少賺）。
    比較適合當成「提醒你暫停加碼、重新檢討」的警報。</p>`;
  const pos = (p) => (p == null ? "" : p >= 0.8 ? "近一年偏高" : p <= 0.2 ? "近一年偏低" : "近一年中間");
  grid.innerHTML = (D.signals || []).filter((x) => x.key !== "tx_front").map((x) => {
    const v = x.fmt === "int" ? fmt.int(x.value) : x.fmt === "ratio" ? x.value.toFixed(2) : (+x.value).toFixed(2);
    const p = x.pct_1y;
    return `<div class="card sg-card"><div class="sg-top"><b>${esc(x.label)}</b><span class="hint">${esc(x.date)}</span></div>
      <div class="sg-val">${v}</div>
      ${p == null ? "" : `<div class="sg-bar" title="近一年的位置：${Math.round(p * 100)}%（0＝一年最低、100＝一年最高）"><i style="left:${Math.round(p * 100)}%"></i></div><div class="hint">${pos(p)}（第 ${Math.round(p * 100)} 百分位）</div>`}
      <div class="chart-box sm"><canvas id="sg_${esc(x.key)}"></canvas></div>
      ${x.note ? `<p class="hint">${esc(x.note)}</p>` : ""}</div>`;
  }).join("");
  if (window.Chart) for (const x of D.signals || []) {
    const cv = $("#sg_" + x.key);
    if (!cv) continue;
    S.charts["sg_" + x.key] = lineChart(cv, x.series.map((r) => r[0]), [{ label: x.label, data: x.series.map((r) => r[1]), color: cssVar("--s1"), width: 1.6 }],
      { yfmt: (v) => (x.fmt === "int" ? fmt.int(v) : (+v).toFixed(2)) });
  }
  const pr = D.progress || {};
  const lacking = Object.entries(pr).filter(([, v]) => v && typeof v === "object" && v.have < v.total);
  note.innerHTML = `<p><b>怎麼看這些訊號：</b>外資期貨淨部位、Put/Call 比、VIX 都是「市場情緒」的參考，單獨看不代表會漲或跌。
    回測（2007～2026）測過外資期貨空單、VIX 倒掛、跌深三種「恐慌訊號」當避險依據，<b>都沒有比不避險好</b>，所以目前只顯示、不用來決定買賣。</p>
    <p class="hint">資料來源：期交所（期貨、選擇權、三大法人）、Yahoo（VIX）。外資期貨部位 2018 年 6 月起才有完整歷史；選擇權每天只存最近 2 個到期月、價平 ±10% 內的合約。
    ${lacking.length ? `回補進度：${lacking.map(([k, v]) => `${esc(k)} ${v.have}/${v.total}`).join("、")}。` : "歷史資料已補齊。"}</p>`;
}

// ---------------------------------------------------------------- 並行模擬
function renderPaper() {
  const P = S.paper, box = $("#paperBody");
  $("#paperChartWrap").hidden = true;
  $("#paperHoldCard").hidden = true;
  if (!P) { box.innerHTML = `<p class="hint">模擬資料還沒產生，每日更新跑過一次之後就會出現。</p>`; return; }
  if (P.status === "waiting") {
    box.innerHTML = `<p>第一次模擬決策會在 <b>${esc(P.first_decision)}</b> 收盤後的下一次每日更新產生，從 ${esc(P.start)} 開始累計績效。</p>
      <p class="hint">模擬的版本：${(S.cfg.paper && S.cfg.paper.versions || []).map(esc).join("、")}</p>`;
    return;
  }
  const V = P.versions || [];
  const pc = (x, d = 1) => (x == null ? "–" : fmt.pct(x, d));
  const cls = (x) => (x == null ? "" : x >= 0 ? "pos" : "neg");
  const sl = (v, mk, b) => { const s = (v.sleeves || {})[mk]; return s && s.cum != null ? `${pc(s.cum)} <span class="hint">vs ${b} ${pc(s.bench_cum)}</span>` : "–"; };
  const mtdTo = (V.find((v) => v.mtd_to) || {}).mtd_to;
  box.innerHTML = `<div class="table-wrap"><table class="data compact"><thead><tr><th>版本</th><th class="num">累計（含本月估計）</th><th class="num">比只買 ETF</th><th class="num">已完成月份</th><th class="num">本月至今${mtdTo ? `<br><span class="hint">估計到 ${esc(mtdTo)}</span>` : ""}</th><th class="num">台股部位</th><th class="num">美股部位</th><th class="num">停損次數</th></tr></thead><tbody>${V.map((v) =>
    `<tr><td><b>${esc(v.name)}</b>${v.changed_since_locked ? ` <span class="pill" title="用最新資料重算，這個月的決策跟當初鎖定的不同；網站顯示鎖定的版本">決策有變動</span>` : ""}</td><td class="num ${cls(v.cum)}">${pc(v.cum)}</td><td class="num ${cls(v.vs_etf)}">${v.name.startsWith("只買 ETF") ? "（對照組）" : pc(v.vs_etf)}</td><td class="num">${pc(v.cum_closed)}<br><span class="hint">${(v.months || []).length} 個月</span></td><td class="num ${cls(v.mtd)}">${pc(v.mtd)}</td><td class="num">${sl(v, "tw", "0050")}</td><td class="num">${sl(v, "us", "SPY")}</td><td class="num">${v.n_stops}</td></tr>`).join("")}</tbody></table></div>
    <p class="hint">開始：${esc(P.start)}（第一次決策 ${esc(P.first_decision)}）；更新：${esc(P.as_of)}。台股、美股部位只算已完成的月份。這只是模擬，實際下單仍照「總覽」的下單清單。</p>`;
  // 累計績效（已完成月份）
  const months = (V[0] || {}).months || [];
  if (months.length >= 2 && window.Chart) {
    $("#paperChartWrap").hidden = false;
    destroyChart("paper");
    const colors = ["--s1", "--s2", "--s3", "--s4", "--s5"];
    S.charts.paper = new Chart($("#paperChart"), {
      type: "line",
      data: { labels: ["開始", ...months], datasets: V.map((v, i) => { let n = 1; return { label: v.name, data: [0, ...v.monthly_net.map((r) => (n *= 1 + r) - 1)], borderColor: cssVar(colors[i % 5]), backgroundColor: cssVar(colors[i % 5]), borderWidth: 2, pointRadius: 2 }; }) },
      options: baseOpts({ yfmt: (x) => fmt.pct(x, 1), legend: true }),
    });
  }
  // 目前持股
  $("#paperHoldCard").hidden = false;
  const blk = (t, a, m) => a.length ? `<div><b>${t}（${a.length}）</b><ul>${a.map(([i, n, w]) => `<li>${esc(i)} ${m === "etf" ? `${esc(zhInfo("etf", i, "").n)} <span class="hint">${esc(n)}</span>` : nameInline(m, i, n)} <b>${fmt.pct(w, 1)}</b></li>`).join("")}</ul></div>` : "";
  $("#paperHold").innerHTML = V.map((v) => `<details${v === V[0] ? " open" : ""}><summary><b>${esc(v.name)}</b> <span class="hint">${esc(v.decision_month || "")} 月底決策</span></summary>
    <div class="holdgrid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:8px 0">${blk("台股", v.holdings.tw, "tw")}${blk("美股", v.holdings.us, "us")}${blk("ETF／大盤", v.holdings.etf, "etf")}</div></details>`).join("");
}

// ---------------------------------------------------------------- 個股部位 vs 大盤（各方案）
function renderSleeveCmp(bt) {
  const V = bt.variants || {};
  const rows = Object.entries(V).filter(([, v]) => v.sleeves && (v.sleeves["台股個股部位"] || v.sleeves["美股個股部位"]));
  if (!rows.length) return;
  $("#sleeveCmpCard").hidden = false;
  const cells = (s, h) => {
    if (!s) return `<td class="num">–</td>`.repeat(5);
    const d = s.cagr - s.bench_cagr;
    const hh = h && h.h1 && h.h2 ? `${fmt.pct(h.h1.cagr - h.h1.bench_cagr)}／${fmt.pct(h.h2.cagr - h.h2.bench_cagr)}` : "–";
    return `<td class="num">${fmt.pct(s.cagr)} <span class="hint">vs ${fmt.pct(s.bench_cagr)}</span></td><td class="num ${d >= 0 ? "pos" : "neg"}">${fmt.pct(d)}<br><span class="hint">前／後半 ${hh}</span></td>` +
      `<td class="num">${s.roll36_beat != null ? fmt.pct(s.roll36_beat, 0) : "–"}</td><td class="num ${s.roll36_worst_excess >= 0 ? "pos" : "neg"}">${s.roll36_worst_excess != null ? fmt.pct(s.roll36_worst_excess) : "–"}${s.roll36_worst_end ? `<br><span class="hint">到 ${esc(s.roll36_worst_end)}</span>` : ""}</td><td class="num">${s.stock_share != null ? fmt.pct(s.stock_share, 0) : "–"}</td>`;
  };
  const half = (v, mk) => { const h = v.sleeve_halves || {}; return h.h1 && h.h2 ? { h1: h.h1[mk], h2: h.h2[mk] } : null; };
  const sub = `<th class="num">年化 vs 大盤</th><th class="num">每年差距</th><th class="num">3 年勝率</th><th class="num">最差 3 年</th><th class="num">實際買股</th>`;
  $("#sleeveCmpTable").innerHTML = `<thead><tr><th rowspan="2">方案</th><th colspan="5" class="num">台股部位 vs 0050</th><th colspan="5" class="num">美股部位 vs SPY</th></tr><tr>${sub}${sub}</tr></thead><tbody>${rows.map(([k, v]) =>
    `<tr><td><b>${esc(k)}</b></td>${cells(v.sleeves["台股個股部位"], half(v, "tw"))}${cells(v.sleeves["美股個股部位"], half(v, "us"))}</tr>`).join("")}</tbody>`;
}

// ---------------------------------------------------------------- 危機壓力測試
function renderCrisis(bt) {
  const V = bt.variants || {};
  const defs = bt.crisis_defs || [];
  const stopV = Object.entries(V).filter(([, v]) => v.is_stop_variant && v.crises);
  if (!stopV.length || !defs.length) return;
  $("#crisisCard").hidden = false;
  const view = S.crisisView || "total";
  $$("#crisisView button").forEach((b) => {
    b.classList.toggle("on", b.dataset.v === view);
    b.onclick = () => { S.crisisView = b.dataset.v; renderCrisis(bt); };
  });
  const cell = (r) => {
    if (!r) return `<td class="num">–</td>`;
    const a = view === "total" ? r.total : r.fall, b = view === "total" ? r.mdd : r.rebound;
    return `<td class="num"><span class="${a >= 0 ? "pos" : "neg"}">${fmt.pct(a)}</span><span class="hint">／${fmt.pct(b)}</span></td>`;
  };
  const names = defs.map((d) => d.name);
  const first = stopV[0][1].crises;
  const head = `<thead><tr><th>方案</th>${names.map((n) => `<th class="num">${esc(n)}${first[n] ? `<br><span class="hint">谷底 ${esc(first[n].trough)}</span>` : ""}</th>`).join("")}<th class="num">停損次數</th></tr></thead>`;
  const rows = stopV.map(([k, v]) => `<tr><td><b>${esc(k)}</b></td>${names.map((n) => cell(v.crises[n] && v.crises[n].strategy)).join("")}<td class="num">${v.n_stops}</td></tr>`).join("");
  const bench = `<tr class="dim"><td>混合基準</td>${names.map((n) => cell(first[n] && first[n].bench)).join("")}<td></td></tr>`;
  $("#crisisTable").innerHTML = head + `<tbody>${rows}${bench}</tbody>`;
}

function renderStress(bt) {
  const st = bt.stress;
  if (!st || !st.results || !Object.keys(st.results).length) return;
  $("#stressCard").hidden = false;
  const sel = $("#stressAsset");
  const assets = Object.keys(st.results);
  if (!sel.options.length) {
    sel.innerHTML = assets.map((a) => `<option>${esc(a)}</option>`).join("");
    sel.onchange = () => renderStress(bt);
  }
  const R = st.results[sel.value || assets[0]] || {};
  const eps = Object.keys(R);
  const head = `<thead><tr><th>規則</th>${eps.map((e) => `<th class="num">${esc(e)}<br><span class="hint">谷底 ${esc(Object.values(R[e])[0].trough)}</span></th>`).join("")}</tr></thead>`;
  const rows = st.rules.map((rn) => `<tr><td><b>${esc(rn)}</b></td>${eps.map((e) => {
    const r = R[e][rn], base = R[e]["無停損"];
    if (!r) return `<td class="num">–</td>`;
    const better = base && rn !== "無停損" ? (r.total >= base.total ? "pos" : "neg") : "";
    return `<td class="num"><span class="${better}">${fmt.pct(r.total)}</span><br><span class="hint">回撤 ${fmt.pct(r.mdd)}・停損 ${r.stops} 次</span></td>`;
  }).join("")}</tr>`).join("");
  $("#stressTable").innerHTML = head + `<tbody>${rows}</tbody>`;
}

function renderFrontier(bt) {
  const P = bt.frontier || [];
  if (!P.length) return;
  $("#frontierCard").hidden = false;
  destroyChart("frontier");
  const col = { stop: cssVar("--s1"), alloc: cssVar("--s2"), bench: cssVar("--text-2"), other: cssVar("--s3"), macro: cssVar("--s3"), factor: cssVar("--s4"), rules: cssVar("--s4") };
  col.macro = cssVar("--s4"); col.factor = cssVar("--s5");
  const groups = [["停損規則", (p) => p.kind === "variant" && p.group === "stop", col.stop],
                  ["信用利差", (p) => p.kind === "variant" && p.group === "macro", col.macro],
                  ["新因子／金融股", (p) => p.kind === "variant" && p.group === "factor", col.factor],
                  ["產業規則", (p) => p.kind === "variant" && p.group === "rules", col.rules || col.factor],
                  ["選股風格", (p) => p.kind === "variant" && p.group === "style", cssVar("--s6")],
                  ["資產配置", (p) => p.kind === "variant" && p.group === "alloc", col.alloc],
                  ["基準（大盤）", (p) => p.kind === "bench", col.bench]];
  S.charts.frontier = new Chart($("#btFrontier"), {
    type: "scatter",
    data: { datasets: groups.map(([label, f, c]) => ({ label, data: P.filter(f).map((p) => ({ x: p.vol, y: p.cagr, n: p.name, mdd: p.mdd })),
      backgroundColor: c, borderColor: c, pointRadius: 6, pointHoverRadius: 8 })) },
    options: { responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: true, labels: { color: cssVar("--text-2") } },
        tooltip: { callbacks: { label: (c) => ` ${c.raw.n}：報酬 ${fmt.pct(c.raw.y)}／波動 ${fmt.pct(c.raw.x)}／最大回撤 ${fmt.pct(c.raw.mdd)}` } } },
      scales: { x: { title: { display: true, text: "年化波動", color: cssVar("--text-2") }, ticks: { color: cssVar("--text-2"), callback: (v) => fmt.pct(v, 0) }, grid: { color: cssVar("--grid") } },
                y: { title: { display: true, text: "年化報酬（扣費後）", color: cssVar("--text-2") }, ticks: { color: cssVar("--text-2"), callback: (v) => fmt.pct(v, 0) }, grid: { color: cssVar("--grid") } } } },
  });
}

function renderTrades(bt) {
  const T = bt.trade_outcomes;
  if (!T || !T["全部"] || !T["全部"].n) return;
  $("#tradeCard").hidden = false;
  const g = S.tradeGroup || "全部";
  $$("#tradeGroup button").forEach((b) => {
    b.classList.toggle("on", b.dataset.g === g);
    b.onclick = () => { S.tradeGroup = b.dataset.g; renderTrades(bt); };
  });
  const R = T[g] || { n: 0, cats: {} };
  const cats = ["大賺", "小賺", "持平", "小跌", "大跌"];
  const cls = { "大賺": "pos", "小賺": "pos", "持平": "", "小跌": "neg", "大跌": "neg" };
  const nm = (x) => (x ? `${esc(x.id)} ${esc(zhInfo(x.market || mktOf(x.id), x.id, x.name).n)}（${fmt.pct(x.ret, 0)}，${esc(x.start)}～${esc(String(x.end).slice(0, 7))}）` : "–");
  $("#tradeSummary").innerHTML = R.n ? `共 ${R.n} 筆；<b>賺錢比例 ${fmt.pct(R.win_rate, 0)}</b>、<b>贏過大盤比例 ${fmt.pct(R.beat_rate, 0)}</b>；每筆平均報酬 ${fmt.pct(R.avg_ret)}（中位數 ${fmt.pct(R.median_ret)}），平均持有 ${R.avg_months.toFixed(1)} 個月；停損出場 ${R.stop_exits} 筆。<br>最好：${nm(R.best)}　最差：${nm(R.worst)}` : "沒有資料";
  $("#tradeTable").innerHTML = `<thead><tr><th>結果</th><th class="num">筆數</th><th>占比</th><th class="num">平均報酬</th><th class="num">平均贏／輸大盤</th><th class="num">平均持有（月）</th></tr></thead><tbody>${cats.map((c) => {
    const x = R.cats[c] || {};
    return `<tr><td><b class="${cls[c]}">${c}</b></td><td class="num">${x.n || 0}</td><td><span class="bar" style="width:${Math.round(160 * (x.share || 0))}px"></span> <span class="hint">${x.share != null ? fmt.pct(x.share, 0) : "–"}</span></td><td class="num">${x.avg_ret != null ? fmt.pct(x.avg_ret) : "–"}</td><td class="num ${x.avg_excess == null ? "" : x.avg_excess >= 0 ? "pos" : "neg"}">${x.avg_excess != null ? fmt.pct(x.avg_excess) : "–"}</td><td class="num">${x.avg_months != null ? x.avg_months.toFixed(1) : "–"}</td></tr>`;
  }).join("")}</tbody>`;
}

// ---------------------------------------------------------------- 資料品質、台股籌碼、美股分析師預估
function qualityNote(mkt, id) {
  const Q = S.quality, flags = ((S.meta || {}).quality_flags || {})[`${mkt}:${id}`];
  if (!flags || !flags.length) return "";
  const det = Q ? Q.issues.filter((o) => o.market === mkt && o.id === id) : [];
  const items = det.length ? det.map((o) => `<b>${esc(o.label)}</b>：${esc(o.text)}`) : flags.map((f) => `<b>${esc(f)}</b>`);
  return `<div class="card warnbox"><b>⚠️ 這檔的資料可能有問題</b>：${items.join("；")}。<br><span class="hint">相關數字（市值、本益比、條件判斷）可能不準，下單前請到公開資訊觀測站或 SEC 原始財報核對。</span></div>`;
}
function qualityCard() {
  const Q = S.quality;
  if (!Q) return "";
  const bad = Q.checks.filter((c) => !c.ok);
  const rows = Q.issues.slice(0, 400);
  return `<div class="qcard"><h2>資料品質檢查 <span class="hint">（${esc(Q.as_of)}，每天自動檢查）</span></h2>
  <ul class="qlist">${Q.checks.map((c) => `<li class="${c.ok ? "ok" : "ng"}">${c.ok ? "✔" : "⚠️"} ${esc(c.text)}</li>`).join("")}</ul>
  ${Object.keys(Q.counts || {}).length ? `<p class="hint">個股提醒：${Object.entries(Q.counts).map(([k, v]) => `${esc(k)} ${v}`).join("、")}。這些股票的數字可能不準（不會改變選股結果，但看到時請多核對）。</p>` : ""}
  ${rows.length ? `<details><summary>看清單（${rows.length} 筆${Q.issues.length > rows.length ? "，只列前 400" : ""}）</summary><div class="table-wrap"><table class="data compact"><thead><tr><th>市場</th><th>公司</th><th>問題</th><th>說明</th></tr></thead><tbody>${rows.map((o) => `<tr><td>${o.market === "tw" ? "台股" : "美股"}</td><td><a href="javascript:void 0" data-q="${o.market}|${esc(o.id)}"><b>${esc(o.id)}</b></a> ${nameInline(o.market, o.id, "")}</td><td>${o.severe ? "<b class=\"neg\">" + esc(o.label) + "</b>" : esc(o.label)}</td><td class="hint">${esc(o.text)}</td></tr>`).join("")}</tbody></table></div></details>` : ""}
  ${bad.length ? `<p class="hint">有 ⚠️ 的項目代表資料來源可能出了狀況；連續好幾天都這樣再處理就好（通常是免費資料源暫時抓不到）。</p>` : ""}
  </div>`;
}
const lots = (v, sign = true) => (v == null ? "–" : `<span class="${sign ? (v > 0 ? "pos" : v < 0 ? "neg" : "") : ""}">${sign && v > 0 ? "+" : ""}${Math.round(v).toLocaleString()}</span>`);
function chipsCard(id) {
  const C = S.chips, r = C && C.rows ? C.rows[id] : null;
  if (!r) return C && C.as_of ? `<div class="card"><h2>籌碼</h2><p class="hint">這檔沒有法人買賣超資料（${esc(C.as_of)}）。</p></div>` : "";
  const row = (k, l) => `<tr><td>${l}</td><td class="num">${lots(r[k + "5"])}</td><td class="num">${lots(r[k + "20"])}</td></tr>`;
  const n = C.days || 0;
  return `<div class="card"><h2>籌碼：三大法人與融資融券 <span class="hint">（${esc(C.as_of)}）</span></h2>
  <div class="grid2 tight"><div class="table-wrap"><table class="data compact"><thead><tr><th>買賣超（張）</th><th class="num">近 5 日</th><th class="num">近 ${Math.min(20, n) || 20} 日</th></tr></thead><tbody>
    ${row("foreign", "外資")}${row("trust", "投信")}${row("dealer", "自營商")}${row("total", "<b>合計</b>")}</tbody></table></div>
  <div class="table-wrap"><table class="data compact"><tbody>
    <tr><td>融資餘額</td><td class="num">${lots(r.margin, false)} 張${r.margin_chg20 != null ? `<br><span class="hint">近 20 日 ${r.margin_chg20 >= 0 ? "+" : ""}${fmt.pct(r.margin_chg20, 1)}</span>` : ""}</td></tr>
    <tr><td>融券餘額</td><td class="num">${lots(r.short, false)} 張</td></tr>
    <tr><td>券資比</td><td class="num">${r.short_ratio == null ? "–" : fmt.pct(r.short_ratio, 1)}</td></tr></tbody></table></div></div>
  <p class="hint">外資、投信連續買超代表大資金在買，但不保證會漲；融資（散戶借錢買）短期大增時籌碼較亂、波動較大；券資比高代表放空的人多，股價上漲時可能出現軋空。籌碼只當參考，不影響系統選股。${n < 20 ? `目前只累積了 ${n} 個交易日的資料。` : ""}K 線圖按「法人」可以看每天的買賣超。</p></div>`;
}
function estCard(id, row) {
  const E = S.est, r = E && E.rows ? E.rows[id] : null;
  if (!r) return E ? `<div class="card"><h2>分析師預估</h2><p class="hint">這檔不在追蹤範圍（只抓持股、追蹤清單、模擬持股和市值前 100 大）。想看的話把它加進追蹤清單。</p></div>` : "";
  const px = row && row.close ? row.close : null;
  const up = r.target && px ? r.target / px - 1 : r.upside;
  const R = r.rating || {}, tot = ["strongBuy", "buy", "hold", "sell", "strongSell"].reduce((a, k) => a + (R[k] || 0), 0);
  const seg = [["strongBuy", "強力買進", "--good", 1], ["buy", "買進", "--good", 0.6], ["hold", "持有", "--muted", 0.8], ["sell", "賣出", "--bad", 0.6], ["strongSell", "強力賣出", "--bad", 1]];
  const bar = tot ? `<div class="rbar">${seg.filter(([k]) => R[k]).map(([k, l, c, o]) => `<span style="flex:${R[k]};background:var(${c});opacity:${o}" title="${l} ${R[k]}">${R[k]}</span>`).join("")}</div>
    <div class="rleg">${seg.filter(([k]) => R[k]).map(([k, l, c, o]) => `<span><i style="background:var(${c});opacity:${o}"></i>${l} ${R[k]}</span>`).join("")}</div>` : "";
  const L = r.last;
  const sur = L && L.est != null && L.actual != null ? `${esc(L.date)} 公布：EPS ${L.actual.toFixed(2)} vs 預估 ${L.est.toFixed(2)}${L.surprise != null ? `（<b class="${L.surprise >= 0 ? "pos" : "neg"}">${L.surprise >= 0.005 ? "超乎預期" : L.surprise <= -0.005 ? "低於預期" : "符合預期"} ${L.surprise >= 0 ? "+" : ""}${(L.surprise * 100).toFixed(1)}%</b>）` : ""}` : "";
  return `<div class="card"><h2>分析師預估 <span class="hint">（Yahoo Finance，${esc(r.asof || "")}）</span></h2>
  <div class="tiles">
    <div class="tile"><div class="k">平均目標價</div><div class="v" style="font-size:20px">${r.target ? fmt.price(r.target) : "–"}</div><div class="d">${up != null ? `比目前 <span class="${up >= 0 ? "pos" : "neg"}">${up >= 0 ? "+" : ""}${fmt.pct(up, 0)}</span>` : ""}${r.target_lo ? `<br>區間 ${fmt.price(r.target_lo)}～${fmt.price(r.target_hi)}` : ""}</div></div>
    <div class="tile"><div class="k">本季 EPS 預估</div><div class="v" style="font-size:20px">${r.eps_q != null ? r.eps_q.toFixed(2) : "–"}</div><div class="d">${r.n ? r.n + " 位分析師" : ""}</div></div>
    <div class="tile"><div class="k">今年／明年 EPS</div><div class="v" style="font-size:20px">${r.eps_y != null ? r.eps_y.toFixed(2) : "–"} / ${r.eps_ny != null ? r.eps_ny.toFixed(2) : "–"}</div><div class="d">${r.eps_ny_growth != null ? `明年成長 ${fmt.pct(r.eps_ny_growth, 0)}` : ""}</div></div>
  </div>
  ${bar ? `<div class="hint" style="margin-bottom:4px">評等分布（${tot} 位）</div>${bar}` : ""}
  ${sur ? `<p>上次財報：${sur}</p>` : ""}
  <p class="hint">分析師目標價平均偏樂觀，常跟著股價調整；比較有參考價值的是「超乎／低於預期」和預估有沒有被往上或往下修。只當參考，不影響系統選股。</p></div>`;
}

// ---------------------------------------------------------------- 自己設的到價提醒（config/alert_levels.yaml）
const GH = (() => {
  const h = location.hostname, seg = location.pathname.split("/").filter(Boolean);
  return h.endsWith(".github.io") && seg[0] ? { owner: h.split(".")[0], repo: seg[0] } : { owner: "KalebChen0509", repo: "stock-screener" };
})();
const ALV_PATH = "config/alert_levels.yaml";
async function alvServer() {
  if (S.alvServer !== undefined) return S.alvServer;
  try {
    const r = await fetch(`https://raw.githubusercontent.com/${GH.owner}/${GH.repo}/main/${ALV_PATH}?t=${Date.now() >> 16}`);
    if (!r.ok) throw new Error(r.status);
    const txt = await r.text();
    S.alvServer = alvNorm(JSON.parse(txt.split("\n").filter((l) => !l.trim().startsWith("#")).join("\n") || "{}"));
  } catch (e) { S.alvServer = null; }
  return S.alvServer;
}
const alvNorm = (x) => ({ tw: { ...((x || {}).tw || {}) }, us: { ...((x || {}).us || {}) }, etf: { ...((x || {}).etf || {}) } });
const canon = (x) => (x && typeof x === "object" ? `{${Object.keys(x).sort().map((k) => JSON.stringify(k) + ":" + canon(x[k])).join(",")}}` : JSON.stringify(x));
const alvSame = (a, b) => canon(alvNorm(a)) === canon(alvNorm(b));
function alvText(L) {
  const block = (m) => {
    const ks = Object.keys(L[m]).sort();
    return ks.length ? `{\n${ks.map((k) => `    ${JSON.stringify(k)}: ${JSON.stringify(L[m][k])}`).join(",\n")}\n  }` : "{}";
  };
  return `# 自己設的到價提醒：網站「看盤」頁按「🔔 提醒」產生，整個檔案直接覆蓋就好。\n# 盤中每 30 分鐘檢查（Yahoo 報價約延遲 15～20 分鐘），同一則提醒一天只寄一次；只是提醒，不會下單。\n{\n  "tw": ${block("tw")},\n  "us": ${block("us")},\n  "etf": ${block("etf")}\n}\n`;
}
async function renderAlertPanel(mkt, id) {
  const box = $("#kAlert");
  if (!box) return;
  const server = await alvServer();
  const draft = store.get("vs_alert_draft", null);
  let L = alvNorm(draft || server || {});
  if (draft && server && alvSame(draft, server)) { store.set("vs_alert_draft", null); }
  const cur = L[mkt][id] || {};
  const z = zhInfo(mkt, id, "");
  const all = ["tw", "us", "etf"].flatMap((m) => Object.entries(L[m]).map(([k, v]) => ({ m, k, v })));
  const pending = draft && !(server && alvSame(draft, server));
  const edit = `https://github.com/${GH.owner}/${GH.repo}/edit/main/${ALV_PATH}`;
  box.innerHTML = `<h2>🔔 盤中到價提醒</h2>
    <p class="hint">持股的停損線、追蹤清單與持股的均線／KD 訊號、合理價上下緣<b>會自動提醒，不用設定</b>。這裡設的是「漲到／跌到某個價位」提醒你。</p>
    <div class="alv-form"><b>${esc(id.replace(/\.TWO?$/, ""))} ${esc(z.n || "")}</b>
      <label>漲到 <input id="alvUp" type="number" inputmode="decimal" step="any" value="${cur.above ?? ""}"> 以上</label>
      <label>跌到 <input id="alvDn" type="number" inputmode="decimal" step="any" value="${cur.below ?? ""}"> 以下</label>
      <button class="chip on" id="alvSave">${cur.above || cur.below ? "更新" : "加入"}</button></div>
    ${all.length ? `<div class="table-wrap"><table class="data compact"><thead><tr><th>股票</th><th class="num">漲到</th><th class="num">跌到</th><th></th></tr></thead><tbody>${all.map((x) => `<tr><td><a href="#chart/${x.m}/${encodeURIComponent(x.k)}"><b>${esc(x.k.replace(/\.TWO?$/, ""))}</b></a> ${esc(zhInfo(x.m, x.k, "").n || "")}</td><td class="num">${x.v.above ?? ""}</td><td class="num">${x.v.below ?? ""}</td><td><button class="chip" data-del="${x.m}|${esc(x.k)}">刪除</button></td></tr>`).join("")}</tbody></table></div>` : `<p class="hint">還沒有設定任何價位。</p>`}
    ${server === null ? `<p class="hint">讀不到 GitHub 上目前的設定（網路或檔案還沒建立），下面是這台裝置上的版本。</p>` : ""}
    ${pending ? `<div class="alv-steps"><b>還差一步：把設定存到 GitHub 才會生效</b>
      <ol><li><button class="chip on" id="alvCopy">① 複製設定</button></li>
      <li><a class="chip" href="${edit}" target="_blank" rel="noopener">② 打開 GitHub 編輯頁 ›</a>：在編輯框裡<b>全選</b>原本的內容、<b>貼上</b>，再按綠色的 <b>Commit changes</b>（彈出視窗再按一次）。</li>
      <li>完成後回到這頁，「還差一步」消失就代表生效了（GitHub 最多約 5 分鐘才會更新）。<button class="chip" id="alvReset">放棄這些修改</button></li></ol>
      <textarea id="alvTxt" readonly rows="6"></textarea></div>` : (all.length ? `<p class="hint">✔ 已存到 GitHub，盤中會檢查。</p>` : "")}`;
  if ($("#alvTxt")) $("#alvTxt").value = alvText(L);
  const save = (nl) => { store.set("vs_alert_draft", nl); renderAlertPanel(mkt, id); };
  $("#alvSave").onclick = () => {
    const a = parseFloat($("#alvUp").value), b = parseFloat($("#alvDn").value), v = {};
    if (isFinite(a) && a > 0) v.above = a;
    if (isFinite(b) && b > 0) v.below = b;
    const nl = alvNorm(L);
    if (Object.keys(v).length) nl[mkt][id] = v; else delete nl[mkt][id];
    save(nl);
  };
  $$("[data-del]", box).forEach((b) => (b.onclick = () => { const [m, k] = b.dataset.del.split("|"); const nl = alvNorm(L); delete nl[m][k]; save(nl); }));
  if ($("#alvCopy")) $("#alvCopy").onclick = async () => {
    const t = $("#alvTxt");
    try { await navigator.clipboard.writeText(t.value); } catch (e) { t.select(); document.execCommand("copy"); }
    $("#alvCopy").textContent = "✔ 已複製";
  };
  if ($("#alvReset")) $("#alvReset").onclick = () => { store.set("vs_alert_draft", null); renderAlertPanel(mkt, id); };
}

function valuationCard(v, row, mkt) {
  const px = row.close;
  const cell = (x) => (x == null || !isFinite(x) ? "–" : fmt.price(x));
  const vsPx = (x) => (x == null || !isFinite(x) || !px ? "" : `<span class="${x >= px ? "pos" : "neg"}">${fmt.pct(x / px - 1, 0)}</span>`);
  const tpl = v.template || row.template;
  const roeYrs = tpl === "cyclical" ? "近 10 年" : "近 5 年";
  const relHow = { cyclical: "同業股價淨值比", construction: "同業股價淨值比", financial: "同業股價淨值比、本益比", growth_watch: "同業 EV/營收" }[tpl] || "同業本益比、股價淨值比、EV/EBIT";
  const rows = [
    ["剩餘收益模型（RIM）", v.val_rim, `以每股淨值為基礎，加上未來超額報酬（ROE − 要求報酬率）的折現；ROE 用${v.roe_used != null ? ` ${fmt.pct(v.roe_used, 1)}（${roeYrs}平均）` : "正常化 ROE"}，10 年內回到要求報酬率`],
    ["自由現金流折現（DCF）", v.val_dcf, `近 3 年平均自由現金流${mkt === "us" ? "（扣股票酬勞）" : ""}；前 5 年成長 ${fmt.pct(v.g1, 1)}，之後降到永續成長`],
    ["同業相對估值", v.val_rel, `${relHow}中位數套用到這家公司（同業：${esc(v.peer_group || row.peer_group || row.industry || "")}）`],
    ["歷史股價淨值比", v.val_pbh, `自己近 10 年股價淨值比中位數 ${v.pb_hist_median == null ? "–" : (+v.pb_hist_median).toFixed(2)} 倍 × 目前淨值；目前位在歷史第 ${v.pb_hist_pct == null ? "–" : Math.round(100 * v.pb_hist_pct)} 百分位`],
  ].filter(([, x], i) => i < 3 || x != null);
  const lbl = v.val_label || "";
  const cls = lbl === "低估" ? "pos" : lbl === "高估" ? "neg" : "";
  return `<div class="card"><h2>估值：合理價區間 ${cell(v.fair_low)}～${cell(v.fair_high)}（中位數 ${cell(v.fair_base)}）</h2>
    <p>目前股價 ${fmt.price(px)}，安全邊際 <b class="${cls}">${v.mos == null ? "–" : fmt.pct(v.mos, 0)}（${esc(lbl)}）</b>，可信度：${esc(v.val_conf || "–")}${tpl ? `　產業模板：${tplTag({ ...row, template: tpl })}，使用模型：${esc(v.val_models || "")}` : ""}</p>
    ${tpl === "cyclical" && v.gm_peak_ratio != null ? `<p class="hint">毛利率是長期平均的 ${(+v.gm_peak_ratio).toFixed(2)} 倍${v.gm_peak_ratio > 1.3 ? "：<b class=\"neg\">可能接近景氣高峰</b>，這時本益比看起來便宜是陷阱" : ""}。</p>` : ""}
    <div class="table-wrap"><table class="data compact"><thead><tr><th>模型</th><th class="num">每股價值</th><th class="num">相對股價</th><th>說明</th></tr></thead><tbody>
    ${rows.map(([n, x, h]) => `<tr><td>${n}</td><td class="num">${cell(x)}</td><td class="num">${vsPx(x)}</td><td class="hint">${h}</td></tr>`).join("")}</tbody></table></div>
    <p class="hint">市場隱含成長率 <b>${v.implied_g == null ? "–" : fmt.pct(v.implied_g, 1)}</b>（要讓 DCF 等於目前市值，前 5 年每年要成長多少）vs 過去 3 年營收年成長 ${v.hist_g == null ? "–" : fmt.pct(v.hist_g, 1)}：${v.implied_g != null && v.hist_g != null ? (v.implied_g > v.hist_g + 0.05 ? "市場期待明顯高於過去表現。" : v.implied_g < v.hist_g - 0.05 ? "市場期待低於過去表現，若能維持成長就有上漲空間。" : "市場期待與過去表現相當。") : ""}<br>
    要求報酬率 ${fmt.pct(v.r_e, 1)} ＝ 無風險利率＋β ${v.beta == null ? "（資料不足，以 1.0 計）" : (+v.beta).toFixed(2)} × 市場風險溢酬；保留率 ${fmt.pct(v.retention, 0)}。<br>
    估值是區間不是預測：輸入假設（成長率、要求報酬率）稍微改變，價值就會變動不少。三個模型差距很大時，可信度標為「低」。</p></div>`;
}


// ---------------------------------------------------------------- 看盤
function kctx(mkt, id) {
  const row = mkt === "etf" ? null : ((S.stocks || {})[mkt] || []).find((r) => r.id === id) || null;
  return { row, port: S.port, earn: S.earn, bt: S.bt };
}
async function kIndex() {
  if (!S.kidx) S.kidx = (await getJSON("data/kline/index.json", true)) || { tw: [], us: [], etf: [] };
  return S.kidx;
}
function kSearchList(q) {
  q = q.trim().toLowerCase(); if (!q) return [];
  const N = S.names || {}, out = [], seen = new Set();
  const idx = S.kidx || { tw: [], us: [], etf: [] };
  const has = { tw: new Set(idx.tw), us: new Set(idx.us), etf: new Set(idx.etf) };
  const push = (m, id, x) => { const k = m + ":" + id; if (seen.has(k)) return; seen.add(k); out.push({ m, id, n: (x && x[0]) || id, d: (x && x[1]) || "", ok: has[m].has(id) }); };
  for (const m of ["tw", "us", "etf"]) {
    const E = N[m] || {};
    if (E[q.toUpperCase()]) push(m, q.toUpperCase(), E[q.toUpperCase()]);
    if (m === "etf") { for (const t of idx.etf) if (t.toLowerCase().startsWith(q)) push("etf", t, E[t]); }
  }
  for (const m of ["tw", "us", "etf"]) {
    for (const [id, x] of Object.entries(N[m] || {})) {
      if (out.length > 60) break;
      if (id.toLowerCase().startsWith(q) || (x[0] || "").toLowerCase().includes(q) || (x[1] || "").toLowerCase().includes(q)) push(m, id, x);
    }
  }
  return out.sort((a, b) => (b.ok - a.ok)).slice(0, 14);
}
function bindChartSearch() {
  const inp = $("#kSearch"), sug = $("#kSug");
  let sel = -1, list = [];
  const show = () => {
    list = kSearchList(inp.value); sel = -1;
    sug.hidden = !list.length;
    sug.innerHTML = list.map((x, i) => `<div data-i="${i}"><b>${esc(x.id.replace(/\.TWO?$/, ""))}</b><span>${esc(x.n)}</span><small>${esc(x.d)}${x.ok ? "" : "（K 線下載中）"}</small></div>`).join("");
    $$("div[data-i]", sug).forEach((el) => (el.onmousedown = (e) => { e.preventDefault(); go(list[+el.dataset.i]); }));
  };
  const go = (x) => { if (!x) return; sug.hidden = true; inp.value = ""; inp.blur(); location.hash = `#chart/${x.m}/${encodeURIComponent(x.id)}`; };
  inp.oninput = async () => { await kIndex(); show(); };
  inp.onkeydown = (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { sel = Math.max(0, Math.min(list.length - 1, sel + (e.key === "ArrowDown" ? 1 : -1))); $$("div[data-i]", sug).forEach((el, i) => el.classList.toggle("on", i === sel)); e.preventDefault(); }
    else if (e.key === "Enter") go(list[sel >= 0 ? sel : 0]);
    else if (e.key === "Escape") sug.hidden = true;
  };
  inp.onblur = () => setTimeout(() => (sug.hidden = true), 150);
}
async function renderChartView(args) {
  await kIndex();
  let [mkt, id] = args || [];
  id = id ? decodeURIComponent(id) : null;
  const recent = store.get("vs_kline_recent", []);
  if (!mkt || !id) {
    const last = recent[0] || ((S.port || {}).holdings || []).filter((h) => h.kind === "stock").map((h) => ({ m: h.market, id: h.id }))[0] || { m: "etf", id: "0050.TW" };
    mkt = last.m; id = last.id;
  }
  store.set("vs_kline_recent", [{ m: mkt, id }, ...recent.filter((x) => !(x.m === mkt && x.id === id))].slice(0, 12));
  // 快速切換：持股＋最近看過
  const H = ((S.port || {}).holdings || []).filter((h) => h.kind === "stock").map((h) => ({ m: h.market, id: h.id }));
  const quick = [...H, ...store.get("vs_kline_recent", [])].filter((x, i, a) => a.findIndex((y) => y.m === x.m && y.id === x.id) === i).slice(0, 20);
  $("#kQuick").innerHTML = quick.map((x) => `<button class="chip ${x.m === mkt && x.id === id ? "on" : ""}" data-m="${x.m}" data-id="${esc(x.id)}">${esc(x.id.replace(/\.TWO?$/, ""))} ${esc(zhInfo(x.m, x.id, "").n || "")}</button>`).join("");
  $$("#kQuick [data-id]").forEach((b) => (b.onclick = () => { location.hash = `#chart/${b.dataset.m}/${encodeURIComponent(b.dataset.id)}`; }));
  const z = zhInfo(mkt, id, "");
  const mlabel_ = { tw: "台股", us: "美股", etf: "ETF" }[mkt] || "";
  $("#kTitle").innerHTML = `<div><div class="t">${esc(id.replace(/\.TWO?$/, ""))} ${esc(z.n || "")}</div><div class="s">${esc(z.d || "")}　<span class="pill">${mlabel_}</span> ${mkt !== "etf" ? `<a href="javascript:void 0" id="kDetail">財報與條件 ›</a>` : ""} <button class="chip" id="kBell">🔔 提醒</button></div></div><div><div class="px" id="kPx">–</div><div class="pc" id="kPc"></div></div>`;
  if ($("#kDetail")) $("#kDetail").onclick = () => openDetail(mkt, id);
  $("#kAlert").hidden = true;
  $("#kBell").onclick = () => { const b = $("#kAlert"); b.hidden = !b.hidden; if (!b.hidden) { renderAlertPanel(mkt, id); b.scrollIntoView({ behavior: "smooth", block: "nearest" }); } };
  if (S.kc) S.kc.destroy();
  S.kc = KC.mount($("#kChart"), { mkt, id, ctx: kctx(mkt, id) });
  const doc = await KC.load(mkt, id);
  if (doc && doc.c.length > 1) {
    const c = doc.c[doc.c.length - 1], p = doc.c[doc.c.length - 2], ch = c / p - 1;
    $("#kPx").textContent = c.toLocaleString("en-US", { maximumFractionDigits: c >= 100 ? 1 : c >= 1 ? 2 : 4 });
    $("#kPc").innerHTML = `<span style="color:var(${ch >= 0 ? "--up" : "--down"})">${ch >= 0 ? "+" : ""}${(ch * 100).toFixed(2)}%</span> <span class="hint">${new Date(doc.t[doc.t.length - 1] * 864e5).toISOString().slice(5, 10).replace("-", "/")}</span>`;
  }
}
