"""
call_auction.py — A 股集合竞价信号分析模块

分析 9:15-9:25 集合竞价窗口，计算三项核心指标：
  1. 竞价溢价率  (auction_close / prev_close - 1) × 100%
  2. 竞价内部动量 (auction_close / auction_open - 1) × 100%
  3. 竞价量比    auction_vol / avg_daily_vol × 240

Usage:
    python scripts/call_auction.py
    python scripts/call_auction.py --code 688981.SH --date 20260520

API dependency: tushare pro
  stk_auction_o : 集合竞价数据（9:25 揭示价）
  daily         : 日线数据（前收盘 / 历史均量）
"""

import sys
import io

import argparse
import base64
import time
from datetime import datetime, timedelta

import matplotlib
matplotlib.use('Agg')
matplotlib.rcParams.update({
    'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'PingFang SC', 'DejaVu Sans'],
    'axes.unicode_minus': False,
})
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import tushare as ts

pro = ts.pro_api()


# ──────────────────────────────────────────────────────────────────────────────
# Trading calendar helper
# ──────────────────────────────────────────────────────────────────────────────

def _get_prev_trading_date(trade_date: str) -> str:
    """
    Return the most recent trading date strictly before trade_date.
    Uses tushare trade_cal; falls back to crude weekday calendar.
    """
    end_dt = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=20)
    start_str = start_dt.strftime('%Y%m%d')
    prev_str = (end_dt - timedelta(days=1)).strftime('%Y%m%d')

    try:
        cal = pro.trade_cal(exchange='SSE', start_date=start_str,
                            end_date=prev_str, is_open='1')
        dates = sorted(cal['cal_date'].tolist(), reverse=True)
        if dates:
            return dates[0]
    except Exception:
        pass

    # Fallback: walk back skipping weekends
    d = end_dt - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime('%Y%m%d')


# ──────────────────────────────────────────────────────────────────────────────
# Data helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_prev_close(ts_code: str, trade_date: str) -> float | None:
    """
    Return the previous trading day's closing price for ts_code.

    Parameters
    ----------
    ts_code    : tushare stock code, e.g. '688981.SH'
    trade_date : report date YYYYMMDD (the CURRENT session date; we fetch the day before)

    Returns
    -------
    float or None if not found
    """
    prev_date = _get_prev_trading_date(trade_date)
    try:
        df = pro.daily(ts_code=ts_code,
                       start_date=prev_date,
                       end_date=prev_date,
                       fields='ts_code,trade_date,close')
        time.sleep(0.2)
        if df is not None and len(df) > 0:
            return float(df.iloc[0]['close'])
    except Exception as e:
        print(f'  [WARN] get_prev_close({ts_code}, {trade_date}): {e}')
    return None


def _get_avg_daily_vol(ts_code: str, trade_date: str, lookback: int = 20) -> float | None:
    """
    Return the mean daily volume over the past `lookback` trading days
    strictly before trade_date.

    vol in tushare daily is in 手 (100 shares). We keep the unit consistent
    with stk_auction_o which returns vol in 手 as well.
    """
    end_dt = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=int(lookback * 1.8) + 10)
    start_str = start_dt.strftime('%Y%m%d')
    prev_str = _get_prev_trading_date(trade_date)

    try:
        df = pro.daily(ts_code=ts_code,
                       start_date=start_str,
                       end_date=prev_str,
                       fields='ts_code,trade_date,vol')
        time.sleep(0.2)
        if df is None or len(df) == 0:
            return None
        df = df.sort_values('trade_date', ascending=False).head(lookback)
        avg = float(df['vol'].mean())
        return avg if not np.isnan(avg) else None
    except Exception as e:
        print(f'  [WARN] _get_avg_daily_vol({ts_code}, {trade_date}): {e}')
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Chart generation
# ──────────────────────────────────────────────────────────────────────────────

def _gauge_bar(ax, value: float, label: str, unit: str = '%',
               lo: float = -5.0, hi: float = 5.0):
    """
    Draw a horizontal gauge bar centered at 0 on `ax`.

    Positive values → green; negative → red.
    Shows the numerical value as large centred text.
    """
    ax.set_xlim(lo, hi)
    ax.set_ylim(0, 1)

    # Background track
    ax.barh(0.5, hi - lo, left=lo, height=0.32,
            color='#eeeeee', zorder=1)

    # Value bar
    bar_color = '#2ecc71' if value >= 0 else '#e74c3c'
    ax.barh(0.5, value, left=0, height=0.32,
            color=bar_color, zorder=2)

    # Zero line
    ax.axvline(0, color='#555555', linewidth=1.2, zorder=3)

    # Value text
    sign = '+' if value >= 0 else ''
    ax.text(0, 0.85, f'{sign}{value:.2f}{unit}',
            ha='center', va='center',
            fontsize=13, fontweight='bold',
            color=bar_color,
            transform=ax.transData)

    # Axis label
    ax.text(0, 0.10, label,
            ha='center', va='center',
            fontsize=7.5, color='#666666',
            transform=ax.transData)

    # Minimal spine styling
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xticks([lo, lo / 2, 0, hi / 2, hi])
    ax.set_xticklabels([f'{lo:.0f}%', '', '0', '', f'+{hi:.0f}%'],
                       fontsize=6.5, color='#888888')
    ax.set_yticks([])


def _vol_ratio_bar(ax, vol_ratio: float):
    """
    Draw a vertical bar showing 竞价量比.
    Colour thresholds:
      < 0.5      → grey   (light / uncertain)
      0.5 – 1.5  → #3498db (blue / normal)
      1.5 – 3.0  → #e67e22 (orange / heavy)
      > 3.0      → #e74c3c (red / extreme)
    """
    if vol_ratio < 0.5:
        bar_color = '#aaaaaa'
    elif vol_ratio < 1.5:
        bar_color = '#3498db'
    elif vol_ratio < 3.0:
        bar_color = '#e67e22'
    else:
        bar_color = '#e74c3c'

    # Cap display at 4× so the chart stays readable for extreme values
    display_max = max(4.0, vol_ratio * 1.15)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, display_max)

    # Bar
    ax.bar(0.5, vol_ratio, width=0.5, color=bar_color, zorder=2)

    # Normal reference line at 1.0
    ax.axhline(1.0, color='#555555', linewidth=1.0,
               linestyle='--', zorder=3, label='基准 1.0×')

    # High conviction line at 1.5
    ax.axhline(1.5, color='#e67e22', linewidth=0.8,
               linestyle=':', zorder=3, label='偏高 1.5×')

    # Value annotation
    ax.text(0.5, vol_ratio + display_max * 0.04, f'{vol_ratio:.2f}×',
            ha='center', va='bottom',
            fontsize=11, fontweight='bold', color=bar_color)

    ax.text(0.5, -display_max * 0.13, '相对日均成交量',
            ha='center', va='top',
            fontsize=7.5, color='#666666',
            transform=ax.transData)

    ax.set_xticks([])
    ax.set_yticks([0, 1.0, 1.5, 2.0, 3.0])
    ax.set_yticklabels(['0', '1×', '1.5×', '2×', '3×'], fontsize=6.5)
    ax.tick_params(axis='y', length=2, pad=2)
    for spine in ['top', 'right', 'bottom']:
        ax.spines[spine].set_visible(False)
    ax.spines['left'].set_linewidth(0.6)


def _build_chart(ts_code: str, trade_date: str,
                 premium_pct: float,
                 momentum_pct: float,
                 vol_ratio: float,
                 signal: str) -> str:
    """
    Render the three-panel chart and return as base64-encoded PNG string.

    Layout: [竞价溢价率] | [竞价内部动量] | [竞价量比]
    Total size ~560×220 px at 100 dpi.
    """
    fig = plt.figure(figsize=(5.6, 2.2), dpi=100)
    fig.patch.set_facecolor('#fafafa')

    # Title strip
    signal_color = {
        '强势开盘': '#2ecc71',
        '正常开盘': '#3498db',
        '弱势开盘': '#e74c3c',
        '无数据':   '#999999',
    }.get(signal, '#555555')

    short_code = ts_code.split('.')[0]
    title_str = f'{short_code}  集合竞价  {trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}   [{signal}]'
    fig.suptitle(title_str, fontsize=8.5, color='#333333',
                 fontweight='bold', y=0.97,
                 bbox=dict(facecolor=signal_color, alpha=0.12,
                           edgecolor='none', boxstyle='round,pad=0.3'))

    # Three sub-axes: leave top margin for title
    ax1 = fig.add_axes([0.04, 0.12, 0.29, 0.72])
    ax2 = fig.add_axes([0.37, 0.12, 0.29, 0.72])
    ax3 = fig.add_axes([0.72, 0.18, 0.24, 0.62])

    # Panel 1: 竞价溢价率
    lo1 = min(-5.0, premium_pct * 1.4 - 0.1)
    hi1 = max(5.0, premium_pct * 1.4 + 0.1)
    _gauge_bar(ax1, premium_pct, label='竞价溢价率  vs 昨收', lo=lo1, hi=hi1)

    # Panel 2: 竞价内部动量
    lo2 = min(-3.0, momentum_pct * 1.4 - 0.1)
    hi2 = max(3.0, momentum_pct * 1.4 + 0.1)
    _gauge_bar(ax2, momentum_pct, label='竞价内部动量  竞价期间价格变动', lo=lo2, hi=hi2)

    # Panel 3: 竞价量比
    _vol_ratio_bar(ax3, vol_ratio)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', dpi=100,
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('ascii')


# ──────────────────────────────────────────────────────────────────────────────
# Narrative generator
# ──────────────────────────────────────────────────────────────────────────────

def _build_narrative(ts_code: str,
                     premium_pct: float,
                     momentum_pct: float,
                     vol_ratio: float,
                     signal: str) -> str:
    """
    Generate a plain Chinese explanation of the call-auction signal.
    Four main scenarios are handled; edge cases default to a neutral template.
    """
    short_code = ts_code.split('.')[0]
    vol_str = f'{vol_ratio:.1f}倍'
    prem_str = f'{premium_pct:+.2f}%'
    mom_str = f'{momentum_pct:+.2f}%'

    if signal == '无数据':
        return f'【{short_code}】集合竞价数据暂无，无法判断开盘前市场情绪。'

    # ── 强势开盘：溢价 + 正向动量 ──────────────────────────────────
    if premium_pct > 0 and momentum_pct > 0:
        vol_comment = ''
        if vol_ratio >= 1.5:
            vol_comment = (f'竞价量是日均的{vol_str}，'
                           f'资金量确认了方向，而非虚张声势。')
        else:
            vol_comment = (f'竞价量约为日均的{vol_str}，'
                           f'成交量虽不突出，但价格方向已经明确。')
        return (
            f'今日开盘前集合竞价显示：{short_code}以高于昨收{prem_str}的价格开盘，'
            f'竞价期间价格进一步上行{mom_str}——'
            f'这说明市场在开盘前就已经形成强烈的做多共识。'
            f'{vol_comment}'
        )

    # ── 低开高走：溢价为负 + 动量为正 ─────────────────────────────
    if premium_pct < 0 and momentum_pct > 0:
        vol_comment = ''
        if vol_ratio >= 1.5:
            vol_comment = (f'竞价量达到日均的{vol_str}——'
                           f'低开高走往往说明有机构在利用低开机会吸筹，'
                           f'需关注开盘后的走势确认。')
        else:
            vol_comment = (f'但竞价量仅日均的{vol_str}，'
                           f'低开高走力度一般，需观察开盘后是否延续。')
        return (
            f'集合竞价显示{short_code}低开{prem_str}，'
            f'但竞价期间价格从低点回升{mom_str}。'
            f'{vol_comment}'
        )

    # ── 弱势开盘：溢价为负 + 动量为负或平 ─────────────────────────
    if premium_pct < 0:
        if vol_ratio >= 1.5:
            extra = (f'竞价量达到日均的{vol_str}，'
                     f'大量成交在低价完成，卖压不容忽视。')
        else:
            extra = (f'竞价量仅日均的{vol_str}，'
                     f'开盘弱势但暂无恐慌性抛盘迹象。')
        return (
            f'集合竞价显示{short_code}以{prem_str}低开，'
            f'竞价期间价格进一步下移{mom_str}。{extra}'
        )

    # ── 平开 / 正常开盘 ──────────────────────────────────────────
    vol_comment = ''
    if vol_ratio >= 1.5:
        vol_comment = (f'虽然竞价价格基本平稳，但竞价量达日均的{vol_str}，'
                       f'显示多空分歧较大，开盘后方向需密切关注。')
    elif vol_ratio < 0.5:
        vol_comment = (f'竞价量仅日均的{vol_str}，'
                       f'市场参与度低，开盘前方向不明朗。')
    else:
        vol_comment = f'竞价量约日均的{vol_str}，属正常水平。'
    return (
        f'集合竞价显示{short_code}以{prem_str}（接近昨收）开盘，'
        f'竞价期间价格变动{mom_str}。{vol_comment}'
    )


# ──────────────────────────────────────────────────────────────────────────────
# Signal classification
# ──────────────────────────────────────────────────────────────────────────────

def _classify_signal(premium_pct: float, momentum_pct: float,
                     vol_ratio: float) -> str:
    """
    Assign one of four signal labels.

    强势开盘 : premium > 0 AND (momentum > 0 OR vol_ratio >= 1.5)
    弱势开盘 : premium < -0.5 AND momentum <= 0
    正常开盘 : otherwise
    """
    if premium_pct > 0 and (momentum_pct > 0 or vol_ratio >= 1.5):
        return '强势开盘'
    if premium_pct < -0.5 and momentum_pct <= 0:
        return '弱势开盘'
    return '正常开盘'


# ──────────────────────────────────────────────────────────────────────────────
# Main public function
# ──────────────────────────────────────────────────────────────────────────────

def call_auction_signal(ts_code: str, trade_date: str) -> dict:
    """
    Compute the A-share opening call-auction signal for a single stock.

    Parameters
    ----------
    ts_code    : tushare stock code, e.g. '688981.SH' or '000880.SZ'
    trade_date : date of the session to analyse, format YYYYMMDD

    Returns
    -------
    dict with keys:
        ts_code               : str
        trade_date            : str
        auction_price         : float  — 9:25 equilibrium price (matched price)
        prev_close            : float  — previous day close
        premium_pct           : float  — 竞价溢价率 (%)
        intraday_momentum_pct : float  — 竞价内部动量 (%)
        vol_ratio             : float  — 竞价量比 (× daily avg)
        auction_amount_wan    : float  — 竞价成交额 (万元)
        signal                : str    — '强势开盘' / '正常开盘' / '弱势开盘' / '无数据'
        chart_b64             : str    — base64 PNG
        narrative             : str    — plain Chinese explanation
    """
    empty = {
        'ts_code': ts_code,
        'trade_date': trade_date,
        'auction_price': None,
        'prev_close': None,
        'premium_pct': None,
        'intraday_momentum_pct': None,
        'vol_ratio': None,
        'auction_amount_wan': None,
        'signal': '无数据',
        'chart_b64': '',
        'narrative': f'【{ts_code.split(".")[0]}】集合竞价数据暂无，无法判断开盘前市场情绪。',
    }

    # ── Step 1: Fetch call-auction row ────────────────────────────────────────
    try:
        df_auction = pro.stk_auction_o(ts_code=ts_code, trade_date=trade_date)
        time.sleep(0.3)
    except Exception as e:
        print(f'  [WARN] stk_auction_o({ts_code}, {trade_date}): {e}')
        return empty

    if df_auction is None or len(df_auction) == 0:
        print(f'  [INFO] stk_auction_o: no data for {ts_code} on {trade_date}')
        return empty

    row = df_auction.iloc[0]
    auction_close = float(row['close'])   # 9:25 revealed / matched price
    auction_open  = float(row['open'])    # 9:15 first revealed price
    auction_vol   = float(row['vol'])     # 手 (100 shares)
    auction_amt   = float(row['amount'])  # 元 → convert to 万元 below

    auction_amount_wan = round(auction_amt / 1e4, 2)

    # ── Step 2: Previous close ────────────────────────────────────────────────
    prev_close = get_prev_close(ts_code, trade_date)
    if prev_close is None:
        print(f'  [WARN] prev_close not found for {ts_code}; returning 无数据')
        return empty

    # ── Step 3: 20-day average volume ─────────────────────────────────────────
    avg_vol = _get_avg_daily_vol(ts_code, trade_date, lookback=20)
    if avg_vol is None or avg_vol == 0:
        print(f'  [WARN] avg_daily_vol not found for {ts_code}; returning 无数据')
        return empty

    # ── Step 4: Compute metrics ───────────────────────────────────────────────
    #   竞价溢价率
    premium_pct = round((auction_close / prev_close - 1) * 100, 3)

    #   竞价内部动量
    #   guard against auction_open == 0 (shouldn't happen, but be safe)
    if auction_open != 0:
        momentum_pct = round((auction_close / auction_open - 1) * 100, 3)
    else:
        momentum_pct = 0.0

    #   竞价量比: stk_auction_o.vol is in shares; daily.vol is in 手 (100 shares).
    #   Auction = 10 min of a 240-min session → multiply by 24 (not 240) for fair-share.
    vol_ratio = round((auction_vol / 100) / avg_vol * 24, 3)

    # ── Step 5: Signal classification ────────────────────────────────────────
    signal = _classify_signal(premium_pct, momentum_pct, vol_ratio)

    # ── Step 6: Chart ─────────────────────────────────────────────────────────
    chart_b64 = _build_chart(
        ts_code=ts_code,
        trade_date=trade_date,
        premium_pct=premium_pct,
        momentum_pct=momentum_pct,
        vol_ratio=vol_ratio,
        signal=signal,
    )

    # ── Step 7: Narrative ─────────────────────────────────────────────────────
    narrative = _build_narrative(ts_code, premium_pct, momentum_pct,
                                 vol_ratio, signal)

    return {
        'ts_code': ts_code,
        'trade_date': trade_date,
        'auction_price': round(auction_close, 4),
        'prev_close': round(prev_close, 4),
        'premium_pct': premium_pct,
        'intraday_momentum_pct': momentum_pct,
        'vol_ratio': vol_ratio,
        'auction_amount_wan': auction_amount_wan,
        'signal': signal,
        'chart_b64': chart_b64,
        'narrative': narrative,
    }


# ──────────────────────────────────────────────────────────────────────────────
# CLI pretty-printer
# ──────────────────────────────────────────────────────────────────────────────

def _print_result(r: dict) -> None:
    sep = '=' * 58
    print(sep)
    if r['signal'] == '无数据':
        print(f"  {r['ts_code']}  {r['trade_date']}  → 无集合竞价数据")
        print(sep)
        return

    code = r['ts_code']
    print(f"  {code}  集合竞价分析  {r['trade_date']}")
    print(sep)
    print(f"  竞价价格  : {r['auction_price']:.3f}  （昨收 {r['prev_close']:.3f}）")
    print(f"  竞价溢价率: {r['premium_pct']:+.3f}%")
    print(f"  竞价内部动量: {r['intraday_momentum_pct']:+.3f}%")
    print(f"  竞价量比  : {r['vol_ratio']:.3f}×  （竞价成交额 {r['auction_amount_wan']:,.0f} 万元）")
    print(f"  信号      : 【{r['signal']}】")
    print(f"\n  叙事: {r['narrative']}")
    chart_len = len(r['chart_b64'])
    if chart_len > 0:
        print(f"\n  图表: base64 PNG ({chart_len} chars) — 可嵌入 HTML <img> 标签")
    print(sep)


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='A股集合竞价信号分析')
    parser.add_argument('--code', type=str, default=None,
                        help='股票代码，如 688981.SH (留空则运行双股演示)')
    parser.add_argument('--date', type=str, default='20260520',
                        help='交易日期 YYYYMMDD')
    args = parser.parse_args()

    DEMO_STOCKS = [
        ('688981.SH', '中芯国际'),
        ('000880.SZ', '潍柴重机'),
    ]

    if args.code is not None:
        stocks = [(args.code, args.code.split('.')[0])]
    else:
        stocks = DEMO_STOCKS

    print(f'\n集合竞价信号分析  报告日期: {args.date}\n')
    for code, name in stocks:
        print(f'正在分析 {name} ({code})...')
        result = call_auction_signal(code, args.date)
        _print_result(result)
        print()
