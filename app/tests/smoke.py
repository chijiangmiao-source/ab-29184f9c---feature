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

failed = [n for n, ok, _ in CHECKS if not ok]
print(f"[smoke] {len(CHECKS) - len(failed)}/{len(CHECKS)} passed")
sys.exit(1 if failed else 0)
