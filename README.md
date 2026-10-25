# 美股与黄金交易日报

固定网页展示最新日报，历史报告可下载 Markdown/JSON。重点关注 MU、MRNA、半导体存储、黄金、布伦特及宏观新闻。

**目前状态：代码、离线验证及一次真实行情采集已完成；当前机器缺少新闻服务凭证，真实新闻生成及 GitHub Pages 上线尚未验证。提交代码不等于已开通每日投递。**

## 你的交付与筛选设置

- **固定网页**，打开收藏即可查看；不在电脑自动弹窗。
- **仅美股实际交易日后的北京时间上午 08:30 更新**。通常是周二至周六；程序使用 NYSE 交易所日历处理休市、提前收市和夏令时。GitHub 定时任务可能有延迟。
- 主榜排除常规时段收盘价 **低于 10 美元**的股票；MU、MRNA 专题保留。
- 榜单附市值、成交额估算及关联新闻；没有核实催化时明确标记，不把急涨视为“机构操纵”的证据。

完整需求见 [NEWS_REPORT_REQUIREMENTS.md](NEWS_REPORT_REQUIREMENTS.md)。

## 上线前需要完成的设置

1. 在 [Actions Secrets](https://github.com/Meizhi-sys/GPT--Trading-news/settings/secrets/actions) 确认已有或添加 `OPENAI_API_KEY`，已有可用绑定则复用，不重复创建。**只在 GitHub 安全设置中填写，不要放入代码或聊天。** 此方案使用 OpenAI API 的联网检索生成中文摘要，需要对应 API 账户有可用额度，可能产生费用；ChatGPT 订阅不自动包含 API 额度。模型默认 `gpt-4.1`，可在 Actions Variables 设置 `OPENAI_MODEL`，替代模型须支持 Responses、web search 和结构化输出。
2. 在 [Pages 设置](https://github.com/Meizhi-sys/GPT--Trading-news/settings/pages) 将 **Build and deployment → Source** 设为 **GitHub Actions**。若当前仓库的可见性/套餐不支持 Pages，先解决该限制；不要为了上线擅自公开私人仓库。
3. 在 [Actions](https://github.com/Meizhi-sys/GPT--Trading-news/actions) 启用工作流（若被禁用），打开 **Daily trading report → Run workflow**，选择 `main` 手动运行一次。手动运行会为最近一个已完成交易日生成报告，周末也可用于验证。
4. 检查 `build`、`deploy`、`verify` 均成功。`deploy` 给出的 **实际 page_url** 才是可以收藏的网页入口；不要把预测的网址当成已上线链接。打开网页核对报告日期、行情截至时间、状态和来源，并验证一次下载。

没有 API 密钥或数据源访问失败时，程序只发布明确的缺失状态，不生成假新闻。`verify` 会将不完整报告或发布失败标为失败。Actions 的文件制品保留 30 天，日报 JSON 也会由机器人提交到 `reports/`，用于长期归档；受保护分支如拒绝机器人提交，需要配置合适的写入权限，不能跳过权限限制。

当前云机器现已真实读取行情和 GitHub 仓库元数据，但启用 Pages、列出 Secrets 和读取 Actions 管理设置返回 `Resource not accessible by integration`（403），因此未能代为启用 Pages 或验证运行，也不能断言仓库没有既有 Secrets。所需 `api.github.com`、`query1.finance.yahoo.com`、`query2.finance.yahoo.com`、`api.openai.com` 已加入**云环境网络配置草稿**；草稿尚需在环境设置中审阅、保存并发布，当前可访问的证据不等于已完成发布。这与 GitHub Actions 的网络和 Secrets 是不同设置。

## 数据来源与限制

- 行情独立读取 Yahoo Finance JSON，不需要交易账户密钥。公开接口没有稳定性保证，可能限流或拒绝访问；必须以真实 runner 的结果验证可用性。可通过替换 `scripts/market_data.py` 接入其他经授权数据源。
- MU、MRNA、SPY、QQQ、SOXX 展示常规时段报价；股票数据必须与所选美股交易日一致，过期数据移除并说明。
- 涨幅榜是 **Yahoo day_gainers 最多 250 个样本中，满足价格至少 10 美元和交易所等条件的前 10**，不是全市场扫描。源数据的 EQUITY 分类不能完全识别 ADR/优先股等证券身份，页面保留此限制；不足 10 支时不填充。若需要严格全市场普通股/ADR 前 10，需要更完整、可核实证券类型的行情数据服务。
- 成交额仅在没有逐笔数据时使用“价格 × 成交量”估算并标注。市值缺失时留空。
- 黄金 `GC=F`、布伦特 `BZ=F` 是近月期货代码，不是现货。具体合约月份尚未核实，不作为特定月份合约结算价；不能跨换月机械比较收益。当前不自动生成长期趋势结论。
- 中文新闻由 Responses API 的 web search 检索并结构化生成，区分事实摘要与相关性分析。链接必须匹配搜索工具实际返回的来源；这不构成独立核实全文。无法核实来源或报道时间的条目不发布，新闻不足时不凑数。
- 对涨幅榜股票优先检索同期财报、订单或产业事件，展示为“关联新闻”，不声称已证明上涨因果。未找到催化的机械排名不等于重点推荐。
- 若本期来源缺失，首页显示本期失败或部分缺失；不以旧报告冒充当日报告。同一天失败重跑不会静默替换先前更完整的内容。

## 本地开发与检查

Python 3.12：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m scripts.generate_report --force
```

从仓库根目录执行。API 密钥通过安全环境注入，不要写到命令文本、提交记录或日志。`--force` 只用于手动补发/验证，仍选择已经收盘的最近交易日。常规定时执行不带参数，在休市时不会生成新一期。

输出：

- `reports/YYYY-MM-DD.json`：按北京时间日期归档的真实结果及缺失说明。
- `site/index.html`：最新一期，含时间、状态和历史入口。
- `site/report.md` / `site/report.json`：最新一期下载。
- `site/reports/YYYY-MM-DD/`：各期网页与下载。

离线测试验证节假日/夏令时、10 美元边界、量价计算、过旧数据、来源校验、重试、HTML 安全、归档和同日重跑；不等同于真实数据或线上交付验证。仅有数据源接口与基础展示实现，完整的财报深度研究、日 K 单边趋势筛选和交叉来源核验仍需后续完善。
