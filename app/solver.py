"""精确整数最小费用流求解器（带上下界）。

算法流程（全程整数运算，不使用浮点数）：

1. 校验输入（规模、整数性、上下界、供需平衡等），错误带字段路径定位。
2. 消去下界：每条边 e=(u,v) 的 lo 单位流量被预先"注入"，节点余额
   b(v) = balance(v) + sum(lo 进入 v) - sum(lo 离开 v)，
   剩余容量 hi-lo 上再求可行流（Dinic 最大流，超源/超汇按 b 符号连接）。
3. 若超源出边未全部饱和，返回不可行证据：未满足需求 + 残量网络中
   超源可达的站点集合。
4. 可行时在完整残量网络上做负费用环消除（Bellman-Ford 找负环，
   沿环增广直至不存在负环；不限制轮次，由整数费用严格下降保证终止）。
5. 最后一次 Bellman-Ford 的距离即为站点势 pi，满足每条正残量边
   约化成本 c + pi[u] - pi[v] >= 0，作为最优性证据随结果返回。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

MAX_STATIONS = 12
MAX_PIPES = 36
INF = 10 ** 30  # 仅作哨兵，绝不进入结果


class ValidationError(Exception):
    """输入校验错误，loc 为字段路径（如 pipes[3].hi）。"""

    def __init__(self, message: str, loc: str):
        super().__init__(message)
        self.message = message
        self.loc = loc


@dataclass(frozen=True)
class Edge:
    id: str
    u: int
    v: int
    lo: int
    hi: int
    cost: int


@dataclass(frozen=True)
class Problem:
    ids: List[str]          # 站点 id，索引即内部节点号
    balance: List[int]      # balance>0 净供给，<0 净需求
    edges: List[Edge]


# ---------------------------------------------------------------- 输入校验

def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _require_int(value: Any, loc: str) -> int:
    if not _is_int(value):
        raise ValidationError("必须为整数（不接受浮点数/布尔值/字符串）", loc)
    return value


def parse_and_validate(payload: Any) -> Problem:
    if not isinstance(payload, dict):
        raise ValidationError("请求体必须是 JSON 对象", "$")

    stations = payload.get("stations")
    if not isinstance(stations, list):
        raise ValidationError("stations 必须是数组", "stations")
    if not (1 <= len(stations) <= MAX_STATIONS):
        raise ValidationError(
            f"站点数量必须在 1..{MAX_STATIONS} 之间，实际 {len(stations)}", "stations")

    ids: List[str] = []
    balance: List[int] = []
    seen_ids = set()
    for i, st in enumerate(stations):
        loc = f"stations[{i}]"
        if not isinstance(st, dict):
            raise ValidationError("站点必须是对象", loc)
        sid = st.get("id")
        if not isinstance(sid, str) or not sid or len(sid) > 32:
            raise ValidationError("站点 id 必须为 1..32 字符的字符串", f"{loc}.id")
        if sid in seen_ids:
            raise ValidationError(f"站点 id 重复: {sid!r}", f"{loc}.id")
        seen_ids.add(sid)
        ids.append(sid)

        bal = st.get("balance")
        supply = st.get("supply")
        demand = st.get("demand")
        if bal is not None and (supply is not None or demand is not None):
            raise ValidationError(
                "balance 与 supply/demand 两种记法只能选其一", loc)
        if bal is not None:
            b = _require_int(bal, f"{loc}.balance")
        else:
            s = _require_int(supply, f"{loc}.supply") if supply is not None else 0
            d = _require_int(demand, f"{loc}.demand") if demand is not None else 0
            if s < 0:
                raise ValidationError("supply 必须 >= 0", f"{loc}.supply")
            if d < 0:
                raise ValidationError("demand 必须 >= 0", f"{loc}.demand")
            if s > 0 and d > 0:
                raise ValidationError("同一站点 supply 与 demand 不能同时为正", loc)
            b = s - d
        balance.append(b)
        if abs(b) > 10 ** 18:
            raise ValidationError("供需绝对值过大（上限 1e18）", loc)

    total_supply = sum(b for b in balance if b > 0)
    total_demand = sum(-b for b in balance if b < 0)
    if total_supply != total_demand:
        raise ValidationError(
            f"总供需不平衡：总供给 {total_supply} != 总需求 {total_demand}",
            "stations")

    pipes = payload.get("pipes")
    if not isinstance(pipes, list):
        raise ValidationError("pipes 必须是数组", "pipes")
    if len(pipes) > MAX_PIPES:
        raise ValidationError(
            f"管路数量不能超过 {MAX_PIPES}，实际 {len(pipes)}", "pipes")

    index = {sid: i for i, sid in enumerate(ids)}
    edges: List[Edge] = []
    seen_eids = set()
    for i, p in enumerate(pipes):
        loc = f"pipes[{i}]"
        if not isinstance(p, dict):
            raise ValidationError("管路必须是对象", loc)
        eid = p.get("id", f"e{i}")
        if not isinstance(eid, str) or not eid or len(eid) > 32:
            raise ValidationError("管路 id 必须为 1..32 字符的字符串", f"{loc}.id")
        if eid in seen_eids:
            raise ValidationError(f"管路 id 重复: {eid!r}", f"{loc}.id")
        seen_eids.add(eid)

        frm = p.get("from")
        to = p.get("to")
        if frm not in index:
            raise ValidationError(f"未知起点站点: {frm!r}", f"{loc}.from")
        if to not in index:
            raise ValidationError(f"未知终点站点: {to!r}", f"{loc}.to")
        if frm == to:
            raise ValidationError("不允许自环管路（from == to）", loc)

        lo = _require_int(p.get("lo"), f"{loc}.lo")
        hi = _require_int(p.get("hi"), f"{loc}.hi")
        cost = _require_int(p.get("cost"), f"{loc}.cost")
        if lo < 0:
            raise ValidationError("下界 lo 必须 >= 0", f"{loc}.lo")
        if hi < lo:
            raise ValidationError(f"上界 hi({hi}) 必须 >= 下界 lo({lo})", f"{loc}.hi")
        if hi > 10 ** 18:
            raise ValidationError("上界 hi 过大（上限 1e18）", f"{loc}.hi")
        if abs(cost) > 10 ** 12:
            raise ValidationError("单位成本绝对值过大（上限 1e12）", f"{loc}.cost")

        edges.append(Edge(eid, index[frm], index[to], lo, hi, cost))

    return Problem(ids, balance, edges)


# ---------------------------------------------------------------- 最大流（Dinic，纯整数）

class _Dinic:
    """节点 0..n-1；弧带 tag 以便回溯原始边流量。"""

    def __init__(self, n: int):
        self.n = n
        self.to: List[int] = []
        self.cap: List[int] = []
        self.tag: List[Optional[int]] = []
        self.g: List[List[int]] = [[] for _ in range(n)]

    def add_arc(self, u: int, v: int, c: int, tag: Optional[int] = None) -> int:
        self.to.append(v)
        self.cap.append(c)
        self.tag.append(tag)
        self.g[u].append(len(self.to) - 1)
        self.to.append(u)
        self.cap.append(0)
        self.tag.append(None)
        self.g[v].append(len(self.to) - 1)
        return len(self.to) - 2

    def maxflow(self, s: int, t: int) -> int:
        flow = 0
        n = self.n
        while True:
            level = [-1] * n
            level[s] = 0
            queue = [s]
            for u in queue:
                for a in self.g[u]:
                    if self.cap[a] > 0 and level[self.to[a]] < 0:
                        level[self.to[a]] = level[u] + 1
                        queue.append(self.to[a])
            if level[t] < 0:
                return flow
            it = [0] * n

            def dfs(u: int, f: int) -> int:
                if u == t:
                    return f
                gi = self.g[u]
                while it[u] < len(gi):
                    a = gi[it[u]]
                    v = self.to[a]
                    if self.cap[a] > 0 and level[v] == level[u] + 1:
                        d = dfs(v, min(f, self.cap[a]))
                        if d > 0:
                            self.cap[a] -= d
                            self.cap[a ^ 1] += d
                            return d
                    it[u] += 1
                return 0

            while True:
                f = dfs(s, INF)
                if f <= 0:
                    break
                flow += f

    def reachable_from(self, s: int) -> List[bool]:
        seen = [False] * self.n
        seen[s] = True
        stack = [s]
        while stack:
            u = stack.pop()
            for a in self.g[u]:
                v = self.to[a]
                if self.cap[a] > 0 and not seen[v]:
                    seen[v] = True
                    stack.append(v)
        return seen


# ---------------------------------------------------------------- 负环检测（Bellman-Ford）

def _find_negative_cycle(arcs: List[Tuple[int, int, int, int]], n: int
                         ) -> Tuple[Optional[List[int]], List[int]]:
    """在残量弧 (u, v, cost, arc_index) 上找负费用环。

    dist 全 0 初始化（等价于虚拟超源以 0 边连接所有点），n 轮松弛后
    仍能松弛的边位于负环上。返回 (环上弧索引列表 或 None, 最终距离 dist)。
    无负环时 dist 即合法势：对每条弧 dist[v] <= dist[u] + cost。
    """
    dist = [0] * n
    pred_arc = [-1] * n
    x = -1
    for _ in range(n):
        x = -1
        for ai, (u, v, c, _cap) in enumerate(arcs):
            if dist[u] + c < dist[v]:
                dist[v] = dist[u] + c
                pred_arc[v] = ai
                x = v
    if x == -1:
        return None, dist

    y = x
    for _ in range(n):
        y = arcs[pred_arc[y]][0]
    cycle: List[int] = []
    cur = y
    while True:
        a = pred_arc[cur]
        cycle.append(a)
        cur = arcs[a][0]
        if cur == y:
            break
    cycle.reverse()
    return cycle, dist


# ---------------------------------------------------------------- 主求解

def _eliminate_lower_bounds(edges: List[Edge], balance: List[int]) -> List[int]:
    """消去下界后的节点余额 b(v)=balance(v)+流入lo-流出lo。"""
    b = list(balance)
    for e in edges:
        b[e.v] += e.lo
        b[e.u] -= e.lo
    return b


def _feasibility_flow(problem: Problem):
    """下界消去 + Dinic 求可行流。

    可行返回 (flow, None)；不可行返回 (None, 失败证据 dict)。
    """
    ids = problem.ids
    n = len(ids)
    edges = problem.edges
    m = len(edges)

    b = _eliminate_lower_bounds(edges, problem.balance)

    # 超源 S=n、超汇 T=n+1 上求可行流
    S, T = n, n + 1
    din = _Dinic(n + 2)
    arc_of_edge: List[int] = []
    for e in edges:
        arc_of_edge.append(din.add_arc(e.u, e.v, e.hi - e.lo, tag=len(arc_of_edge)))
    s_arc_of_node: List[Optional[int]] = [None] * n
    t_arc_of_node: List[Optional[int]] = [None] * n
    for v in range(n):
        if b[v] > 0:
            s_arc_of_node[v] = din.add_arc(S, v, b[v])
        elif b[v] < 0:
            t_arc_of_node[v] = din.add_arc(v, T, -b[v])
    din.maxflow(S, T)

    unmet = []
    for v in range(n):
        a = t_arc_of_node[v]
        if a is not None and din.cap[a] > 0:
            unmet.append({
                "station": ids[v],
                "required": -b[v],
                "unsatisfied": din.cap[a],
            })
    stranded = []
    for v in range(n):
        a = s_arc_of_node[v]
        if a is not None and din.cap[a] > 0:
            stranded.append({
                "station": ids[v],
                "available": b[v],
                "stranded": din.cap[a],
            })
    if unmet or stranded:
        reach = din.reachable_from(S)
        return None, {
            "unmet_demand": unmet,
            "stranded_supply": stranded,
            "reachable_from_source": [ids[v] for v in range(n) if reach[v]],
        }

    # 初始流 = lo + 变换网络上的流量
    flow = [0] * m
    for k, e in enumerate(edges):
        a = arc_of_edge[k]
        flow[k] = e.lo + din.cap[a ^ 1]  # 反向弧容量即已推送流量
    return flow, None


def _infeasible_report(evidence: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "infeasible",
        "algorithm": "lower-bound-elimination + dinic-maxflow (exact integers)",
        "unmet_demand": evidence["unmet_demand"],
        "stranded_supply": evidence["stranded_supply"],
        "reachable_from_source": evidence["reachable_from_source"],
        "cut_invariant": "残量可达集 R 与其补集之间，所有正向管输能力之和"
                        "不足以同时满足下界义务与净供需，故无可行流。",
        "note": "超源未能向超汇送满：列出未满足需求（汇侧弧残余）、"
                "滞留供给（源侧弧残余）及残量网络中自超源可达的站点集合。",
    }


def _build_residual_arcs(edges: List[Edge], flow: List[int]
                         ) -> Tuple[List[Tuple[int, int, int, int]], List[int],
                                    List[int]]:
    """构造完整残量网络：正向弧 cost、反向弧 -cost（仅容量为正的弧）。"""
    arcs: List[Tuple[int, int, int, int]] = []
    arc_edge: List[int] = []
    arc_dir: List[int] = []
    for k, e in enumerate(edges):
        fwd = e.hi - flow[k]
        if fwd > 0:
            arc_edge.append(k)
            arc_dir.append(1)
            arcs.append((e.u, e.v, e.cost, fwd))
        bwd = flow[k] - e.lo
        if bwd > 0:
            arc_edge.append(k)
            arc_dir.append(-1)
            arcs.append((e.v, e.u, -e.cost, bwd))
    return arcs, arc_edge, arc_dir


def _cancel_negative_cycles(problem: Problem, flow: List[int]
                            ) -> Tuple[int, List[int]]:
    """在完整残量网络上消除全部负费用环（Bellman-Ford，轮次不设上限）。

    整数费用下每次增广使总费用严格下降，故必然终止。
    返回 (消除环数, 终止势)。
    """
    edges = problem.edges
    n = len(problem.ids)
    cycles_cancelled = 0
    potentials = [0] * n
    while True:
        arcs, arc_edge, arc_dir = _build_residual_arcs(edges, flow)
        cycle, dist = _find_negative_cycle(arcs, n)
        if cycle is None:
            potentials = dist
            break
        delta = min(arcs[a][3] for a in cycle)
        for a in cycle:
            flow[arc_edge[a]] += arc_dir[a] * delta
        cycles_cancelled += 1
    return cycles_cancelled, potentials


def solve(problem: Problem) -> Dict[str, Any]:
    # 1)-3) 下界消去并求一个整数可行流
    flow, evidence = _feasibility_flow(problem)
    if flow is None:
        return _infeasible_report(evidence)

    # 4) 完整残量网络上消除负费用环
    cycles_cancelled, potentials = _cancel_negative_cycles(problem, flow)

    total_cost = sum(flow[k] * e.cost for k, e in enumerate(problem.edges))
    return _optimal_report(
        problem, flow, total_cost, potentials, cycles_cancelled,
        "lower-bound-elimination + dinic-maxflow + "
        "cycle-cancelling (Bellman-Ford, exact integers)")


def _optimal_report(problem: Problem, flow: List[int], total_cost: int,
                    potentials: List[int], cycles_cancelled: int,
                    algorithm: str) -> Dict[str, Any]:
    ids = problem.ids
    n = len(ids)
    edges = problem.edges

    # 5) 逐边复算值 + 最优性证据
    edge_reports = []
    for k, e in enumerate(edges):
        residual = []
        fwd = e.hi - flow[k]
        if fwd > 0:
            residual.append({
                "direction": "forward",
                "from": ids[e.u],
                "to": ids[e.v],
                "capacity": fwd,
                "cost": e.cost,
                "reduced_cost": e.cost + potentials[e.u] - potentials[e.v],
            })
        bwd = flow[k] - e.lo
        if bwd > 0:
            residual.append({
                "direction": "backward",
                "from": ids[e.v],
                "to": ids[e.u],
                "capacity": bwd,
                "cost": -e.cost,
                "reduced_cost": -e.cost + potentials[e.v] - potentials[e.u],
            })
        edge_reports.append({
            "id": e.id,
            "from": ids[e.u],
            "to": ids[e.v],
            "flow": flow[k],
            "lo": e.lo,
            "hi": e.hi,
            "cost": e.cost,
            "cost_contribution": flow[k] * e.cost,
            "residual": residual,
        })

    node_check = []
    for v in range(n):
        inflow = sum(flow[k] for k, e in enumerate(edges) if e.v == v)
        outflow = sum(flow[k] for k, e in enumerate(edges) if e.u == v)
        node_check.append({
            "station": ids[v],
            "inflow": inflow,
            "outflow": outflow,
            "net_out": outflow - inflow,
            "balance": problem.balance[v],
            "ok": (outflow - inflow) == problem.balance[v],
        })

    return {
        "status": "optimal",
        "algorithm": "lower-bound-elimination + dinic-maxflow + "
                     "cycle-cancelling (Bellman-Ford, exact integers)",
        "total_cost": total_cost,
        "cycles_cancelled": cycles_cancelled,
        "flows": [{"id": e.id, "flow": flow[k]} for k, e in enumerate(edges)],
        "edges": edge_reports,
        "potentials": {ids[v]: potentials[v] for v in range(n)},
        "node_check": node_check,
        "evidence": {
            "optimality": "全部正残量边的约化成本 cost + pi[u] - pi[v] >= 0，"
                          "由 Bellman-Ford 终止距离（势）保证，见 edges[].residual。",
            "integrality": "所有计算均为任意精度整数，未使用浮点数。",
        },
    }


def solve_payload(payload: Any) -> Dict[str, Any]:
    """校验 + 求解；ValidationError 由调用方转换为 400 响应。"""
    return solve(parse_and_validate(payload))


# ------------------------------------------------- 低扰动改线（字典序双目标）
#
# 给定来源（已最优）流量 f0，在“相同供需 + 覆盖后界/成本”的修订网络上：
#   第一级：最小化修订总成本  Σ f_k * c_k
#   第二级：在第一级最优集合内最小化总扰动 Σ |f_k - f0_k|
#   平  局：按原管路标识顺序的流量向量 (f_0, f_1, ...) 字典序取小
#
# 关键点（全程整数，无浮点、无加权大数）：
# * |f - f0| 是凸分段线性费用，边际费用在投影点 t=clamp(f0, lo, hi) 两侧
#   分别为 -1（朝 f0 移动）与 +1（远离 f0）。残量弧按容量分段后，弧权与
#   当前流量无关，标准负环消除即可精确处理任意大增广量（不会一步跨过 f0
#   导致边际失配）。
# * 负环判据用三元组标签 (成本环和, 扰动环和, 流量变化向量) 的字典序比较；
#   第三维直接是 m 元整数元组的字典序比较，不是大数加权和。
# * 三级证据各用一组合法势：成本势 pi（全残量网）、扰动势 p（约化成本为 0
#   的保最优面子网）、字典序势 q（前两级约化费用均为 0 的子网，值为 m 维
#   整数向量），均可独立复算。

def _clamp(x: int, lo: int, hi: int) -> int:
    return lo if x < lo else hi if x > hi else x


def _perturbation_arcs(edges: List[Edge], flow: List[int], base: List[int]
                       ) -> List[Tuple[int, int, int, int, int, int, int, str]]:
    """分段凸费用 |f-f0| 的残量弧。

    每条弧 = (u, v, cap, cost, w, edge_index, direction, segment)
      cost：第一级边际费用（正向 c、反向 -c）
      w：第二级边际费用（-1 朝来源流量移动 / +1 远离来源流量）
      direction：+1 使该管流量增加，-1 使之减少
    """
    arcs: List[Tuple[int, int, int, int, int, int, int, str]] = []
    for k, e in enumerate(edges):
        f = flow[k]
        t = _clamp(base[k], e.lo, e.hi)  # 边际换号点（f0 投影到可行区间）
        # 正向（增流）
        if f < t:  # 朝 f0 增流
            arcs.append((e.u, e.v, t - f, e.cost, -1, k, 1, "toward"))
        cap_away = e.hi - (f if f > t else t)
        if cap_away > 0:  # 越过 t 继续增流（远离 f0）
            arcs.append((e.u, e.v, cap_away, e.cost, 1, k, 1, "away"))
        # 反向（减流）
        if f > t:  # 朝 f0 减流
            arcs.append((e.v, e.u, f - t, -e.cost, -1, k, -1, "toward"))
        cap_away_b = (f if f < t else t) - e.lo
        if cap_away_b > 0:  # 越过 t 继续减流（远离 f0）
            arcs.append((e.v, e.u, cap_away_b, -e.cost, 1, k, -1, "away"))
    return arcs


def _lexicographic_cycle(
        arcs: List[Tuple[int, int, int, int, int, int, int, str]],
        n: int, m: int) -> Optional[List[int]]:
    """在分段残量弧上找 (成本, 扰动, 流量向量) 字典序负环。

    标签全零初始化（等价虚拟超源以零边连接所有点），n 轮松弛后仍可下降的
    节点位于负环上，沿前驱回溯提取环。第三维是 m 元整数元组，直接按管路
    顺序做字典序比较。
    """
    zero_vec = (0,) * m
    lab_c = [0] * n
    lab_w = [0] * n
    lab_v: List[Tuple[int, ...]] = [zero_vec] * n
    pred = [-1] * n
    x = -1
    for _ in range(n):
        x = -1
        for ai, (u, v, cap, c, w, k, d, _seg) in enumerate(arcs):
            if cap <= 0:
                continue
            vu = lab_v[u]
            cand_v = vu[:k] + (vu[k] + d,) + vu[k + 1:]
            cc, ww = lab_c[u] + c, lab_w[u] + w
            if (cc < lab_c[v] or
                    (cc == lab_c[v] and
                     (ww < lab_w[v] or
                      (ww == lab_w[v] and cand_v < lab_v[v])))):
                lab_c[v], lab_w[v], lab_v[v] = cc, ww, cand_v
                pred[v] = ai
                x = v
    if x == -1:
        return None

    y = x
    for _ in range(n):
        y = arcs[pred[y]][0]
    cycle: List[int] = []
    cur = y
    while True:
        ai = pred[cur]
        cycle.append(ai)
        cur = arcs[ai][0]
        if cur == y:
            break
    cycle.reverse()
    return cycle


def _bf_distances(arcs: List[Tuple[int, int, int]], n: int) -> List[int]:
    """整数弧 (u,v,w)（全零初始化）的 Bellman-Ford 最短距离；调用方保证无负环。"""
    dist = [0] * n
    for _ in range(n):
        changed = False
        for u, v, w in arcs:
            nd = dist[u] + w
            if nd < dist[v]:
                dist[v] = nd
                changed = True
        if not changed:
            break
    return dist


def _lex_potentials(g00: List[Tuple[int, int, int, int]], n: int, m: int
                    ) -> List[Tuple[int, ...]]:
    """零成本/零扰动面子网 (u,v,edge,dir) 上的字典序势（m 维整数向量）。"""
    zero_vec = (0,) * m
    vec = [zero_vec] * n
    for _ in range(n):
        changed = False
        for u, v, k, d in g00:
            vu = vec[u]
            cand = vu[:k] + (vu[k] + d,) + vu[k + 1:]
            if cand < vec[v]:
                vec[v] = cand
                changed = True
        if not changed:
            break
    return vec


def solve_reroute(problem: Problem, base_flow: List[int]) -> Dict[str, Any]:
    """在修订网络上求字典序双目标最优改线流。base_flow 为来源原流量（基准）。"""
    ids = problem.ids
    n = len(ids)
    m = len(problem.edges)
    edges = problem.edges

    # 第一级准备：与普通求解完全相同的下界消去 + Dinic 整数可行流
    flow, evidence = _feasibility_flow(problem)
    if flow is None:
        report = _infeasible_report(evidence)
        report["infeasible_stage"] = "reroute_revised_network"
        return report

    # 第一级：先求修订成本最低的可行流（完整残量网负环消除）
    cost_cycles, _pi0 = _cancel_negative_cycles(problem, flow)
    min_cost = sum(flow[k] * e.cost for k, e in enumerate(edges))

    # 第二级 + 平局裁决：只沿“字典序负环”增广
    # （成本最优时残量网不存在成本负环，故所有改进环的成本环和必为 0）
    lexicographic_cycles = 0
    while True:
        arcs = _perturbation_arcs(edges, flow, base_flow)
        cycle = _lexicographic_cycle(arcs, n, m)
        if cycle is None:
            break
        delta = min(arcs[i][2] for i in cycle)
        for i in cycle:
            _u, _v, _cap, _c, _w, k, d, _seg = arcs[i]
            flow[k] += d * delta
        lexicographic_cycles += 1

    total_cost = sum(flow[k] * e.cost for k, e in enumerate(edges))
    assert total_cost == min_cost, "二级优化破坏了一级成本最优"
    total_change = sum(abs(flow[k] - base_flow[k]) for k in range(m))

    # ---- 三级独立复算证据
    # (1) 成本势 pi：全残量网（不分段）每条正残量弧约化成本 >= 0
    cost_arcs: List[Tuple[int, int, int]] = []
    for k, e in enumerate(edges):
        if e.hi - flow[k] > 0:
            cost_arcs.append((e.u, e.v, e.cost))
        if flow[k] - e.lo > 0:
            cost_arcs.append((e.v, e.u, -e.cost))
    pi = _bf_distances(cost_arcs, n)

    # (2) 扰动势 p：仅在约化成本为 0 的保最优面子网段上，w2 约化费用 >= 0
    seg_arcs = _perturbation_arcs(edges, flow, base_flow)
    zero_face = [(ai, a) for ai, a in enumerate(seg_arcs)
                 if a[3] + pi[a[0]] - pi[a[1]] == 0]
    p = _bf_distances([(a[0], a[1], a[4]) for _ai, a in zero_face], n)

    # (3) 字典序势 q：前两级约化费用均为 0 的网段上，向量约化费用字典序 >= 0
    g00: List[Tuple[int, int, int, int]] = []
    g00_idx = set()  # 指向 seg_arcs 的弧索引（同管同向可能有朝源/离源两段）
    for ai, a in zero_face:
        u, v, _cap, _c, w, k, d, _seg = a
        if w + p[u] - p[v] == 0:
            g00.append((u, v, k, d))
            g00_idx.add(ai)
    q = _lex_potentials(g00, n, m)

    perturbation_arcs = []
    for ai, a in enumerate(seg_arcs):
        u, v, cap, c, w, k, d, seg = a
        rc = c + pi[u] - pi[v]
        rw = w + p[u] - p[v] if rc == 0 else None
        if ai in g00_idx:
            qu, qv = q[u], q[v]
            lex_slack = tuple(qu[j] + (d if j == k else 0) - qv[j]
                              for j in range(m))
        else:
            lex_slack = None
        perturbation_arcs.append({
            "pipe": edges[k].id,
            "direction": "forward" if d == 1 else "backward",
            "segment": "toward_source" if seg == "toward" else "away_from_source",
            "from": ids[u],
            "to": ids[v],
            "capacity": cap,
            "cost": c,
            "w2": w,
            "reduced_cost": rc,
            "reduced_w2": rw,
            "lex_slack": list(lex_slack) if lex_slack is not None else None,
        })

    changes_core = [{
        "id": edges[k].id,
        "source_flow": base_flow[k],
        "new_flow": flow[k],
        "delta": flow[k] - base_flow[k],
        "abs_change": abs(flow[k] - base_flow[k]),
    } for k in range(m)]

    # 标准最优报告（edges[].residual 的成本约化证据、node_check 等）保持同构，
    # 再附改线两级目标、逐管改变量与分段残量弧的字典序约化费用证据。
    report = _optimal_report(
        problem, flow, total_cost, pi, cost_cycles,
        "reroute: lower-bound-elimination + dinic-feasible-flow + "
        "cost cycle-cancelling then lexicographic "
        "(cost, total_abs_change, flow-by-pipe-id) cycle-cancelling "
        "(exact integers, piecewise-linear margins, no floats)")
    report["reroute_cycles"] = {"cost_cycles": cost_cycles,
                                "lexicographic_cycles": lexicographic_cycles}
    report["objectives"] = {
        "level1_revised_cost": total_cost,
        "level2_total_abs_change": total_change,
        "tie_break": "flow vector in original pipe id order (lexicographically smallest)",
    }
    report["changes_core"] = changes_core
    report["perturbation_evidence"] = {
        "pipe_order": [e.id for e in edges],
        "potentials": {ids[v]: pi[v] for v in range(n)},
        "perturbation_potentials": {ids[v]: p[v] for v in range(n)},
        "lex_potentials": {ids[v]: list(q[v]) for v in range(n)},
        "arcs": perturbation_arcs,
        "explained": [
            "第一级：全部正残量弧 reduced_cost = cost + pi[u] - pi[v] >= 0，"
            "故修订网络中不存在成本负环，新流为最小费用流。",
            "第二级：reduced_cost = 0 的弧构成保持成本最优的残量面子网；"
            "其上 w2 + p[u] - p[v] >= 0（w2 为 |f-f0| 的分段边际费用，"
            "-1 朝来源流量、+1 远离来源流量），故不存在成本不变的负扰动环，"
            "总绝对改变量在成本最优集合内最小。",
            "平局：两级约化费用均为 0 的弧上，字典序势 q（按原管路标识序的"
            "整数向量）满足 q[u] + dir*e_k >=lex q[v]（lex_slack 字典序非负），"
            "故不存在保持前两级且使流量向量字典序更小的环。",
        ],
    }
    report["evidence"]["tie_break"] = (
        "第三级直接以整数元组字典序比较流量向量裁决，未使用浮点数或加权大数。")
    return report
