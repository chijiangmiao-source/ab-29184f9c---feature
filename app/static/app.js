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

const rrCard = document.querySelector("#rerouteCard");
const rrSourceId = document.querySelector("#rrSourceId");
const rrAuditId = document.querySelector("#rrAuditId");
const rrChangeBody = document.querySelector("#rrChangeTable tbody");
const rrBanner = document.querySelector("#rrBanner");
const rrBody = document.querySelector("#rrBody");

// 最近一次展示的求解结果（用于发起改线）
let currentSource = null;  // {auditId, result}
const RR_MAX = 6;

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
    const auditIdUsed = data.audit_id || (auditId || null);
    showResult(r.status, r, data);
    if (r.status === "optimal" && auditIdUsed) {
      currentSource = { auditId: auditIdUsed, result: r };
      rrSourceId.value = auditIdUsed;
      if (!rrAuditId.value.trim())
        rrAuditId.value = `${auditIdUsed}-rr`;
      rrChangeBody.innerHTML = "";
      rrCard.classList.remove("hidden");
    } else {
      currentSource = null;
      rrCard.classList.add("hidden");
    }
  } catch (e) {
    showResult("invalid", { error: `请求失败: ${e}`, loc: "$" });
  }
}

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

// ------------------------------------------------------------ 低扰动改线
function rrPipeIds() {
  return currentSource ? currentSource.result.edges.map(e => e.id) : [];
}

function addRrChangeRow(presetId = "") {
  if (rrChangeBody.rows.length >= RR_MAX) return;
  const tr = el("tr");
  const idSel = el("select");
  idSel.appendChild(el("option", { value: "", text: "选择管路…" }));
  for (const pid of rrPipeIds())
    idSel.appendChild(el("option", { value: pid, text: pid }));
  if (presetId && rrPipeIds().includes(presetId)) idSel.value = presetId;
  const mkInt = () => el("input", { type: "text", inputmode: "integer",
                                   placeholder: "（不改）" });
  const loIn = mkInt(), hiIn = mkInt(), costIn = mkInt();
  tr.appendChild(el("td", {}, idSel));
  tr.appendChild(el("td", {}, loIn));
  tr.appendChild(el("td", {}, hiIn));
  tr.appendChild(el("td", {}, costIn));
  tr.appendChild(el("td", {}, el("button", {
    type: "button", text: "删除",
    onclick: () => tr.remove(),
  })));
  rrChangeBody.appendChild(tr);
}

function buildRrChanges() {
  const seen = new Set();
  const changes = [];
  [...rrChangeBody.rows].forEach((row, i) => {
    const sel = row.querySelector("select");
    const ins = row.querySelectorAll("input");
    const pid = sel.value;
    if (!pid) throw { loc: `changes[${i}].id`, message: "请选择管路标识" };
    if (seen.has(pid))
      throw { loc: `changes[${i}].id`, message: `管路重复: ${pid}` };
    seen.add(pid);
    const ch = { id: pid };
    const fields = [["lo", ins[0]], ["hi", ins[1]], ["cost", ins[2]]];
    let given = 0;
    for (const [name, inp] of fields) {
      const t = inp.value.trim();
      if (t) {
        ch[name] = safeInt(t, `changes[${i}].${name}`);
        given += 1;
      }
    }
    if (!given)
      throw { loc: `changes[${i}]`, message: "至少给出一个新的 lo/hi/cost" };
    changes.push(ch);
  });
  return changes;
}

function renderRrOptimal(r) {
  rrBody.appendChild(el("div", { class: "kv" },
    el("div", { class: "box" },
      el("div", { class: "label", text: "修订成本（第一级）" }),
      el("div", { class: "value mono",
                  text: r.objectives.revised_cost })),
    el("div", { class: "box" },
      el("div", { class: "label", text: "L1 扰动 Σ|新−原|（第二级）" }),
      el("div", { class: "value mono",
                  text: r.objectives.perturbation_l1 })),
    el("div", { class: "box" },
      el("div", { class: "label", text: "裁决管路序（第三级）" }),
      el("div", { class: "mono", style: "font-size:12px",
                  text: r.objectives.tie_order.join(" → ") }))));

  const rows = r.pipe_changes.map(c => {
    const mark = name =>
      c.changed_fields.includes(name)
        ? el("span", { class: "rr-changed", text: "改" }) : null;
    const fmt = (oldV, newV, name) =>
      el("span", { class: "mono" },
        el("span", {}, mark(name)),
        el("span", { text: ` ${oldV} → ${newV}` }));
    const deltaCls = c.abs_delta === 0 ? "rc-zero mono"
      : "rc-neg mono";
    return el("tr", {},
      el("td", { class: "mono", text: c.id }),
      el("td", { text: `${c.from} → ${c.to}` }),
      el("td", {}, fmt(c.old_lo, c.new_lo, "lo")),
      el("td", {}, fmt(c.old_hi, c.new_hi, "hi")),
      el("td", {}, fmt(c.old_cost, c.new_cost, "cost")),
      el("td", { class: "mono", text: `${c.source_flow} → ${c.flow}` }),
      el("td", { class: deltaCls,
                 text: `${c.signed_delta >= 0 ? "+" : ""}${c.signed_delta}` }),
      el("td", { class: "mono", text: c.abs_delta }));
  });
  rrBody.appendChild(el("h3", {
    text: "修改项与原/新流量对照（含逐管改变量）" }));
  rrBody.appendChild(el("div", { class: "table-wrap" },
    el("table", { class: "result" },
      el("thead", {}, el("tr", {},
        el("th", { text: "管路" }), el("th", { text: "方向" }),
        el("th", { text: "lo 原→新" }), el("th", { text: "hi 原→新" }),
        el("th", { text: "cost 原→新" }),
        el("th", { text: "流量 原→新" }),
        el("th", { text: "有符号改变量" }),
        el("th", { text: "|改变量|" }))),
      el("tbody", {}, ...rows))));

  const potRows = Object.entries(r.potentials).map(([s, p]) =>
    el("tr", {},
      el("td", { text: s }),
      el("td", { class: "mono", text: p.cost }),
      el("td", { class: "mono", text: p.perturbation })));
  rrBody.appendChild(el("details", {},
    el("summary", { text: "成对站点势 (π成本, π扰动) 与字典序约化费用" }),
    el("p", { class: "hint",
      text: "每条正残量弧的 [成本约化费用, 扰动约化费用] 字典序 ≥ [0,0]；"
            + "第一分量单独 ≥0 即修订成本最优。" }),
    el("div", { class: "table-wrap" },
      el("table", { class: "result mono" },
        el("thead", {}, el("tr", {},
          el("th", { text: "站点" }),
          el("th", { text: "π 成本" }),
          el("th", { text: "π 扰动" }))),
        el("tbody", {}, ...potRows))),
    el("div", { class: "table-wrap" },
      el("table", { class: "result mono" },
        el("thead", {}, el("tr", {},
          el("th", { text: "弧（管/方向）" }),
          el("th", { text: "容量" }),
          el("th", { text: "原费用" }),
          el("th", { text: "[成本rc, 扰动rc]" }))),
        el("tbody", {},
          ...r.edges.flatMap(e => e.residual.map(a => el("tr", {},
            el("td", { text: `${e.id} ${a.direction === "forward" ? "正向" : "反向"}` }),
            el("td", { text: a.capacity }),
            el("td", { text: a.cost }),
            el("td", { class: a.lex_reduced_cost[1] < 0 ? "rc-neg"
                       : "rc-zero",
                       text: `[${a.lex_reduced_cost[0]}, ${a.lex_reduced_cost[1]}]` })))))))));

  const nrows = r.node_check.map(n => el("tr", {},
    el("td", { text: n.station }),
    el("td", { class: "mono", text: n.inflow }),
    el("td", { class: "mono", text: n.outflow }),
    el("td", { class: "mono", text: n.net_out }),
    el("td", { class: "mono", text: n.balance }),
    el("td", { class: n.ok ? "ok mono" : "bad mono",
               text: n.ok ? "✓" : "✗" })));
  rrBody.appendChild(el("details", {},
    el("summary", { text: "改线后逐站净供需复算" }),
    el("div", { class: "table-wrap" },
      el("table", { class: "result" },
        el("thead", {}, el("tr", {},
          el("th", { text: "站点" }), el("th", { text: "流入" }),
          el("th", { text: "流出" }), el("th", { text: "净流出" }),
          el("th", { text: "应等于" }), el("th", { text: "" }))),
        el("tbody", {}, ...nrows)))));
}

function renderRrInfeasible(r) {
  rrBody.appendChild(el("p", {},
    el("b", { class: "bad", text: "覆盖后无可行流。" }),
    el("span", { class: "hint", text: ` ${r.note || ""}` })));
  const rows = r.unmet_demand.map(u => el("tr", {},
    el("td", { text: u.station }),
    el("td", { class: "mono", text: u.required }),
    el("td", { class: "mono bad", text: u.unsatisfied })));
  rrBody.appendChild(el("div", { class: "table-wrap" },
    el("table", { class: "result" },
      el("thead", {}, el("tr", {},
        el("th", { text: "节点" }), el("th", { text: "需要量" }),
        el("th", { text: "未满足量" }))),
      el("tbody", {}, ...rows))));
  rrBody.appendChild(el("p", { class: "hint mono",
    text: "残量可达站点：" + (r.reachable_from_source.join(", ") || "（空）") }));
}

function showRr(kind, r, envelope) {
  rrBody.innerHTML = "";
  rrBanner.innerHTML = "";
  if (kind === "optimal") {
    rrBanner.appendChild(el("div", { class: "banner optimal",
      text: `✓ 改线完成：修订成本 ${r.objectives.revised_cost} · `
            + `L1 扰动 ${r.objectives.perturbation_l1}`
            + (envelope?.replayed ? "（审计重放原记录）" : "") }));
    renderRrOptimal(r);
  } else if (kind === "infeasible") {
    rrBanner.appendChild(el("div", { class: "banner infeasible",
      text: "✗ 覆盖后修订网络无可行流（证据如下）" }));
    renderRrInfeasible(r);
  } else {
    rrBanner.appendChild(el("div", { class: "banner invalid",
      text: `⚠ ${r.loc || "$"} — ${r.error}` }));
  }
  rrCard.classList.remove("hidden");
  rrCard.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function submitReroute() {
  if (!currentSource) {
    showRr("invalid", { error: "请先取得带审计标识的 optimal 求解结果",
                        loc: "source_audit_id" });
    return;
  }
  let changes;
  try {
    changes = buildRrChanges();
  } catch (e) {
    showRr("invalid", e);
    return;
  }
  const body = { source_audit_id: currentSource.auditId, changes };
  const rrId = rrAuditId.value.trim();
  const headers = { "Content-Type": "application/json" };
  if (rrId) headers["X-Audit-Id"] = rrId;
  try {
    const resp = await fetch("/api/reroute", {
      method: "POST", headers, body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (resp.status === 400 || resp.status === 404 || resp.status === 409) {
      showRr("invalid", { error: data.error, loc: data.loc });
      return;
    }
    const r = data.result || data;
    showRr(r.status, r, data);
  } catch (e) {
    showRr("invalid", { error: `请求失败: ${e}`, loc: "$" });
  }
}

async function fetchRerouteRecord() {
  const rid = document.querySelector("#rrFetchId").value.trim();
  if (!rid) {
    showRr("invalid", { error: "请填写改线审计标识", loc: "audit_id" });
    return;
  }
  try {
    const resp = await fetch(`/api/records/${encodeURIComponent(rid)}`);
    const data = await resp.json();
    if (resp.status !== 200) {
      showRr("invalid", { error: data.error, loc: "audit_id" });
      return;
    }
    const r = data.result;
    if (r?.kind !== "reroute") {
      showRr("invalid", {
        error: `审计 ${rid} 不是改线记录（status=${r?.status ?? data.status}）`,
        loc: "audit_id" });
      return;
    }
    currentSource = null;  // 读取的历史改线仅作展示
    rrSourceId.value = r.source_audit_id;
    showRr(r.status, r, { replayed: true });
  } catch (e) {
    showRr("invalid", { error: `请求失败: ${e}`, loc: "$" });
  }
}

document.querySelector("#rrAdd").onclick = () => addRrChangeRow();
document.querySelector("#rrSubmit").onclick = submitReroute;
document.querySelector("#rrFetch").onclick = fetchRerouteRecord;

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
