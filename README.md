# GitHub 涨星日报

每天定时调用 GitHub 官方 Search API 抓取候选仓库，写入 SQLite 快照库，
与上一次快照对比算出日增 star，输出 Top 10 榜单并自动提交回本仓库。

## 最新一期 · 2026-11-16

| # | 仓库 | 今日 | 总星 | 语言 | 简介 |
|---:|------|-----:|-----:|------|------|
| 1 | [huamidev/GestaltX](https://github.com/huamidev/GestaltX) | **≈+1,108** | 7.8k | Swift | An MobileGestalt editor for iOS 27.x, featuring region-code twe… |
| 2 | [deepseek-ai/chat-demo](https://github.com/deepseek-ai/chat-demo) | **≈+798** | 3.2k | TypeScript | Chat Demo |
| 3 | [duwqijlk/muse-protocol](https://github.com/duwqijlk/muse-protocol) | **≈+534** | 1.1k | Python | muse.ai 注册与邀请码兑换协议研究（纯 HTTP Python 实现） |
| 4 | [YellowLaborerMarket/Gemini-Config-Checker-](https://github.com/YellowLaborerMarket/Gemini-Config-Checker-) | **≈+473** | 473 | Makefile | Chain region scanner: find proxy configs that get you into Goog… |
| 5 | [apex-compute/ANR](https://github.com/apex-compute/ANR) | **≈+446** | 2.2k | C++ | Apex Neural Rendering (ANR) by Apex Compute |
| 6 | [Kianuni/Erosion-CN](https://github.com/Kianuni/Erosion-CN) | **≈+357** | 2.1k | Swift | iOS / iPadOS 系统配置与 MobileGestalt 研究工具箱（简体中文防白苹果安全加固版） |
| 7 | [hitkeeen/blender-ai-mcp-addon](https://github.com/hitkeeen/blender-ai-mcp-addon) | **≈+340** | 1.4k | — | AI assistant and MCP server for Blender: visual chat, scene too… |
| 8 | [DevedevT/FFmpeg-GUI-Editor-Windows](https://github.com/DevedevT/FFmpeg-GUI-Editor-Windows) | **≈+307** | 1.2k | Visual Basic .NET | Lightweight portable FFmpeg GUI & lossless video trimmer for Wi… |
| 9 | [Noniv/abyssal-garden](https://github.com/Noniv/abyssal-garden) | **≈+281** | 1.1k | JavaScript | A dreamy underwater world you can swim through in your browser:… |
| 10 | [FrigadeHQ/yogurt](https://github.com/FrigadeHQ/yogurt) | **≈+278** | 1.7k | TypeScript | Private AI meeting notes that stay on your computer. A free, op… |

完整报告（含增速榜与数据说明）：[`reports/2026-11-16.md`](reports/2026-11-16.md)

---

## 工作原理

```
Search API 5 个分片召回 → 去重 → UPSERT 当日快照(SQLite)
                                    ↓
              读取最近可用基线 → 计算日增 → 排序 Top 10
                                    ↓
                     渲染 Markdown → commit 回仓库
```

- **为什么需要快照**：GitHub 没有任何 star 历史接口，日增只能靠两次快照相减，因此首次运行无法出榜。
- **候选池**：`created:>=7天前` 抓新仓库，叠加以 star 区间分层的 `pushed:>=1天前` 抓活跃老仓库。
- **1000 条上限**：任一查询命中超过 1000 条时自动二分 star 区间，直到每片都能完整召回。
- **断档处理**：基线取「最近一次早于今天的快照」而非硬编码 T-1；间隔 2~4 天按日均摊平并标 `~`，超过 4 天弃权。
- **同名合并**：按去掉结尾数字与分隔符后的名称合并批量建号的雷同仓库，只保留最强的一个。

## 部署到自己的仓库

1. Fork 或新建仓库，推入本目录全部文件。
2. 确认 `Settings → Actions → General → Workflow permissions` 勾选 **Read and write permissions**，否则 bot 无法提交结果。
3. 无需配置任何 Secret —— 内置 `GITHUB_TOKEN` 已足够（公开数据读取 + 对本仓库写入）。
4. 默认每天 UTC 00:10（北京时间 08:10）运行，也可在 Actions 页面手动触发并指定补跑日期。

> 若默认分支开启了分支保护，需放行 `github-actions[bot]` 直接推送，否则提交步骤会失败。

## 一键运行（Windows）

双击 `run-daily.bat` 即可。它会自检 Python 版本、首次运行时自动装依赖、生成报告并打开 `reports/` 目录。
想免去每次配 token，把 `.env.example` 复制为 `.env` 并填入 token —— `.env` 已被 gitignore 排除。

## 本地运行

需要 Python 3.10 或更新版本。

```bash
pip install -e .
export GITHUB_TOKEN=<your_token>   # 只需要 public_repo 读权限
python -m star_daily run           # 抓取 + 出报告
python -m star_daily report        # 只用已有数据重出报告
```

```bash
pytest                             # 49 个测试：日增计算、断档归一、同名合并、渲染转义、幂等写入、.env 读取
```

## 目录

| 路径 | 说明 |
|------|------|
| `src/star_daily/api.py` | Search API 客户端：限速、重试、分页、区间二分 |
| `src/star_daily/store.py` | SQLite schema、幂等写入、基线查询 |
| `src/star_daily/ranking.py` | 日增计算与榜单排序（纯函数） |
| `src/star_daily/report.py` | Markdown 渲染（纯函数） |
| `src/star_daily/cli.py` | `collect` / `report` / `run` 入口 |
| `data/state.db` | 快照库，随每次运行提交，保留最近 14 天 |
| `reports/` | 历史日报，永久留存 |
| `examples/sample-report.md` | 报告格式预览（样例数据） |
