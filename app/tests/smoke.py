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

# ---------------------------------------------------------------- 低扰动改线
print("[smoke] ---- 低扰动改线 /api/reroute ----")

# 以 smoke-run-1（SAMPLE 的 optimal 记录）为来源，把 p1 成本改成 -5
rr_body = {"source_audit_id": "smoke-run-1",
           "changes": [{"id": "p1", "cost": -5}]}
s, b6 = call("POST", "/api/reroute", rr_body, {"X-Audit-Id": "smoke-rr-1"})
r6 = b6.get("result", {})
check("改线返回 optimal", s == 200 and r6.get("status") == "optimal"
      and r6.get("kind") == "reroute", f"{s} {r6.get('status')}")

if r6.get("status") == "optimal":
    # 独立复算：界、节点平衡、修订成本、两级势与字典序约化费用
    src_flows = {f["id"]: f["flow"] for f in r1["flows"]}
    flows = {f["id"]: f["flow"] for f in r6["flows"]}
    newcost = {p["id"]: p["cost"] for p in SAMPLE["pipes"]}
    newcost["p1"] = -5
    bounds = {p["id"]: (p["lo"], p["hi"]) for p in SAMPLE["pipes"]}
    recomputed_cost = 0
    recomputed_l1 = 0
    for pid, f in flows.items():
        lo, hi = bounds[pid]
        assert lo <= f <= hi, f"管 {pid} 越界"
        recomputed_cost += f * newcost[pid]
        recomputed_l1 += abs(f - src_flows[pid])
    net = {x["id"]: 0 for x in SAMPLE["stations"]}
    for p in SAMPLE["pipes"]:
        net[p["from"]] += flows[p["id"]]
        net[p["to"]] -= flows[p["id"]]
    bal = {x["id"]: x["balance"] for x in SAMPLE["stations"]}
    check("改线逐站净供需复算", net == bal, str(net))
    check("改线修订成本可独立复算",
          recomputed_cost == r6["objectives"]["revised_cost"] ==
          r6.get("total_cost"),
          f"{recomputed_cost} != {r6.get('objectives', {}).get('revised_cost')}")
    check("L1 扰动 = 逐管绝对改变量之和",
          recomputed_l1 == r6["objectives"]["perturbation_l1"] and
          recomputed_l1 == sum(c["abs_delta"] for c in r6["pipe_changes"]),
          str(recomputed_l1))
    check("逐管有符号改变量一致",
          all(c["signed_delta"] == c["flow"] - c["source_flow"]
              for c in r6["pipe_changes"]))
    check("修改项对照（新旧成本）",
          any(c["id"] == "p1" and c["old_cost"] == 2 and
              c["new_cost"] == -5 and c["changed_fields"] == ["cost"]
              for c in r6["pipe_changes"]))
    pi = r6.get("potentials", {})
    rc_cost_ok = True
    rc_lex_ok = True
    for e in r6.get("edges", []):
        for a in e["residual"]:
            rc_c = a["cost"] + pi[a["from"]]["cost"] - pi[a["to"]]["cost"]
            rc_l = (a["perturbation_marginal_cost"]
                    + pi[a["from"]]["perturbation"]
                    - pi[a["to"]]["perturbation"])
            if a["cost_reduced_cost"] != rc_c or rc_c < 0:
                rc_cost_ok = False
            if a["lex_reduced_cost"] != [rc_c, rc_l] or (rc_c, rc_l) < (0, 0):
                rc_lex_ok = False
    check("第一级约化成本全部 >= 0（修订成本最优可独立复算）", rc_cost_ok)
    check("字典序约化费用 [成本,扰动] 全部 >= [0,0]（最低扰动可复算）",
          rc_lex_ok)
    check("冻结来源站点/管路顺序/原流量",
          r6["frozen"]["pipe_order"] == [p["id"] for p in SAMPLE["pipes"]] and
          r6["frozen"]["source_flows"] == src_flows and
          r6["frozen"]["changes"] == [{"id": "p1", "cost": -5}])
    check("改线审计记录可经 GET 取回",
          call("GET", "/api/records/smoke-rr-1")[0] == 200)

# 同标识同载荷重放
s, b7 = call("POST", "/api/reroute", rr_body, {"X-Audit-Id": "smoke-rr-1"})
check("改线同标识同载荷重放原记录",
      s == 200 and b7.get("replayed") is True and
      b7.get("result") == r6, str(s))

# 同标识改载荷 -> 409
rr_changed = {"source_audit_id": "smoke-run-1",
              "changes": [{"id": "p1", "cost": -6}]}
s, b8 = call("POST", "/api/reroute", rr_changed,
             {"X-Audit-Id": "smoke-rr-1"})
check("改线同标识改载荷被拒绝 409", s == 409 and "不同载荷" in b8.get("error", ""),
      str(s))

# 来源不存在 -> 404
s, b9 = call("POST", "/api/reroute",
             {"source_audit_id": "no-such-record", "changes": []})
check("来源审计不存在返回 404", s == 404 and
      b9.get("loc") == "source_audit_id", str(s))

# 来源非最优（先存一条 infeasible 记录）
s, _ = call("POST", "/api/solve", bad_cut, {"X-Audit-Id": "smoke-inf-1"})
s, b10 = call("POST", "/api/reroute",
              {"source_audit_id": "smoke-inf-1", "changes": []})
check("来源非最优被拒绝 409", s == 409 and
      b10.get("loc") == "source_audit_id", str(s))

# 覆盖导致不可行：D1 唯一入口 p4 上界压到 0
s, b11 = call("POST", "/api/reroute",
              {"source_audit_id": "smoke-run-1",
               "changes": [{"id": "p4", "hi": 0}]})
r11 = b11.get("result", b11)
check("不可行覆盖返回证据",
      s == 200 and r11.get("status") == "infeasible" and
      bool(r11.get("unmet_demand")) and
      "reachable_from_source" in r11 and
      r11.get("frozen", {}).get("source_flows", {}).get("p4") ==
      src_flows.get("p4"), str(r11)[:200])

# 覆盖后边界非法 hi < lo
s, b12 = call("POST", "/api/reroute",
              {"source_audit_id": "smoke-run-1",
               "changes": [{"id": "p1", "lo": 100}]})
check("覆盖后边界非法定位到 changes[0].hi",
      s == 400 and b12.get("loc") == "changes[0].hi", str(b12))

# 覆盖项引用不存在的管路
s, b13 = call("POST", "/api/reroute",
              {"source_audit_id": "smoke-run-1",
               "changes": [{"id": "nope", "hi": 3}]})
check("覆盖项标识冲突（未知管路）被拒绝",
      s == 400 and b13.get("loc") == "changes[0].id", str(b13))

# 同一管在覆盖项中重复
s, b14 = call("POST", "/api/reroute",
              {"source_audit_id": "smoke-run-1",
               "changes": [{"id": "p1", "hi": 8}, {"id": "p1", "cost": 3}]})
check("覆盖项同管重复被拒绝", s == 400 and
      b14.get("loc") == "changes[1].id", str(b14))

# 超过 6 条覆盖
s, b15 = call("POST", "/api/reroute",
              {"source_audit_id": "smoke-run-1",
               "changes": [{"id": f"p{i+1}"} for i in range(7)]})
check("超过六条覆盖被拒绝", s == 400 and b15.get("loc") == "changes",
      str(b15))

# 浮点覆盖值被拒绝
s, b16 = call("POST", "/api/reroute",
              {"source_audit_id": "smoke-run-1",
               "changes": [{"id": "p1", "cost": 1.5}]})
check("覆盖值非整数被拒绝", s == 400 and
      b16.get("loc") == "changes[0].cost", str(b16))

# 旧求解接口行为不变：无审计标识直接返回裸结果体
s, b17 = call("POST", "/api/solve", SAMPLE)
check("旧求解接口（无审计标识）行为不变",
      s == 200 and b17.get("status") == "optimal" and
      "result" not in b17 and "replayed" not in b17 and
      b17.get("total_cost") == r1.get("total_cost"), str(s))

failed = [n for n, ok, _ in CHECKS if not ok]
print(f"[smoke] {len(CHECKS) - len(failed)}/{len(CHECKS)} passed")
sys.exit(1 if failed else 0)
