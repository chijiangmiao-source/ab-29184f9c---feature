"""求解器测试：正确性、上下界、最优性（暴力枚举对照）、失败证据、大整数。"""

import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solver import (  # noqa: E402
    MAX_PIPES,
    MAX_STATIONS,
    ValidationError,
    parse_and_validate,
    solve,
    solve_payload,
)


def mk(stations, pipes):
    return {"stations": [{"id": s, "balance": b} for s, b in stations],
            "pipes": [
                {"id": f"e{i}", "from": u, "to": v, "lo": lo, "hi": hi,
                 "cost": c}
                for i, (u, v, lo, hi, c) in enumerate(pipes)]}


def verify_solution(res, prob):
    """独立复算：界、节点平衡、费用求和、约化成本非负。"""
    assert res["status"] == "optimal"
    ids = prob.ids
    bal = dict(zip(ids, prob.balance))
    flows = {f["id"]: f["flow"] for f in res["flows"]}
    recomputed_cost = 0
    for e in prob.edges:
        f = flows[e.id]
        assert e.lo <= f <= e.hi, f"边 {e.id} 流量 {f} 越界 [{e.lo},{e.hi}]"
        recomputed_cost += f * e.cost
    net = {s: 0 for s in ids}
    for e in prob.edges:
        net[ids[e.u]] += flows[e.id]
        net[ids[e.v]] -= flows[e.id]
    for s in ids:
        assert net[s] == bal[s], f"站点 {s} 净流出 {net[s]} != {bal[s]}"
    assert recomputed_cost == res["total_cost"], "费用求和不一致"
    pi = res["potentials"]
    for er in res["edges"]:
        for arc in er["residual"]:
            assert arc["capacity"] > 0
            rc = arc["cost"] + pi[arc["from"]] - pi[arc["to"]]
            assert rc == arc["reduced_cost"], "约化成本回显与势不一致"
            assert rc >= 0, f"正残量边 {er.id}/{arc['direction']} 约化成本 {rc} < 0"
    return recomputed_cost


def brute_force_min_cost(prob):
    """枚举所有界内整数流，返回最小费用（仅小规模用）。"""
    ranges = [range(e.lo, e.hi + 1) for e in prob.edges]
    best = None
    n = len(prob.ids)
    for combo in itertools.product(*ranges):
        net = [0] * n
        cost = 0
        for k, e in enumerate(prob.edges):
            f = combo[k]
            net[e.u] += f
            net[e.v] -= f
            cost += f * e.cost
        if net == prob.balance:
            best = cost if best is None else min(best, cost)
    return best


class TestBasic(unittest.TestCase):
    def test_simple_cheapest_route(self):
        # 源有 3，汇需 3；两条路径一贵一便宜
        payload = mk([("S", 3), ("T", -3)],
                     [("S", "T", 0, 5, 7),
                      ("S", "T", 0, 2, 2)])
        res = solve_payload(payload)
        prob = parse_and_validate(payload)
        cost = verify_solution(res, prob)
        f = {x["id"]: x["flow"] for x in res["flows"]}
        self.assertEqual(f["e0"], 1)  # 容量 2 的便宜管先用满
        self.assertEqual(f["e1"], 2)
        self.assertEqual(cost, 1 * 7 + 2 * 2)
        self.assertEqual(brute_force_min_cost(prob), cost)

    def test_min_max_throughput_binding(self):
        # 最小通量管路 lo=3 必须承载；最大通量 hi=2 限制便宜路径
        payload = mk([("S", 5), ("M", 0), ("T", -5)],
                     [("S", "M", 3, 3, 1),   # 通量被钉死为 3
                      ("S", "T", 0, 2, 1),   # 便宜但最大通量 2
                      ("M", "T", 0, 10, 9),
                      ("S", "T", 0, 10, 20)])
        res = solve_payload(payload)
        prob = parse_and_validate(payload)
        cost = verify_solution(res, prob)
        f = {x["id"]: x["flow"] for x in res["flows"]}
        self.assertEqual(f["e0"], 3)
        self.assertEqual(f["e1"], 2)
        self.assertEqual(cost, 3 * 1 + 2 * 1 + 3 * 9)
        self.assertEqual(brute_force_min_cost(prob), cost)

    def test_negative_cost_cycle_is_used(self):
        # D1->D2 负费用：应把货从 D1 转去 D2，触发环消除改进
        payload = mk([("S1", 6), ("S2", 4), ("V1", 0), ("V2", 0),
                      ("D1", -3), ("D2", -7)],
                     [("S1", "V1", 1, 8, 2),
                      ("S2", "V1", 0, 5, 4),
                      ("S2", "V2", 1, 6, 1),
                      ("V1", "D1", 0, 4, 0),
                      ("V1", "D2", 0, 9, 3),
                      ("V2", "D2", 0, 7, 2),
                      ("D1", "D2", 0, 3, -1)])
        res = solve_payload(payload)
        prob = parse_and_validate(payload)
        cost = verify_solution(res, prob)
        f = {x["id"]: x["flow"] for x in res["flows"]}
        # p4(V1->D1) 容量 4 且 D1 需 3：p4 - p7 = 3，故 p7 至多 1；
        # 便宜路径 V1->D1(cost 0) 恰好让 p7=1（负费用，仍被用到）。
        self.assertEqual(f["e6"], 1)
        self.assertEqual(cost, 29)
        self.assertEqual(brute_force_min_cost(prob), cost)
        self.assertGreaterEqual(res["cycles_cancelled"], 1)

    def test_lower_bound_forces_expensive_edge(self):
        # 便宜路径容量足够，但贵管 lo=2 强制使用
        payload = mk([("S", 4), ("T", -4)],
                     [("S", "T", 2, 5, 100),
                      ("S", "T", 0, 10, 1)])
        res = solve_payload(payload)
        prob = parse_and_validate(payload)
        cost = verify_solution(res, prob)
        f = {x["id"]: x["flow"] for x in res["flows"]}
        self.assertEqual(f["e0"], 2)
        self.assertEqual(f["e1"], 2)
        self.assertEqual(cost, 202)
        self.assertEqual(brute_force_min_cost(prob), cost)

    def test_zero_supply_zero_flow(self):
        payload = mk([("A", 0), ("B", 0)],
                     [("A", "B", 0, 5, -3)])  # 有负环倾向但无货
        res = solve_payload(payload)
        prob = parse_and_validate(payload)
        cost = verify_solution(res, prob)
        self.assertEqual(cost, 0)
        self.assertEqual(res["flows"][0]["flow"], 0)

    def test_large_integers_exact(self):
        big = 10 ** 18
        payload = mk([("S", big), ("T", -big)],
                     [("S", "T", 0, big, 10 ** 6)])
        res = solve_payload(payload)
        prob = parse_and_validate(payload)
        cost = verify_solution(res, prob)
        self.assertEqual(cost, big * 10 ** 6)  # 10^24，远超浮点安全整数
        self.assertIsInstance(cost, int)


class TestInfeasible(unittest.TestCase):
    def test_infeasible_cut_evidence(self):
        # T 需求 5，但唯一进入 T 的边容量 2
        payload = mk([("S", 5), ("T", -5)],
                     [("S", "T", 0, 2, 1)])
        res = solve_payload(payload)
        self.assertEqual(res["status"], "infeasible")
        self.assertEqual(res["unmet_demand"][0]["station"], "T")
        self.assertEqual(res["unmet_demand"][0]["unsatisfied"], 3)
        # S 可从超源经残量到达，T 不可达
        self.assertIn("S", res["reachable_from_source"])
        self.assertNotIn("T", res["reachable_from_source"])

    def test_lower_bound_makes_infeasible(self):
        # lo=8 超过汇承受能力（需求仅 3）：消界后 S 承担 5 单位净输出义务
        payload = mk([("S", 3), ("T", -3)],
                     [("S", "T", 8, 10, 1)])
        res = solve_payload(payload)
        self.assertEqual(res["status"], "infeasible")
        self.assertTrue(any(u["station"] == "S" and u["unsatisfied"] == 5
                            for u in res["unmet_demand"]))
        self.assertIn("T", res["reachable_from_source"])

    def test_isolated_demand(self):
        payload = mk([("S", 2), ("X", -2)], [])
        res = solve_payload(payload)
        self.assertEqual(res["status"], "infeasible")
        self.assertEqual(res["reachable_from_source"], ["S"])


class TestValidation(unittest.TestCase):
    def test_supply_demand_imbalance(self):
        payload = mk([("S", 4), ("T", -3)], [])
        with self.assertRaises(ValidationError) as ctx:
            parse_and_validate(payload)
        self.assertIn("不平衡", ctx.exception.message)
        self.assertEqual(ctx.exception.loc, "stations")

    def test_hi_less_than_lo(self):
        payload = mk([("S", 1), ("T", -1)],
                     [("S", "T", 5, 4, 1)])
        with self.assertRaises(ValidationError) as ctx:
            parse_and_validate(payload)
        self.assertEqual(ctx.exception.loc, "pipes[0].hi")

    def test_float_rejected(self):
        payload = {"stations": [{"id": "S", "balance": 2.0},
                                {"id": "T", "balance": -2}],
                   "pipes": []}
        with self.assertRaises(ValidationError) as ctx:
            parse_and_validate(payload)
        self.assertEqual(ctx.exception.loc, "stations[0].balance")

    def test_duplicate_station(self):
        payload = mk([("S", 1), ("S", -1)], [])
        with self.assertRaises(ValidationError) as ctx:
            parse_and_validate(payload)
        self.assertIn("重复", ctx.exception.message)

    def test_too_many_stations(self):
        payload = mk([(f"S{i}", 0) for i in range(MAX_STATIONS + 1)], [])
        with self.assertRaises(ValidationError):
            parse_and_validate(payload)

    def test_too_many_pipes(self):
        stations = [("S", 1), ("T", -1)]
        pipes = [("S", "T", 0, 1, 0) for _ in range(MAX_PIPES + 1)]
        with self.assertRaises(ValidationError):
            parse_and_validate(mk(stations, pipes))

    def test_unknown_endpoint(self):
        payload = {"stations": [{"id": "S", "balance": 1},
                                {"id": "T", "balance": -1}],
                   "pipes": [{"id": "x", "from": "S", "to": "Z",
                              "lo": 0, "hi": 1, "cost": 0}]}
        with self.assertRaises(ValidationError) as ctx:
            parse_and_validate(payload)
        self.assertEqual(ctx.exception.loc, "pipes[0].to")


class TestOptimalityBruteForce(unittest.TestCase):
    def test_random_small_networks_match_bruteforce(self):
        import random
        rng = random.Random(20260927)
        for trial in range(60):
            n = rng.randint(2, 4)
            ids = [f"v{i}" for i in range(n)]
            # 随机生成平衡供需
            total = rng.randint(0, 5)
            bals = [0] * n
            for _ in range(total):
                bals[rng.randrange(n)] += 1
                bals[rng.randrange(n)] -= 1
            stations = list(zip(ids, bals))
            pipes = []
            used = set()
            for _ in range(rng.randint(1, 5)):
                u, v = rng.sample(range(n), 2)
                if (u, v) in used:
                    continue
                used.add((u, v))
                hi = rng.randint(0, 4)
                lo = rng.randint(0, hi)
                c = rng.randint(-3, 5)
                pipes.append((ids[u], ids[v], lo, hi, c))
            payload = mk(stations, pipes)
            prob = parse_and_validate(payload)
            res = solve(prob)
            expected = brute_force_min_cost(prob)
            if expected is None:
                self.assertEqual(res["status"], "infeasible",
                                 f"trial {trial}: {payload}")
            else:
                self.assertEqual(res["status"], "optimal")
                self.assertEqual(verify_solution(res, prob), expected,
                                 f"trial {trial}: {payload}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
