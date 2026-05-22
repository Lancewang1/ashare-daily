"""
tushare_fetch.py — canonical data fetcher for A-share daily reports.
Replicates Wind data exactly using 30/60 TRADING DAY windows.

Usage:
    python scripts/tushare_fetch.py --date 20260520

Key findings vs Wind:
- Prices (stock + index): identical to Wind at 30/60 trading-day windows
- Quarterly YoY: must use income table manually, NOT fina_indicator.netprofit_yoy
  (fina_indicator uses 归母净利润 which differs from total net income)
- 申万半导体 proxy: 512480.SH ETF (~0.1ppt diff from Wind SW index)
- 申万汽车: no good ETF proxy — use Wind or build from component stocks
"""
import tushare as ts
import pandas as pd
import time
import argparse
import json

pro = ts.pro_api()


def returns_td(df, d30=30, d60=60):
    """Return dict of 30d/60d returns using trading-day windows."""
    df = df.sort_values('trade_date').reset_index(drop=True)
    c = df['close'].values
    n = len(c)
    c_t  = c[-1]
    c_30 = c[-(d30+1)] if n > d30 else c[0]
    c_60 = c[-(d60+1)] if n > d60 else c[0]
    return {
        'close': round(c_t, 4),
        'ret_30d': round((c_t/c_30 - 1)*100, 2),
        'ret_60d': round((c_t/c_60 - 1)*100, 2),
        'anchor_30d': df['trade_date'].iloc[-(d30+1)] if n > d30 else df['trade_date'].iloc[0],
        'anchor_60d': df['trade_date'].iloc[-(d60+1)] if n > d60 else df['trade_date'].iloc[0],
    }


def income_yoy(ts_code, period='20260331'):
    """Compute revenue and net profit YoY from income table (accurate, matches Wind)."""
    year = int(period[:4])
    prev_period = f'{year-1}{period[4:]}'
    df_cur  = pro.income(ts_code=ts_code, period=period,      report_type='1')
    time.sleep(0.3)
    df_prev = pro.income(ts_code=ts_code, period=prev_period, report_type='1')
    time.sleep(0.3)
    if len(df_cur) == 0 or len(df_prev) == 0:
        return None
    r_cur  = df_cur.iloc[0]
    r_prev = df_prev.iloc[0]
    rev_cur  = r_cur.total_revenue
    rev_prev = r_prev.total_revenue
    np_cur   = r_cur.n_income
    np_prev  = r_prev.n_income
    return {
        'period': period,
        'revenue_yoy': round((rev_cur/rev_prev - 1)*100, 2) if rev_prev else None,
        'netprofit_yoy': round((np_cur/np_prev - 1)*100, 2) if np_prev else None,
        'revenue_cur': round(rev_cur/1e8, 2),   # 亿元
        'netprofit_cur': round(np_cur/1e8, 2),
    }


def fetch_all(report_date='20260520', q_period='20260331'):
    """Full data pull for daily report generation."""
    start = '20260101'
    result = {}

    # ── Broad market ─────────────────────────────────────────────────────────
    print('Fetching broad market indices...')
    for code, key in [('000300.SH', 'csi300'), ('000852.SH', 'csi1000')]:
        df = pro.index_daily(ts_code=code, start_date=start, end_date=report_date)
        result[key] = returns_td(df)
        time.sleep(0.2)

    # ── Sector proxies ────────────────────────────────────────────────────────
    print('Fetching sector proxies...')
    # 申万半导体 proxy: 512480.SH (30d within 0.1ppt of Wind SW index)
    df_semi = pro.fund_daily(ts_code='512480.SH', start_date=start, end_date=report_date)
    result['semi_etf'] = returns_td(df_semi)
    result['semi_etf']['name'] = '半导体ETF(512480)'
    result['semi_etf']['note'] = '≈申万半导体, within 0.1ppt'
    time.sleep(0.2)

    # 汽车: no good single ETF proxy — best available is 516110
    df_auto = pro.fund_daily(ts_code='516110.SH', start_date=start, end_date=report_date)
    result['auto_etf'] = returns_td(df_auto)
    result['auto_etf']['name'] = '汽车ETF(516110)'
    result['auto_etf']['note'] = 'APPROXIMATE — differs from SW801880, use Wind for precision'
    time.sleep(0.2)

    # ── Lead-lag stocks: price returns ───────────────────────────────────────
    print('Fetching lead-lag stock prices...')
    ll_stocks = {
        'catl':    '300750.SZ',
        'byd':     '002594.SZ',
        'luxshare': '002475.SZ',
        'naura':   '002371.SZ',
        'smic':    '688981.SH',
        'weichai': '000880.SZ',
    }
    result['stocks'] = {}
    for key, code in ll_stocks.items():
        df = pro.daily(ts_code=code, start_date=start, end_date=report_date)
        result['stocks'][key] = {**returns_td(df), 'ts_code': code}
        time.sleep(0.2)

    # ── Quarterly financials ─────────────────────────────────────────────────
    print(f'Fetching Q1 financials (period={q_period})...')
    result['financials'] = {}
    for key, code in ll_stocks.items():
        fi = income_yoy(code, q_period)
        result['financials'][key] = fi

    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default='20260520', help='Report date YYYYMMDD')
    parser.add_argument('--period', default='20260331', help='Q period YYYYMMDD')
    args = parser.parse_args()

    data = fetch_all(args.date, args.period)

    print('\n' + '='*60)
    print('RESULTS SUMMARY')
    print('='*60)
    print(f'\n沪深300: 30d={data["csi300"]["ret_30d"]:+.2f}%, 60d={data["csi300"]["ret_60d"]:+.2f}%')
    print(f'中证1000: 30d={data["csi1000"]["ret_30d"]:+.2f}%, 60d={data["csi1000"]["ret_60d"]:+.2f}%')
    csi300_30 = data['csi300']['ret_30d']
    print(f'\n半导体ETF(≈申万半导体): 30d={data["semi_etf"]["ret_30d"]:+.2f}% '
          f'(vs CSI300 超额{data["semi_etf"]["ret_30d"]-csi300_30:+.1f}ppt)')
    print(f'汽车ETF(近似申万汽车): 30d={data["auto_etf"]["ret_30d"]:+.2f}% '
          f'(vs CSI300 超额{data["auto_etf"]["ret_30d"]-csi300_30:+.1f}ppt)')

    print('\n股票30d涨跌幅:')
    names = {'catl':'宁电','byd':'比亚迪','luxshare':'立讯','naura':'北华创','smic':'中芯','weichai':'潍柴'}
    for k, n in names.items():
        s = data['stocks'][k]
        print(f'  {n}: 30d={s["ret_30d"]:+.2f}%, 60d={s["ret_60d"]:+.2f}%')

    print(f'\nQ1 {args.period[:4]} 财务:')
    for k, n in names.items():
        f = data['financials'].get(k)
        if f:
            print(f'  {n}: 营收YoY={f["revenue_yoy"]:+.2f}%, 净利YoY={f["netprofit_yoy"]:+.2f}%')

    # Save JSON for downstream use
    out = f'scripts/data_{args.date}.json'
    with open(out, 'w', encoding='utf-8') as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2, default=str)
    print(f'\nSaved to {out}')
