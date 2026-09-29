# 低温光谱仪校准液路 · 带上下界的精确整数最小费用流与低扰动改线

工程师在浏览器录入阀站（净供给/净需求）与有向管路（稳定标识、整数上下界
与单位成本），后端以**任意精度整数**先消去下界求可行流，再在完整残量网络中
消除全部负费用改进，返回各管流量、最小总成本，以及可逐边复算的最优性证据
（站点势 π 与正残量边约化成本）。

在一条**已完成且状态为 optimal** 的液路审计记录上，工程师还可发起**低扰动
改线**：提交一个稳定改线标识及至多六条按管路标识给出的新整数上下界或单位
成本；后端在**相同供需**下先求修订网络成本最低的可行流，再只在该成本最优
集合中最小化对来源流量的总绝对改变量，仍并列时按原管路标识序的流量向量
裁决，返回新流、两级目标、逐管改变量、成对站点势与每条正残量弧的字典序
约化费用。

## 算法（`app/solver.py`，全程整数，无浮点、无贪心、不限轮次）

1. **校验**：站点 ≤ 12、管路 ≤ 36；全部字段必须是整数（拒绝浮点数/布尔/字符串）；
   `lo ≥ 0`、`hi ≥ lo`；总供给必须等于总需求。错误带字段路径定位
   （如 `pipes[3].hi`）。
2. **下界消去**：每条边先注入 `lo`，节点余额变为
   `b(v) = balance(v) + Σlo(进入 v) − Σlo(离开 v)`，边剩余容量 `hi−lo`。
3. **可行流（Dinic 最大流，整数）**：超源向 `b>0` 的点连容量 `b` 的边，
   `b<0` 的点向超汇连容量 `−b` 的边。超源出边未送满即无可行流，返回
   **未满足需求**（汇侧弧残余）、**滞留供给**（源侧弧残余）与残量网络中
   **超源可达站点集合**作为失败 cut 证据。
4. **最优性（负环消除）**：在完整残量网络（正向弧 `cost`、反向弧 `−cost`）上
   用 Bellman–Ford 找负费用环并沿环增广，循环至不存在负环。每次增广使整数
   总费用严格下降，故必然终止；不预设轮次上限。
5. **势与复算**：最后一轮 Bellman–Ford 的距离即为势 π，满足每条正残量边
   `c + π[u] − π[v] ≥ 0`（残量网络无负弧 ⇒ 当前流为最小费用流）。API 回传
   逐边流量/界/费用贡献、每条正残量弧的容量和约化成本、逐站流入/流出/净流出
   对照，供独立复算。

## 低扰动改线（`app/reroute.py` + `solver.solve_reroute`，全程整数）

请求 `POST /api/reroute` 携带
`{source_audit_id, modifications:[{pipe, lo?, hi?, cost?}, ...]}` 与
`X-Audit-Id`（改线自身的稳定标识）。

1. **冻结来源**：站点（标识/顺序/供需）、管路标识序、来源流量 `f0`、本次
   覆盖项均从来源审计记录重建；**拒绝**来源不存在(404)、来源非 optimal(409)、
   改线标识与来源标识冲突(409)、同标识改载荷重放(409)、覆盖管路不在来源中、
   覆盖项标识重复、覆盖项超过 6 条、覆盖后 `lo<0`/`hi<lo`（字段路径定位）。
   改线记录不能再次作为改线来源（不可链式改线）。
2. **第一级（修订成本最低）**：在覆盖后的界/成本上，复用同一套下界消去 +
   Dinic 求整数可行流（不可行则返回未满足需求/滞留供给/残量可达集证据），
   再在完整残量网络上消除全部负费用环。
3. **第二级（最低扰动）**：只在成本最优集合内，最小化
   `Σ_k |f_k − f0_k|`。`|f−f0|` 是凸分段线性费用，边际费用在投影点
   `t=clamp(f0,lo,hi)` 两侧分别为 −1（朝来源流量移动）与 +1（远离）；
   残量弧按容量分段后弧权与当前流量无关，标准负环消除即可精确处理任意大
   增广量——**不是加权大数、不用浮点、也不是仅复用来源方案**（零扰动结果
   同样重新求可行流并验证成本最优）。
4. **平局裁决**：负环以三元组标签
   `(成本环和, 扰动环和, 流量改变向量)` 做字典序比较，第三维直接是按**原
   管路标识顺序**的 m 元整数元组比较；沿找到的字典序负环增广直至不存在。
5. **两级独立复算证据**：结果回传
   - `objectives`：`level1_revised_cost`、`level2_total_abs_change` 与
     平局规则；
   - `changes_core`：逐管 `source_flow/new_flow/delta/abs_change`；
   - `frozen`：来源站点/管路/原流量、覆盖项、修订后管路；
   - `edges[].residual` 与 `potentials`：成本势 π，全部正残量弧
     `cost+π[u]−π[v] ≥ 0`（成本最优）；
   - `perturbation_evidence`：每条正残量**分段**弧（朝源/离源、容量、弧
     成本、二级边际 `w2`）的成本约化费用、二级约化费用 `w2+p[u]−p[v]`
     （扰动势 p，仅在零成本面子网给出）与字典序松弛向量
     `q[u]+dir·e_k −lex q[v]`（字典序势 q，按管路序的整数向量），三套势
     均可逐条弧独立复算。

> 同标识 + 同载荷重传改线请求同样返回首次原记录（`replayed:true`）；
> 同标识 + 改载荷返回 409。改线记录经 `GET /api/records/<id>` 读取。

## 审计标识（`app/audit.py`）

- 请求带头 `X-Audit-Id: <标识>`（或在 JSON 中放 `audit_id`）。
- **同标识 + 同载荷**重传：不重新求解，直接返回首次原记录（`replayed: true`）。
- **同标识 + 改载荷**：HTTP 409 拒绝，并回显已存载荷指纹。
- 载荷指纹为规范化 JSON（排序键）的 SHA-256；记录落盘 `/data/audit.json`，
  服务/容器重启后仍可重放与查询（`GET /api/records/<id>`）。

## 运行（Docker Compose，需 Docker + Compose v2）

```bash
# 可选：自定义宿主机端口
export HOST_PORT=9090          # 默认 8080

docker compose up -d --build web     # 仅启动页面与 API
# 浏览器打开 http://localhost:9090
curl -s http://localhost:9090/health
```

### 一次性核验（verify 容器自行退出并以退出码报告）

```bash
./verify.sh                    # = docker compose up --build --abort-on-container-exit --exit-code-from verify
echo $?                        # 0 = 全部通过
```

`verify` 服务：

1. 等待 `web` 健康检查通过后启动；
2. 在镜像内运行求解器、改线双目标与 API 单元测试（含随机网络对暴力枚举的
   最优性/最低扰动/字典序裁决对照）；
3. 对运行中的 web 做本题 API 冒烟（健康检查、最优解与约化成本证据、审计
   重放/冲突 409、不可行证据、非法输入定位，以及**字典序改线、覆盖不可行
   证据、改线重放冲突、旧求解接口兼容**）；
4. 全部通过则 `exit 0` 并退出，任何失败为非零退出码（`restart: "no"`，不常驻）。

## 本地开发（仅需 Python 3.11 标准库，无第三方依赖）

```bash
cd app
python3 -m unittest discover -s tests -v
PORT=8080 AUDIT_DB=/tmp/audit.json python3 server.py
python3 tests/smoke.py http://localhost:8080  # API 冒烟（含改线）
```

## API 摘要

| 方法/路径 | 说明 |
| --- | --- |
| `GET /health` | 健康检查 |
| `GET /` | 录入、求解复算与低扰动改线页面 |
| `POST /api/solve` | 提交 `{stations:[{id,balance}], pipes:[{id,from,to,lo,hi,cost}]}` |
| `POST /api/reroute` | 从 optimal 来源记录改线 `{source_audit_id, modifications:[{pipe,lo?,hi?,cost?}]}`（至多 6 项，带头 `X-Audit-Id`） |
| `GET /api/records/<audit_id>` | 取回审计原记录（求解或改线） |

`balance>0` 为净供给、`<0` 为净需求（也支持 `supply`/`demand` 记法）。
成功返回 `status:"optimal"`、`total_cost`、`flows`、`potentials`、`edges[].residual`
（约化成本证据）与 `node_check`；无可行流返回 `status:"infeasible"`、
`unmet_demand`、`stranded_supply`、`reachable_from_source`；非法输入返回
400 与 `loc` 字段路径。改线成功额外返回 `objectives`（两级目标）、
`changes_core`（逐管改变量）、`frozen`（来源与覆盖冻结快照）与
`perturbation_evidence`（分段弧的三级字典序约化费用与三套势）；来源不存在
404、来源非最优或标识冲突 409、覆盖项非法 400。
