"""HTTP API 与审计幂等测试（线程内启动真实 HTTP 服务）。"""

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

os.environ.setdefault("AUDIT_DB", os.path.join(tempfile.mkdtemp(), "audit.json"))

import server  # noqa: E402

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
        {"id": "p7", "from": "D1", "to": "D2", "lo": 0, "hi": 3, "cost": -1}],
}


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def req(self, method, path, body=None, headers=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=headers or {})
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode()
                ctype = resp.headers.get("Content-Type", "")
                return resp.status, (json.loads(raw)
                                     if "application/json" in ctype else raw)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_health_and_page(self):
        status, body = self.req("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        status, html = self.req("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("最小费用流", html)
        status, js = self.req("GET", "/static/app.js")
        self.assertEqual(status, 200)

    def test_solve_evidence(self):
        status, body = self.req("POST", "/api/solve", SAMPLE,
                                {"X-Audit-Id": "run-evidence-1"})
        self.assertEqual(status, 200)
        r = body["result"]
        self.assertEqual(r["status"], "optimal")
        self.assertFalse(body["replayed"])
        # 独立复算费用、平衡与约化成本
        flows = {f["id"]: f["flow"] for f in r["flows"]}
        cost = 0
        net = {s["id"]: 0 for s in SAMPLE["stations"]}
        for p in SAMPLE["pipes"]:
            f = flows[p["id"]]
            self.assertTrue(p["lo"] <= f <= p["hi"])
            cost += f * p["cost"]
            net[p["from"]] += f
            net[p["to"]] -= f
        bal = {s["id"]: s["balance"] for s in SAMPLE["stations"]}
        self.assertEqual(net, bal)
        self.assertEqual(cost, r["total_cost"])
        pi = r["potentials"]
        for e in r["edges"]:
            for arc in e["residual"]:
                self.assertGreaterEqual(arc["reduced_cost"], 0)
                self.assertEqual(
                    arc["reduced_cost"],
                    arc["cost"] + pi[arc["from"]] - pi[arc["to"]])

    def test_audit_same_payload_replays_original(self):
        headers = {"X-Audit-Id": "run-replay-1"}
        s1, b1 = self.req("POST", "/api/solve", SAMPLE, headers)
        s2, b2 = self.req("POST", "/api/solve", SAMPLE, headers)
        self.assertEqual(s1, s2, 200)
        self.assertFalse(b1["replayed"])
        self.assertTrue(b2["replayed"])
        self.assertEqual(b1["result"], b2["result"])  # 原记录字节级一致
        self.assertEqual(b1["payload_fingerprint"], b2["payload_fingerprint"])
        # 记录可通过 API 取回
        s3, b3 = self.req("GET", "/api/records/run-replay-1")
        self.assertEqual(s3, 200)
        self.assertEqual(b3["result"], b1["result"])

    def test_audit_changed_payload_rejected(self):
        s1, b1 = self.req("POST", "/api/solve", SAMPLE,
                          {"X-Audit-Id": "run-conflict-1"})
        self.assertEqual(s1, 200)
        changed = json.loads(json.dumps(SAMPLE))
        changed["pipes"][0]["cost"] = 999
        s2, b2 = self.req("POST", "/api/solve", changed,
                          {"X-Audit-Id": "run-conflict-1"})
        self.assertEqual(s2, 409)
        self.assertIn("不同载荷", b2["error"])
        self.assertEqual(b2["conflict"]["payload_fingerprint"],
                         b1["payload_fingerprint"])

    def test_invalid_locates_field(self):
        bad = {"stations": [{"id": "S", "balance": 4},
                            {"id": "T", "balance": -3}], "pipes": []}
        status, body = self.req("POST", "/api/solve", bad)
        self.assertEqual(status, 400)
        self.assertEqual(body["loc"], "stations")
        bad2 = json.loads(json.dumps(SAMPLE))
        bad2["pipes"][2]["hi"] = 0  # lo=1 > hi
        status, body = self.req("POST", "/api/solve", bad2)
        self.assertEqual(status, 400)
        self.assertEqual(body["loc"], "pipes[2].hi")

    def test_infeasible_evidence(self):
        payload = {"stations": [{"id": "S", "balance": 5},
                                {"id": "T", "balance": -5}],
                   "pipes": [{"id": "x", "from": "S", "to": "T",
                              "lo": 0, "hi": 2, "cost": 1}]}
        status, body = self.req("POST", "/api/solve", payload)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "infeasible")
        self.assertEqual(body["unmet_demand"][0]["station"], "T")
        self.assertEqual(body["unmet_demand"][0]["unsatisfied"], 3)
        self.assertIn("S", body["reachable_from_source"])


class RerouteApiTest(unittest.TestCase):
    """低扰动改线 HTTP 接口：成功、对照、审计重放/冲突、各类拒绝。"""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def req(self, method, path, body=None, headers=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=headers or {})
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode()
                ctype = resp.headers.get("Content-Type", "")
                return resp.status, (json.loads(raw)
                                     if "application/json" in ctype else raw)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def _optimal_source(self, sid="api-src"):
        # 三条并联管：来源流 e0=3（最便宜）
        payload = {"stations": [{"id": "S", "balance": 3},
                                {"id": "T", "balance": -3}],
                   "pipes": [
                       {"id": "e0", "from": "S", "to": "T", "lo": 0,
                        "hi": 3, "cost": 0},
                       {"id": "e1", "from": "S", "to": "T", "lo": 0,
                        "hi": 3, "cost": 1},
                       {"id": "e2", "from": "S", "to": "T", "lo": 0,
                        "hi": 3, "cost": 1}]}
        s, b = self.req("POST", "/api/solve", payload,
                        {"X-Audit-Id": sid})
        self.assertEqual(s, 200)
        self.assertEqual(b["result"]["status"], "optimal")
        return b, payload

    def test_reroute_lexicographic_and_evidence(self):
        src_env, _ = self._optimal_source("api-src-lex")
        # 成本全改平且 e0 上界压到 1：并列方案按管路标识序裁决
        body_in = {"source_audit_id": "api-src-lex",
                   "modifications": [
                       {"pipe": "e0", "hi": 1},
                       {"pipe": "e1", "cost": 0},
                       {"pipe": "e2", "cost": 0}]}
        s, b = self.req("POST", "/api/reroute", body_in,
                        {"X-Audit-Id": "api-rr-lex"})
        self.assertEqual(s, 200, b)
        r = b["result"]
        self.assertEqual(r["status"], "optimal")
        flows = {f["id"]: f["flow"] for f in r["flows"]}
        self.assertEqual([flows[k] for k in ("e0", "e1", "e2")],
                         [1, 0, 2])
        self.assertEqual(r["objectives"]["level1_revised_cost"], 0)
        self.assertEqual(r["objectives"]["level2_total_abs_change"], 4)
        # 逐管改变量
        chg = {c["id"]: c for c in r["changes_core"]}
        self.assertEqual(chg["e1"]["source_flow"], 0)
        self.assertEqual(chg["e1"]["new_flow"], 0)
        self.assertEqual(chg["e2"]["delta"], 2)
        self.assertEqual(chg["e2"]["abs_change"], 2)
        # 冻结快照
        self.assertEqual(r["frozen"]["pipe_order"], ["e0", "e1", "e2"])
        self.assertEqual(len(r["frozen"]["modifications"]), 3)
        self.assertEqual(r["frozen"]["source_pipes"][0]["cost"], 0)
        self.assertEqual(r["frozen"]["revised_pipes"][0]["hi"], 1)
        # 三级证据可独立复算
        pe = r["perturbation_evidence"]
        pi = pe["potentials"]
        for a in pe["arcs"]:
            self.assertGreaterEqual(a["reduced_cost"], 0)
            self.assertEqual(
                a["reduced_cost"], a["cost"] + pi[a["from"]] - pi[a["to"]])
            if a["reduced_w2"] is not None:
                self.assertGreaterEqual(a["reduced_w2"], 0)
        # 记录可经审计读取接口取回
        s, got = self.req("GET", "/api/records/api-rr-lex")
        self.assertEqual(s, 200)
        self.assertEqual(got["result"], r)

    def test_reroute_replay_and_conflict(self):
        self._optimal_source("api-src-rep")
        body_in = {"source_audit_id": "api-src-rep",
                   "modifications": [{"pipe": "e1", "cost": 0}]}
        s, b1 = self.req("POST", "/api/reroute", body_in,
                         {"X-Audit-Id": "api-rr-rep"})
        self.assertEqual(s, 200)
        self.assertFalse(b1["replayed"])
        s, b2 = self.req("POST", "/api/reroute", body_in,
                         {"X-Audit-Id": "api-rr-rep"})
        self.assertEqual(s, 200)
        self.assertTrue(b2["replayed"])
        self.assertEqual(b1["result"], b2["result"])
        changed = {"source_audit_id": "api-src-rep",
                   "modifications": [{"pipe": "e2", "cost": 0}]}
        s, b3 = self.req("POST", "/api/reroute", changed,
                         {"X-Audit-Id": "api-rr-rep"})
        self.assertEqual(s, 409)
        self.assertIn("不同载荷", b3["error"])

    def test_reroute_rejects(self):
        # 来源不存在
        s, b = self.req("POST", "/api/reroute",
                        {"source_audit_id": "no-such-rec",
                         "modifications": [{"pipe": "e0", "cost": 1}]})
        self.assertEqual(s, 404)
        self.assertEqual(b["loc"], "source_audit_id")

        # 非最优（不可行）来源
        inf = {"stations": [{"id": "S", "balance": 3},
                            {"id": "T", "balance": -3}],
               "pipes": [{"id": "x", "from": "S", "to": "T",
                          "lo": 0, "hi": 1, "cost": 1}]}
        s, _ = self.req("POST", "/api/solve", inf,
                        {"X-Audit-Id": "api-src-inf"})
        self.assertEqual(s, 200)
        s, b = self.req("POST", "/api/reroute",
                        {"source_audit_id": "api-src-inf",
                         "modifications": [{"pipe": "x", "hi": 9}]})
        self.assertEqual(s, 409)
        self.assertIn("optimal", b["error"])

        self._optimal_source("api-src-bad")
        # 标识冲突：改线 id == 来源 id
        s, b = self.req(
            "POST", "/api/reroute",
            {"source_audit_id": "api-src-bad",
             "modifications": [{"pipe": "e0", "cost": 1}]},
            {"X-Audit-Id": "api-src-bad"})
        self.assertEqual(s, 409)
        self.assertIn("标识冲突", b["error"])

        # 覆盖项管路标识冲突（重复）
        s, b = self.req("POST", "/api/reroute",
                        {"source_audit_id": "api-src-bad", "modifications": [
                            {"pipe": "e0", "cost": 1},
                            {"pipe": "e0", "hi": 2}]})
        self.assertEqual(s, 400)
        self.assertEqual(b["loc"], "modifications[1].pipe")

        # 覆盖后边界非法
        s, b = self.req("POST", "/api/reroute",
                        {"source_audit_id": "api-src-bad",
                         "modifications": [{"pipe": "e0", "lo": 9}]})
        self.assertEqual(s, 400)
        self.assertIn("覆盖后", b["error"])

        # 覆盖超过 6 条（需要 >=7 条不同管）
        big = {"stations": [{"id": "S", "balance": 7},
                            {"id": "T", "balance": -7}],
               "pipes": [{"id": f"g{i}", "from": "S", "to": "T",
                          "lo": 0, "hi": 7, "cost": 0}
                         for i in range(7)]}
        s, _ = self.req("POST", "/api/solve", big,
                        {"X-Audit-Id": "api-src-7"})
        self.assertEqual(s, 200)
        s, b = self.req("POST", "/api/reroute",
                        {"source_audit_id": "api-src-7",
                         "modifications": [{"pipe": f"g{i}", "cost": 1}
                                           for i in range(7)]})
        self.assertEqual(s, 400)
        self.assertEqual(b["loc"], "modifications")

    def test_reroute_infeasible_coverage_evidence(self):
        # 唯一管路容量 5 承载需求 5；覆盖后 hi=2 -> 不可行，给出证据
        payload = {"stations": [{"id": "S", "balance": 5},
                                {"id": "T", "balance": -5}],
                   "pipes": [{"id": "x", "from": "S", "to": "T",
                              "lo": 0, "hi": 5, "cost": 1}]}
        s, _ = self.req("POST", "/api/solve", payload,
                        {"X-Audit-Id": "api-src-cut"})
        self.assertEqual(s, 200)
        s, b = self.req(
            "POST", "/api/reroute",
            {"source_audit_id": "api-src-cut",
             "modifications": [{"pipe": "x", "hi": 2}]},
            {"X-Audit-Id": "api-rr-cut"})
        self.assertEqual(s, 200)
        r = b["result"]
        self.assertEqual(r["status"], "infeasible")
        self.assertEqual(r["unmet_demand"][0]["station"], "T")
        self.assertEqual(r["unmet_demand"][0]["unsatisfied"], 3)
        self.assertIn("S", r["reachable_from_source"])
        # 不可行改线也落审计，可重放原证据
        s, b2 = self.req(
            "POST", "/api/reroute",
            {"source_audit_id": "api-src-cut",
             "modifications": [{"pipe": "x", "hi": 2}]},
            {"X-Audit-Id": "api-rr-cut"})
        self.assertTrue(b2["replayed"])
        self.assertEqual(b2["result"]["status"], "infeasible")

    def test_cannot_reroute_from_reroute_record(self):
        self._optimal_source("api-src-chain")
        body_in = {"source_audit_id": "api-src-chain",
                   "modifications": [{"pipe": "e1", "cost": 0}]}
        s, _ = self.req("POST", "/api/reroute", body_in,
                        {"X-Audit-Id": "api-rr-chain"})
        self.assertEqual(s, 200)
        s, b = self.req(
            "POST", "/api/reroute",
            {"source_audit_id": "api-rr-chain",
             "modifications": [{"pipe": "e0", "cost": 9}]})
        self.assertEqual(s, 409)
        self.assertIn("改线记录", b["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
