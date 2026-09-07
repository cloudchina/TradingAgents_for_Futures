# 新闻搜索迁移：Serper → TianAPI + akshare

## 背景

Serper API (Google Search) 的 API key 不可用。初版替换为 TianAPI（天行数据），后新增 akshare `futures_news_shmet` 作为可选的第二新闻源，支持配置切换或双源合并。



## 新闻源架构

```
news_config.json
  └─ source: "tianapi" | "akshare" | "both"
       ├─ tianapi → apis.tianapi.com/caijing/index (泛财经期货新闻)
       ├─ akshare → ak.futures_news_shmet(symbol) (上海金属网期货新闻)
       └─ both → 两源合并，按 title 前 20 字符去重
```

### akshare 分类映射

akshare 接口支持按品种分类查询，内部维护了品种名到分类的映射：

| 品种 | akshare 分类 | 品种 | akshare 分类 |
|------|-------------|------|-------------|
| 铜 | 铜 | 铝 | 铝 |
| 锌 | 锌 | 铅 | 铅 |
| 镍 | 镍 | 锡 | 锡 |
| 黄金/白银 | 贵金属 | 其他品种 | 全部（兜底） |

有直接匹配的分类用对应分类查询，其余用"全部"补拉，合并后按 `akshare_days` 配置过滤日期。

## 关键技术决策

### 1. 为什么不用 TianAPI 的 `word` 筛选参数

TianAPI 的 `word` 参数虽然支持按品种名称筛选，但返回的是**历史旧闻**（2019-2023年），不是近期新闻。而无 `word` 参数的默认接口返回的是**近日财经新闻**（约半个月内）。

结论：**只用不带 `word` 的批量拉取 + 客户端关键词匹配。**

### 2. 同义词匹配

TianAPI/akshare 返回的新闻是泛财经类，标题里很少直接出现"原油"、"螺纹钢"等品种名。因此建立了同义词表：

```
"原油" → ["原油", "石油", "油价"]
"螺纹钢" → ["螺纹钢", "螺纹", "钢材", "钢铁"]
```

匹配时检查标题和描述中是否包含任意同义词。

### 3. 日期窗口

- TianAPI：3 天窗口（原 Serper 为 24 小时，TianAPI 滞后较大）
- akshare：`akshare_days` 配置项控制，默认 5 天，可调整

### 4. 一次 API 调用策略（TianAPI）

TianAPI 对多个品种只发起 **1次** API 请求（拉取 40 条），然后逐个品种做客户端关键词匹配。akshare 按分类查询，通常 1-3 次调用（直接匹配的分类 + "全部"兜底）。

## 配置说明

```json
{
  "enabled": true,
  "source": "both",
  "akshare_days": 5,
  "keywords": ["中国期货 市场情绪", "大宗商品 趋势分析"]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `enabled` | bool | 是否启用新闻搜索 |
| `source` | string | `"tianapi"` / `"akshare"` / `"both"` |
| `akshare_days` | int | akshare 日期过滤天数，默认 5 |
| `keywords` | list | 回退泛关键字（无品种新闻时使用） |

## 已知限制

- 不是所有品种都能匹配到新闻（取决于各 API 当日推送内容）
- 若无品种新闻，自动回退到配置的泛关键字搜索
- TianAPI URL 可能是协议相对路径（如 `//caijing.chinadaily.com.cn/...`），前端展示时需补全
- akshare 新闻无独立 URL 字段，link 为空
