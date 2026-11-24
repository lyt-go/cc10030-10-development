# 地理空间轨迹与网格聚合分析平台

面向定位点采集、坐标标准化、网格切分、轨迹分段驻留识别、空间关联聚合与区域维度产出的地理空间数据分析平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 定位源接入 | `geo_source` | 采集批次 | 批次编号、采集终端、定位方式 |
| 坐标标准化 | `coord_transform` | 转换规则 | 规则编号、源坐标系、目标坐标系 |
| 轨迹切分 | `track_segment` | 轨迹分段 | 分段编号、终端编号、起始时间 |
| 驻留识别 | `dwell_detect` | 驻留事件 | 事件编号、分段编号、中心坐标 |
| 网格索引 | `grid_index` | 网格单元 | 网格编号、网格层级、边界范围 |
| 围栏判定 | `geofence_rule` | 电子围栏 | 围栏编号、围栏形状、顶点序列 |
| 空间关联 | `spatial_join` | 关联任务 | 任务编号、左数据集、右数据集 |
| 兴趣点匹配 | `poi_match` | 匹配候选 | 候选编号、驻留事件、兴趣点编号 |
| 区域维度 | `region_dim` | 区域层级 | 区域编号、上级区域、区域名称 |
| 热力聚合 | `heatmap_aggregate` | 聚合窗口 | 窗口编号、网格层级、时间粒度 |
| 起止流量 | `od_matrix` | 流量单元 | 单元编号、起点网格、终点网格 |
| 路径还原 | `route_restore` | 还原路段 | 路段编号、分段编号、路段序列 |
| 密度估算 | `density_estimate` | 密度网格 | 估算编号、网格编号、带宽参数 |
| 轨迹质量 | `geo_quality` | 质量报告 | 报告编号、批次编号、漂移点数 |
| 点位归并 | `dedup_merge` | 归并批次 | 归并编号、批次编号、去重键列 |
| 分析快照 | `geo_snapshot` | 版本快照 | 快照编号、分析口径、网格层级 |
| 历史回补 | `geo_backfill` | 回补任务 | 回补编号、目标数据集、起始日期 |
| 结果导出 | `geo_export` | 导出任务 | 导出编号、快照编号、导出格式 |
| 区域授权交付 | `region_delivery` | 区域交付任务 | 交付编号、合作方编号、授权子区、匿名行数、窗口范围 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 区域授权交付（`region_delivery`）

面向区域合作方的匿名统计交付，核心规则与接口（前缀 `/api/region-delivery`）：

- **逐点子区裁切**：`POST /subzones` 登记子区多边形，`POST /batch-points` 按点的
  原始位置逐点判定归属并生成网格×子区贡献统计；`GET /cross-boundary-contribution/{batch_key}`
  在交付前核对每个跨界格实际贡献的子区与点数，校验合计与网格总量一致。
- **授权时点台账**：`/authorizations/grant|revoke` 对授权追加/收窄留痕，有效授权按
  每个子区最近一条生效记录计算；请求登记时快照当时授权，发包时按**发送时刻**授权裁切。
- **匿名交付包**：`POST /requests` 登记请求，`POST /requests/{id}/deliver` 发包。
  外发行只含网格、子区与匿名点数；终端标识、点位编号、坐标、时间戳等可还原单条轨迹的
  字段由白名单清洗剔除，`GET /requests/{id}/anonymity-check` 递归自检。
- **边界说明**：未授权子区、跨界格被截留贡献、整格出界只产生 `boundary_notes`，
  不出数据；内部复核副本（`/requests/{id}/internal-review`）保留完整子区贡献。
- **审校边界**：`POST /requests/{id}/review` 下审校员仅能看到自身授权子区的共享观测
  归属；越权查看其他区域或请求整格明细（`full_grid=true`）时返回零行加边界说明。
- **可领/重领范围**：`/claimable-scope` 与 `/reclaimable-scope` 始终以当前有效授权
  为准；被收窄子区的历史交付留存内部复核，但不再支持新下载或重领。

领域：数据工程
子方向：数据分析与数据应用
