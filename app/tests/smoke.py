"""本题 API 冒烟脚本（在运行中的 web 容器内执行，访问本机服务）。

覆盖：健康检查、页面、最优求解 + 约化成本证据、审计重放原记录、
改载荷拒绝(409)、不可行证据、非法输入字段定位。任一断言失败即以
非零退出码结束。
"""

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"


def call(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers=headers or {})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
            return resp.status, (
                json.loads(raw) if "json" in resp.headers.get(
                    "Content-Type", "") else raw)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


SAMPLE = {
    "stations": [
        {"id": "S1", "balance": 6}, {"id": "S2", "balance": 4},
        {"id": "V1", "balance": 0}, {"id": "V2", "balance": 0},
        {"id": "D1", "balance": -3}, {"id": "D2", "balance": -7}],
    "pipes": [
        {"id": "p1", "from": "S1", "to": "V1", "lo": 1, "hi": 8, "cost": 2},
        {"id": "p2", "from": "S2", "to": "V1", "lo": 0, "hi": 5, "cost": 4},
        {"id": "p3", "from": "S2", "to": "V2", "lo": 1, "hi": 6, "cost": 1},
        {"id": "p4", "from": "V1", "to": "D1", "lo": 0, "hi": 4, "cost": 0},
        {"id": "p5", "from": "V1", "to": "D2", "lo": 0, "hi": 9, "cost": 3},
        {"id": "p6", "from": "V2", "to": "D2", "lo": 0, "hi": 7, "cost": 2},
        {"id": "p7", "from": "D1", "to": "D2", "lo": 0, "hi": 3,
         "cost": -1}],
}

CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append((name, bool(cond), detail))
    print(("  PASS " if cond else "  FAIL ") + name +
          (f" — {detail}" if detail and not cond else ""))


print(f"[smoke] target = {BASE}")

s, h = call("GET", "/health")
check("GET /health 200/ok", s == 200 and h.get("status") == "ok", str(h))

s, page = call("GET", "/")
check("GET / 页面", s == 200 and "最小费用流" in page)

s, b1 = call("POST", "/api/solve", SAMPLE, {"X-Audit-Id": "smoke-run-1"})
r1 = b1.get("result", {})
check("POST /api/solve 最优", s == 200 and r1.get("status") == "optimal",
      f"{s} {r1.get('status')}")
check("总成本为整数", isinstance(r1.get("total_cost"), int))
pi = r1.get("potentials", {})
rc_ok = all(
    a["reduced_cost"] >= 0 and
    a["reduced_cost"] == a["cost"] + pi[a["from"]] - pi[a["to"]]
    for e in r1.get("edges", []) for a in e["residual"])
check("正残量边约化成本全部 >= 0（最优性证据）", rc_ok)
node_ok = all(n["ok"] for n in r1.get("node_check", []))
check("逐站净供需复算全部通过", node_ok)

s, b2 = call("POST", "/api/solve", SAMPLE, {"X-Audit-Id": "smoke-run-1"})
check("同标识同载荷重放原记录",
      s == 200 and b2.get("replayed") is True and
      b2.get("result") == r1, str(s))

changed = json.loads(json.dumps(SAMPLE))
changed["pipes"][0]["cost"] = 42
s, b3 = call("POST", "/api/solve", changed, {"X-Audit-Id": "smoke-run-1"})
check("同标识改载荷被拒绝 409", s == 409 and "不同载荷" in b3.get("error", ""),
      str(s))

bad_cut = {"stations": [{"id": "S", "balance": 5},
                        {"id": "T", "balance": -5}],
           "pipes": [{"id": "x", "from": "S", "to": "T",
                      "lo": 0, "hi": 2, "cost": 1}]}
s, b4 = call("POST", "/api/solve", bad_cut)
check("无可行流返回未满足需求与可达集",
      s == 200 and b4.get("status") == "infeasible" and
      b4["unmet_demand"][0]["station"] == "T" and
      b4["unmet_demand"][0]["unsatisfied"] == 3 and
      "S" in b4["reachable_from_source"], str(b4)[:200])

bad_input = {"stations": [{"id": "S", "balance": 1.5},
                          {"id": "T", "balance": -1}], "pipes": []}
s, b5 = call("POST", "/api/solve", bad_input)
check("非法输入定位字段", s == 400 and b5.get("loc") == "stations[0].balance",
      str(b5))

# ------------------------------------------------------------ 低扰动改线
RR_NET = {
    "stations": [{"id": "S", "balance": 3}, {"id": "T", "balance": -3}],
    "pipes": [
        {"id": "e0", "from": "S", "to": "T", "lo": 0, "hi": 3, "cost": 0},
        {"id": "e1", "from": "S", "to": "T", "lo": 0, "hi": 3, "cost": 1},
        {"id": "e2", "from": "S", "to": "T", "lo": 0, "hi": 3, "cost": 1},
    ],
}
s, b = call("POST", "/api/solve", RR_NET, {"X-Audit-Id": "rr-src-1"})
check("改线来源求解 optimal", s == 200 and b["result"]["status"] == "optimal",
      str(s))
src_flows = {f["id"]: f["flow"] for f in b["result"]["flows"]}
check("来源流走满最便宜管 e0", src_flows == {"e0": 3, "e1": 0, "e2": 0},
      str(src_flows))

# 成本全改平 + e0 上界压到 1：并列方案 (1,2,0)/(1,1,1)/(1,0,2) 按标识序裁决
rr_req = {"source_audit_id": "rr-src-1", "modifications": [
    {"pipe": "e0", "hi": 1}, {"pipe": "e1", "cost": 0},
    {"pipe": "e2", "cost": 0}]}
s, b = call("POST", "/api/reroute", rr_req, {"X-Audit-Id": "rr-run-1"})
r = b.get("result", {})
flows = {f["id"]: f["flow"] for f in r.get("flows", [])}
check("字典序改线 200/optimal", s == 200 and r.get("status") == "optimal",
      f"{s} {r.get('status')}")
check("平局按管路标识序裁决得 (e0,e1,e2)=(1,0,2)",
      [flows.get(k) for k in ("e0", "e1", "e2")] == [1, 0, 2], str(flows))
check("一级目标=修订最小成本 0",
      r.get("objectives", {}).get("level1_revised_cost") == 0)
check("二级目标=总绝对改变量 4",
      r.get("objectives", {}).get("level2_total_abs_change") == 4)
chg = {c["id"]: c for c in r.get("changes_core", [])}
check("逐管改变量给出原流量/新流量/Δ/|Δ|",
      chg.get("e2") == {"id": "e2", "source_flow": 0, "new_flow": 2,
                        "delta": 2, "abs_change": 2},
      str(chg.get("e2")))
# 冻结：来源站点/管路序/原流量 + 本次覆盖项 + 修订管路
fr = r.get("frozen", {})
check("冻结来源管路顺序与原流量",
      fr.get("pipe_order") == ["e0", "e1", "e2"] and
      fr.get("source_flows") == [{"id": "e0", "flow": 3},
                                 {"id": "e1", "flow": 0},
                                 {"id": "e2", "flow": 0}])
check("冻结覆盖项与修订后管路",
      len(fr.get("modifications", [])) == 3 and
      fr.get("revised_pipes", [{}])[0].get("hi") == 1 and
      fr.get("source_pipes", [{}])[0].get("hi") == 3)
# 两级证据独立复算
pe = r.get("perturbation_evidence", {})
pi = pe.get("potentials", {})
pm = pe.get("perturbation_potentials", {})
qm = pe.get("lex_potentials", {})
arc_ok = bool(pe.get("arcs")) and all(
    a["reduced_cost"] >= 0 and
    a["reduced_cost"] == a["cost"] + pi[a["from"]] - pi[a["to"]] and
    (a["reduced_w2"] is None or
     (a["reduced_w2"] >= 0 and
      a["reduced_w2"] == a["w2"] + pm[a["from"]] - pm[a["to"]])) and
    (a["lex_slack"] is None or
     (len(a["lex_slack"]) == 3 and
      next((x for x in a["lex_slack"] if x != 0), 0) >= 0))
    for a in pe.get("arcs", []))
check("每条正残量分段弧：成本/扰动/字典序三级约化费用均可复算非负", arc_ok)
check("成对站点势 π/p/q 齐全且 q 为按管路序向量",
      set(pi) == {"S", "T"} and set(pm) == {"S", "T"} and
      all(len(qm[s]) == 3 for s in qm))
check("改线流逐站净供需复算通过（相同供需）",
      all(n["ok"] for n in r.get("node_check", [])))
check("改线结果未复用来源方案（确为重新求解）",
      flows != src_flows and r.get("total_cost") == 0 and
      isinstance(r.get("cycles_cancelled"), int))

# 改线审计重放
s, b_rep = call("POST", "/api/reroute", rr_req, {"X-Audit-Id": "rr-run-1"})
check("改线同标识同载荷重放原记录",
      s == 200 and b_rep.get("replayed") is True and
      b_rep.get("result") == r)
# 改线审计改载荷冲突
rr_changed = {"source_audit_id": "rr-src-1",
              "modifications": [{"pipe": "e1", "cost": 0}]}
s, b_con = call("POST", "/api/reroute", rr_changed,
                {"X-Audit-Id": "rr-run-1"})
check("改线同标识改载荷拒绝 409", s == 409 and "不同载荷" in b_con.get("error", ""))
# 改线审计可经读取接口取回
s, b_get = call("GET", "/api/records/rr-run-1")
check("GET 改线审计记录", s == 200 and b_get.get("result", {}).get("frozen")
      and b_get["result"]["objectives"]["level2_total_abs_change"] == 4)

# 覆盖后不可行：唯一管 hi 5 -> 2，需求 5
CUT_NET = {"stations": [{"id": "S", "balance": 5},
                        {"id": "T", "balance": -5}],
           "pipes": [{"id": "x", "from": "S", "to": "T",
                      "lo": 0, "hi": 5, "cost": 1}]}
s, _ = call("POST", "/api/solve", CUT_NET, {"X-Audit-Id": "rr-src-cut"})
s, b = call("POST", "/api/reroute",
            {"source_audit_id": "rr-src-cut",
             "modifications": [{"pipe": "x", "hi": 2}]},
            {"X-Audit-Id": "rr-run-cut"})
rc = b.get("result", {})
check("覆盖后不可行返回证据",
      s == 200 and rc.get("status") == "infeasible" and
      rc["unmet_demand"][0]["station"] == "T" and
      rc["unmet_demand"][0]["unsatisfied"] == 3 and
      "S" in rc.get("reachable_from_source", []), str(rc)[:200])

# 拒绝类
s, b = call("POST", "/api/reroute",
            {"source_audit_id": "missing-id",
             "modifications": [{"pipe": "x", "cost": 1}]})
check("来源不存在拒绝 404", s == 404 and b.get("loc") == "source_audit_id")

s, b = call("POST", "/api/reroute",
            {"source_audit_id": "rr-src-cut",
             "modifications": [{"pipe": "x", "cost": 1}]},
            {"X-Audit-Id": "rr-src-cut"})
check("改线标识与来源标识冲突拒绝 409",
      s == 409 and "标识冲突" in b.get("error", ""))

# 不可行来源
s, _ = call("POST", "/api/solve",
            {"stations": [{"id": "A", "balance": 3}, {"id": "B", "balance": -3}],
             "pipes": [{"id": "z", "from": "A", "to": "B",
                        "lo": 0, "hi": 1, "cost": 1}]},
            {"X-Audit-Id": "rr-src-infeasible"})
s, b = call("POST", "/api/reroute",
            {"source_audit_id": "rr-src-infeasible",
             "modifications": [{"pipe": "z", "hi": 9}]})
check("非最优来源拒绝 409", s == 409 and "optimal" in b.get("error", ""))

s, b = call("POST", "/api/reroute",
            {"source_audit_id": "rr-src-1", "modifications": [
                {"pipe": "e0", "cost": 1}, {"pipe": "e0", "hi": 2}]})
check("覆盖项管路标识冲突拒绝 400",
      s == 400 and b.get("loc") == "modifications[1].pipe")

s, b = call("POST", "/api/reroute",
            {"source_audit_id": "rr-src-1",
             "modifications": [{"pipe": "e0", "lo": 9}]})
check("覆盖后边界非法拒绝 400",
      s == 400 and "覆盖后" in b.get("error", ""))

s, b = call("POST", "/api/reroute",
            {"source_audit_id": "rr-src-1",
             "modifications": [{"pipe": "e0", "hi": 1.5}]})
check("覆盖值非整数拒绝 400",
      s == 400 and b.get("loc") == "modifications[0].hi")

s, b = call("POST", "/api/reroute",
            {"source_audit_id": "rr-src-1",
             "modifications": [{"pipe": "nope", "hi": 2}]})
check("覆盖未知管路拒绝 400",
      s == 400 and b.get("loc") == "modifications[0].pipe")

# 旧求解接口与审计读取行为保持不变（重放 smoke-run-1）
s, b = call("POST", "/api/solve", SAMPLE, {"X-Audit-Id": "smoke-run-1"})
check("旧求解接口审计重放行为不变",
      s == 200 and b.get("replayed") is True and
      b["result"]["status"] == "optimal")

failed = [n for n, ok, _ in CHECKS if not ok]
print(f"[smoke] {len(CHECKS) - len(failed)}/{len(CHECKS)} passed")
sys.exit(1 if failed else 0)
