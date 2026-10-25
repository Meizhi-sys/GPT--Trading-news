"""Render verified-input reports without templates, scripts, or external assets."""

from __future__ import annotations

from html import escape
import math
import re
from urllib.parse import unquote, urlsplit


STATUS_LABELS = {"complete": "资料齐备", "partial": "部分资料缺失", "failed": "本期生成失败"}


def _text(value: object) -> str:
    return "—" if value is None or value == "" else str(value)


def _html(value: object) -> str:
    return escape(_text(value), quote=True)


def _number(value: object, *, money: bool = False, percent: bool = False) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "—"
    if percent:
        return f"{value:+,.2f}%"
    if money:
        return f"${value:,.2f}"
    return f"{value:,.0f}"


def _safe_url(value: object, *, relative: bool = False) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    decoded = unquote(value)
    if any(ord(char) < 32 or ord(char) == 127 for char in decoded) or "\\" in decoded:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
            # Invalid or non-numeric ports must not pass merely because a hostname exists.
            parsed.port
            return value
        if not relative or parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            return None
    except ValueError:
        return None
    # Archive pages live at reports/YYYY-MM-DD/index.html. Their sibling
    # archive links have one fixed, generated shape, rather than free traversal.
    if re.fullmatch(r"\.\./\.\./reports/\d{4}-\d{2}-\d{2}/index\.html", decoded):
        return value
    if decoded.startswith("/") or any(part == ".." for part in decoded.split("/")):
        return None
    if re.fullmatch(r"(?:\./)?[A-Za-z0-9_./-]+\.(?:html|md|json)", decoded):
        return value
    return None


def _link(value: object, title: object, *, relative: bool = False) -> str:
    safe = _safe_url(value, relative=relative)
    if not safe:
        return f'<span class="muted">{_html(title)}（链接不可用）</span>'
    return f'<a href="{escape(safe, quote=True)}" rel="noopener noreferrer">{_html(title)}</a>'


def _errors(report: dict) -> list[str]:
    errors = []
    for group in (report, report.get("market_data") or {}, report.get("news") or {}):
        for value in group.get("errors") or []:
            message = str(value)
            if message and message not in errors:
                errors.append(message)
    return errors


def _rows(values: object) -> list[dict]:
    return [row for row in values if isinstance(row, dict)] if isinstance(values, list) else []


def _scope(gainers: dict) -> str:
    return _text(gainers.get("scope") or "Yahoo 当日涨幅榜最多 250 个样本中符合价格条件的股票；不是全市场榜单。")


def _quote_table(rows: list[dict]) -> str:
    if not rows:
        return '<p class="empty">本期没有可核实报价，未用示例或历史价格代替。</p>'
    body = []
    for row in rows:
        source = _link(row.get("source_url"), "行情原始来源")
        body.append(
            "<tr>"
            f'<th scope="row">{_html(row.get("symbol"))}<small>{_html(row.get("name") or row.get("label"))}</small></th>'
            f'<td>{_number(row.get("price"), money=True)}</td>'
            f'<td>{_number(row.get("change_percent"), percent=True)}</td>'
            f'<td>{_number(row.get("volume"))}</td>'
            f'<td>{_html(row.get("market_time"))}<small>{source}</small></td>'
            "</tr>"
        )
    return ('<div class="table-scroll"><table><thead><tr><th>标的</th><th>价格 / 美元</th>'
            '<th>日变动</th><th>成交量</th><th>行情时点 / 来源</th></tr></thead><tbody>'
            + "".join(body) + "</tbody></table></div>")


def _gainers_table(rows: list[dict]) -> str:
    if not rows:
        return '<p class="empty">本期没有可验证的涨幅榜，未以虚构股票补足十名。</p>'
    body = []
    for index, row in enumerate(rows, 1):
        catalyst = row.get("catalyst") or "未找到可核实催化，不作为重点研究推荐"
        related = " · ".join(_link(source.get("url"), source.get("title") or "相关新闻")
                             for source in _rows(row.get("related_sources")))
        body.append(
            f'<tr><td>{index}</td><th scope="row">{_html(row.get("symbol"))}'
            f'<small>{_html(row.get("name"))}</small></th>'
            f'<td>{_number(row.get("price"), money=True)}</td>'
            f'<td>{_number(row.get("change_percent"), percent=True)}</td>'
            f'<td>{_number(row.get("volume"))}</td>'
            f'<td>{_number(row.get("turnover", row.get("estimated_turnover_usd")), money=True)}'
            f'<small>{_html(row.get("turnover_note") or row.get("turnover_method") or row.get("turnover_basis"))}</small></td>'
            f'<td>{_number(row.get("market_cap"), money=True)}</td>'
            f'<td>{_html(catalyst)}<small>{related}</small><small>{_link(row.get("source_url"), "行情来源")}</small></td></tr>'
        )
    return ('<div class="table-scroll"><table><thead><tr><th>#</th><th>股票</th><th>收盘价</th>'
            '<th>涨幅</th><th>成交量</th><th>成交额 / 美元</th><th>市值 / 美元</th><th>催化 / 来源</th>'
            '</tr></thead><tbody>' + "".join(body) + "</tbody></table></div>")


def render_report(report: dict, archive: list[dict]) -> str:
    """Return a static Chinese report; all caller-controlled text is escaped."""
    markets = report.get("market_data") or {}
    gainers = markets.get("gainers") or {}
    status = report.get("status") if report.get("status") in STATUS_LABELS else "failed"
    errors = _errors(report)
    warning = ""
    if status != "complete":
        warning = ('<div class="notice" role="status"><strong>本期资料不完整</strong>'
                   '请查看下方缺失说明。页面日期表示报告生成日，不代表缺失行情已更新。</div>')
    attempt = report.get("latest_attempt") or {}
    if isinstance(attempt, dict) and attempt:
        attempt_errors = "".join(f"<li>{_html(value)}</li>" for value in attempt.get("errors") or [])
        warning += ('<div class="notice" role="status"><strong>最近一次重试未成功，以下保留先前版本</strong>'
                    f'<p>重试时间：{_html(attempt.get("generated_at"))} · 重试状态：'
                    f'{_html(STATUS_LABELS.get(attempt.get("status"), "状态未知"))}</p>'
                    f'<ul>{attempt_errors}</ul></div>')
    limitations = "".join(f"<li>{_html(value)}</li>" for value in gainers.get("limitations") or [])
    commodity_notes = []
    for row in _rows(markets.get("commodities")):
        notes = row.get("limitations") or []
        if isinstance(notes, str):
            notes = [notes]
        text = "；".join(map(str, notes))
        commodity_notes.append(f'<li>{_html(row.get("symbol"))}：{_html(row.get("contract") or row.get("label") or "期货报价")}；单位：{_html(row.get("unit"))}；具体合约月份：{_html(row.get("contract_month"))}。{_html(text)}</li>')
    articles = []
    for item in _rows((report.get("news") or {}).get("items")):
        links = " · ".join(_link(source.get("url"), source.get("title") or "原文")
                           for source in _rows(item.get("sources"))) or "原文链接缺失"
        articles.append(
            '<article class="news-card">'
            f'<span class="category">{_html(item.get("category"))}</span><h3>{_html(item.get("title"))}</h3>'
            f'<p class="meta">事件日期：{_html(item.get("event_date"))} · 报道日期：{_html(item.get("published_at"))}</p>'
            f'<p class="meta">相关代码：{_html("、".join(str(symbol) for symbol in item.get("related_symbols") or []))}</p>'
            f'<p><b>事实摘要</b> {_html(item.get("summary"))}</p>'
            f'<p class="analysis"><b>与你相关 · 分析</b> {_html(item.get("relevance"))}</p>'
            f'<p class="sources">{links}</p></article>'
        )
    news = "".join(articles) or '<p class="empty">本期没有可核实的新闻摘要。请勿将空白栏目理解为市场没有发生变化。</p>'
    news_warnings = "".join(f'<li>{_html(value)}</li>' for value in (report.get("news") or {}).get("warnings") or [])
    market_notes = markets.get("limitations") or []
    if isinstance(market_notes, str):
        market_notes = [market_notes]
    market_limitations = "".join(f'<li>{_html(value)}</li>' for value in market_notes)
    history = "".join(
        f'<li>{_link(item.get("url"), item.get("date"), relative=True)} '
        f'<span class="muted">{_html(STATUS_LABELS.get(item.get("status"), "状态未知"))}</span></li>'
        for item in _rows(archive)
    ) or "<li>暂无历史归档。</li>"
    missing = "".join(f"<li>{_html(value)}</li>" for value in errors) or "<li>本期没有报告额外错误；仍请核对数据时点和来源口径。</li>"
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer"><title>{_html(report.get("report_date"))} · 交易晨报</title>
<style>
:root{{color-scheme:dark;--bg:#091323;--panel:#112139;--ink:#eaf0f8;--muted:#a7b5ca;--gold:#ecc577;--line:#2a3b53}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.75 system-ui,-apple-system,"Microsoft YaHei",sans-serif}}
main{{max-width:1180px;margin:auto;padding:40px 24px 60px}}a{{color:var(--gold);text-underline-offset:4px}}a:focus-visible{{outline:2px solid var(--gold);outline-offset:4px}}
header{{border-top:3px solid var(--gold);padding:24px 0 32px}}.eyebrow,.category{{color:var(--gold);font-size:.8rem;letter-spacing:.12em}}h1{{font-size:clamp(2rem,5vw,3.4rem);line-height:1.2;margin:12px 0}}
h2{{font-size:1.35rem;margin:0 0 14px}}h3{{font-size:1.15rem;line-height:1.55;margin:8px 0}}p{{margin:10px 0}}.muted,.meta,small{{color:var(--muted)}}.meta{{font-size:.86rem}}
.stamp{{display:flex;gap:12px;align-items:center;flex-wrap:wrap}}.date{{font-size:1.35rem;font-weight:700}}.badge{{border:1px solid var(--gold);border-radius:20px;padding:2px 12px;color:var(--gold);font-size:.82rem}}
.notice{{background:#342918;border-left:4px solid var(--gold);padding:14px 18px;margin:0 0 24px}}.notice strong{{display:block}}section{{margin:0 0 26px;padding:24px;background:var(--panel);border:1px solid var(--line);border-radius:12px}}
.downloads{{display:flex;gap:18px;flex-wrap:wrap;margin-top:16px}}.table-scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:.88rem;text-align:left}}td,th{{padding:13px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
thead th{{color:var(--muted);font-weight:500;white-space:nowrap}}small{{display:block;font-size:.75rem;font-weight:400;min-width:90px}}.empty{{padding:14px 0;color:var(--muted)}}.news-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}}
.news-card{{padding:20px;border:1px solid var(--line);border-radius:8px;background:var(--bg);overflow-wrap:anywhere}}.news-card p{{font-size:.92rem}}.analysis{{color:#ccdbef}}.sources{{font-size:.8rem!important}}ul{{padding-left:22px}}li{{margin:5px 0;overflow-wrap:anywhere}}footer{{color:var(--muted);font-size:.82rem}}
@media(max-width:720px){{main{{padding:20px 12px}}section{{padding:16px}}.news-grid{{grid-template-columns:1fr}}td,th{{padding:10px 8px}}}}
</style></head><body><main>
<header><div class="eyebrow">MARKETS · TECHNOLOGY · WORLD</div><h1>交易晨报</h1>
<div class="stamp"><span class="date">{_html(report.get("report_date"))}</span><span class="badge">{STATUS_LABELS[status]}</span></div>
<p class="meta">美股交易日：{_html(report.get("session_date"))} · 实际生成：{_html(report.get("generated_at"))}</p>
<p class="meta">行情截至：{_html(markets.get("as_of"))} · 最近成功更新：{_html(report.get("last_success_at"))}</p>
<p class="muted">美股交易日结束后的北京时间上午 08:30 计划更新；调度可能延迟。数据日期以各标的时点为准。</p>
<nav class="downloads" aria-label="下载报告"><a href="./report.md">下载 Markdown</a><a href="./report.json">下载数据 JSON</a><a href="#archive">历史归档</a></nav></header>
{warning}
<section><h2>关注资产 · 行情事实</h2><p class="meta">行情来自数据源及程序计算，报价与新闻分析分开呈现。</p>{_quote_table(_rows(markets.get("equities")))}<ul class="meta">{market_limitations}</ul></section>
<section><h2>黄金与布伦特 · 期货口径</h2>{_quote_table(_rows(markets.get("commodities")))}<ul class="meta">{''.join(commodity_notes)}</ul><p class="meta">期货不等于现货；合约换月可能影响价格与收益可比性，不能直接视为连续持仓收益。</p></section>
<section><h2>收盘价 ≥ 10 美元的涨幅前 10 · 有限样本</h2><p class="meta">{_html(_scope(gainers))}</p><p class="meta">普通股 / ADR；不含 ETF。低于 10 美元的股票排除。缺乏催化不等于已证实操纵。</p>{_gainers_table(_rows(gainers.get("rows")))}<ul class="meta">{limitations}</ul></section>
<section><h2>新闻与影响</h2><p class="meta">AI 辅助摘要；事实摘要与相关性分析分开，重大决定请核对原文。没有原文支撑的内容不应补入。</p><ul class="meta">{news_warnings}</ul><div class="news-grid">{news}</div></section>
<section><h2>数据缺失与口径说明</h2><ul>{missing}</ul></section>
<section id="archive"><h2>历史归档</h2><ul>{history}</ul></section>
<footer>涨幅排名不代表日 K 上涨趋势；趋势需要独立的历史价格及量能证据。页面不会自动执行交易。</footer>
</main></body></html>'''


def _md(value: object) -> str:
    text = escape(_text(value), quote=False).replace("\n", " ").replace("\r", " ")
    return re.sub(r"([\\`*_[\]{}|])", r"\\\1", text)


def _md_link(url: object, title: object) -> str:
    safe = _safe_url(url)
    if not safe:
        return _md(title) + "（链接不可用）"
    return f"[{_md(title)}](<{safe.replace('>', '%3E').replace('<', '%3C')}>)"


def render_markdown(report: dict) -> str:
    """Return a portable text version with the same status and source disclosures."""
    markets = report.get("market_data") or {}
    status = STATUS_LABELS.get(report.get("status"), STATUS_LABELS["failed"])
    lines = [f"# 交易晨报 · {_md(report.get('report_date'))}", "", f"状态：{status}", "",
             f"美股交易日：{_md(report.get('session_date'))}  ",
             f"实际生成：{_md(report.get('generated_at'))}  ",
             f"行情截至：{_md(markets.get('as_of'))}  ",
             f"最近成功更新：{_md(report.get('last_success_at'))}", ""]
    if report.get("status") != "complete":
        lines.extend(["> 本期资料不完整。报告日期不代表缺失行情已更新，详见数据缺失说明。", ""])
    attempt = report.get("latest_attempt") or {}
    if isinstance(attempt, dict) and attempt:
        lines.extend(["> **最近一次重试未成功，以下保留先前版本。**", "",
                      f"重试时间：{_md(attempt.get('generated_at'))}；重试状态：{_md(STATUS_LABELS.get(attempt.get('status'), '状态未知'))}", ""])
        lines.extend(f"- {_md(value)}" for value in attempt.get("errors") or [])
        lines.append("")
    for title, key in [("关注资产 · 行情事实", "equities"), ("黄金与布伦特 · 期货口径", "commodities")]:
        lines.extend([f"## {title}", "", "| 标的 | 价格 / 美元 | 日变动 | 成交量 | 行情时点 | 来源 |",
                      "| --- | ---: | ---: | ---: | --- | --- |"])
        rows = _rows(markets.get(key))
        for row in rows:
            lines.append(f"| {_md(row.get('symbol'))} | {_number(row.get('price'), money=True)} | {_number(row.get('change_percent'), percent=True)} | {_number(row.get('volume'))} | {_md(row.get('market_time'))} | {_md_link(row.get('source_url'), '行情来源')} |")
        if not rows:
            lines.extend(["", "没有可核实报价，未以示例数据补齐。"])
        lines.append("")
    lines.extend(["期货不等于现货；合约换月可能影响价格及收益可比性。", "",
                  "## 收盘价 ≥ 10 美元的涨幅前 10 · 有限样本", "",
                  _md(_scope(markets.get("gainers") or {})), "",
                  "| # | 股票 | 收盘价 | 涨幅 | 成交量 | 成交额 / 美元 | 市值 / 美元 | 催化 / 来源 |",
                  "| ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |"])
    gainers = markets.get("gainers") or {}
    for index, row in enumerate(_rows(gainers.get("rows")), 1):
        turnover = row.get('turnover', row.get('estimated_turnover_usd'))
        turnover_note = row.get('turnover_note') or row.get('turnover_method') or row.get('turnover_basis')
        related = " · ".join(_md_link(source.get("url"), source.get("title") or "相关新闻")
                             for source in _rows(row.get("related_sources")))
        related = f" · {related}" if related else ""
        lines.append(f"| {index} | {_md(row.get('symbol'))} · {_md(row.get('name'))} | {_number(row.get('price'), money=True)} | {_number(row.get('change_percent'), percent=True)} | {_number(row.get('volume'))} | {_number(turnover, money=True)}（{_md(turnover_note)}） | {_number(row.get('market_cap'), money=True)} | {_md(row.get('catalyst') or '未找到可核实催化')}{related} · {_md_link(row.get('source_url'), '行情来源')} |")
    if not _rows(gainers.get("rows")):
        lines.extend(["", "没有可验证的涨幅榜，未以虚构股票补足十名。"])
    lines.extend(["", *[f"- {_md(value)}" for value in gainers.get("limitations") or []], "",
                  "## 新闻与影响", "", "AI 辅助摘要；请核对原文。事实与分析分开呈现。", ""])
    lines.extend(f"- {_md(value)}" for value in (report.get("news") or {}).get("warnings") or [])
    if (report.get("news") or {}).get("warnings"):
        lines.append("")
    for item in _rows((report.get("news") or {}).get("items")):
        lines.extend([f"### {_md(item.get('title'))}", "",
                      f"分类：{_md(item.get('category'))}；事件日期：{_md(item.get('event_date'))}；报道日期：{_md(item.get('published_at'))}", "",
                      f"相关代码：{_md('、'.join(str(symbol) for symbol in item.get('related_symbols') or []))}", "",
                      f"**事实摘要：** {_md(item.get('summary'))}", "",
                      f"**与你相关（分析）：** {_md(item.get('relevance'))}", "",
                      "来源：" + " · ".join(_md_link(source.get("url"), source.get("title") or "原文") for source in _rows(item.get("sources"))), ""])
    if not _rows((report.get("news") or {}).get("items")):
        lines.extend(["本期没有可核实的新闻摘要，不代表市场没有发生变化。", ""])
    lines.extend(["## 数据缺失与口径说明", ""])
    lines.extend(f"- {_md(value)}" for value in _errors(report))
    if not _errors(report):
        lines.append("未报告额外错误；仍须核对数据时点和来源口径。")
    return "\n".join(lines) + "\n"
