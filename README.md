# 精密量块多目标间隙规范分配

8–16 块标识唯一、整数纳米长度的量块，同时配出 2–4 个带允许偏差的目标间隙。
后端用**可证明完整的状态压缩搜索**求规范分配，并返回任意精度的同优方案数与每块
在所有同优方案中的去向；前端为 React 单页应用，后端为真实 FastAPI 服务。

## 业务规则

- 每块量块至多使用一次；每个间隙分到 1–6 块；允许存在未用块。
- 每个间隙实长必须落在 `[目标 - 允许偏差, 目标 + 允许偏差]`（含端点）内。
- 对全部完整分配按下列次序**依次精确最小化**：
  1. 最大绝对偏差 D\*
  2. 偏差绝对值之和 S\*
  3. 已用块数 U\*
- 同优方案按以下次序裁决唯一**规范解**：按目标顺序，每个间隙内升序标识数组，
  再比未用标识数组。
- 每块的跨方案去向：
  - `fixed`：在所有同优方案中固定用于某间隙；
  - `flexible`：可在多个间隙之间、或“使用/未使用”之间流转；
  - `unused`：在所有同优方案中均未使用。

## 算法（不枚举全部完整分配、不截断）

见 `backend/app/solver.py` 顶部文档。要点：

1. 每个间隙枚举 1–6 块且和落在偏差带内的可行子集（位掩码）。
2. 带回溯上限的 DFS 快速给出可行 incumbent（仅作剪枝界，不用于判定可行性）。
3. 阶段一状态压缩 DP 只求最小 D\*（候选按 `|dev| ≤ incumbent D` 预过滤，
   最大偏差对扩展单调，剪枝可证安全）。
4. 阶段二在 `|dev| ≤ D*` 的紧候选集上做轻量 Pareto DP 求 S\*、U\*。
5. 阶段三以 `(D,S,U)` 全部不超过最优值的**精确键→计数**层（Python 大整数，
   不做支配合并）重建前缀/后缀层，得到精确同优方案数。
6. 块去向是边际事实：前后向精确层按残差 `(S,U)` 分桶，配合按需构建的
   子集 zeta 表做 O(1) 不相交存在性查询，即“双向合并”而不物化完整分配。

## 目录

```
backend/app/solver.py    精确求解器
backend/app/main.py      FastAPI 接口、字段级校验、任意精度序列化
backend/tests/           暴力 oracle 交叉验证（370+ 随机用例）与 API 测试
frontend/                React + Vite 页面（构建产物输出到 backend/static）
scripts/smoke_http.py    真实 HTTP 冒烟（抢占反例 + 无解边界 + 回显）
scripts/verify           单次验证：测试 -> 构建检查 -> HTTP 冒烟
verify                   根入口包装
Dockerfile / docker-compose.yml
```

## 本地运行

```bash
# 后端
python3 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
PYTHONPATH=backend uvicorn app.main:app --reload   # 工作目录 backend

# 前端开发（可选）
cd frontend && npm install && npm run dev
```

生产访问直接由 FastAPI 返回 `backend/static` 构建产物。

## Docker（端口可配置 + 健康检查）

```bash
APP_PORT=9000 docker compose up --build
# 浏览器访问 http://localhost:9000
```

- `APP_PORT`：宿主机访问端口（默认 8000）；`INTERNAL_PORT`：容器内监听端口。
- 镜像与 compose 均定义了 `/health` 健康检查。

## 单次验证 verify

```bash
./verify                      # 默认端口 8000
APP_PORT=8123 ./verify        # 指定冒烟端口
SKIP_DOCKER=1 ./verify        # 跳过 docker（守护进程不可用时自动本地等价检查）
```

内容与退出码：

1. 后端代码测试（含与暴力枚举 oracle 的一致性：目标值、精确计数、块去向、规范解）；
2. 构建检查（Python 编译/导入、前端生产构建；有 Docker 守护进程时构建镜像并
   校验 compose，否则做 Dockerfile/compose 静态检查）；
3. 启动**真实 uvicorn**，通过 HTTP 冒烟：
   - 量块抢占反例：逐间隙贪心最近组合会抢走另一间隙唯一可行块；联合最优仍可达；
   - 无解边界：需要超过 6 块才能成组的间隙返回 422 字段级原因并回显输入；
   - 重复标识校验同样回显输入。

成功以退出码 `0` 结束，任一步失败以非零退出码结束。

## 接口

`POST /api/solve`

```json
{
  "blocks":  [{"label": "G1", "length": 4}],
  "targets": [{"length": 10, "tolerance": 1}]
}
```

- 长度/目标/偏差只接受整数（整数或严格整数字符串，拒绝 `3.5`、`1e2`、布尔）。
- 校验失败或无可行分配返回 `422`：`errors` 为字段级原因，`input` 原样回显；
  前端保留用户输入，且**不用失败结果覆盖上次成功结论**。
- 成功返回 `cooptimal_count`（十进制字符串，任意精度不丢精度）、各间隙实长/
  偏差、规范解使用块、未用块与每块去向。
