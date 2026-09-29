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

def solve(problem: Problem) -> Dict[str, Any]:
    ids = problem.ids
    n = len(ids)
    edges = problem.edges
    m = len(edges)

    # 1) 消去下界后的节点余额 b(v) = balance(v) + 流入lo - 流出lo
    b = list(problem.balance)
    for e in edges:
        b[e.v] += e.lo
        b[e.u] -= e.lo

    # 2) 超源 S=n、超汇 T=n+1 上求可行流
    S, T = n, n + 1
    din = _Dinic(n + 2)
    arc_of_edge: List[int] = []
    for e in edges:
        arc_of_edge.append(din.add_arc(e.u, e.v, e.hi - e.lo, tag=len(arc_of_edge)))
    s_arc_of_node: List[Optional[int]] = [None] * n
    t_arc_of_node: List[Optional[int]] = [None] * n
    need = 0
    for v in range(n):
        if b[v] > 0:
            s_arc_of_node[v] = din.add_arc(S, v, b[v])
            need += b[v]
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
        return {
            "status": "infeasible",
            "algorithm": "lower-bound-elimination + dinic-maxflow (exact integers)",
            "unmet_demand": unmet,
            "stranded_supply": stranded,
            "reachable_from_source": [ids[v] for v in range(n) if reach[v]],
            "cut_invariant": "残量可达集 R 与其补集之间，所有正向管输能力之和"
                            "不足以同时满足下界义务与净供需，故无可行流。",
            "note": "超源未能向超汇送满：列出未满足需求（汇侧弧残余）、"
                    "滞留供给（源侧弧残余）及残量网络中自超源可达的站点集合。",
        }

    # 3) 可行：初始流 = lo + 变换网络上的流量
    flow = [0] * m
    for k, e in enumerate(edges):
        a = arc_of_edge[k]
        flow[k] = e.lo + din.cap[a ^ 1]  # 反向弧容量即已推送流量

    # 4) 完整残量网络上消除负费用环（Bellman-Ford，轮次不设上限）
    cycles_cancelled = 0
    potentials = [0] * n
    while True:
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
        cycle, dist = _find_negative_cycle(arcs, n)
        if cycle is None:
            potentials = dist
            break
        delta = min(arcs[a][3] for a in cycle)
        for a in cycle:
            flow[arc_edge[a]] += arc_dir[a] * delta
        cycles_cancelled += 1

    total_cost = 0
    for k, e in enumerate(edges):
        total_cost += flow[k] * e.cost

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
