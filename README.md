# GitHub 涨星日报

每天定时调用 GitHub 官方 Search API 抓取候选仓库，写入 SQLite 快照库，与上一次快照对比算出日增 star，输出 Top 10 榜单并自动提交回本仓库。

> 本文件由 `star_daily` 每日自动覆盖更新，「工作原理」和「本地运行」两节会原样保留。

## 最新一期

_尚未运行。首次执行 `python -m star_daily run` 后，这里会出现当日 Top 10。_

---

## 工作原理

```
Search API 5 个分片召回 → 去重 → UPSERT 当日快照(SQLite)
                                    ↓
              读取最近可用基线 → 计算日增 → 排序 Top 10
                                    ↓
                     渲染 Markdown → commit 回仓库
```

- **为什么需要快照**：GitHub 没有任何 star 历史接口，日增只能靠两次快照相减，因此首次运行无法出榜。首次运行会用仓库创建至今的平均日增兜底（`≈` 标记），次日自动切换为真实差值。
- **候选池**：`created:>=7天前` 抓新仓库，叠加以 star 区间分层的 `pushed:>=1天前` 抓活跃老仓库。搜索接口没有「按增长排序」，抓到谁才能给谁算增量，所以召回策略直接决定榜单质量。
- **1000 条上限**：任一查询命中超过 1000 条时自动二分 star 区间，直到每片都能完整召回。细分到下限仍截断时，会在报告顶部标注「数据不完整」。
- **断档处理**：基线取「最近一次早于今天的快照」而非硬编码 T-1；间隔 2~4 天时按日均摊平并标 `~`，超过 4 天则弃权不参与排名。
- **同名合并**：GitHub 上存在同一模板批量建号的仓库（名字雷同、描述一致、星标数相同），会因为集中在最近几天而集体冲进新库分片高位。报告会按「去掉结尾数字与分隔符后的名称」合并，只保留最强的一个，并在顶部注明合并数量。

## 部署到自己的仓库

1. Fork 或新建仓库，推入本目录全部文件。
2. 确认 `Settings → Actions → General → Workflow permissions` 勾选 **Read and write permissions**（否则 bot 无法提交结果）。
3. 无需配置任何 Secret —— 使用 Actions 内置的 `GITHUB_TOKEN`，公开数据读取和对本仓库的写入权限都已足够。
4. 默认每天 UTC 00:10（北京时间 08:10）运行，也可在 Actions 页面手动触发并指定补跑日期。

> 若默认分支开启了分支保护，需放行 `github-actions[bot]` 直接推送，否则提交步骤会失败。

## 一键运行（Windows）

双击 `run-daily.bat` 即可。它会自检 Python 版本、首次运行时自动装依赖、生成报告并打开 `reports/` 目录。

想免去每次配 token，把 `.env.example` 复制为 `.env` 并填入 token —— `.env` 已被 gitignore 排除，不会误提交。

```bat
copy .env.example .env
:: 编辑 .env，把 GITHUB_TOKEN= 后面补上你的 token
```

需要放到桌面的话，右键 `run-daily.bat` → 发送到 → 桌面快捷方式。

## 本地运行

```bash
pip install -e .
export GITHUB_TOKEN=<your_token>   # 只需要 public_repo 读权限
python -m star_daily run           # 抓取 + 出报告
python -m star_daily report        # 只用已有数据重出报告
python -m star_daily collect       # 只抓取入库
```

未设置 `GITHUB_TOKEN` 时使用未认证限额（Search API 仅 10 次/分钟），能跑但会明显变慢。

```bash
pytest                             # 49 个测试，覆盖日增计算、断档归一、同名合并、渲染转义、幂等写入、.env 读取
```

## 目录

| 路径 | 说明 |
|------|------|
| `src/star_daily/api.py` | Search API 客户端：限速、重试、分页、star 区间二分 |
| `src/star_daily/store.py` | SQLite schema、幂等 UPSERT、基线查询、保留期清理 |
| `src/star_daily/ranking.py` | 日增计算与榜单排序（纯函数） |
| `src/star_daily/report.py` | Markdown 渲染（纯函数） |
| `src/star_daily/cli.py` | `collect` / `report` / `run` 入口 |
| `data/state.db` | 快照库，随每次运行提交，保留最近 14 天 |
| `reports/` | 历史日报，永久留存 |
| `examples/sample-report.md` | 报告格式预览（样例数据） |
