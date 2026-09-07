# 商品期货 Trading Agents 系统

AI 驱动的期货量化交易分析系统，支持 Docker 部署和直接运行两种方式。

## 🏗️ 架构

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Vue 3     │────▶│   Nginx     │────▶│  FastAPI    │
│  Frontend   │     │  (Proxy)    │     │  Backend    │
│  (Element+) │     │             │     │  (Python)   │
└─────────────┘     └─────────────┘     └─────────────┘
                           ▲                    │
                    npm start              ┌────┴────┐
                    (一键启动)             │         │
                                     ┌────▼───┐ ┌──▼──────────┐
                                     │ 分析引擎 │ │ 数据/配置     │
                                     │(LLM/AI)│ │(YAML/CSV)   │
                                     └────────┘ └─────────────┘
```

## 🚀 两种运行方式

### 方式一：直接运行（推荐开发/调试）

```bash
# 一键启动（自动拉起前后端，Ctrl+C 停止）
npm start
```

`start.js` 自动完成：
- 检测可用的 Python（优先 3.11）
- 首次运行自动创建 `backend/.env` 和安装前端依赖
- 启动后端 FastAPI，等待健康检查通过
- 启动前端 Vite Dev Server（热更新）
- 访问 http://localhost:3000

也可分别启动：
```bash
npm run dev:backend   # 仅后端 (端口 8000)
npm run dev:frontend  # 仅前端 (端口 3000，需后端已启动)
```

### 方式二：Docker 部署

```bash
# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API Key

# 构建并启动（使用国内镜像加速）
docker-compose up -d --build

# 查看日志
docker-compose logs -f

# 停止
docker-compose down
```

- 前端：http://localhost（Nginx 端口 80）
- API 文档：http://localhost:8000/docs
- 数据持久化通过 `futures-data` volume

**切换镜像源**（默认清华 PyPI + 淘宝 npm）：
```bash
docker-compose build \
  --build-arg PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/ \
  --build-arg NPM_REGISTRY=https://registry.npmmirror.com
```

## 📁 项目结构

```
futures-trading-web/
├── start.js                    # 一键启动脚本
├── package.json                # 根项目脚本
├── docker-compose.yml          # Docker Compose 编排
├── .env.example                # 环境变量模板
│
├── backend/                    # FastAPI 后端
│   ├── main.py                 # 应用入口
│   ├── requirements.txt        # Python 依赖
│   ├── Dockerfile              # 后端镜像（含国内镜像源）
│   ├── .dockerignore
│   ├── .env.example            # 后端环境变量模板
│   ├── config/
│   │   └── commodities.yaml    # 品种配置（59个品种，可编辑增删）
│   ├── core/
│   │   └── settings.py         # 全局配置
│   ├── models/                 # Pydantic 数据模型
│   │   ├── analysis.py         # 分析相关模型
│   │   └── data.py             # 数据管理模型
│   ├── routers/                # API 路由
│   │   ├── analysis.py         # 分析管理 API
│   │   ├── data.py             # 数据管理 API（含品种列表、主力合约）
│   │   ├── scheduled.py        # 定时分析 API
│   │   └── system.py           # 系统状态 API
│   ├── services/               # 业务逻辑
│   │   ├── analysis_service.py # 分析流程管理
│   │   ├── cache_service.py    # 缓存管理（pickle）
│   │   ├── commodity_service.py# 品种配置 + 主力合约自动获取
│   │   ├── data_service.py     # 数据管理 + 自动创建目录
│   │   ├── scheduled_service.py# 定时任务
│   │   └── word_service.py     # Word 报告生成
│   └── modules/                # 原始数据更新脚本（akshare）
│       ├── inventory_updater.py
│       ├── positioning_updater.py
│       ├── term_structure_updater.py
│       ├── technical_updater.py
│       ├── basis_updater.py
│       └── receipt_updater.py
│
└── frontend/                   # Vue 3 前端
    ├── src/
    │   ├── App.vue             # 根组件（侧边栏布局）
    │   ├── main.js             # 应用入口
    │   ├── api/                # Axios 请求层
    │   ├── router/             # Vue Router
    │   ├── stores/             # Pinia 状态管理
    │   ├── views/              # 页面
    │   │   ├── DataManagement.vue  # 数据管理
    │   │   ├── DataUpdate.vue      # 数据更新（品种表格+主力合约）
    │   │   ├── AnalysisConfig.vue  # 分析配置
    │   │   └── AnalysisResults.vue # 分析结果
    │   ├── components/         # 公共组件
    │   │   └── SystemStatus.vue
    │   └── assets/styles/
    ├── Dockerfile              # 前端镜像（多阶段构建）
    ├── .dockerignore
    ├── nginx.conf              # Nginx 配置
    ├── package.json
    └── vite.config.js          # Vite 配置（含 API 代理）
```

## 📊 品种配置

品种配置文件：`backend/config/commodities.yaml`

```yaml
commodities:
  - symbol: AU        # 品种代码
    name: 沪金         # 中文名称
    exchange: SHFE    # 交易所
    category: 贵金属   # 分类
```

- 预置 **59 个品种**，覆盖 SHFE / INE / DCE / CZCE / GFEX 五大交易所
- 用户可直接编辑此文件增删品种，程序自动读取
- 程序启动时自动为所有品种创建数据目录（6模块 × 59品种 = 354个目录）
- 主力合约通过 akshare `futures_display_main_sina` 自动获取，缓存到 `config/dominant_contracts.yaml`
- 前端"数据更新"页面可查看品种列表、主力合约、各模块数据状态

## 📡 API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/system/status` | 系统状态 |
| GET | `/api/system/commodities` | 支持品种列表 |
| GET | `/api/system/health` | 健康检查 |
| GET | `/api/data/commodities` | 品种配置（含主力合约+数据状态） |
| GET | `/api/data/dominant-contracts` | 主力合约映射 |
| POST | `/api/data/dominant-contracts/refresh` | 从 akshare 刷新主力合约 |
| GET | `/api/data/status` | 数据状态（6模块） |
| GET | `/api/data/varieties/{module}` | 模块品种详情 |
| GET | `/api/data/commodity/{symbol}` | 品种数据检查 |
| POST | `/api/data/update/{module}` | 更新模块数据（支持 target_date/varieties 参数） |
| POST | `/api/analysis/submit` | 提交分析任务 |
| GET | `/api/analysis/status` | 分析状态 |
| GET | `/api/analysis/progress` | 分析进度 |
| GET | `/api/analysis/results/{symbol}` | 品种分析结果 |
| GET | `/api/analysis/cache/list` | 缓存列表 |
| POST | `/api/analysis/cache/load/{symbol}/{date}` | 加载缓存 |
| DELETE | `/api/analysis/cache/{symbol}/{date}` | 删除缓存 |
| POST | `/api/analysis/word-report` | 生成 Word 报告 |
| GET | `/api/scheduled/config` | 定时配置 |
| POST | `/api/scheduled/start` | 启动定时分析 |
| POST | `/api/scheduled/stop` | 停止定时分析 |
| GET | `/api/scheduled/status` | 定时状态 |

## 🎯 功能模块

1. **数据管理** — 查看 6 大模块数据状态、品种数据范围、记录数
2. **数据更新** — 品种表格（代码/名称/交易所/主力合约/数据状态）、多选品种更新、6 模块一键更新
3. **分析配置** — 手动分析（选品种/模块/模式/辩论轮数/AI模型）+ 自动定时分析
4. **分析结果** — 实时进度、6 模块详情、多空辩论、交易员建议、风控意见、CIO 最终决策、Word 报告导出

## 🔧 配置说明

### 环境变量（`backend/.env`）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DASHSCOPE_API_KEY` | 百炼 API 密钥（必需） | - |
| `SERPER_API_KEY` | Serper 搜索密钥（可选） | - |
| `TIAN_API_KEY` | 天行 API Key（新闻检索，读**系统环境变量**，不写入代码库） | - |
| `SMTP_HOST/PORT/USER/PASSWORD/FROM/TO` | SMTP 邮件配置（可选，用于定时分析 `auto_email`；写在 `backend/.env` 即可，无需系统环境变量） | - |
| `DATA_ROOT_DIR` | 数据根目录 | `./data/qihuo/database` |
| `CACHE_DIR` | 缓存目录 | `./data/qihuo/cache/analysis` |
| `LOGS_DIR` | 日志目录 | `./data/logs` |

### 6 大分析模块

| 模块 | 数据文件 | 说明 |
|------|----------|------|
| inventory | inventory.csv | 库存数据 |
| positioning | long_position_ranking.csv | 持仓席位 |
| term_structure | term_structure.csv | 期限结构 |
| technical_analysis | ohlc_data.csv | 技术面指标 |
| basis | basis_data.csv | 基差数据 |
| receipt | receipt.csv | 仓单数据 |

## 🕐 无人值守自动分析（后台运行）

`auto_email` / `auto_word` / `update_data_before_analysis` 面向 **Linux/Windows 后台无人值守**场景：每天到点后自动 更新数据 → 多空辩论分析 → 保存 Word 报告 → 发送邮件，全程无需打开前端页面。

- 前端「分析配置 → 自动分析」可开启/关闭、配置触发时刻与上述附加选项，配置由后端常驻守护（`/api/scheduled/*`）。
- 也可不经前端，直接用系统计划任务定时调用单次脚本（更适合 Linux cron / Windows 任务计划程序）：

```bash
# Windows 计划任务或 cron 示例
python backend/run_scheduled_task.py \
  --commodities AU,RB,I \
  --modules technical,term_structure,basis,inventory,positioning,news \
  --auto-word --auto-email
```

- 使用 `--auto-email` 前，在 `backend/.env` 填写 `SMTP_*` 即可（普通配置，无需系统环境变量）；Docker 部署则在仓库根 `.env` 配置（docker-compose 会自动透传）。支持 SSL 465 / STARTTLS 587。
- Word 报告自动保存在后端 `RESULTS_DIR/auto_reports/` 目录。
- 同一脚本还可用于**每日收市后补跑**：`SCHEDULED_UPDATE_DATA=true` 时先自动更新数据再分析。

## 🔄 与原项目的关系

- 保留原有 6 大分析模块和 5 阶段决策流程（分析师→辩论→交易员→风控→CIO）
- `modules/` 目录为原始 akshare 数据更新脚本，通过 `importlib` 直接调用（非 subprocess）
- 数据目录结构与原项目兼容
- 缓存格式兼容原项目 pickle 缓存

## 📝 License

MIT
