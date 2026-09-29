"use strict";

const MAX_STATIONS = 12;
const MAX_PIPES = 36;

const stationBody = document.querySelector("#stationTable tbody");
const pipeBody = document.querySelector("#pipeTable tbody");
const counter = document.querySelector("#counter");
const requestPreview = document.querySelector("#requestPreview");
const resultCard = document.querySelector("#resultCard");
const statusBanner = document.querySelector("#statusBanner");
const resultBody = document.querySelector("#resultBody");

const rerouteCard = document.querySelector("#rerouteCard");
const modBody = document.querySelector("#modTable tbody");
const reroutePreview = document.querySelector("#reroutePreview");
const rerouteResultCard = document.querySelector("#rerouteResultCard");
const rerouteBanner = document.querySelector("#rerouteBanner");
const rerouteBody = document.querySelector("#rerouteBody");

// 最近一次求解（改线来源）：必须带稳定审计标识、状态 optimal
let lastSource = null;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) if (c != null) node.appendChild(c);
  return node;
}

// ------------------------------------------------------------ 动态行
function addStationRow(id = "", balance = "") {
  if (stationBody.rows.length >= MAX_STATIONS) return;
  const tr = el("tr");
  const idInput = el("input", { type: "text", maxlength: "32", value: id });
  const balInput = el("input", { type: "text", inputmode: "integer", value: balance });
  tr.appendChild(el("td", {}, idInput));
  tr.appendChild(el("td", {}, balInput));
  tr.appendChild(el("td", {}, el("button", {
    type: "button", text: "删除",
    onclick: () => { tr.remove(); renderCounter(); },
  })));
  stationBody.appendChild(tr);
  renderCounter();
}

function stationIds() {
  return [...stationBody.rows].map(row =>
    row.querySelector("input").value.trim());
}

function addPipeRow(p = {}) {
  if (pipeBody.rows.length >= MAX_PIPES) return;
  const tr = el("tr");
  const idIn = el("input", { type: "text", maxlength: "32", value: p.id || "" });
  const fromSel = el("select");
  const toSel = el("select");
  const loIn = el("input", { type: "text", inputmode: "integer", value: p.lo ?? "" });
  const hiIn = el("input", { type: "text", inputmode: "integer", value: p.hi ?? "" });
  const costIn = el("input", { type: "text", inputmode: "integer",
                               value: p.cost ?? "" });
  const refresh = () => {
    const ids = stationIds();
    for (const [sel, cur] of [[fromSel, p.from], [toSel, p.to]]) {
      const old = sel.value || cur;
      sel.innerHTML = "";
      sel.appendChild(el("option", { value: "", text: "选择…" }));
      for (const sid of ids) sel.appendChild(el("option", { value: sid, text: sid }));
      if (old && ids.includes(old)) sel.value = old;
    }
  };
  stationBody.addEventListener("change", refresh);
  stationBody.addEventListener("input", refresh);
  tr.appendChild(el("td", {}, idIn));
  tr.appendChild(el("td", {}, fromSel));
  tr.appendChild(el("td", {}, toSel));
  tr.appendChild(el("td", {}, loIn));
  tr.appendChild(el("td", {}, hiIn));
  tr.appendChild(el("td", {}, costIn));
  tr.appendChild(el("td", {}, el("button", {
    type: "button", text: "删除",
    onclick: () => { tr.remove(); renderCounter(); },
  })));
  pipeBody.appendChild(tr);
  refresh();
  renderCounter();
}

function renderCounter() {
  counter.textContent =
    `站点 ${stationBody.rows.length}/${MAX_STATIONS} · 管路 ${pipeBody.rows.length}/${MAX_PIPES}`;
}

// ------------------------------------------------------------ 构造请求
function safeInt(v, loc) {
  const t = String(v ?? "").trim();
  if (!/^[+-]?\d+$/.test(t)) throw { loc, message: "必须是整数" };
  return Number(t);
}

function buildPayload() {
  const stations = [];
  const ids = new Set();
  [...stationBody.rows].forEach((row, i) => {
    const inputs = row.querySelectorAll("input");
    const sid = inputs[0].value.trim();
    if (!sid) throw { loc: `stations[${i}].id`, message: "站点标识不能为空" };
    if (ids.has(sid)) throw { loc: `stations[${i}].id`, message: `站点重复: ${sid}` };
    ids.add(sid);
    const balance = safeInt(inputs[1].value, `stations[${i}].balance`);
    stations.push({ id: sid, balance });
  });

  const pipes = [];
  const eids = new Set();
  [...pipeBody.rows].forEach((row, i) => {
    const inputs = row.querySelectorAll("input");
    const selects = row.querySelectorAll("select");
    const eid = inputs[0].value.trim() || `e${i}`;
    if (eids.has(eid)) throw { loc: `pipes[${i}].id`, message: `管路标识重复: ${eid}` };
    eids.add(eid);
    if (!selects[0].value) throw { loc: `pipes[${i}].from`, message: "请选择起点" };
    if (!selects[1].value) throw { loc: `pipes[${i}].to`, message: "请选择终点" };
    if (selects[0].value === selects[1].value)
      throw { loc: `pipes[${i}]`, message: "起点终点不能相同" };
    const lo = safeInt(inputs[1].value, `pipes[${i}].lo`);
    const hi = safeInt(inputs[2].value, `pipes[${i}].hi`);
    const cost = safeInt(inputs[3].value, `pipes[${i}].cost`);
    pipes.push({ id: eid, from: selects[0].value, to: selects[1].value,
                 lo, hi, cost });
  });
  return { stations, pipes };
}

// ------------------------------------------------------------ 渲染结果
function rcClass(rc) { return rc < 0 ? "rc-neg" : rc === 0 ? "rc-zero" : "rc-pos"; }

function renderOptimal(r) {
  const boxes = el("div", { class: "kv" },
    el("div", { class: "box" },
      el("div", { class: "label", text: "总成本" }),
      el("div", { class: "value", text: r.total_cost })),
    el("div", { class: "box" },
      el("div", { class: "label", text: "消除的负环数" }),
      el("div", { class: "value", text: r.cycles_cancelled })),
    el("div", { class: "box" },
      el("div", { class: "label", text: "算法" }),
      el("div", { style: "font-size:12px", text: r.algorithm })),
  );
  resultBody.appendChild(boxes);

  const potRows = Object.entries(r.potentials)
    .map(([s, p]) => el("tr", {},
      el("td", { text: s }), el("td", { class: "mono", text: p })));
  resultBody.appendChild(el("h3", { text: "站点势 π（正残量边 c + π[u] − π[v] ≥ 0）" }));
  resultBody.appendChild(el("div", { class: "table-wrap" },
    el("table", { class: "result mono" },
      el("thead", {}, el("tr", {},
        el("th", { text: "站点" }), el("th", { text: "π" }))),
      el("tbody", {}, ...potRows))));

  const rows = r.edges.map(e => {
    const rcs = e.residual.map(x =>
      `${x.direction === "forward" ? "正向" : "反向"} 容${x.capacity} ` +
      `<span class="${rcClass(x.reduced_cost)}">c*=${x.reduced_cost}</span>`);
    return el("tr", {},
      el("td", { class: "mono", text: e.id }),
      el("td", { text: `${e.from} → ${e.to}` }),
      el("td", { class: "mono", text: `${e.flow} ∈ [${e.lo}, ${e.hi}]` }),
      el("td", { class: "mono", text: e.cost }),
      el("td", { class: "mono", text: e.cost_contribution }),
      el("td", { html: rcs.join("<br>") || "无正残量弧" }));
  });
  resultBody.appendChild(el("h3", { text: "逐边流量与残量约化成本（复算证据）" }));
  resultBody.appendChild(el("div", { class: "table-wrap" },
    el("table", { class: "result" },
      el("thead", {}, el("tr", {},
        el("th", { text: "管路" }), el("th", { text: "方向" }),
        el("th", { text: "流量（界）" }), el("th", { text: "单位成本" }),
        el("th", { text: "费用贡献" }), el("th", { text: "正残量弧约化成本" }))),
      el("tbody", {}, ...rows))));

  const nrows = r.node_check.map(n => el("tr", {},
    el("td", { text: n.station }),
    el("td", { class: "mono", text: n.inflow }),
    el("td", { class: "mono", text: n.outflow }),
    el("td", { class: "mono", text: n.net_out }),
    el("td", { class: "mono", text: n.balance }),
    el("td", { class: n.ok ? "ok mono" : "bad mono", text: n.ok ? "✓" : "✗" })));
  resultBody.appendChild(el("details", { open: "" },
    el("summary", { text: "逐站净供需复算（流出−流入 = balance？）" }),
    el("div", { class: "table-wrap" },
      el("table", { class: "result" },
        el("thead", {}, el("tr", {},
          el("th", { text: "站点" }), el("th", { text: "流入" }),
          el("th", { text: "流出" }),
          el("th", { text: "净流出" }), el("th", { text: "应等于" }),
          el("th", { text: "" }))),
        el("tbody", {}, ...nrows)))));
}

function renderInfeasible(r) {
  resultBody.appendChild(el("p", {}, el("b", { class: "bad", text: "无可行流。" }),
    el("span", { text: r.note })));
  const rows = r.unmet_demand.map(u => el("tr", {},
    el("td", { text: u.station }),
    el("td", { class: "mono", text: u.required }),
    el("td", { class: "mono bad", text: u.unsatisfied })));
  resultBody.appendChild(el("h3", { text: "未满足需求（超源出边残余）" }));
  resultBody.appendChild(el("div", { class: "table-wrap" },
    el("table", { class: "result" },
      el("thead", {}, el("tr", {},
        el("th", { text: "节点" }), el("th", { text: "需要量" }),
        el("th", { text: "未满足量" }))),
      el("tbody", {}, ...rows))));
  resultBody.appendChild(el("h3", { text: "残量可达站点（失败 cut 证据）" }));
  resultBody.appendChild(el("p", { class: "mono", text:
    r.reachable_from_source.join(", ") || "（空）" }));
}

function renderInvalid(e) {
  resultBody.appendChild(el("p", {},
    el("b", { class: "bad", text: "输入非法：" }),
    el("span", { class: "mono", text: ` ${e.loc} — ${e.error}` })));
}

async function submit() {
  let payload;
  try {
    payload = buildPayload();
  } catch (e) {
    showResult("invalid", { error: e.message, loc: e.loc });
    return;
  }
  requestPreview.textContent = JSON.stringify(payload, null, 2);
  const auditId = document.querySelector("#auditId").value.trim();
  try {
    const headers = { "Content-Type": "application/json" };
    if (auditId) headers["X-Audit-Id"] = auditId;
    const resp = await fetch("/api/solve", {
      method: "POST", headers, body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (resp.status === 400) {
      showResult("invalid", { error: data.error, loc: data.loc });
      return;
    }
    if (resp.status === 409) {
      showResult("invalid", {
        error: `${data.error}（已存指纹 ${
          (data.conflict?.payload_fingerprint || "").slice(0, 12)}…）`,
        loc: "X-Audit-Id",
      });
      return;
    }
    const r = data.result || data;
    showResult(r.status, r, data);
    registerSource(data, r);
  } catch (e) {
    showResult("invalid", { error: `请求失败: ${e}`, loc: "$" });
  }
}

// ------------------------------------------------------------ 低扰动改线
const MAX_MODS = 6;

function registerSource(envelope, r) {
  // 只有带稳定审计标识且 optimal 的求解结果才能作为改线来源
  if (r.status === "optimal" && envelope?.audit_id) {
    lastSource = { auditId: envelope.audit_id, result: r };
    rerouteCard.classList.remove("hidden");
    resetModRows();
  } else {
    lastSource = null;
    rerouteCard.classList.add("hidden");
    rerouteResultCard.classList.add("hidden");
  }
}

function addModRow(pipeId = "") {
  if (modBody.rows.length >= MAX_MODS) return;
  const tr = el("tr");
  const pipeSel = el("select");
  pipeSel.appendChild(el("option", { value: "", text: "选择管路…" }));
  if (lastSource) {
    for (const e of lastSource.result.edges)
      pipeSel.appendChild(el("option", { value: e.id, text: e.id }));
  }
  if (pipeId) pipeSel.value = pipeId;
  const mkInt = () => el("input", { type: "text", inputmode: "integer", placeholder: "不覆盖" });
  const loIn = mkInt(), hiIn = mkInt(), costIn = mkInt();
  tr.appendChild(el("td", {}, pipeSel));
  tr.appendChild(el("td", {}, loIn));
  tr.appendChild(el("td", {}, hiIn));
  tr.appendChild(el("td", {}, costIn));
  tr.appendChild(el("td", {}, el("button", {
    type: "button", text: "删除",
    onclick: () => tr.remove(),
  })));
  modBody.appendChild(tr);
}

function resetModRows() {
  modBody.innerHTML = "";
  addModRow();
  reroutePreview.textContent = "";
}

function buildReroutePayload() {
  if (!lastSource) throw { loc: "source_audit_id", message: "缺少 optimal 来源审计记录" };
  const mods = [];
  const seen = new Set();
  [...modBody.rows].forEach((row, i) => {
    const sel = row.querySelector("select");
    const ins = row.querySelectorAll("input");
    if (!sel.value) throw { loc: `modifications[${i}].pipe`, message: "请选择管路标识" };
    if (seen.has(sel.value))
      throw { loc: `modifications[${i}].pipe`, message: `管路覆盖重复: ${sel.value}` };
    seen.add(sel.value);
    const item = { pipe: sel.value };
    const fields = [["lo", ins[0]], ["hi", ins[1]], ["cost", ins[2]]];
    let given = 0;
    for (const [fld, inp] of fields) {
      const t = inp.value.trim();
      if (t) { item[fld] = safeInt(t, `modifications[${i}].${fld}`); given++; }
    }
    if (!given) throw { loc: `modifications[${i}]`, message: "每项至少覆盖一个字段" };
    mods.push(item);
  });
  if (!mods.length) throw { loc: "modifications", message: "至少给出一条覆盖项" };
  return { source_audit_id: lastSource.auditId, modifications: mods };
}

function renderRerouteOptimal(r, envelope) {
  rerouteBanner.appendChild(el("div", { class: "banner optimal",
    text: `✓ 改线完成：修订成本 ${r.total_cost}，总绝对改变量 ${
      r.objectives.level2_total_abs_change}` +
      (envelope?.replayed ? "（审计重放原记录）" : "") }));

  const boxes = el("div", { class: "kv" },
    el("div", { class: "box" },
      el("div", { class: "label", text: "一级：修订最小成本" }),
      el("div", { class: "value", text: r.objectives.level1_revised_cost })),
    el("div", { class: "box" },
      el("div", { class: "label", text: "二级：总绝对改变量 Σ|新−原|" }),
      el("div", { class: "value", text: r.objectives.level2_total_abs_change })),
    el("div", { class: "box" },
      el("div", { class: "label", text: "平局裁决" }),
      el("div", { style: "font-size:12px", text: "原管路标识序流量向量字典序最小" })));
  rerouteBody.appendChild(boxes);

  // 修改项与原/新流量对照
  const srcEdges = Object.fromEntries(r.frozen.source_pipes.map(p => [p.id, p]));
  const newEdges = Object.fromEntries(r.frozen.revised_pipes.map(p => [p.id, p]));
  const modMap = {};
  for (const m of r.frozen.modifications) modMap[m.pipe] = m;

  const cellBound = (id, edge, fld) => {
    const overridden = modMap[id] && fld in modMap[id];
    return el("td", { class: overridden ? "changed mono" : "mono",
      text: String(edge[fld]) });
  };
  const rows = r.changes_core.map(c => {
    const se = srcEdges[c.id], ne = newEdges[c.id];
    return el("tr", { class: c.abs_change ? "" : "unchanged" },
      el("td", { class: "mono", text: c.id }),
      el("td", { text: `${se.from} → ${se.to}` }),
      cellBound(c.id, se, "lo"), cellBound(c.id, ne, "lo"),
      cellBound(c.id, se, "hi"), cellBound(c.id, ne, "hi"),
      cellBound(c.id, se, "cost"), cellBound(c.id, ne, "cost"),
      el("td", { class: "mono", text: c.source_flow }),
      el("td", { class: c.abs_change ? "changed mono" : "mono", text: c.new_flow }),
      el("td", { class: "mono", text: c.delta }),
      el("td", { class: "mono", text: c.abs_change }));
  });
  rerouteBody.appendChild(el("h3", { text: "逐管对照（黄底=本次覆盖字段或流量改变）" }));
  rerouteBody.appendChild(el("div", { class: "table-wrap" },
    el("table", { class: "result" },
      el("thead", {}, el("tr", {},
        ...["管路", "方向", "原 lo", "新 lo", "原 hi", "新 hi",
            "原 cost", "新 cost", "原流量", "新流量", "Δ", "|Δ|"]
          .map(t => el("th", { text: t })))),
      el("tbody", {}, ...rows))));

  // 两级目标的独立复算证据
  const pe = r.perturbation_evidence;
  const arcRows = pe.arcs.map(a => el("tr", {},
    el("td", { class: "mono", text: a.pipe }),
    el("td", { text: `${a.from}→${a.to}` }),
    el("td", { text: a.direction === "forward" ? "正向" : "反向" }),
    el("td", { class: "hint", style: "font-size:12px",
      text: a.segment === "toward_source" ? "朝来源流量" : "远离来源流量" }),
    el("td", { class: "mono", text: a.capacity }),
    el("td", { class: "mono", text: a.cost }),
    el("td", { class: rcClass(a.reduced_cost) + " mono", text: a.reduced_cost }),
    el("td", { class: "mono", text: a.w2 }),
    el("td", { class: a.reduced_w2 == null ? "hint mono" :
                   rcClass(a.reduced_w2) + " mono",
      text: a.reduced_w2 == null ? "—" : a.reduced_w2 }),
    el("td", { class: "mono", style: "font-size:12px",
      text: a.lex_slack == null ? "—" : `[${a.lex_slack.join(", ")}]` })));
  rerouteBody.appendChild(el("h3", {
    text: "每条正残量分段弧的字典序约化费用（成本势 π / 扰动势 p / 字典序势 q）" }));
  rerouteBody.appendChild(el("details", {},
    el("summary", { text: "三级最优性如何独立复算？" }),
    el("ul", {}, ...pe.explained.map(t => el("li", { style: "font-size:13px", text: t })))));
  rerouteBody.appendChild(el("div", { class: "table-wrap" },
    el("table", { class: "result" },
      el("thead", {}, el("tr", {},
        ...["管路", "弧", "方向", "分段", "容量", "弧成本",
            "成本约化 c+πu−πv", "w2", "扰动约化 w2+pu−pv", "字典序松弛 q"]
          .map(t => el("th", { text: t })))),
      el("tbody", {}, ...arcRows))));

  const stRows = Object.entries(pe.potentials).map(([s]) => el("tr", {},
    el("td", { text: s }),
    el("td", { class: "mono", text: pe.potentials[s] }),
    el("td", { class: "mono", text: pe.perturbation_potentials[s] }),
    el("td", { class: "mono", style: "font-size:12px",
      text: `[${pe.lex_potentials[s].join(", ")}]` })));
  rerouteBody.appendChild(el("details", {},
    el("summary", { text: "成对站点势：π（成本）/ p（扰动）/ q（字典序向量）" }),
    el("div", { class: "table-wrap" },
      el("table", { class: "result" },
        el("thead", {}, el("tr", {},
          el("th", { text: "站点" }), el("th", { text: "π" }),
          el("th", { text: "p" }), el("th", { text: "q（按管路标识序）" }))),
        el("tbody", {}, ...stRows)))));

  const nodeOk = r.node_check.every(n => n.ok);
  rerouteBody.appendChild(el("p", { class: nodeOk ? "ok" : "bad",
    text: nodeOk ? "逐站净供需复算全部通过（相同供需，仅改界/成本）。"
                 : "存在站点净供需不满足！" }));
}

function showReroute(kind, r, envelope) {
  rerouteResultCard.classList.remove("hidden");
  rerouteBody.innerHTML = "";
  rerouteBanner.innerHTML = "";
  if (kind === "optimal") renderRerouteOptimal(r, envelope);
  else if (kind === "infeasible") {
    rerouteBanner.appendChild(el("div", { class: "banner infeasible",
      text: "✗ 覆盖后修订网络无可行流（下界消去证据，可独立复算）" }));
    const rows = (r.unmet_demand || []).map(u => el("tr", {},
      el("td", { text: u.station }),
      el("td", { class: "mono", text: u.required }),
      el("td", { class: "mono bad", text: u.unsatisfied })));
    if (rows.length) {
      rerouteBody.appendChild(el("h3", { text: "未满足需求（超源汇侧弧残余）" }));
      rerouteBody.appendChild(el("div", { class: "table-wrap" },
        el("table", { class: "result" },
          el("thead", {}, el("tr", {},
            el("th", { text: "站点" }), el("th", { text: "需要量" }),
            el("th", { text: "未满足量" }))),
          el("tbody", {}, ...rows))));
    }
    rerouteBody.appendChild(el("h3", { text: "残量可达站点（失败 cut 证据）" }));
    rerouteBody.appendChild(el("p", { class: "mono",
      text: (r.reachable_from_source || []).join(", ") || "（空）" }));
  } else {
    rerouteBanner.appendChild(el("div", { class: "banner invalid",
      text: "⚠ 改线请求被拒绝" }));
    rerouteBody.appendChild(el("p", {},
      el("b", { class: "bad", text: "输入非法：" }),
      el("span", { class: "mono", text: ` ${r.loc} — ${r.error}` })));
  }
  rerouteResultCard.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function submitReroute() {
  let payload;
  try {
    payload = buildReroutePayload();
  } catch (e) {
    showReroute("invalid", { error: e.message, loc: e.loc });
    return;
  }
  reroutePreview.textContent = JSON.stringify(payload, null, 2);
  const auditId = document.querySelector("#rerouteAuditId").value.trim();
  if (!auditId) {
    showReroute("invalid", {
      error: "改线必须提交稳定改线审计标识（用于重放与读取）",
      loc: "audit_id",
    });
    return;
  }
  try {
    const headers = { "Content-Type": "application/json" };
    if (auditId) headers["X-Audit-Id"] = auditId;
    const resp = await fetch("/api/reroute", {
      method: "POST", headers, body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (resp.status === 400 || resp.status === 404 || resp.status === 409) {
      const r = data.result && data.status === "invalid" ? data.result : data;
      showReroute("invalid", { error: r.error, loc: r.loc || "audit_id" }, data);
      return;
    }
    showReroute(data.result.status, data.result, data);
  } catch (e) {
    showReroute("invalid", { error: `请求失败: ${e}`, loc: "$" });
  }
}

async function loadRerouteRecord() {
  const auditId = document.querySelector("#rerouteAuditId").value.trim();
  if (!auditId) {
    showReroute("invalid", { error: "请先填写改线审计标识", loc: "audit_id" });
    return;
  }
  try {
    const resp = await fetch(`/api/records/${encodeURIComponent(auditId)}`);
    const data = await resp.json();
    if (resp.status !== 200) {
      showReroute("invalid", { error: data.error || "记录不存在", loc: "audit_id" });
      return;
    }
    const r = data.result;
    if (data.status === "optimal" && !r.frozen) {
      showReroute("invalid",
        { error: `标识 ${auditId} 是一条原始求解记录，不是改线审计记录`,
          loc: "audit_id" });
      return;
    }
    showReroute(data.status === "optimal" ? "optimal"
              : data.status === "infeasible" ? "infeasible" : "invalid",
                r, { audit_id: data.audit_id, replayed: true });
  } catch (e) {
    showReroute("invalid", { error: `请求失败: ${e}`, loc: "$" });
  }
}

document.querySelector("#addMod").onclick = () => addModRow();
document.querySelector("#submitReroute").onclick = submitReroute;
document.querySelector("#loadRerouteRec").onclick = loadRerouteRecord;

function showResult(kind, r, envelope) {
  resultCard.classList.remove("hidden");
  resultBody.innerHTML = "";
  statusBanner.innerHTML = "";
  if (kind === "optimal") {
    statusBanner.appendChild(el("div", { class: "banner optimal",
      text: `✓ 已取得最小费用可行流${envelope?.replayed ? "（审计重放原记录）" : ""}` }));
    renderOptimal(r);
  } else if (kind === "infeasible") {
    statusBanner.appendChild(el("div", { class: "banner infeasible",
      text: "✗ 网络无可行流（下列证据可复算定位瓶颈）" }));
    renderInfeasible(r);
  } else {
    statusBanner.appendChild(el("div", { class: "banner invalid",
      text: "⚠ 输入校验失败" }));
    renderInvalid(r);
  }
}

// ------------------------------------------------------------ 初始化
document.querySelector("#addStation").onclick = () => addStationRow();
document.querySelector("#addPipe").onclick = () => addPipeRow();
document.querySelector("#submitBtn").onclick = submit;
document.querySelector("#clearAll").onclick = () => {
  stationBody.innerHTML = "";
  pipeBody.innerHTML = "";
  renderCounter();
};

const SAMPLE = {
  stations: [
    { id: "S1", balance: 6 }, { id: "S2", balance: 4 },
    { id: "V1", balance: 0 }, { id: "V2", balance: 0 },
    { id: "D1", balance: -3 }, { id: "D2", balance: -7 },
  ],
  pipes: [
    { id: "p1", from: "S1", to: "V1", lo: 1, hi: 8, cost: 2 },
    { id: "p2", from: "S2", to: "V1", lo: 0, hi: 5, cost: 4 },
    { id: "p3", from: "S2", to: "V2", lo: 1, hi: 6, cost: 1 },
    { id: "p4", from: "V1", to: "D1", lo: 0, hi: 4, cost: 0 },
    { id: "p5", from: "V1", to: "D2", lo: 0, hi: 9, cost: 3 },
    { id: "p6", from: "V2", to: "D2", lo: 0, hi: 7, cost: 2 },
    { id: "p7", from: "D1", to: "D2", lo: 0, hi: 3, cost: -1 },
  ],
};
document.querySelector("#loadSample").onclick = () => {
  stationBody.innerHTML = "";
  pipeBody.innerHTML = "";
  for (const s of SAMPLE.stations) addStationRow(s.id, String(s.balance));
  for (const p of SAMPLE.pipes) addPipeRow(p);
};
for (const s of SAMPLE.stations) addStationRow(s.id, String(s.balance));
for (const p of SAMPLE.pipes) addPipeRow(p);
renderCounter();
