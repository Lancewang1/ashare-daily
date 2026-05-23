"""
billboard.py — 龙虎榜机构席位解析，供 A 股个股日报使用。

Usage:
    python scripts/billboard.py --code 000880.SZ --date 20260520
    python scripts/billboard.py --code 688981.SH --date 20260520

API dependency: tushare pro (top_list + top_inst)
    top_list  : 龙虎榜股票列表，按交易日查询
    top_inst  : 龙虎榜席位明细，每席位一行
                side='0' → 该席位出现在买入 Top 榜
                side='1' → 该席位出现在卖出 Top 榜
                同一席位可同时出现在两个 side（各持一行），需按 side 分别处理
"""
import sys
import io

# Ensure UTF-8 output on Windows
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import tushare as ts
import pandas as pd
import time
import argparse
from datetime import datetime, timedelta

pro = ts.pro_api()

# ──────────────────────────────────────────────────────────────────────────────
# Seat classification helpers
# ──────────────────────────────────────────────────────────────────────────────

# Seats treated as institutional money (not retail broker seats)
INST_KEYWORDS = ['机构专用', '机构', '沪股通专用', '深股通专用', '沪股通', '深股通']
# Aggregate/investor-type categories that appear in STAR Market top_inst
# (These are NOT individual seat names but investor-class aggregates)
AGG_KEYWORDS = ['自然人', '中小投资者', '其他自然人']


def classify_seat(exalter: str) -> str:
    """
    Classify a seat name into one of three categories:
      'inst'   — 机构专用席位 or Stock Connect (沪/深股通)
      'agg'    — 聚合统计行 (自然人 / 中小投资者 etc., not actionable)
      'retail' — 具体营业部 (游资 / 散户 brokerage seat)
    """
    if pd.isna(exalter):
        return 'agg'
    ex = str(exalter).strip()
    for kw in AGG_KEYWORDS:
        if ex == kw:
            return 'agg'
    for kw in INST_KEYWORDS:
        if kw in ex:
            return 'inst'
    return 'retail'


# ──────────────────────────────────────────────────────────────────────────────
# Trading calendar helper
# ──────────────────────────────────────────────────────────────────────────────

def get_recent_trading_dates(end_date: str, n: int = 10) -> list:
    """
    Return up to n recent trading dates (YYYYMMDD strings) ending on or before
    end_date, by pulling tushare trade_cal.
    """
    # Parse end_date and compute a start window (go back ~3× calendar days)
    end_dt = datetime.strptime(end_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=n * 3)
    start_str = start_dt.strftime('%Y%m%d')

    try:
        cal = pro.trade_cal(exchange='SSE', start_date=start_str, end_date=end_date,
                            is_open='1')
        dates = sorted(cal['cal_date'].tolist(), reverse=True)
        return dates[:n]
    except Exception:
        # Fallback: crude calendar (skip weekends; misses holidays)
        dates = []
        d = end_dt
        while len(dates) < n:
            if d.weekday() < 5:  # Mon–Fri
                dates.append(d.strftime('%Y%m%d'))
            d -= timedelta(days=1)
        return dates


# ──────────────────────────────────────────────────────────────────────────────
# Core query functions
# ──────────────────────────────────────────────────────────────────────────────

def query_top_list(trade_date: str, ts_code: str = None) -> pd.DataFrame:
    """Pull top_list for a date, optionally filtering to one stock."""
    kwargs = dict(trade_date=trade_date, limit=5000)
    if ts_code:
        kwargs['ts_code'] = ts_code
    try:
        df = pro.top_list(**kwargs)
        time.sleep(0.3)
        return df if df is not None else pd.DataFrame()
    except Exception:
        time.sleep(0.5)
        return pd.DataFrame()


def query_top_inst(trade_date: str, ts_code: str = None) -> pd.DataFrame:
    """Pull top_inst seat details for a date, optionally filtering to one stock."""
    kwargs = dict(trade_date=trade_date, limit=5000)
    if ts_code:
        kwargs['ts_code'] = ts_code
    try:
        df = pro.top_inst(**kwargs)
        time.sleep(0.3)
        return df if df is not None else pd.DataFrame()
    except Exception:
        time.sleep(0.5)
        return pd.DataFrame()


# ──────────────────────────────────────────────────────────────────────────────
# Main analysis function
# ──────────────────────────────────────────────────────────────────────────────

def parse_seat_details(df_inst: pd.DataFrame) -> dict:
    """
    Parse top_inst rows for a single stock into structured metrics.

    top_inst data model (verified against real data):
      side='0'  → this seat is in the TOP BUYERS list (buy value meaningful)
      side='1'  → this seat is in the TOP SELLERS list (sell value meaningful)

    Dedup rule (confirmed from 000880 raw data):
      Some seats appear in BOTH side=0 AND side=1 with IDENTICAL (exalter, buy, sell).
      These represent a single institution that both bought and sold heavily, appearing on
      both lists. Count once using their net_buy (buy − sell).
      Unique side=0-only rows: institutions on buy list only.
      Unique side=1-only rows: institutions on sell list only.

    For '机构专用': multiple rows with the same name but DIFFERENT buy/sell values are
    DISTINCT institutions using the 机构专用 channel. They are NOT duplicates.

    Aggregate rows ('自然人', '中小投资者', etc.) are investor-class subtotals — excluded.

    Returns dict with inst / retail / raw seat list metrics.
    """
    if df_inst is None or len(df_inst) == 0:
        return {
            'inst_net_buy': 0.0,
            'retail_net_buy': 0.0,
            'inst_seat_count': 0,
            'retail_seat_count': 0,
            'top_buyers': [],
            'top_sellers': [],
            'all_seats': [],
        }

    df = df_inst.copy()
    df['side'] = df['side'].astype(str).str.strip()
    df['buy'] = pd.to_numeric(df['buy'], errors='coerce').fillna(0.0)
    df['sell'] = pd.to_numeric(df['sell'], errors='coerce').fillna(0.0)
    df['net_buy'] = pd.to_numeric(df['net_buy'], errors='coerce').fillna(0.0)
    df['seat_type'] = df['exalter'].apply(classify_seat)

    # Exclude investor-class aggregate rows (not individual seats)
    df_seats = df[df['seat_type'] != 'agg'].copy().reset_index(drop=True)

    # ── Deduplicate cross-side duplicates ────────────────────────────────────
    # A row appearing in both side=0 AND side=1 with the same (exalter, buy, sell)
    # is the SAME seat listed on both lists. Keep only one copy (side=0 preferred).
    side0 = df_seats[df_seats['side'] == '0'].copy()
    side1 = df_seats[df_seats['side'] == '1'].copy()

    # Find rows that appear identically in both sides
    key_cols = ['exalter', 'buy', 'sell']
    side0_keys = set(zip(side0['exalter'], side0['buy'].round(0), side0['sell'].round(0)))
    side1_keys = set(zip(side1['exalter'], side1['buy'].round(0), side1['sell'].round(0)))
    dup_keys = side0_keys & side1_keys  # exact matches in both sides

    # Build deduplicated seat list:
    # 1. All side=0 rows (kept as-is; they have the full buy+sell data)
    # 2. Side=1 rows that are NOT exact duplicates of any side=0 row
    side1_unique = side1[
        ~side1.apply(
            lambda r: (r['exalter'], round(r['buy'], 0), round(r['sell'], 0)) in dup_keys,
            axis=1
        )
    ].copy()

    dedup_seats = pd.concat([side0, side1_unique], ignore_index=True)

    # ── Inst vs retail split ─────────────────────────────────────────────────
    inst_df = dedup_seats[dedup_seats['seat_type'] == 'inst']
    retail_df = dedup_seats[dedup_seats['seat_type'] == 'retail']

    inst_net = inst_df['net_buy'].sum() / 1e4       # convert to 万元
    retail_net = retail_df['net_buy'].sum() / 1e4

    inst_count = len(inst_df)
    retail_count = len(retail_df)

    # ── Top buyers / sellers (from raw side-specific lists, dedup-aware) ─────
    # For display: top buyers = side=0 rows sorted by buy desc
    # For display: top sellers = side=1 rows sorted by sell desc (can include dup seats)
    all_buy_seats = side0.sort_values('buy', ascending=False)
    all_sell_seats = df_seats[df_seats['side'] == '1'].sort_values('sell', ascending=False)

    top_buyers = []
    for _, row in all_buy_seats.head(3).iterrows():
        top_buyers.append({
            'name': row['exalter'],
            'buy_wan': round(row['buy'] / 1e4, 0),
            'seat_type': row['seat_type'],
        })

    top_sellers = []
    for _, row in all_sell_seats.head(3).iterrows():
        top_sellers.append({
            'name': row['exalter'],
            'sell_wan': round(row['sell'] / 1e4, 0),
            'seat_type': row['seat_type'],
        })

    # ── All seats summary (deduplicated) ────────────────────────────────────
    all_seats = []
    for _, row in dedup_seats.sort_values('net_buy', ascending=False).iterrows():
        all_seats.append({
            'name': row['exalter'],
            'seat_type': row['seat_type'],
            'buy_wan': round(row['buy'] / 1e4, 0),
            'sell_wan': round(row['sell'] / 1e4, 0),
            'net_wan': round(row['net_buy'] / 1e4, 0),
        })

    return {
        'inst_net_buy': round(inst_net, 0),
        'retail_net_buy': round(retail_net, 0),
        'inst_seat_count': inst_count,
        'retail_seat_count': retail_count,
        'top_buyers': top_buyers,
        'top_sellers': top_sellers,
        'all_seats': all_seats,
    }


def build_narrative(result: dict) -> str:
    """Build a one-sentence Chinese narrative for the daily report."""
    if not result['on_billboard']:
        return '未上龙虎榜（换手率/涨幅未达阈值）'

    inst_net = result['inst_net_buy']
    retail_net = result['retail_net_buy']

    # Inst direction
    if inst_net > 0:
        inst_str = f'机构专用席位净买入+{inst_net:.0f}万'
    elif inst_net < 0:
        inst_str = f'机构专用席位净卖出{inst_net:.0f}万'
    else:
        inst_str = '机构专用席位净平'

    # Retail direction
    if retail_net > 0:
        retail_str = f'游资营业部合计净买入+{retail_net:.0f}万'
    elif retail_net < 0:
        retail_str = f'游资营业部合计净卖出{retail_net:.0f}万'
    else:
        retail_str = '游资营业部净平'

    # Interpretation
    if inst_net > 0 and retail_net < 0:
        interp = '机构在接筹，游资在出货'
    elif inst_net < 0 and retail_net > 0:
        interp = '机构在出货，游资在接盘'
    elif inst_net > 0 and retail_net > 0:
        interp = '机构游资同向净买入，多头合力'
    elif inst_net < 0 and retail_net < 0:
        interp = '机构游资同向净卖出，空头合力'
    else:
        interp = '席位方向分歧，多空平衡'

    return f'龙虎榜{inst_str}，{retail_str}——{interp}'


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def get_billboard_summary(ts_code: str, trade_date: str, lookback_days: int = 5) -> dict:
    """
    查询股票龙虎榜数据，返回结构化摘要。

    Parameters
    ----------
    ts_code      : tushare 股票代码，如 '688981.SH', '000880.SZ'
    trade_date   : 报告日期，格式 YYYYMMDD（优先查此日；未上榜则向前回溯）
    lookback_days: 未上榜时向前回溯的最大交易日数

    Returns
    -------
    dict with keys:
        on_billboard  : bool — 是否在回溯窗口内上过榜
        billboard_date: str  — 上榜日期（YYYYMMDD）
        reason        : str  — 上榜原因（来自交易所公告）
        inst_net_buy  : float — 机构专用席位净买入（万元），正=净买入
        retail_net_buy: float — 游资营业部净买入（万元）
        inst_seat_count : int — 机构类席位数量
        retail_seat_count: int — 游资营业部席位数量
        top_buyers    : list[dict] — Top3买入席位 name/buy_wan/seat_type
        top_sellers   : list[dict] — Top3卖出席位 name/sell_wan/seat_type
        all_seats     : list[dict] — 所有席位汇总
        narrative     : str — 可直接插入报告的一句话叙事
    """
    # Get recent trading dates to search
    search_dates = get_recent_trading_dates(trade_date, n=lookback_days + 1)
    # Ensure the specified trade_date is first
    if trade_date in search_dates:
        search_dates.remove(trade_date)
    search_dates = [trade_date] + search_dates
    search_dates = list(dict.fromkeys(search_dates))[:lookback_days + 1]

    empty_result = {
        'on_billboard': False,
        'billboard_date': None,
        'reason': None,
        'inst_net_buy': 0.0,
        'retail_net_buy': 0.0,
        'inst_seat_count': 0,
        'retail_seat_count': 0,
        'top_buyers': [],
        'top_sellers': [],
        'all_seats': [],
        'narrative': '未上龙虎榜（换手率/涨幅未达阈值）',
    }

    for d in search_dates:
        # Query top_list for this stock on this date
        df_tl = query_top_list(d, ts_code=ts_code)

        if len(df_tl) == 0:
            # If direct query returns nothing, try full day pull and filter
            # (some tushare subscriptions require the full-day pull)
            df_full = query_top_list(d)
            df_tl = df_full[df_full['ts_code'] == ts_code] if len(df_full) > 0 else pd.DataFrame()

        if len(df_tl) == 0:
            continue  # Not on billboard this date

        # Found on billboard
        reason = df_tl['reason'].iloc[0] if 'reason' in df_tl.columns else ''

        # Query seat details
        df_inst = query_top_inst(d, ts_code=ts_code)
        if len(df_inst) == 0:
            # Fallback: pull full day and filter
            df_inst_all = query_top_inst(d)
            df_inst = df_inst_all[df_inst_all['ts_code'] == ts_code] if len(df_inst_all) > 0 else pd.DataFrame()

        seat_data = parse_seat_details(df_inst)

        result = {
            'on_billboard': True,
            'billboard_date': d,
            'reason': reason,
            **seat_data,
        }
        result['narrative'] = build_narrative(result)
        return result

    return empty_result


# ──────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────────────────────

def _fmt_wan(val: float) -> str:
    """Format 万元 value with sign."""
    if val >= 0:
        return f'+{val:,.0f}万'
    return f'{val:,.0f}万'


def print_summary(ts_code: str, name: str, result: dict) -> None:
    sep = '=' * 55
    print(sep)
    print(f'  {name} ({ts_code})')
    print(sep)
    if not result['on_billboard']:
        print('  未上龙虎榜（近5个交易日内换手率/涨幅均未达阈值）')
        print(sep)
        return

    print(f'  上榜日期  : {result["billboard_date"]}')
    print(f'  上榜原因  : {result["reason"]}')
    print(f'  机构席位净买入: {_fmt_wan(result["inst_net_buy"])}  '
          f'（{result["inst_seat_count"]} 个席位）')
    print(f'  游资席位净买入: {_fmt_wan(result["retail_net_buy"])}  '
          f'（{result["retail_seat_count"]} 个席位）')

    if result['top_buyers']:
        print('\n  Top 买入席位:')
        for i, s in enumerate(result['top_buyers'], 1):
            tag = '[机构]' if s['seat_type'] == 'inst' else '[游资]'
            print(f'    {i}. {s["name"]} {tag}  买入{_fmt_wan(s["buy_wan"])}')

    if result['top_sellers']:
        print('\n  Top 卖出席位:')
        for i, s in enumerate(result['top_sellers'], 1):
            tag = '[机构]' if s['seat_type'] == 'inst' else '[游资]'
            print(f'    {i}. {s["name"]} {tag}  卖出{_fmt_wan(s["sell_wan"])}')

    print(f'\n  叙事: "{result["narrative"]}"')
    print(sep)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='龙虎榜机构席位解析')
    parser.add_argument('--code', type=str, help='股票代码，如 688981.SH')
    parser.add_argument('--date', type=str, default='20260520', help='报告日期 YYYYMMDD')
    parser.add_argument('--lookback', type=int, default=5, help='回溯交易日数')
    parser.add_argument('--demo', action='store_true', help='运行两只目标股演示')
    args = parser.parse_args()

    DEMO_STOCKS = {
        '688981.SH': '中芯国际',
        '000880.SZ': '潍柴重机',
    }

    if args.demo or args.code is None:
        print(f'\n报告日期: {args.date}  回溯交易日: {args.lookback}\n')
        for code, name in DEMO_STOCKS.items():
            result = get_billboard_summary(code, args.date, lookback_days=args.lookback)
            print_summary(code, name, result)
            print()
    else:
        # Single stock
        name = args.code
        result = get_billboard_summary(args.code, args.date, lookback_days=args.lookback)
        print_summary(args.code, name, result)
