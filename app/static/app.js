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
