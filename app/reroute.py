"""低扰动改线（reroute）：从一条 optimal 审计记录出发施加管路覆盖，

在**相同供需**下按三级字典序求新整数流，全程整数有序费用元组，不使用
加权大数、不使用浮点数、也不仅复用来源方案：

  1. 第一级：修订网络（新上下界/新单位成本，站点与管路顺序冻结自来源）
     上重新做下界消去 + Dinic 可行流 + 单级负环消除，最小化修订总成本；
  2. 第二级：仅在修订成本最优的流集合内，用有序费用对
     (边际修订成本, 边际 L1 扰动) 做负环消除，最小化
     Σ_k |f_k - f0_k|（f0 为来源原流量）。|f-f0| 是分段线性函数，正向
     残量弧在 f<f0 时边际 -1、f>=f0 时 +1（反向对称），环增广在跨越
     折点 f0 前截断，保证每一步使用的都是真实边际；
  3. 第三级：仍并列时，按来源管路标识顺序（管路数组顺序）依次把各管
     流量钉死为当前最小值（对已裁决管置 lo=hi=f），以三元组
     (边际成本, 边际扰动, 该管 ±1 边际) 做负环消除，得到按原管路序
     字典序最小的流量向量。

最优性证据（均可独立复算）：
  - 第一级势 pi_c 与第二级势 (pi_c, pi_l)：最终流完整残量网络上每条
    正残量弧的字典序约化费用
      (c' + pi_c[u] - pi_c[v],  Δ|f-f0| + pi_l[u] - pi_l[l]) >= (0, 0)，
    其中第一分量单独 >= 0 即修订成本最优，两分量合起来字典序 >= 0
    即成本最优中的最低扰动；
  - 逐管原/新流量、有符号改变量与绝对改变量、逐站净供需复算。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from solver import (
    Edge,
    ValidationError,
    is_plain_int,
    lower_bound_feasible,
    parse_and_validate,
    cycle_cancel,
    _build_residual_arcs,
    _find_negative_cycle,
    _node_check,
)

MAX_CHANGES = 6
_COVER_KEYS = ("lo", "hi", "cost")


class RerouteError(Exception):
    """改线请求错误。http_status: 400 非法输入 / 409 来源状态冲突。"""

    def __init__(self, message: str, loc: str = "$", http_status: int = 400):
        super().__init__(message)
        self.message = message
        self.loc = loc
        self.http_status = http_status


# ---------------------------------------------------------------- 请求校验

def _validate_changes(raw: Any, pipe_ids: List[str]
                      ) -> Tuple[List[Dict[str, int]], Dict[str, int]]:
    """校验覆盖项，返回 (规范化覆盖列表（按来源管路序）, 按管 id 的覆盖映射)。"""
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise RerouteError("changes 必须是数组", "changes")
    if len(raw) > MAX_CHANGES:
        raise RerouteError(
            f"覆盖项至多 {MAX_CHANGES} 条，实际 {len(raw)} 条", "changes")

    index = {pid: i for i, pid in enumerate(pipe_ids)}
    by_id: Dict[str, Dict[str, int]] = {}
    for i, ch in enumerate(raw):
        loc = f"changes[{i}]"
        if not isinstance(ch, dict):
            raise RerouteError("覆盖项必须是对象", loc)
        pid = ch.get("id")
        if not isinstance(pid, str) or not pid:
            raise RerouteError("覆盖项必须给出管路标识 id", f"{loc}.id")
        if pid not in index:
            raise RerouteError(f"覆盖项引用了来源中不存在的管路: {pid!r}",
                               f"{loc}.id")
        if pid in by_id:
            raise RerouteError(f"同一管路在覆盖项中重复出现: {pid!r}",
                               f"{loc}.id")
        keys = [k for k in ch if k != "id"]
        if not keys:
            raise RerouteError("覆盖项至少给出 lo/hi/cost 中的一个新值", loc)
        unknown = [k for k in keys if k not in _COVER_KEYS]
        if unknown:
            raise RerouteError(f"覆盖项含未知字段 {unknown[0]!r}，只允许 "
                               "lo / hi / cost", f"{loc}.{unknown[0]}")
        item: Dict[str, int] = {"id": pid}
        for k in keys:
            v = ch[k]
            if not is_plain_int(v):
                raise RerouteError(
                    "必须为整数（不接受浮点数/布尔值/字符串）", f"{loc}.{k}")
            item[k] = v
        by_id[pid] = item

    ordered = [by_id[pid] for pid in pipe_ids if pid in by_id]
    return ordered, {pid: index[pid] for pid in by_id}


def _apply_coverage(source_payload: Dict[str, Any],
                    changes: List[Dict[str, int]]
                    ) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """构造修订后的求解载荷（冻结站点与管路顺序），并返回新旧界/成本对照。"""
    cover = {ch["id"]: ch for ch in changes}
    change_pos = {ch["id"]: i for i, ch in enumerate(changes)}
    revised_pipes: List[Dict[str, Any]] = []
    diffs: Dict[str, Dict[str, Any]] = {}
    for i, p in enumerate(source_payload["pipes"]):
        np = dict(p)
        ch = cover.get(p["id"])
        diff = {"id": p["id"], "old_lo": p["lo"], "old_hi": p["hi"],
                "old_cost": p["cost"], "changed": []}
        if ch is not None:
            for k in _COVER_KEYS:
                if k in ch:
                    np[k] = ch[k]
                    diff["changed"].append(k)
        diff.update({"new_lo": np["lo"], "new_hi": np["hi"],
                     "new_cost": np["cost"]})
        diffs[p["id"]] = diff
        # 覆盖后立即在来源管坐标上做边界合法性检查（字段可定位到覆盖项）
        cloc = (f"changes[{change_pos[p['id']]}]" if ch is not None
                else f"pipes[{i}]")
        if np["lo"] < 0:
            raise RerouteError("覆盖后下界 lo 必须 >= 0", f"{cloc}.lo")
        if np["hi"] < np["lo"]:
            raise RerouteError(
                f"覆盖后上界 hi({np['hi']}) 小于下界 lo({np['lo']})，边界非法",
                f"{cloc}.hi")
        if np["hi"] > 10 ** 18:
            raise RerouteError("覆盖后上界 hi 过大（上限 1e18）", f"{cloc}.hi")
        if abs(np["cost"]) > 10 ** 12:
            raise RerouteError("覆盖后单位成本绝对值过大（上限 1e12）",
                               f"{cloc}.cost")
        revised_pipes.append(np)

    return ({"stations": [dict(s) for s in source_payload["stations"]],
             "pipes": revised_pipes}), diffs


# ---------------------------------------------------------------- 主入口

def reroute(source_record: Dict[str, Any], payload: Any) -> Dict[str, Any]:
    """依据来源审计记录与本次请求执行低扰动改线。"""
    if not isinstance(payload, dict):
        raise RerouteError("请求体必须是 JSON 对象 {source_audit_id, changes}",
                           "$")

    source_id = payload.get("source_audit_id")
    if not isinstance(source_id, str) or not source_id:
        raise RerouteError("必须给出来源审计标识 source_audit_id",
                           "source_audit_id")
    if source_record.get("audit_id") != source_id:
        raise RerouteError("来源审计标识与记录不一致", "source_audit_id", 409)
    if source_record.get("status") != "optimal":
        raise RerouteError(
            f"来源审计 {source_id!r} 状态为 "
            f"{source_record.get('status')!r}，只有 optimal 记录可发起改线",
            "source_audit_id", 409)
    source_result = source_record.get("result")
    source_payload = source_record.get("payload")
    if not isinstance(source_result, dict) or \
            source_result.get("status") != "optimal":
        raise RerouteError("来源审计缺少 optimal 结果体，无法发起改线",
                           "source_audit_id", 409)
    if source_result.get("kind") == "reroute":
        raise RerouteError(
            "来源审计本身是改线记录；请以最初的液路求解记录作为来源",
            "source_audit_id", 409)
    if not isinstance(source_payload, dict) or \
            "stations" not in source_payload or "pipes" not in source_payload:
        raise RerouteError(
            "来源审计记录缺少冻结载荷（旧版记录请重新求解后再发起改线）",
            "source_audit_id", 409)

    try:
        source_prob = parse_and_validate(source_payload)
    except ValidationError as exc:
        raise RerouteError(f"来源载荷无法重建: {exc.message}", exc.loc, 409)

    changes, _ = _validate_changes(payload.get("changes"),
                                   [e.id for e in source_prob.edges])

    revised_payload, diffs = _apply_coverage(source_payload, changes)
    try:
        problem = parse_and_validate(revised_payload)
    except ValidationError as exc:
        raise RerouteError(exc.message, exc.loc)

    ids = problem.ids
    n = len(ids)
    edges = problem.edges
    m = len(edges)

    # 来源原流量（按来源管路顺序；管序与修订网络一致）
    src_flow_map = {f["id"]: f["flow"] for f in source_result["flows"]}
    if set(src_flow_map) != {e.id for e in edges}:
        raise RerouteError("来源流量与管路标识集合不一致", "source_audit_id", 409)
    f0 = [src_flow_map[e.id] for e in edges]

    frozen = {
        "stations": [{"id": s_id, "balance": b}
                     for s_id, b in zip(ids, problem.balance)],
        "pipe_order": [e.id for e in edges],
        "source_flows": {e.id: f0[k] for k, e in enumerate(edges)},
        "changes": changes,
    }

    # 1) 修订网络上重新求可行流（下界消去 + Dinic）
    flow, evidence = lower_bound_feasible(ids, problem.balance, edges)
    if evidence is not None:
        evidence["kind"] = "reroute"
        evidence["status"] = "infeasible"
        evidence["source_audit_id"] = source_id
        evidence["frozen"] = frozen
        evidence["note"] = (
            "覆盖后的修订网络无可行流：下列未满足需求/滞留供给与残量可达集"
            "为不可行覆盖证据（来源站点、管路顺序、原流量已冻结）。")
        return evidence

    costs = [e.cost for e in edges]

    # 2) 第一级：修订成本最小（单级负环消除）
    def w_cost(k: int, d: int, f: int) -> Tuple[int, ...]:
        return (d * costs[k],)

    flow, _pi_cost_t, cc_cost = cycle_cancel(edges, flow, n, 1, w_cost)

    # 3) 第二级：成本最优集合内最小化 Σ|f-f0|（有序费用对）
    def marg_l1(k: int, d: int, f: int) -> int:
        # 沿残量弧推进一步的 |f-f0| 增量
        if d == +1:
            return -1 if f < f0[k] else +1
        return -1 if f > f0[k] else +1

    def stable_l1(k: int, d: int, f: int) -> Optional[int]:
        # 边际在 |f-f0| 折点处翻转，截断到折点（含），杜绝跨折点误用旧边际
        if d == +1 and f < f0[k]:
            return f0[k] - f
        if d == -1 and f > f0[k]:
            return f - f0[k]
        return None

    def w_perturb(k: int, d: int, f: int) -> Tuple[int, ...]:
        return (d * costs[k], marg_l1(k, d, f))

    flow, _pi_pair, cc_pert = cycle_cancel(
        edges, flow, n, 2, w_perturb, stable_l1)

    # 4) 第三级：按来源管路标识序的流量向量字典序裁决。
    #    每裁决一条管，就把已裁决管钉死（lo=hi=当前流），使后续任何
    #    负环移动都不能改变已裁决分量。负三元环只能出现在前两分量
    #    恰为 (0,0) 的环上，故裁决过程严格保持修订成本与 L1 扰动不变。
    cc_tie = 0
    for j in range(m):
        pinned = [
            Edge(e.id, e.u, e.v,
                 flow[t] if t < j else e.lo,
                 flow[t] if t < j else e.hi,
                 e.cost)
            for t, e in enumerate(edges)
        ]

        def w_tie(k: int, d: int, f: int, j: int = j) -> Tuple[int, ...]:
            return (d * costs[k], marg_l1(k, d, f), d if k == j else 0)

        flow, _pi_tie, cc_j = cycle_cancel(
            pinned, flow, n, 3, w_tie, stable_l1)
        cc_tie += cc_j

    revised_cost = sum(flow[k] * costs[k] for k in range(m))
    perturbation = sum(abs(flow[k] - f0[k]) for k in range(m))

    # 5) 在**最终流**的完整残量网络上重新取证：
    #    - 单级费用 Bellman-Ford 距离即修订成本势 pi_c；
    #    - 有序费用对的 Bellman-Ford 距离即 (pi_c, pi_l)，其第一分量与
    #      单级距离一致（同零初始化），一并用于字典序约化费用复算。
    final_arcs = _build_residual_arcs(edges, flow, w_perturb)
    none_cycle, pi_pair = _find_negative_cycle(final_arcs, n, 2)
    if none_cycle is not None:  # 理论上不可达：防御性校验
        raise RerouteError("内部错误：最终流仍存在字典序负环", "$", 500)
    pi_c = [p[0] for p in pi_pair]
    pi_l = [p[1] for p in pi_pair]
    edge_reports: List[Dict[str, Any]] = []
    pipe_changes: List[Dict[str, Any]] = []
    for k, e in enumerate(edges):
        residual = []
        fwd = e.hi - flow[k]
        if fwd > 0:
            rc_c = e.cost + pi_c[e.u] - pi_c[e.v]
            rc_l = marg_l1(k, +1, flow[k]) + pi_l[e.u] - pi_l[e.v]
            residual.append({
                "direction": "forward",
                "from": ids[e.u], "to": ids[e.v],
                "capacity": fwd, "cost": e.cost,
                "perturbation_marginal_cost": marg_l1(k, +1, flow[k]),
                "cost_reduced_cost": rc_c,
                "perturbation_reduced_cost": rc_l,
                "lex_reduced_cost": [rc_c, rc_l],
            })
        bwd = flow[k] - e.lo
        if bwd > 0:
            rc_c = -e.cost + pi_c[e.v] - pi_c[e.u]
            rc_l = marg_l1(k, -1, flow[k]) + pi_l[e.v] - pi_l[e.u]
            residual.append({
                "direction": "backward",
                "from": ids[e.v], "to": ids[e.u],
                "capacity": bwd, "cost": -e.cost,
                "perturbation_marginal_cost": marg_l1(k, -1, flow[k]),
                "cost_reduced_cost": rc_c,
                "perturbation_reduced_cost": rc_l,
                "lex_reduced_cost": [rc_c, rc_l],
            })
        edge_reports.append({
            "id": e.id, "from": ids[e.u], "to": ids[e.v],
            "flow": flow[k], "lo": e.lo, "hi": e.hi, "cost": e.cost,
            "cost_contribution": flow[k] * e.cost,
            "residual": residual,
        })

        d = diffs[e.id]
        pipe_changes.append({
            "id": e.id, "from": ids[e.u], "to": ids[e.v],
            "changed_fields": d["changed"],
            "old_lo": d["old_lo"], "new_lo": d["new_lo"],
            "old_hi": d["old_hi"], "new_hi": d["new_hi"],
            "old_cost": d["old_cost"], "new_cost": d["new_cost"],
            "source_flow": f0[k], "flow": flow[k],
            "signed_delta": flow[k] - f0[k],
            "abs_delta": abs(flow[k] - f0[k]),
        })

    return {
        "kind": "reroute",
        "status": "optimal",
        "source_audit_id": source_id,
        "algorithm": "lower-bound-elimination + dinic-maxflow + "
                     "lexicographic cycle-cancelling on ordered integer "
                     "cost pairs (revised cost, L1 perturbation), then "
                     "pipe-order flow-vector tie-break (exact integers)",
        "frozen": frozen,
        "objectives": {
            "revised_cost": revised_cost,
            "perturbation_l1": perturbation,
            "tie_order": [e.id for e in edges],
        },
        "total_cost": revised_cost,
        "perturbation_l1": perturbation,
        "flows": [{"id": e.id, "flow": flow[k]}
                  for k, e in enumerate(edges)],
        "pipe_changes": pipe_changes,
        "edges": edge_reports,
        "potentials": {
            ids[v]: {"cost": pi_c[v], "perturbation": pi_l[v]}
            for v in range(n)
        },
        "node_check": _node_check(ids, problem.balance, edges, flow),
        "cycles_cancelled": {
            "cost_stage": cc_cost,
            "perturbation_stage": cc_pert,
            "tie_stage": cc_tie,
        },
        "evidence": {
            "cost_optimality":
                "第一级势 pi_c 使最终流每条正残量弧的约化修订成本 "
                "c' + pi_c[u] - pi_c[v] >= 0（见 edges[].residual 的 "
                "lex_reduced_cost[0]），故修订总成本最小。",
            "perturbation_optimality":
                "有序费用对 (边际修订成本, 边际|f-f0|) 的势 (pi_c, pi_l) "
                "使每条正残量弧 lex_reduced_cost = [rc_cost, rc_perturbation] "
                "字典序 >= [0,0]，故在全部修订成本最优流中 L1 扰动最小；"
                "perturbation_l1 等于逐管 abs_delta 之和。",
            "tie_break":
                "仍并列时依次钉死来源管路序 tie_order 中更早的管路流量并"
                "消除负环，所得流量向量在 (修订成本, L1扰动, 流量向量) "
                "字典序下最小。",
            "integrality": "所有计算均为任意精度整数有序元组，未使用浮点数"
                           "或加权大数。",
        },
    }
