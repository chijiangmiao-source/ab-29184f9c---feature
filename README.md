# 低温光谱仪校准液路 · 带上下界的精确整数最小费用流 / 低扰动改线

工程师在浏览器录入阀站（净供给/净需求）与有向管路（稳定标识、整数上下界
与单位成本），后端以**任意精度整数**先消去下界求可行流，再在完整残量网络中
消除全部负费用改进，返回各管流量、最小总成本，以及可逐边复算的最优性证据
（站点势 π 与正残量边约化成本）。

工程师还可从一条**已完成且状态为 optimal 的审计记录**发起**低扰动改线**：
提交稳定改线标识与至多六条按管路标识给出的新整数上下界或单位成本。服务
冻结来源站点、管路顺序、原流量与本次覆盖项，在相同供需下先求**修订成本
最低**的可行流，再只在该成本最优集合中最小化所有管路相对来源流量的
**绝对改变量之和**，仍并列时按**原管路标识序的流量向量**裁决。

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

## 低扰动改线（`app/reroute.py`，`POST /api/reroute`）

来源必须是状态 `optimal` 的求解审计记录；来源不存在 (404)、非最优 (409)、
覆盖项标识冲突（未知管路/同管重复）或覆盖后边界非法 (`hi<lo`，400) 一律拒绝。
覆盖至多 6 条，每条 `{id, lo?, hi?, cost?}` 只给需要改的整数字段；站点、
管路顺序、来源原流量与覆盖项被冻结并随结果回显（`frozen`）。

三级字典序优化（全程整数有序元组，**不用加权大数、不用浮点数、不仅复用来源
方案**）：

1. **修订成本最低**：在覆盖后的修订网络上重新做下界消去 + Dinic 可行流 +
   单级负环消除；覆盖导致无可行流时返回与求解接口同构的不可行证据
   （未满足需求、滞留供给、残量可达集）。
2. **最低扰动**：在完整残量网络上以有序费用对
   `(边际修订成本, 边际|f−f0|)` 做 Bellman–Ford 负环消除。
   `|f−f0|` 是分段线性函数，正向残量弧在 `f<f0` 时边际 −1、`f≥f0` 时 +1
   （反向对称），环增广在跨越折点 `f0` 前截断，保证用的都是真实边际。
3. **流量向量裁决**：仍并列时，按来源管路标识序逐条把管路流量钉死
   （`lo=hi=当前流`），以三元组 `(边际成本, 边际扰动, 该管±1 边际)` 消去
   负环，已裁决分量不再改变。

返回体 `result` 含：新流 `flows`、两级目标 `objectives{revised_cost,
perturbation_l1, tie_order}`、`pipe_changes[]`（新旧 lo/hi/cost、原/新流量、
`signed_delta`、`abs_delta`）、成对站点势
`potentials[site] = {cost, perturbation}`，以及每条正残量弧的
`lex_reduced_cost = [成本约化费用, 扰动约化费用]`（逐分量均可用回传的势
独立复算；第一分量全 ≥0 见证成本最优，二元组字典序全 ≥[0,0] 见证最低
扰动）。改线记录同样落盘，支持同标识同载荷重放与改载荷 409，
`GET /api/records/<id>` 可读回。

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
2. 在镜像内运行求解器与 API 单元测试（含 60 组随机网络对暴力枚举的最优性
   对照，以及改线对 `(修订成本, L1扰动, 流量向量)` 字典序暴力枚举的对照）；
3. 对运行中的 web 做本题 API 冒烟（健康检查、最优解与约化成本证据、审计重放/
   冲突 409、不可行证据、非法输入定位，以及**字典序改线两级证据复算、
   不可行覆盖证据、改线重放冲突与旧求解接口不变性**）；
4. 全部通过则 `exit 0` 并退出，任何失败为非零退出码（`restart: "no"`，不常驻）。

## 本地开发（仅需 Python 3.11 标准库，无第三方依赖）

```bash
cd app
python3 -m unittest discover -s tests -v      # 全部单元测试
PORT=8080 AUDIT_DB=/tmp/audit.json python3 server.py
python3 tests/smoke.py http://localhost:8080  # API 冒烟
```

## API 摘要

| 方法/路径 | 说明 |
| --- | --- |
| `GET /health` | 健康检查 |
| `GET /` | 录入与复算页面 |
| `POST /api/solve` | 提交 `{stations:[{id,balance}], pipes:[{id,from,to,lo,hi,cost}]}` |
| `POST /api/reroute` | 改线 `{source_audit_id, changes:[{id,lo?,hi?,cost?}]}`（≤6 条覆盖） |
| `GET /api/records/<audit_id>` | 取回审计原记录（求解与改线均可） |

`balance>0` 为净供给、`<0` 为净需求（也支持 `supply`/`demand` 记法）。
成功返回 `status:"optimal"`、`total_cost`、`flows`、`potentials`、`edges[].residual`
（约化成本证据）与 `node_check`；无可行流返回 `status:"infeasible"`、
`unmet_demand`、`stranded_supply`、`reachable_from_source`；非法输入返回
400 与 `loc` 字段路径。
