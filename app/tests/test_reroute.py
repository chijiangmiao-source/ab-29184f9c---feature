"""低扰动改线求解器测试：字典序双目标最优性、分段边际费用、证据复算。

双目标（修订成本 → 总绝对改变量 → 管路标识序流量向量）以小规模网络对
全枚举裁决做对照；三级证据（成本势 / 扰动势 / 字典序势）逐条弧复算。
"""

import itertools
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solver import parse_and_validate, solve, solve_reroute  # noqa: E402


def mk(stations, pipes):
    return {"stations": [{"id": s, "balance": b} for s, b in stations],
            "pipes": [
                {"id": f"e{i}", "from": u, "to": v, "lo": lo, "hi": hi,
                 "cost": c}
                for i, (u, v, lo, hi, c) in enumerate(pipes)]}


def all_feasible(prob):
    out = []
    for combo in itertools.product(*[range(e.lo, e.hi + 1)
                                     for e in prob.edges]):
        net = [0] * len(prob.ids)
        for k, e in enumerate(prob.edges):
            net[e.u] += combo[k]
            net[e.v] -= combo[k]
        if net == prob.balance:
            out.append(combo)
    return out


def lex_optimum(prob, base):
    cands = all_feasible(prob)
    if not cands:
        return None
    cost_of = lambda f: sum(f[k] * e.cost
                            for k, e in enumerate(prob.edges))
    dist_of = lambda f: sum(abs(f[k] - base[k])
                            for k in range(len(prob.edges)))
    cmin = min(cost_of(f) for f in cands)
    cands = [f for f in cands if cost_of(f) == cmin]
    dmin = min(dist_of(f) for f in cands)
    cands = [f for f in cands if dist_of(f) == dmin]
    return sorted(cands)[0], cmin, dmin


def base_flows(res):
    return [f["flow"] for f in res["flows"]]


def check_evidence(self, res, prob):
    """三级证据独立复算。"""
    ids = prob.ids
    m = len(prob.edges)
    flows = {f["id"]: f["flow"] for f in res["flows"]}
    pe = res["perturbation_evidence"]
    pi, pmap, qmap = (pe["potentials"], pe["perturbation_potentials"],
                      pe["lex_potentials"])
    # 分段容量必须为正且与当前流量/界自洽
    for a in pe["arcs"]:
        self.assertGreater(a["capacity"], 0)
        # 一级：成本约化费用回显一致且非负
        rc = a["cost"] + pi[a["from"]] - pi[a["to"]]
        self.assertEqual(rc, a["reduced_cost"])
        self.assertGreaterEqual(rc, 0)
        # 二级：仅零成本面子网给扰动约化，回显一致且非负
        if a["reduced_w2"] is not None:
            self.assertEqual(rc, 0)
            rw = a["w2"] + pmap[a["from"]] - pmap[a["to"]]
            self.assertEqual(rw, a["reduced_w2"])
            self.assertGreaterEqual(rw, 0)
        # 三级：字典序松弛按管路序为 m 维向量且字典序非负
        if a["lex_slack"] is not None:
            self.assertEqual(rc, 0)
            self.assertEqual(a["reduced_w2"], 0)
            self.assertEqual(len(a["lex_slack"]), m)
            first = next((x for x in a["lex_slack"] if x != 0), 0)
            self.assertGreaterEqual(first, 0)
    # 势按站点给出，字典序势为按管路序的 m 维整数向量
    self.assertEqual(set(pi), set(ids))
    self.assertEqual(set(pmap), set(ids))
    self.assertEqual(set(qmap), set(ids))
    for s in ids:
        self.assertEqual(len(qmap[s]), m)
        self.assertTrue(all(isinstance(x, int) for x in qmap[s]))
    # 可行性 + 界 + 成本
    for e in prob.edges:
        f = flows[e.id]
        self.assertTrue(e.lo <= f <= e.hi)
    for n in res["node_check"]:
        self.assertTrue(n["ok"])


class TestRerouteSolver(unittest.TestCase):
    def test_cost_then_perturbation_basic(self):
        # 原网络：e0 贵、e1 便宜，来源流走满 e1
        payload = mk([("S", 3), ("T", -3)],
                     [("S", "T", 0, 3, 7), ("S", "T", 0, 3, 2)])
        prob = parse_and_validate(payload)
        src = solve(prob)
        self.assertEqual(src["status"], "optimal")
        base = base_flows(src)
        self.assertEqual(base, [0, 3])

        # 改线：把 e0 改得比 e1 更便宜
        revised = mk([("S", 3), ("T", -3)],
                     [("S", "T", 0, 3, 1), ("S", "T", 0, 3, 2)])
        rprob = parse_and_validate(revised)
        res = solve_reroute(rprob, base)
        self.assertEqual(res["status"], "optimal")
        # 最小成本 = 3*1=3（必须改走 e0），不是复用来源方案的成本 6
        self.assertEqual(res["total_cost"], 3)
        f = [x["flow"] for x in res["flows"]]
        self.assertEqual(f, [3, 0])
        self.assertEqual(res["objectives"]["level2_total_abs_change"], 6)
        check_evidence(self, res, rprob)

    def test_lexicographic_tie_break_by_pipe_order(self):
        # 三条并联管；来源 e0=3,e1=0,e2=0（原成本 0/1/1）。
        payload = mk([("S", 3), ("T", -3)],
                     [("S", "T", 0, 3, 0), ("S", "T", 0, 3, 1),
                      ("S", "T", 0, 3, 1)])
        prob = parse_and_validate(payload)
        base = base_flows(solve(prob))
        self.assertEqual(base, [3, 0, 0])

        # 覆盖：e0 上界收紧为 1，三条成本全改平（0,0,0）
        revised = mk([("S", 3), ("T", -3)],
                     [("S", "T", 0, 1, 0), ("S", "T", 0, 3, 0),
                      ("S", "T", 0, 3, 0)])
        rprob = parse_and_validate(revised)
        res = solve_reroute(rprob, base)
        f = [x["flow"] for x in res["flows"]]
        # 成本同为 0、总改变量同为 4 的方案有 (1,2,0)/(1,1,1)/(1,0,2)，
        # 按管路标识序流量向量字典序裁决应得 (1,0,2)
        self.assertEqual(f, [1, 0, 2])
        self.assertEqual(res["total_cost"], 0)
        self.assertEqual(res["objectives"]["level2_total_abs_change"], 4)
        # 存在第三级（成本0、扰动0）字典序改进环被消除
        self.assertGreaterEqual(
            res["reroute_cycles"]["lexicographic_cycles"], 1)
        check_evidence(self, res, rprob)

    def test_base_flow_outside_new_bounds_uses_projection(self):
        # 来源 e0=5；改线把 e0 上界压到 2，多余 3 单必须走更贵的 e1。
        payload = mk([("S", 5), ("T", -5)],
                     [("S", "T", 0, 5, 1), ("S", "T", 0, 5, 9)])
        base = base_flows(solve(parse_and_validate(payload)))
        self.assertEqual(base, [5, 0])
        revised = mk([("S", 5), ("T", -5)],
                     [("S", "T", 0, 2, 1), ("S", "T", 0, 5, 9)])
        rprob = parse_and_validate(revised)
        res = solve_reroute(rprob, base)
        f = [x["flow"] for x in res["flows"]]
        self.assertEqual(f, [2, 3])
        self.assertEqual(res["objectives"]["level2_total_abs_change"], 6)
        # t0=clamp(5,0,2)=2：最终 e0 顶在上界，其正向无残量弧、反向为“离源”段
        e0_arcs = [a for a in res["perturbation_evidence"]["arcs"]
                   if a["pipe"] == "e0"]
        self.assertTrue(all(a["segment"] == "away_from_source"
                            for a in e0_arcs))
        check_evidence(self, res, rprob)

    def test_zero_change_when_source_stays_cost_optimal(self):
        # 只改一条无关管路的成本，来源流在新网络仍唯一最优 => 改变量 0，
        # 且结果是重新求解得到而非复用（算法字段与证据齐全）。
        payload = mk([("S", 2), ("A", 0), ("T", -2)],
                     [("S", "A", 0, 2, 1), ("A", "T", 0, 2, 1),
                      ("S", "T", 0, 2, 5)])
        base = base_flows(solve(parse_and_validate(payload)))
        revised = mk([("S", 2), ("A", 0), ("T", -2)],
                     [("S", "A", 0, 2, 1), ("A", "T", 0, 2, 1),
                      ("S", "T", 0, 2, 50)])
        rprob = parse_and_validate(revised)
        res = solve_reroute(rprob, base)
        self.assertEqual([x["flow"] for x in res["flows"]], base)
        self.assertEqual(res["objectives"]["level2_total_abs_change"], 0)
        self.assertEqual(res["total_cost"], 4)
        check_evidence(self, res, rprob)

    def test_covered_network_infeasible_returns_evidence(self):
        payload = mk([("S", 5), ("T", -5)],
                     [("S", "T", 0, 5, 1)])
        base = base_flows(solve(parse_and_validate(payload)))
        revised = mk([("S", 5), ("T", -5)],
                     [("S", "T", 0, 2, 1)])
        res = solve_reroute(parse_and_validate(revised), base)
        self.assertEqual(res["status"], "infeasible")
        self.assertEqual(res["unmet_demand"][0]["station"], "T")
        self.assertEqual(res["unmet_demand"][0]["unsatisfied"], 3)
        self.assertEqual(res["infeasible_stage"], "reroute_revised_network")

    def test_random_networks_match_full_enumeration(self):
        rng = random.Random(20260929)
        checked = 0
        for trial in range(180):
            n = rng.randint(2, 4)
            ids = [f"v{i}" for i in range(n)]
            total = rng.randint(0, 5)
            bals = [0] * n
            for _ in range(total):
                bals[rng.randrange(n)] += 1
                bals[rng.randrange(n)] -= 1
            pipes = []
            used = set()
            for _ in range(rng.randint(1, 5)):
                u, v = rng.sample(range(n), 2)
                if (u, v) in used:
                    continue
                used.add((u, v))
                hi = rng.randint(0, 4)
                lo = rng.randint(0, hi)
                pipes.append((ids[u], ids[v], lo, hi, rng.randint(-3, 5)))
            payload = mk(list(zip(ids, bals)), pipes)
            prob = parse_and_validate(payload)
            src = solve(prob)
            if src["status"] != "optimal":
                continue
            base = base_flows(src)
            rev = [list(p) for p in pipes]
            for idx in rng.sample(range(len(rev)),
                                  rng.randint(1, min(3, len(rev)))):
                field = rng.randint(0, 2)
                if field == 0:
                    rev[idx][2] = rng.randint(0, rev[idx][3])
                elif field == 1:
                    rev[idx][3] = rng.randint(rev[idx][2], 5)
                else:
                    rev[idx][4] = rng.randint(-4, 6)
            rpayload = mk(list(zip(ids, bals)), [tuple(p) for p in rev])
            rprob = parse_and_validate(rpayload)
            res = solve_reroute(rprob, base)
            expected = lex_optimum(rprob, base)
            if expected is None:
                self.assertEqual(res["status"], "infeasible", f"trial {trial}")
                continue
            ef, ec, ed = expected
            self.assertEqual(res["status"], "optimal", f"trial {trial}")
            self.assertEqual(res["total_cost"], ec, f"trial {trial}")
            self.assertEqual(res["objectives"]["level2_total_abs_change"],
                             ed, f"trial {trial}")
            self.assertEqual(tuple(x["flow"] for x in res["flows"]), ef,
                             f"trial {trial}")
            check_evidence(self, res, rprob)
            checked += 1
        self.assertGreater(checked, 40)


class TestPrepareRerouteValidation(unittest.TestCase):
    def _source_rec(self, status="optimal", result=None):
        if result is None:
            payload = mk([("S", 2), ("T", -2)],
                         [("S", "T", 0, 3, 1)])
            result = solve(parse_and_validate(payload))
        return {"audit_id": "src", "status": status,
                "payload_fingerprint": "x", "result": result}

    def test_frozen_snapshot_contents(self):
        from reroute import prepare_reroute
        payload = mk([("S", 2), ("T", -2)],
                     [("S", "T", 0, 3, 1), ("S", "T", 0, 3, 2)])
        rec = self._source_rec(result=solve(parse_and_validate(payload)))
        problem, base, mods, frozen = prepare_reroute(
            rec, [{"pipe": "e1", "cost": 0}])
        self.assertEqual(base, [2, 0])
        self.assertEqual(frozen["pipe_order"], ["e0", "e1"])
        self.assertEqual(mods, [{"pipe": "e1", "cost": 0}])
        self.assertEqual(len(frozen["revised_pipes"]), 2)
        self.assertEqual(frozen["revised_pipes"][1]["cost"], 0)
        self.assertEqual(frozen["source_pipes"][1]["cost"], 2)
        # 站点与原流量冻结
        self.assertEqual(frozen["stations"][0], {"id": "S", "balance": 2})
        self.assertEqual(frozen["source_flows"],
                         [{"id": "e0", "flow": 2}, {"id": "e1", "flow": 0}])

    def test_rejects(self):
        from reroute import prepare_reroute
        from solver import ValidationError
        rec = self._source_rec()

        with self.assertRaises(ValidationError) as cx:
            prepare_reroute(rec, {"pipe": "e0"})
        self.assertEqual(cx.exception.loc, "modifications")

        with self.assertRaises(ValidationError) as cx:
            prepare_reroute(rec, [])
        self.assertEqual(cx.exception.loc, "modifications")

        with self.assertRaises(ValidationError) as cx:
            prepare_reroute(rec, [{"pipe": "zzz", "hi": 2}])
        self.assertEqual(cx.exception.loc, "modifications[0].pipe")

        with self.assertRaises(ValidationError) as cx:
            prepare_reroute(rec, [{"pipe": "e0", "hi": 2},
                                  {"pipe": "e0", "cost": 3}])
        self.assertIn("重复", cx.exception.message)

        with self.assertRaises(ValidationError) as cx:
            prepare_reroute(rec, [{"pipe": "e0", "hi": 1.5}])
        self.assertEqual(cx.exception.loc, "modifications[0].hi")

        with self.assertRaises(ValidationError) as cx:
            prepare_reroute(rec, [{"pipe": "e0"}])
        self.assertIn("至少给出", cx.exception.message)

        with self.assertRaises(ValidationError) as cx:
            prepare_reroute(rec, [{"pipe": "e0", "bogus": 1}])
        self.assertIn("未知字段", cx.exception.message)

        with self.assertRaises(ValidationError) as cx:
            prepare_reroute(rec, [{"pipe": "e0", "lo": 9}])
        self.assertIn("覆盖后", cx.exception.message)

        many = [{"pipe": "e0", "cost": i} for i in range(7)]  # noqa: F841
        # 7 条不同管路：覆盖项超过至多 6 条的上限
        p7 = mk([("S", 8), ("T", -8)],
                [("S", "T", 0, 8, 0) for _ in range(7)])
        rec7 = self._source_rec(result=solve(parse_and_validate(p7)))
        with self.assertRaises(ValidationError) as cx:
            prepare_reroute(rec7, [{"pipe": f"e{i}", "cost": 1}
                                   for i in range(7)])
        self.assertEqual(cx.exception.loc, "modifications")


if __name__ == "__main__":
    unittest.main(verbosity=2)
