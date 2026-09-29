"""低扰动改线（reroute）测试：两级字典序目标、暴力枚举对照、证据、拒绝路径。"""

import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reroute import RerouteError, reroute  # noqa: E402
from solver import parse_and_validate  # noqa: E402


def mk(stations, pipes):
    return {"stations": [{"id": s, "balance": b} for s, b in stations],
            "pipes": [
                {"id": f"e{i}", "from": u, "to": v, "lo": lo, "hi": hi,
                 "cost": c}
                for i, (u, v, lo, hi, c) in enumerate(pipes)]}


def solve_record(audit_id, payload, result):
    """伪造一条 optimal 审计记录（结构与 AuditStore.save 一致）。"""
    return {"audit_id": audit_id, "status": "optimal", "payload": payload,
            "result": result}


def reroute_result(record, changes):
    out = reroute(record, {"source_audit_id": record["audit_id"],
                           "changes": changes})
    assert out["status"] == "optimal", out
    return out


def verify_reroute(res, revised_prob, f0_by_id):
    """独立复算改线结果：界、平衡、两级费用、字典序约化费用。"""
    ids = revised_prob.ids
    edges = revised_prob.edges
    flows = {f["id"]: f["flow"] for f in res["flows"]}
    cost = 0
    l1 = 0
    for e in edges:
        f = flows[e.id]
        assert e.lo <= f <= e.hi, f"管 {e.id} 流量 {f} 越界 [{e.lo},{e.hi}]"
        cost += f * e.cost
        l1 += abs(f - f0_by_id[e.id])
    net = {s: 0 for s in ids}
    for e in edges:
        net[ids[e.u]] += flows[e.id]
        net[ids[e.v]] -= flows[e.id]
    bal = dict(zip(ids, revised_prob.balance))
    for s in ids:
        assert net[s] == bal[s], f"站点 {s} 不平衡 {net[s]} != {bal[s]}"
    assert cost == res["objectives"]["revised_cost"]
    assert l1 == res["objectives"]["perturbation_l1"]
    assert sum(pc["abs_delta"] for pc in res["pipe_changes"]) == l1
    pi = res["potentials"]
    for er in res["edges"]:
        for a in er["residual"]:
            assert a["capacity"] > 0
            rc_c = a["cost"] + pi[a["from"]]["cost"] - pi[a["to"]]["cost"]
            rc_l = (a["perturbation_marginal_cost"]
                    + pi[a["from"]]["perturbation"]
                    - pi[a["to"]]["perturbation"])
            assert a["cost_reduced_cost"] == rc_c
            assert a["perturbation_reduced_cost"] == rc_l
            assert a["lex_reduced_cost"] == [rc_c, rc_l]
            assert rc_c >= 0, f"修订成本约化费用为负: {er.id} {rc_c}"
            assert (rc_c, rc_l) >= (0, 0), \
                f"字典序约化费用为负: {er.id} {(rc_c, rc_l)}"
    assert all(n["ok"] for n in res["node_check"])
    return cost, l1, [flows[e.id] for e in edges]


def brute_force_lex(prob, f0):
    """枚举界内全部整数可行流，取 (成本, L1扰动, 流量向量) 字典序最小。"""
    best = None
    for combo in itertools.product(
            *[range(e.lo, e.hi + 1) for e in prob.edges]):
        net = [0] * len(prob.ids)
        cost = 0
        for k, e in enumerate(prob.edges):
            net[e.u] += combo[k]
            net[e.v] -= combo[k]
            cost += combo[k] * e.cost
        if net == prob.balance:
            key = (cost, sum(abs(combo[k] - f0[k])
                             for k in range(len(combo))), combo)
            if best is None or key < best[0]:
                best = (key, list(combo))
    return best


class TestRerouteBruteForce(unittest.TestCase):
    def test_random_small_networks(self):
        import random
        rng = random.Random(20260929)
        checked = 0
        for trial in range(260):
            n = rng.randint(2, 4)
            ids = [f"v{i}" for i in range(n)]
            total = rng.randint(0, 4)
            bals = [0] * n
            for _ in range(total):
                bals[rng.randrange(n)] += 1
                bals[rng.randrange(n)] -= 1
            stations = list(zip(ids, bals))
            pipes, used = [], set()
            for _ in range(rng.randint(1, 5)):
                u, v = rng.sample(range(n), 2)
                if (u, v) in used:
                    continue
                used.add((u, v))
                hi = rng.randint(0, 4)
                lo = rng.randint(0, hi)
                pipes.append((ids[u], ids[v], lo, hi, rng.randint(-3, 5)))
            source_payload = mk(stations, pipes)
            src_prob = parse_and_validate(source_payload)
            # 先求来源（普通最小费用流）
            from solver import solve
            src = solve(src_prob)
            if src["status"] != "optimal":
                continue
            record = solve_record("src", source_payload, src)
            f0 = [next(f["flow"] for f in src["flows"] if f["id"] == e.id)
                  for e in src_prob.edges]

            # 随机 0..3 条覆盖（界或成本）
            k = rng.randint(0, min(3, len(pipes)))
            idxs = rng.sample(range(len(pipes)), k)
            changes = []
            for i in idxs:
                ch = {"id": f"e{i}"}
                field = rng.choice(["lo", "hi", "cost"])
                if field == "cost":
                    ch["cost"] = rng.randint(-3, 5)
                elif field == "lo":
                    ch["lo"] = rng.randint(0, pipes[i][3])
                else:
                    ch["hi"] = rng.randint(pipes[i][2], 5)
                changes.append(ch)
            try:
                out = reroute(record, {"source_audit_id": "src",
                                       "changes": changes})
            except RerouteError:
                # 覆盖后非法（新 lo>新 hi 等），暴力侧也跳过
                continue

            cover = {c["id"]: c for c in changes}
            revised_pipes = []
            for p in source_payload["pipes"]:
                np = dict(p)
                c = cover.get(p["id"])
                if c:
                    for kk in ("lo", "hi", "cost"):
                        if kk in c:
                            np[kk] = c[kk]
                revised_pipes.append(np)
            revised_payload = {"stations": source_payload["stations"],
                               "pipes": revised_pipes}
            rev_prob = parse_and_validate(revised_payload)
            expect = brute_force_lex(rev_prob, f0)
            if expect is None:
                self.assertEqual(out["status"], "infeasible",
                                 f"trial {trial}")
                self.assertIn("unmet_demand", out)
                continue
            checked += 1
            cost, l1, vec = verify_reroute(
                out, rev_prob, {e.id: f0[i]
                                for i, e in enumerate(src_prob.edges)})
            self.assertEqual((cost, l1, tuple(vec)), expect[0],
                             f"trial {trial}: {source_payload} / {changes}")
        self.assertGreater(checked, 40)


class TestRerouteSemantics(unittest.TestCase):
    def _sample_record(self, audit_id="r-src"):
        payload = mk(
            [("S", 4), ("M", 0), ("T", -4)],
            [("S", "T", 0, 4, 5),    # e0 直达较贵
             ("S", "M", 0, 4, 1),    # e1
             ("M", "T", 0, 4, 1)])   # e2 合计便宜
        from solver import solve
        res = solve(parse_and_validate(payload))
        self.assertEqual(res["status"], "optimal")
        return solve_record(audit_id, payload, res), payload, res

    def test_no_changes_keeps_source_flow(self):
        rec, payload, src = self._sample_record()
        out = reroute_result(rec, [])
        rev = parse_and_validate(payload)
        f0 = {f["id"]: f["flow"] for f in src["flows"]}
        cost, l1, vec = verify_reroute(out, rev, f0)
        self.assertEqual(l1, 0)  # 来源本身即成本最优，零扰动可达
        self.assertEqual([f["flow"] for f in out["flows"]],
                         [f["flow"] for f in src["flows"]])
        self.assertEqual(cost, src["total_cost"])

    def test_cheapen_direct_pipe_shifts_flow(self):
        rec, payload, src = self._sample_record()
        out = reroute_result(rec, [{"id": "e0", "cost": 1}])
        flows = {f["id"]: f["flow"] for f in out["flows"]}
        self.assertEqual(flows["e0"], 4)  # 直达变便宜，全部改走直达
        self.assertEqual(out["objectives"]["revised_cost"], 4)
        # 原流量 e0=0 -> 改变量 4；e1=e2=4 -> 0
        d = {pc["id"]: pc for pc in out["pipe_changes"]}
        self.assertEqual(d["e0"]["source_flow"], 0)
        self.assertEqual(d["e0"]["signed_delta"], 4)
        self.assertEqual(d["e0"]["abs_delta"], 4)
        self.assertEqual(d["e0"]["old_cost"], 5)
        self.assertEqual(d["e0"]["new_cost"], 1)
        self.assertEqual(d["e0"]["changed_fields"], ["cost"])

    def test_tighten_upper_bound_forces_detour(self):
        rec, payload, src = self._sample_record()
        # 来源走 e1,e2 各 4；把 e1 上界压到 1，剩余必须走贵的直达 e0
        out = reroute_result(rec, [{"id": "e1", "hi": 1}])
        flows = {f["id"]: f["flow"] for f in out["flows"]}
        self.assertEqual(flows["e1"], 1)
        self.assertEqual(flows["e2"], 1)
        self.assertEqual(flows["e0"], 3)
        rev_payload = dict(payload)
        rev_payload["pipes"] = [
            dict(p, hi=1) if p["id"] == "e1" else p for p in payload["pipes"]]
        rev = parse_and_validate(rev_payload)
        f0 = {f["id"]: f["flow"] for f in src["flows"]}
        verify_reroute(out, rev, f0)
        # L1: e1 4->1 (3), e2 4->1 (3), e0 0->3 (3) = 9
        self.assertEqual(out["objectives"]["perturbation_l1"], 9)

    def test_infeasible_coverage_evidence(self):
        rec, payload, src = self._sample_record()
        # 所有路径总上界 4；把 e0、e1 上界都压到 1（e2 仍 4，但进 M 只有 1）
        out = reroute(rec, {"source_audit_id": "r-src",
                            "changes": [{"id": "e0", "hi": 1},
                                        {"id": "e1", "hi": 1}]})
        self.assertEqual(out["status"], "infeasible")
        self.assertTrue(out["unmet_demand"])
        self.assertIn("reachable_from_source", out)
        self.assertEqual(out["source_audit_id"], "r-src")
        # 冻结内容随证据返回
        self.assertEqual(out["frozen"]["pipe_order"], ["e0", "e1", "e2"])
        self.assertEqual(out["frozen"]["source_flows"]["e0"], 0)

    def test_frozen_station_order_and_balance(self):
        rec, payload, src = self._sample_record()
        out = reroute_result(rec, [{"id": "e0", "cost": 1}])
        self.assertEqual([s["id"] for s in out["frozen"]["stations"]],
                         [s["id"] for s in payload["stations"]])
        self.assertEqual(out["frozen"]["changes"], [{"id": "e0", "cost": 1}])

    def test_tie_break_by_source_pipe_order(self):
        # 三条并行管 [0,4]，成本 (0,1,1)，总量 4：来源唯一最优 (4,0,0)。
        # 覆盖 e0.hi=2 后，剩余 2 单位在 e1/e2 间任意分配，修订成本
        # （都走成本 1）与 L1 扰动（恒为 4）全部并列；按来源管路序的
        # 流量向量字典序应取第二分量最小的 (2,0,2)。
        payload = mk([("S", 4), ("T", -4)],
                     [("S", "T", 0, 4, 0),
                      ("S", "T", 0, 4, 1),
                      ("S", "T", 0, 4, 1)])
        from solver import solve
        src = solve(parse_and_validate(payload))
        self.assertEqual([f["flow"] for f in src["flows"]], [4, 0, 0])
        rec = solve_record("tie", payload, src)
        out = reroute_result(rec, [{"id": "e0", "hi": 2}])
        self.assertEqual([f["flow"] for f in out["flows"]], [2, 0, 2])
        self.assertEqual(out["objectives"]["revised_cost"], 2)
        self.assertEqual(out["objectives"]["perturbation_l1"], 4)


class TestRerouteRejections(unittest.TestCase):
    def _rec(self, status="optimal", result=None, payload=None, audit_id="x"):
        payload = payload if payload is not None else mk(
            [("S", 1), ("T", -1)], [("S", "T", 0, 2, 1)])
        result = result if result is not None else {"status": status}
        return {"audit_id": audit_id, "status": status, "payload": payload,
                "result": result}

    def test_source_not_optimal(self):
        rec = self._rec(status="infeasible",
                        result={"status": "infeasible"})
        with self.assertRaises(RerouteError) as cx:
            reroute(rec, {"source_audit_id": "x", "changes": []})
        self.assertEqual(cx.exception.http_status, 409)
        self.assertEqual(cx.exception.loc, "source_audit_id")

    def test_source_id_mismatch(self):
        rec = self._rec(audit_id="a")
        with self.assertRaises(RerouteError) as cx:
            reroute(rec, {"source_audit_id": "b", "changes": []})
        self.assertEqual(cx.exception.http_status, 409)

    def test_unknown_pipe_in_change(self):
        with self.assertRaises(RerouteError) as cx:
            reroute(self._rec(), {"source_audit_id": "x",
                                  "changes": [{"id": "nope", "hi": 3}]})
        self.assertEqual(cx.exception.loc, "changes[0].id")

    def test_duplicate_pipe_in_changes(self):
        with self.assertRaises(RerouteError) as cx:
            reroute(self._rec(), {"source_audit_id": "x",
                                  "changes": [{"id": "e0", "hi": 3},
                                              {"id": "e0", "cost": 2}]})
        self.assertEqual(cx.exception.loc, "changes[1].id")

    def test_too_many_changes(self):
        payload = mk([("S", 1), ("T", -1)],
                     [("S", "T", 0, 9, 1)] * 1)
        payload["pipes"] = [
            {"id": f"p{i}", "from": "S", "to": "T", "lo": 0, "hi": 9,
             "cost": 1} for i in range(7)]
        from solver import solve
        rec = solve_record("x", payload, solve(parse_and_validate(payload)))
        with self.assertRaises(RerouteError) as cx:
            reroute(rec, {"source_audit_id": "x",
                          "changes": [{"id": f"p{i}", "cost": 2}
                                      for i in range(7)]})
        self.assertEqual(cx.exception.loc, "changes")

    def test_float_change_rejected(self):
        with self.assertRaises(RerouteError) as cx:
            reroute(self._rec(), {"source_audit_id": "x",
                                  "changes": [{"id": "e0", "hi": 2.5}]})
        self.assertEqual(cx.exception.loc, "changes[0].hi")

    def test_coverage_hi_below_lo(self):
        with self.assertRaises(RerouteError) as cx:
            reroute(self._rec(), {"source_audit_id": "x",
                                  "changes": [{"id": "e0", "hi": -1}]})
        self.assertEqual(cx.exception.loc, "changes[0].hi")

    def test_coverage_new_lo_above_old_hi(self):
        # 原 e0: lo=0,hi=2；覆盖 lo=3 后 hi(2) < lo(3)
        with self.assertRaises(RerouteError) as cx:
            reroute(self._rec(), {"source_audit_id": "x",
                                  "changes": [{"id": "e0", "lo": 3}]})
        self.assertEqual(cx.exception.loc, "changes[0].hi")

    def test_empty_change_item(self):
        with self.assertRaises(RerouteError) as cx:
            reroute(self._rec(), {"source_audit_id": "x",
                                  "changes": [{"id": "e0"}]})
        self.assertEqual(cx.exception.loc, "changes[0]")

    def test_unknown_field(self):
        with self.assertRaises(RerouteError) as cx:
            reroute(self._rec(), {"source_audit_id": "x",
                                  "changes": [{"id": "e0", "from": "S"}]})
        self.assertEqual(cx.exception.loc, "changes[0].from")

    def test_changes_not_list(self):
        with self.assertRaises(RerouteError) as cx:
            reroute(self._rec(), {"source_audit_id": "x", "changes": {}})
        self.assertEqual(cx.exception.loc, "changes")


if __name__ == "__main__":
    unittest.main(verbosity=2)
