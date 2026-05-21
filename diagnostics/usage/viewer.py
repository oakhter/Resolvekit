#!/usr/bin/env python3
"""
diagnostics/usage/viewer.py — API cost and usage dashboard.

Usage:
    python diagnostics/usage/viewer.py
    python diagnostics/usage/viewer.py --from 2026-01-01
    python diagnostics/usage/viewer.py --from 2026-01-01 --to 2026-04-30
    python diagnostics/usage/viewer.py --no-chart
    python diagnostics/usage/viewer.py --save-chart diagnostics/usage/report.png
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_ROOT / ".cache" / "matplotlib"))

try:
    import pandas as pd
    import matplotlib
    matplotlib.use("Agg")   # non-interactive backend; works without a display
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
except ImportError as e:
    print(f"❌ Missing dependency: {e}")
    print("   Run:  pip install pandas matplotlib")
    sys.exit(1)

try:
    import psycopg2
    from dotenv import load_dotenv
except ImportError as e:
    print(f"❌ Missing dependency: {e}")
    sys.exit(1)

load_dotenv(PROJECT_ROOT / ".env")
DATABASE_URL     = os.getenv("DATABASE_URL")
OPS_SCHEMA       = os.getenv("OPS_SCHEMA", "ops")

# ── Pricing table (USD per token) ────────────────────────────
PRICING = {
    "gpt-4.1-mini": {"input": 0.40  / 1_000_000, "output": 1.60  / 1_000_000},
    "gpt-4o-mini":  {"input": 0.15  / 1_000_000, "output": 0.60  / 1_000_000},
    "gpt-4o":       {"input": 2.50  / 1_000_000, "output": 10.00 / 1_000_000},
    "gpt-4.1":      {"input": 2.00  / 1_000_000, "output": 8.00  / 1_000_000},
}

SEP  = "─" * 56
SEP2 = "═" * 56


def get_ops_conn():
    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{OPS_SCHEMA}", public;')
    return conn


def table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f"{OPS_SCHEMA}.{table_name}",))
        return cur.fetchone()[0] is not None


def query_df(conn, sql: str, params: tuple, columns: list[str],
             parse_dates: list[str] | None = None) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    df = pd.DataFrame(rows, columns=columns)
    for col in parse_dates or []:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col])
    return df


def empty_api_calls_df() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "model", "endpoint", "tokens_in", "tokens_out",
        "latency_ms", "cost_usd", "error", "created_at",
    ])


def empty_feedback_df() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "rating", "email_was_edited", "from_cache",
        "response_time_ms", "confidence", "created_at",
    ])


def load_api_calls(from_dt: str, to_dt: str) -> pd.DataFrame:
    sql = """
        SELECT model, endpoint, tokens_in, tokens_out, latency_ms, cost_usd, error, created_at
        FROM api_calls
        WHERE created_at BETWEEN %s AND %s
        ORDER BY created_at
    """
    with get_ops_conn() as conn:
        if not table_exists(conn, "api_calls"):
            return empty_api_calls_df()
        return query_df(
            conn,
            sql,
            (from_dt, to_dt),
            ["model", "endpoint", "tokens_in", "tokens_out",
             "latency_ms", "cost_usd", "error", "created_at"],
            parse_dates=["created_at"],
        )


def load_feedback(from_dt: str, to_dt: str) -> pd.DataFrame:
    sql = """
        SELECT rating, email_was_edited, from_cache, response_time_ms, confidence, created_at
        FROM feedback
        WHERE created_at BETWEEN %s AND %s
        ORDER BY created_at
    """
    try:
        with get_ops_conn() as conn:
            if not table_exists(conn, "feedback"):
                return empty_feedback_df()
            return query_df(
                conn,
                sql,
                (from_dt, to_dt),
                ["rating", "email_was_edited", "from_cache",
                 "response_time_ms", "confidence", "created_at"],
                parse_dates=["created_at"],
            )
    except Exception:
        return empty_feedback_df()


def fmt_usd(v: float) -> str:
    return f"${v:.6f}" if v < 0.01 else f"${v:.4f}"


def print_summary(df: pd.DataFrame, fb: pd.DataFrame, from_d: str, to_d: str) -> None:
    print(f"\n{SEP2}")
    print(f"  API Usage Report — ResolveKit")
    print(f"  Period : {from_d}  →  {to_d}")
    print(SEP2)

    if df.empty:
        print("  No API calls recorded in this period.")
        print("  If this is a fresh setup, the api_calls table may not be populated yet.")
        print(f"{SEP2}\n")
        return

    n          = len(df)
    tok_in     = int(df["tokens_in"].sum())
    tok_out    = int(df["tokens_out"].sum())
    total_cost = float(df["cost_usd"].sum())
    avg_lat    = float(df["latency_ms"].mean())
    errors     = int(df["error"].sum())

    print(f"  Total API calls   {n:>10,}")
    print(f"  Tokens in         {tok_in:>10,}")
    print(f"  Tokens out        {tok_out:>10,}")
    print(f"  Total tokens      {tok_in + tok_out:>10,}")
    print(f"  Total cost        {fmt_usd(total_cost):>10}")
    print(f"  Avg latency       {avg_lat:>9.0f}ms")
    print(f"  Avg cost/call     {fmt_usd(total_cost/n):>10}")
    print(f"  Errors            {errors:>10,}")

    if not fb.empty:
        print(SEP)
        total_fb   = len(fb)
        thumbs_up  = (fb["rating"] == "thumbs_up").sum()
        thumbs_dn  = (fb["rating"] == "thumbs_down").sum()
        edits      = fb["email_was_edited"].sum()
        cache_hits = fb["from_cache"].sum()
        print(f"  Feedback records  {total_fb:>10,}")
        print(f"  Thumbs up         {thumbs_up:>10,}")
        print(f"  Thumbs down       {thumbs_dn:>10,}")
        print(f"  Email edits       {edits:>10,}")
        print(f"  Cache hits        {cache_hits:>10,}")

    print(f"{SEP2}\n")


def print_daily(df: pd.DataFrame) -> None:
    if df.empty:
        return

    df = df.copy()
    df["date"] = df["created_at"].dt.date
    daily = df.groupby("date").agg(
        calls     =("model",      "count"),
        tokens_in =("tokens_in",  "sum"),
        tokens_out=("tokens_out", "sum"),
        cost      =("cost_usd",   "sum"),
        lat_avg   =("latency_ms", "mean"),
    ).reset_index().sort_values("date", ascending=False)

    hdr = f"  {'Date':<12}  {'Calls':>6}  {'Tok In':>9}  {'Tok Out':>8}  {'Cost':>11}  {'Avg Lat':>8}"
    print("  Daily Breakdown")
    print(f"  {SEP}")
    print(hdr)
    print(f"  {SEP}")
    for _, r in daily.iterrows():
        print(
            f"  {str(r['date']):<12}  {int(r['calls']):>6}  "
            f"{int(r['tokens_in']):>9,}  {int(r['tokens_out']):>8,}  "
            f"{fmt_usd(r['cost']):>11}  {r['lat_avg']:>7.0f}ms"
        )
    print()


def print_by_model(df: pd.DataFrame) -> None:
    if df.empty:
        return

    by_m = df.groupby(["model", "endpoint"]).agg(
        calls     =("model",      "count"),
        tokens_in =("tokens_in",  "sum"),
        tokens_out=("tokens_out", "sum"),
        cost      =("cost_usd",   "sum"),
    ).reset_index()

    hdr = f"  {'Model':<16}  {'Endpoint':<12}  {'Calls':>6}  {'Tok In':>9}  {'Cost':>11}"
    print("  By Model / Endpoint")
    print(f"  {SEP}")
    print(hdr)
    print(f"  {SEP}")
    for _, r in by_m.iterrows():
        print(
            f"  {r['model']:<16}  {r['endpoint']:<12}  {int(r['calls']):>6}  "
            f"{int(r['tokens_in']):>9,}  {fmt_usd(r['cost']):>11}"
        )
    print()


def make_charts(df: pd.DataFrame, save_path: str) -> None:
    if df.empty:
        print("  No data to chart.\n")
        return

    df = df.copy()
    df["date"] = df["created_at"].dt.date
    daily = df.groupby("date").agg(
        calls     =("model",      "count"),
        tokens_in =("tokens_in",  "sum"),
        tokens_out=("tokens_out", "sum"),
        cost      =("cost_usd",   "sum"),
    ).reset_index()
    daily["date"]            = pd.to_datetime(daily["date"])
    daily                    = daily.sort_values("date")
    daily["cumulative_cost"] = daily["cost"].cumsum()

    # Dark theme matching the app's design
    BG_DEEP  = "#080f1e"
    BG_CARD  = "#132040"
    BORDER   = "#1e3a5f"
    TEXT     = "#c8d8ec"
    BLUE     = "#4A9FE0"
    TEAL     = "#1aaa96"
    ORANGE   = "#f0b429"
    GREEN    = "#4ade80"

    fig, axes = plt.subplots(2, 2, figsize=(14, 8))
    fig.patch.set_facecolor(BG_DEEP)
    fig.suptitle("ResolveKit — API Usage", color=TEXT, fontsize=13, fontweight="bold", y=0.99)

    for ax in axes.flat:
        ax.set_facecolor(BG_CARD)
        ax.tick_params(colors=TEXT, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(BORDER)
        ax.title.set_color(TEXT)
        ax.xaxis.label.set_color(TEXT)
        ax.yaxis.label.set_color(TEXT)

    dates = daily["date"]
    fmt   = mdates.DateFormatter("%b %d")
    loc   = mdates.AutoDateLocator()

    # 1 — Daily cost
    axes[0, 0].bar(dates, daily["cost"], color=BLUE, alpha=0.85, width=0.7)
    axes[0, 0].set_title("Daily Cost (USD)")
    axes[0, 0].yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:.4f}"))
    axes[0, 0].xaxis.set_major_formatter(fmt); axes[0, 0].xaxis.set_major_locator(loc)
    axes[0, 0].tick_params(axis="x", rotation=30)

    # 2 — Daily calls
    axes[0, 1].bar(dates, daily["calls"], color=TEAL, alpha=0.85, width=0.7)
    axes[0, 1].set_title("Daily API Calls")
    axes[0, 1].xaxis.set_major_formatter(fmt); axes[0, 1].xaxis.set_major_locator(loc)
    axes[0, 1].tick_params(axis="x", rotation=30)

    # 3 — Cumulative cost
    axes[1, 0].plot(dates, daily["cumulative_cost"], color=ORANGE, linewidth=2)
    axes[1, 0].fill_between(dates, daily["cumulative_cost"], alpha=0.12, color=ORANGE)
    axes[1, 0].set_title("Cumulative Cost (USD)")
    axes[1, 0].yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:.4f}"))
    axes[1, 0].xaxis.set_major_formatter(fmt); axes[1, 0].xaxis.set_major_locator(loc)
    axes[1, 0].tick_params(axis="x", rotation=30)

    # 4 — Stacked token bars
    axes[1, 1].bar(dates, daily["tokens_in"],  color=BLUE,  alpha=0.8, label="Input",  width=0.7)
    axes[1, 1].bar(dates, daily["tokens_out"], bottom=daily["tokens_in"],
                   color=GREEN, alpha=0.8, label="Output", width=0.7)
    axes[1, 1].set_title("Daily Tokens")
    axes[1, 1].legend(facecolor=BG_CARD, labelcolor=TEXT, fontsize=8, edgecolor=BORDER)
    axes[1, 1].xaxis.set_major_formatter(fmt); axes[1, 1].xaxis.set_major_locator(loc)
    axes[1, 1].tick_params(axis="x", rotation=30)

    plt.tight_layout(rect=[0, 0, 1, 0.97])

    plt.savefig(save_path, dpi=150, bbox_inches="tight", facecolor=BG_DEEP)
    print(f"  Chart saved → {save_path}\n")
    plt.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="ResolveKit — API Usage Viewer")
    parser.add_argument("--from", dest="from_date",   default=None,
                        help="Start date YYYY-MM-DD (default: 30 days ago)")
    parser.add_argument("--to",   dest="to_date",     default=None,
                        help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--no-chart", action="store_true", help="Skip chart generation")
    parser.add_argument("--save-chart", dest="save_chart", default=None,
                        help="Path to save chart PNG (default: diagnostics/usage/report.png)")
    args = parser.parse_args()

    today     = datetime.today().date()
    from_date = args.from_date or str(today - timedelta(days=30))
    to_date   = args.to_date   or str(today)
    from_dt   = from_date + " 00:00:00"
    to_dt     = to_date   + " 23:59:59"

    if not DATABASE_URL:
        print("DATABASE_URL is not set in .env")
        sys.exit(1)

    try:
        df = load_api_calls(from_dt, to_dt)
        fb = load_feedback(from_dt, to_dt)
    except Exception as e:
        print(f"❌ Database error: {e}")
        sys.exit(1)

    print_summary(df, fb, from_date, to_date)
    print_daily(df)
    print_by_model(df)

    if not args.no_chart:
        chart_path = args.save_chart or str(PROJECT_ROOT / "diagnostics" / "usage" / "report.png")
        make_charts(df, chart_path)


if __name__ == "__main__":
    main()
