# 离心机转子配平规划器 (Centrifuge Rotor Balance Planner)

在给定**转子槽位角度、试管质量、禁用槽位与锁定试管**约束下，搜索近似最优的对称布局，
计算**一阶不平衡量（离心合力/合力矩）与二阶不平衡量**，对锁定试管执行**增量重排**，
逐步**解释每个配对依据**，支持**方案回放**与**JSON 导出**。

技术栈：Python 3.11 · FastAPI · SQLite · 原生 HTML/CSS/JS（无前端构建）· Docker Compose。

---

## 一键启动

```bash
docker compose up --build
```

启动后浏览器访问：

- 应用界面：<http://localhost:8000>
- 健康检查：<http://localhost:8000/api/health>
- OpenAPI 文档：<http://localhost:8000/docs>

停止：`docker compose down`（数据保留在宿主机 `./data/balancer.db`）。

## 端口与配置

| 项目 | 默认值 | 说明 |
| --- | --- | --- |
| 服务端口 | `8000` | 在 `docker-compose.yml` 的 `ports` 修改宿主端口，如 `"18000:8000"` |
| 数据库文件 | `/app/data/balancer.db` | 环境变量 `BALANCER_DB`，通过卷 `./data:/app/data` 持久化 |
| 槽位数 | 12 | 界面/API 可改（2–128），支持任意（含非均匀）角度 |
| 转子半径 R | 100 mm | 用于把不平衡量换算为离心力/力矩 |
| 转速 | 4000 rpm | 仅影响力学量（F、M），不影响布局优化 |
| 一阶权重 w₁ | 1.0 | 一阶不平衡量（主导离心合力）权重 |
| 二阶权重 w₂ | 1e-6 | 二阶（2 倍频）权重，默认仅在一阶几乎持平时作裁决 |
| 移动罚权 | 0.0 | 增量重排时每支被移动试管的罚分；调大可减少扰动 |
| 随机起点数 | 10 | 多起点搜索的起点数 |
| 退火链长度 | 2500 | 每个起点模拟退火步数（越大越慢、通常越优） |

> 物理量定义见 `GET /api/config`。

## 本地（不用 Docker）开发运行

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

核心求解算法 `app/balancer.py` 仅依赖标准库，可单独在 Python 中调用/测试：

```python
from app.balancer import run_plan
result = run_plan({
    "slots": [{"index": i, "angle": i*30, "enabled": True} for i in range(12)],
    "tubes": [{"id": 1, "mass": 50}, {"id": 2, "mass": 50},
              {"id": 3, "mass": 51}, {"id": 4, "mass": 49}],
    "radius_mm": 100, "rpm": 4000,
})
print(result["metrics"])
```

---

## 使用流程

1. **转子参数**：设置槽位数与半径/转速，点“按槽位数生成均布角度”，也可逐槽改角度；
   在左侧表格或转子图上切换槽位**启用/禁用**。
2. **试管清单**：录入编号与质量（g）；需要固定的试管勾选“锁定”并选择固定槽位。
3. **计算配平方案**：后端在质量与角度约束下做多起点构造 + 模拟退火 + 爬山局部搜索，
   极小化 `w₁·|U₁|² + w₂·|U₂|²`。
4. 查看右侧**合力矩、二阶不平衡量、评级**，以及转子图上的合力向量（蓝=U₁，绿=U₂）。
5. **配对依据**标签页解释每一对对径/近对径试管的质量差、夹角与对消比例；
   无搭档的试管说明其补偿方式。
6. **方案回放**：用滑块/播放键逐步回放构造与微调过程，每步显示当时的 U₁/U₂ 与文字理由。
7. **增量重排**：先算出方案；增删试管或新增锁定后勾选“基于当前结果做增量重排”再计算。
   系统沿用上一方案中仍有效的摆放，仅重放受影响的试管，并标注“已移动”。
8. **导出 JSON / 历史方案**：保存到 SQLite，可再次载入、导出或删除。
9. **方案比较**：在“历史方案”中勾选两条已保存方案，点“比较选中的两条方案”，
   即可按试管编号合并摆放明细（新增 / 移除 / 换槽 / 未变），并并列查看两方案的
   一阶、二阶不平衡量及差值（B − A）。若两方案的转子几何或求解输入（转速、权重、
   试管清单等）不同，比较视图会显著提示参数差异——此时指标差值**不是同条件优化对比**，
   不能仅凭差值评判优劣。

## 物理模型

单面转子、所有试管位于同一半径 R：

- 一阶不平衡量（质径积）：`U₁ = R·Σ mₖ·e^{iθₖ}`，单位 g·mm
- 二阶不平衡量：`U₂ = R·Σ mₖ·e^{i·2θₖ}`，单位 g·mm
- 离心合力：`F = ω²·|U₁|·1e-6`（N，g·mm → kg·m）
- 合力矩：`M = F·R/1000`（N·m）
- 等效偏心距：`e = |U₁| / Σm`，评级 A ≤1μm，B ≤5μm，C ≤20μm，D >20μm

## HTTP API 摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/plan` | 提交槽位/试管/参数求解；请求体见 `/docs`，返回结果并可落库 |
| GET | `/api/plans?limit=` | 历史方案列表（含关键指标） |
| GET | `/api/plans/compare?a=&b=` | 比较两条已保存方案：摆放明细合并标注、指标并列与差值、参数差异 |
| GET | `/api/plan/{id}` | 获取某方案完整请求与结果（含回放步骤） |
| GET | `/api/plan/{id}/export` | 下载该方案 JSON |
| DELETE | `/api/plan/{id}` | 删除方案 |
| GET | `/api/config` | 默认参数与物理量说明 |

`POST /api/plan` 增量重排时传入 `previous: [{tube_id, slot}, ...]`（并可带 `parent_id`）。
返回 JSON 顶层含 `metrics / placements / slot_state / pairs / singletons / steps / summary`。

## 目录结构

```
.
├── app/
│   ├── balancer.py     # 配平引擎(构造/退火/爬山、力学量、配对解释、回放快照)
│   ├── compare.py      # 两方案比较(摆放合并标注、指标差值、参数差异判定)
│   └── main.py         # FastAPI 路由 + SQLite 持久化
├── static/
│   ├── index.html      # 单页界面
│   └── app.js          # 转子 SVG、指标、配对解释、回放、导出、历史
├── data/               # SQLite 数据(运行后生成, 已持久化挂载)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```
