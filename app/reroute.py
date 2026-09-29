"""低扰动改线的来源重建与覆盖项校验。

改线请求只携带来源审计标识与至多六条覆盖项；修订网络必须在服务端从
**来源审计记录**冻结重建：
- 站点（标识、顺序、供需）冻结；
- 管路标识与顺序冻结；
- 来源流量 f0 冻结（二级目标的基准）；
- 本次覆盖项经规范化后随改线审计记录冻结。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from solver import Problem, ValidationError, parse_and_validate

MAX_MODIFICATIONS = 6
_OVERRIDE_FIELDS = ("lo", "hi", "cost")


def rebuild_source_payload(source_rec: Dict[str, Any]
                           ) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """从一条 solve 审计记录重建原始请求载荷与 {管路id: 原流量}。"""
    result = source_rec.get("result")
    if not isinstance(result, dict):
        raise ValidationError("来源审计记录缺少求解结果", "source_audit_id")

    node_check = result.get("node_check")
    if not isinstance(node_check, list) or not node_check:
        raise ValidationError(
            "来源审计记录缺少逐站供需信息，无法冻结站点", "source_audit_id")
    stations = []
    for n in node_check:
        if not isinstance(n, dict) or not isinstance(n.get("station"), str) \
                or not isinstance(n.get("balance"), int):
            raise ValidationError(
                "来源审计记录的逐站供需信息不完整", "source_audit_id")
        stations.append({"id": n["station"], "balance": n["balance"]})

    src_edges = result.get("edges")
    if not isinstance(src_edges, list) or not src_edges:
        raise ValidationError(
            "来源审计记录缺少管路信息，无法冻结管路顺序", "source_audit_id")
    pipes = []
    for e in src_edges:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str):
            raise ValidationError(
                "来源审计记录的管路信息不完整", "source_audit_id")
        pipes.append({"id": e["id"], "from": e["from"], "to": e["to"],
                      "lo": e["lo"], "hi": e["hi"], "cost": e["cost"]})

    flow_map: Dict[str, int] = {}
    for f in result.get("flows", []):
        if isinstance(f, dict) and isinstance(f.get("id"), str) \
                and isinstance(f.get("flow"), int):
            flow_map[f["id"]] = f["flow"]
    missing = [p["id"] for p in pipes if p["id"] not in flow_map]
    if missing:
        raise ValidationError(
            f"来源审计记录缺少管路流量: {', '.join(missing)}",
            "source_audit_id")

    return {"stations": stations, "pipes": pipes}, flow_map


def prepare_reroute(source_rec: Dict[str, Any], modifications: Any
                    ) -> Tuple[Problem, List[int], List[Dict[str, Any]],
                               Dict[str, Any]]:
    """校验覆盖项并在冻结的来源网络上构造修订 Problem。

    返回 (修订问题, 与管路同序的来源流量, 规范化覆盖项, 冻结快照)。
    """
    payload, flow_map = rebuild_source_payload(source_rec)
    src_pipes = payload["pipes"]
    by_id = {p["id"]: p for p in src_pipes}

    if not isinstance(modifications, list):
        raise ValidationError(
            f"modifications 必须是 1..{MAX_MODIFICATIONS} 项的数组",
            "modifications")
    if not (1 <= len(modifications) <= MAX_MODIFICATIONS):
        raise ValidationError(
            f"覆盖项数量必须在 1..{MAX_MODIFICATIONS} 之间，实际 "
            f"{len(modifications)}", "modifications")

    normalized: List[Dict[str, Any]] = []
    overrides: Dict[str, Dict[str, int]] = {}
    seen = set()
    for i, m in enumerate(modifications):
        loc = f"modifications[{i}]"
        if not isinstance(m, dict):
            raise ValidationError("覆盖项必须是对象", loc)
        extra = set(m) - {"pipe", *_OVERRIDE_FIELDS}
        if extra:
            raise ValidationError(
                f"覆盖项含未知字段: {sorted(extra)[0]}（仅允许 pipe/lo/hi/cost）",
                loc)
        pid = m.get("pipe")
        if not isinstance(pid, str) or not pid:
            raise ValidationError("覆盖项必须按管路标识给出 pipe", f"{loc}.pipe")
        if pid not in by_id:
            raise ValidationError(
                f"管路标识 {pid!r} 不在来源审计记录中（管路集合已冻结）",
                f"{loc}.pipe")
        if pid in seen:
            raise ValidationError(
                f"管路标识 {pid!r} 在覆盖项中重复（同一管路只能覆盖一次）",
                f"{loc}.pipe")
        seen.add(pid)

        item: Dict[str, Any] = {"pipe": pid}
        vals: Dict[str, int] = {}
        for fld in _OVERRIDE_FIELDS:
            if fld in m:
                v = m[fld]
                if not isinstance(v, int) or isinstance(v, bool):
                    raise ValidationError(
                        "覆盖值必须为整数（不接受浮点数/布尔值/字符串）",
                        f"{loc}.{fld}")
                vals[fld] = v
                item[fld] = v
        if not vals:
            raise ValidationError(
                "每项至少给出 lo / hi / cost 中的一个新整数值", loc)
        normalized.append(item)
        overrides[pid] = vals

    # 按原管路顺序应用覆盖，并在“覆盖后”立即做边界合法性判定
    new_pipes = []
    for i, p in enumerate(src_pipes):
        ov = overrides.get(p["id"], {})
        lo = ov.get("lo", p["lo"])
        hi = ov.get("hi", p["hi"])
        cost = ov.get("cost", p["cost"])
        if lo < 0:
            raise ValidationError("覆盖后下界 lo 必须 >= 0",
                                  _loc_of(overrides, p["id"], normalized, "lo"))
        if hi < lo:
            raise ValidationError(
                f"覆盖后上界 hi({hi}) 必须 >= 下界 lo({lo})",
                _loc_of(overrides, p["id"], normalized, "hi"))
        if hi > 10 ** 18:
            raise ValidationError("覆盖后上界 hi 过大（上限 1e18）",
                                  _loc_of(overrides, p["id"], normalized, "hi"))
        if abs(cost) > 10 ** 12:
            raise ValidationError("覆盖后单位成本绝对值过大（上限 1e12）",
                                  _loc_of(overrides, p["id"], normalized, "cost"))
        new_pipes.append({"id": p["id"], "from": p["from"], "to": p["to"],
                          "lo": lo, "hi": hi, "cost": cost})

    revised_payload = {"stations": payload["stations"], "pipes": new_pipes}
    problem = parse_and_validate(revised_payload)  # 复用全部整数/规模校验
    base_flow = [flow_map[e.id] for e in problem.edges]

    frozen = {
        "stations": payload["stations"],
        "pipe_order": [p["id"] for p in src_pipes],
        "source_pipes": src_pipes,
        "source_flows": [{"id": p["id"], "flow": flow_map[p["id"]]}
                        for p in src_pipes],
        "modifications": normalized,
        "revised_pipes": [
            {"id": e.id, "from": problem.ids[e.u], "to": problem.ids[e.v],
             "lo": e.lo, "hi": e.hi, "cost": e.cost}
            for e in problem.edges
        ],
    }
    return problem, base_flow, normalized, frozen


def _loc_of(overrides: Dict[str, Dict[str, int]], pid: str,
            normalized: List[Dict[str, Any]], fld: str) -> str:
    """覆盖后非法时定位到本次覆盖项的字段（沿用原值则定位管路本身）。"""
    if fld in overrides.get(pid, {}):
        for i, item in enumerate(normalized):
            if item["pipe"] == pid:
                return f"modifications[{i}].{fld}"
    return f"pipes(pipe={pid}).{fld}"
