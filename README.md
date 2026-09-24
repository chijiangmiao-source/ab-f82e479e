# 量块间隙精密分配（Gauge Block Allocator）

用一套已校准量块同时配出多个目标间隙的**精确整数最优**求解系统。逐目标贪心
挑选"最接近组合"会抢占关键量块、使整批最大偏差增大；本系统对整批联合寻优。

- 前端：React + Vite（录入 8–16 块、2–4 个目标间隙，查看规范分配与量块去向）
- 后端：FastAPI，调用一个 C++ 状态压缩搜索引擎（子进程）
- 整数纳米、任意偏差范围；同优方案数以**任意精度十进制字符串**返回
- 一次搜索即可标记每块：**固定到某间隙 / 可在多处（含未用）流转 / 始终未用**
- 不枚举完整分配、不截断；失败返回字段级原因且**不覆盖上次成功结论**

## 优化目标（严格按序字典序）

1. 最大绝对偏差 `max_g |实长_g − 目标_g|`
2. 偏差绝对值之和 `Σ_g |…|`
3. 已用量块数

再按**目标顺序内、各间隙升序标识数组，最后未用标识数组升序**裁决唯一规范解。

约束：每块至多使用一次；每间隙 1–6 块；各间隙偏差不得超过其允许偏差；未用块允许。

## 算法与完整性（backend/app/solver.cpp）

完整解是 `(S_0,…,S_{j-1})` 个两两不交子集，规模界 5^16 < 2^38（n≤16，
掩码放入 uint32），**绝不枚举完整分配**。

- **Pass 1（最小最大偏差）**：按目标顺序做掩码 DP，`mm[g][m]` 只保留占用掩码
  `m` 下可达到的最小前缀最大偏差。仅对 `max` 单调分量做每状态单值剪枝是安全的
  （后缀只会取 max，不会让更小前缀变差）。末层最小值得 `M*`；不可达即无可行分配。
- **Pass 2（偏差和、用块数）**：边限定为 `|偏差| ≤ M*`。由 `M*` 的最小性，该界
  内任何完整解的最大偏差恰为 `M*`，于是剩余目标 `(Σ|偏差|, 用块数)` 对后缀完全
  可加；每个占用掩码只保留字典序最优前缀并**精确累加同优前缀数**（严格被支配的
  前缀不可能再成为最优后缀，无损）。对称的反向 DP 给出每个状态的最优延续。
- **量块去向**：一趟前向×反向转移合并，落在全局最优上的转移贡献
  `fwdCnt × bwdCnt`（不同完整解），逐块累加它流向各间隙/未用的方案数。
- **规范解**：边按"成员标识升序数组"字典序排序，逐间隙取第一条可延拓到全局最优
  的边；此后未用集合唯一确定。

同优计数在结构界 5^16 内用 uint64 精确累加，序列化为字符串，前端用 BigInt 显示。

最坏规模 n=16、j=4（约 1.2×10^11 个完整分配）在普通机器上约几十至一百毫秒。

## 目录

```
backend/app/solver.cpp   精确搜索引擎（状态压缩 DP + 前/反向合并）
backend/app/main.py      FastAPI：字段级校验、求解器调用、静态托管
backend/tests/           针对真实 uvicorn 的端到端 unittest
frontend/                React 页面（src/App.jsx）
Dockerfile               多阶段：构建前端 → 编译 C++ → 运行 FastAPI
docker-compose.yml       可配置端口 + 健康检查
verify                   单次入口：构建检查 + 代码测试 + HTTP 冒烟
```

## 本地运行

```bash
# 后端（需 g++；建议虚拟环境）
python3 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
g++ -O2 -std=c++17 -o backend/app/bin/solver backend/app/solver.cpp
( cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 )

# 前端开发（可选，带 /api 代理）
cd frontend && npm install && npm run dev
```

生产构建的前端会被复制到 `backend/app/static` 由 FastAPI 直接托管，访问
`http://localhost:8000/`。

## Docker

```bash
docker compose up --build            # 默认宿主端口 8000
PORT=9090 docker compose up --build  # 自定义访问端口
CONTAINER_PORT=8080 PORT=9090 docker compose up --build  # 同时改容器监听口
```

镜像内置 `HEALTHCHECK`（`/health`），compose 亦声明健康检查。

## 一键验证

```bash
./verify
```

依次执行（任一步失败即以非零退出码结束）：

1. **构建检查**：C++ 求解器编译（-Wall -Wextra）、React 生产构建、Python 依赖
2. **代码测试**：unittest（健康检查/首页、抢占反例、无解边界、零容差边界、
   字段级错误回显、6 块上限、504,504,000 同优方案精确计数等）
3. **HTTP 冒烟**：真实 HTTP 调用
   - 量块抢占反例：逐目标贪心最大偏差 **15**，整批最优 **11**
   - 无解边界：单间隙各自可行但无法不交分配 → 422 `infeasible`
   - 非整数输入 → 422 字段级原因且不含解

求解器本身已用 Python 暴力枚举在数百个随机小例上交叉核对
（最优三元组、同优总数、逐块流向、规范解全部一致）。

## API

`POST /api/solve`

```json
{
  "blocks":  [{"id": "P", "length": 100}],
  "targets": [{"target": 90, "tolerance": 11}]
}
```

成功 200：`maxAbsDev / sumAbsDev / usedCount / totalOptimal(字符串) /
canonical[[ids…]…] / gaps[{target,tolerance,actual,deviation}] / unused[ids] /
destinations[{id, kind: fixed|flexible|always_unused, gap?, flows:[{gap:-1=未用,
count}]}]`。

无可行分配返回 422 `{ok:false, infeasible:true, errors:[{field,message}], echo}`；
校验错误同为 422 且带逐字段原因与原始 `echo`。前端在失败时保留全部输入，且不
用失败结果覆盖上一次成功结论。
