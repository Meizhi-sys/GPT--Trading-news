"""Build a dated report and a static site; never manufacture missing data."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

BEIJING = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")


def target_session(now: datetime, force: bool = False) -> str | None:
    """At 08:30 Beijing, the New York date is the session just completed.

    Real exchange holidays and early closes are resolved by XNYS. Manual force
    selects the most recent *completed* session, including on weekends.
    """
    if now.tzinfo is None:
        raise ValueError("now must have a timezone")
    calendar = xcals.get_calendar("XNYS")
    day = (now.astimezone(NEW_YORK).date() if force else
           now.astimezone(BEIJING).date() - timedelta(days=1)).isoformat()
    if not force and not calendar.is_session(day):
        return None
    session = calendar.date_to_session(day, direction="previous")
    if calendar.session_close(session).to_pydatetime() > now:
        if not force:
            return None
        session = calendar.previous_session(session)
    return session.strftime("%Y-%m-%d")


def report_status(markets: dict, news: dict, errors: list[str]) -> str:
    has_data = bool(markets.get("equities") or markets.get("commodities")
                    or markets.get("gainers", {}).get("rows") or news.get("items"))
    if not has_data:
        return "failed"
    return "partial" if errors else "complete"


def validate_session(markets: dict, session: str) -> list[str]:
    """Reject equity data from another session, rather than label stale data today."""
    errors = []
    current = []
    for item in markets.get("equities", []):
        try:
            dt = datetime.fromisoformat(str(item["market_time"]).replace("Z", "+00:00"))
            if dt.tzinfo is None or dt.astimezone(NEW_YORK).date().isoformat() != session:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            errors.append(f"{item.get('symbol', '?')} 行情日期与交易日 {session} 不匹配，已移除。")
        else:
            current.append(item)
    markets["equities"] = current
    rows = []
    for item in markets.get("gainers", {}).get("rows", []):
        try:
            dt = datetime.fromisoformat(str(item["market_time"]).replace("Z", "+00:00"))
            if dt.tzinfo is None or dt.astimezone(NEW_YORK).date().isoformat() != session:
                raise ValueError
            if float(item["price"]) < 10:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            errors.append(f"涨幅榜 {item.get('symbol', '?')} 日期或价格不合要求，已移除。")
        else:
            rows.append(item)
    markets.setdefault("gainers", {})["rows"] = rows
    if not current:
        errors.append("本期重点股票行情缺失。")
    if not rows:
        errors.append("本期符合条件的涨幅榜缺失。")
    return errors


def save_json(path: Path, value: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def attach_related_news(markets: dict, news: dict) -> None:
    """Associate cited stories with tickers, without asserting price causation."""
    for row in markets.get("gainers", {}).get("rows", []):
        matches = [item for item in news.get("items", [])
                   if row.get("symbol") in item.get("related_symbols", [])]
        if matches:
            row["catalyst"] = "关联新闻（未证实上涨因果）：" + "；".join(
                item["title"] for item in matches[:2])
            row["related_sources"] = [s for item in matches[:2] for s in item.get("sources", [])]


def build_site(reports_dir: Path, site_dir: Path) -> int:
    from scripts.render_report import render_markdown, render_report
    reports = []
    for path in sorted(reports_dir.glob("????-??-??.json"), reverse=True):
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("report_date") != path.stem:
            raise ValueError(f"Report date does not match filename: {path.name}")
        reports.append(value)
    if not reports:
        return 0
    archive = [{"date": r["report_date"], "status": r["status"],
                "url": f"reports/{r['report_date']}/index.html"} for r in reports]
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
    for i, report in enumerate(reports):
        day = report["report_date"]
        folder = site_dir / "reports" / day
        folder.mkdir(parents=True, exist_ok=True)
        relative_archive = [{**a, "url": "../../" + a["url"]} for a in archive]
        (folder / "index.html").write_text(render_report(report, relative_archive), encoding="utf-8")
        (folder / "report.md").write_text(render_markdown(report), encoding="utf-8")
        save_json(folder / "report.json", report)
        if i == 0:
            (site_dir / "index.html").write_text(render_report(report, archive), encoding="utf-8")
            (site_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
            save_json(site_dir / "report.json", report)
    return len(reports)


def run(now: datetime, reports_dir: Path, site_dir: Path, force: bool = False) -> dict:
    from scripts.market_data import collect_market_data
    from scripts.news_analysis import collect_news
    session = target_session(now, force=force)
    if session is None:
        build_site(reports_dir, site_dir)
        return {"generated": False, "status": "skipped", "reason": "不是已完成的美股交易日；不生成新一期。"}
    previous_urls = []
    for path in sorted(reports_dir.glob("????-??-??.json"), reverse=True)[:3]:
        previous = json.loads(path.read_text(encoding="utf-8"))
        previous_urls.extend(s["url"] for i in previous.get("news", {}).get("items", [])
                             for s in i.get("sources", []) if s.get("url"))
    markets = collect_market_data(now)
    errors = list(markets.get("errors", [])) + validate_session(markets, session)
    news = collect_news(now, markets, session, previous_urls=previous_urls)
    attach_related_news(markets, news)
    errors.extend(news.get("errors", []))
    if not markets.get("commodities"):
        errors.append("黄金及布伦特行情缺失。")
    if not news.get("items"):
        errors.append("本期未取得可引用的新闻分析。")
    report = {"schema_version": 1, "report_date": now.astimezone(BEIJING).date().isoformat(),
              "session_date": session, "generated_at": now.isoformat(),
              "market_data": markets, "news": news, "errors": list(dict.fromkeys(errors))}
    report["status"] = report_status(markets, news, report["errors"])
    report["last_success_at"] = now.isoformat() if report["status"] == "complete" else None
    if report["last_success_at"] is None:
        for path in sorted(reports_dir.glob("????-??-??.json"), reverse=True):
            prior = json.loads(path.read_text(encoding="utf-8"))
            if prior.get("status") == "complete":
                report["last_success_at"] = prior.get("generated_at")
                break
    target = reports_dir / f"{report['report_date']}.json"
    # Do not overwrite a successful same-date report with a failed retry.
    if target.exists():
        old = json.loads(target.read_text(encoding="utf-8"))
        priority = {"failed": 0, "partial": 1, "complete": 2}
        if priority.get(old.get("status"), 0) > priority[report["status"]]:
            save_json(reports_dir / "last-attempt.json", {
                "generated_at": now.isoformat(), "status": report["status"],
                "errors": report["errors"], "retained_report": target.name})
            # Keep the data-completeness status intact: otherwise a later
            # partial retry could overwrite a previously complete report.
            old["latest_attempt"] = {"generated_at": now.isoformat(),
                                     "status": report["status"], "errors": report["errors"]}
            save_json(target, old)
        else:
            save_json(target, report)
    else:
        save_json(target, report)
    build_site(reports_dir, site_dir)
    return {"generated": True, "status": report["status"], "report_date": report["report_date"],
            "session_date": session}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="手动为最近已完成交易日生成一期")
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    parser.add_argument("--site-dir", type=Path, default=Path("site"))
    parser.add_argument("--latest-md", type=Path, default=Path("LATEST_REPORT.md"),
                        help="固定 GitHub 日报入口，无需 Pages 也能查看")
    args = parser.parse_args()
    result = run(datetime.now(timezone.utc), args.reports_dir, args.site_dir, args.force)
    if result["generated"]:
        args.latest_md.parent.mkdir(parents=True, exist_ok=True)
        args.latest_md.write_text((args.site_dir / "report.md").read_text(encoding="utf-8"), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"generated={str(result['generated']).lower()}\n")
            handle.write(f"status={result['status']}\n")
    return 0  # Publish explicit degraded status too; workflow separately flags failure.


if __name__ == "__main__":
    raise SystemExit(main())
