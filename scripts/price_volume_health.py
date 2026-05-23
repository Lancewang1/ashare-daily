"""
price_volume_health.py
======================
量价健康度分析

功能：
1. 拉取个股近20个交易日日线数据（收盘价 + 成交量）
2. 计算5日/20日涨跌幅、量比（近5日均量 vs 前15日均量）
3. 判断量价健康形态（量价齐升、量缩价涨、放量下跌、缩量回调等）
4. 生成双轴图表（成交量柱 + 收盘价折线）+ 散户叙事
"""

from __future__ import annotations
import sys, io, time, base64
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

import matplotlib
matplotlib.use('Agg')
matplotlib.rcParams.update({
    'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'DejaVu Sans'],
    'axes.unicode_minus': False,
})
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

import tushare as ts
pro = ts.pro_api()


def _fetch_daily(ts_code: str, trade_date: str) -> pd.DataFrame:
    end_dt = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=35)
    try:
        df = pro.daily(
            ts_code=ts_code,
            start_date=start_dt.strftime('%Y%m%d'),
            end_date=trade_date,
            fields='trade_date,close,vol,amount',
        )
        time.sleep(0.2)
    except Exception as e:
        print(f'  [WARN] price_volume_health daily fetch: {e}')
        return pd.DataFrame()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.copy()
    df['trade_date'] = df['trade_date'].astype(str)
    for col in ('close', 'vol', 'amount'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['close', 'vol'])
    df = df.sort_values('trade_date').reset_index(drop=True)
    return df.tail(20).reset_index(drop=True)


def _compute_metrics(df: pd.DataFrame) -> dict:
    if len(df) < 6:
        return {}

    close = df['close'].values
    vol   = df['vol'].values

    # ── 价格变化 ─────────────────────────────────────────────────
    price_chg_5d  = float((close[-1] / close[-6] - 1) * 100)  if len(close) >= 6 else 0.0
    price_chg_20d = float((close[-1] / close[0]  - 1) * 100)

    # ── 量比（近5日 vs 前15日均量）──────────────────────────────
    vol_avg_5d    = float(np.mean(vol[-5:]))
    vol_avg_prev  = float(np.mean(vol[:-5])) if len(vol) > 5 else float(np.mean(vol))
    vol_ratio     = vol_avg_5d / vol_avg_prev if vol_avg_prev > 0 else 1.0

    if vol_ratio > 1.3:
        vol_trend = '放量'
    elif vol_ratio < 0.7:
        vol_trend = '缩量'
    else:
        vol_trend = '平量'

    # ── 量价形态 ─────────────────────────────────────────────────
    price_up = price_chg_5d >= 0
    vol_up   = vol_ratio > 1.0

    if price_up and vol_up:
        pattern = '量价齐升（健康）'
    elif price_up and not vol_up:
        pattern = '量缩价涨（警惕顶部）'
    elif not price_up and vol_up:
        pattern = '放量下跌（抛压）'
    else:
        pattern = '缩量下跌（无人接盘）'

    # ── 信号 ─────────────────────────────────────────────────────
    if pattern == '量价齐升（健康）':
        signal = '量价齐升（健康）'
    elif price_up and price_chg_5d > 5 and vol_ratio < 0.8:
        signal = '量价背离（警惕顶部）'
    elif not price_up and vol_up:
        signal = '放量下跌（抛压）'
    elif not price_up and vol_ratio < 0.7 and price_chg_5d > -3:
        signal = '缩量回调（正常）'
    elif not price_up and vol_ratio < 0.7:
        signal = '缩量下跌（无人接盘）'
    else:
        signal = pattern

    return {
        'df': df,
        'price_chg_5d': round(price_chg_5d, 2),
        'price_chg_20d': round(price_chg_20d, 2),
        'vol_avg_5d': round(vol_avg_5d, 0),
        'vol_avg_prev': round(vol_avg_prev, 0),
        'vol_ratio': round(vol_ratio, 3),
        'vol_trend': vol_trend,
        'pattern': pattern,
        'signal': signal,
    }


def _build_chart(metrics: dict, ts_code: str) -> str:
    df      = metrics['df']
    dates   = df['trade_date'].tolist()
    vol_yi  = (df['vol'].values / 1e8).tolist()   # 手 → 亿股（vol单位是手=100股）
    close   = df['close'].tolist()
    n       = len(dates)
    x       = list(range(n))
    vol_ratio = metrics['vol_ratio']

    fig, ax1 = plt.subplots(figsize=(8, 3.0), facecolor='white')
    fig.subplots_adjust(left=0.09, right=0.91, top=0.84, bottom=0.16)

    # ── 成交量柱状（左轴）────────────────────────────────────────
    bar_colors = []
    for v in vol_yi:
        r = v / (float(np.mean(vol_yi)) if np.mean(vol_yi) > 0 else 1)
        if r > 1.3:
            bar_colors.append('#d62728')   # 放量 → 红
        elif r < 0.7:
            bar_colors.append('#4393c3')   # 缩量 → 蓝
        else:
            bar_colors.append('#999999')   # 平量 → 灰

    bars = ax1.bar(x, vol_yi, color=bar_colors, alpha=0.70, width=0.7, zorder=2)
    ax1.set_ylabel('成交量 (亿手)', fontsize=7, color='#555555')
    ax1.tick_params(axis='y', labelsize=6, colors='#555555')
    ax1.spines['top'].set_visible(False)
    ax1.spines['left'].set_alpha(0.4)

    # ── 收盘价折线（右轴）────────────────────────────────────────
    ax2 = ax1.twinx()
    ax2.plot(x, close, color='#2166ac', linewidth=1.8, zorder=3)
    ax2.set_ylabel('收盘价 (元)', fontsize=7, color='#2166ac')
    ax2.tick_params(axis='y', labelsize=6, colors='#2166ac')
    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_alpha(0.5)

    # ── X轴日期 ───────────────────────────────────────────────────
    step = max(1, n // 5)
    ax1.set_xticks(x[::step])
    ax1.set_xticklabels([dates[i][4:] for i in x[::step]], fontsize=6)
    ax1.spines['bottom'].set_alpha(0.4)

    # ── 标题 & 图例 ────────────────────────────────────────────────
    code_disp = ts_code.split('.')[0]
    ax1.set_title(f'{code_disp} 量价健康度（近{n}日）', fontsize=8.5, fontweight='bold', pad=5)

    p_red  = mpatches.Patch(color='#d62728', alpha=0.7, label='放量(>1.3x)')
    p_blue = mpatches.Patch(color='#4393c3', alpha=0.7, label='缩量(<0.7x)')
    p_gray = mpatches.Patch(color='#999999', alpha=0.7, label='平量')
    p_line = mpatches.Patch(color='#2166ac', alpha=0.9, label='收盘价')
    ax1.legend(handles=[p_red, p_blue, p_gray, p_line],
               fontsize=5.5, loc='upper left', framealpha=0.7, ncol=2)

    # 量比标注
    ax1.text(0.99, 0.97,
             f'量比 {metrics["vol_ratio"]:.2f}x  {metrics["vol_trend"]}',
             transform=ax1.transAxes, ha='right', va='top',
             fontsize=6.5, color='#333333',
             bbox=dict(boxstyle='round,pad=0.3', facecolor='#f5f5f5', alpha=0.8))

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def _build_narrative(ts_code: str, metrics: dict) -> str:
    code     = ts_code.split('.')[0]
    signal   = metrics['signal']
    chg5     = metrics['price_chg_5d']
    chg20    = metrics['price_chg_20d']
    vol_r    = metrics['vol_ratio']
    vol_trend = metrics['vol_trend']
    pattern  = metrics['pattern']

    chg5_str  = f'{chg5:+.1f}%'
    chg20_str = f'{chg20:+.1f}%'
    vol_r_str = f'{vol_r:.2f}倍'

    if '量价齐升' in signal:
        return (
            f"{code}近5日股价{chg5_str}，20日{chg20_str}，"
            f"同期成交量是前15日均量的**{vol_r_str}**（{vol_trend}）。"
            f"**量价齐升是健康上涨的标志**——价格上涨有真实的买单承接，"
            f"说明市场参与者在积极入场，而非只是缺乏卖盘的被动上涨。"
            f"这类走势通常可持续性较强，是技术形态中最受认可的强势信号。"
        )
    elif '背离' in signal or '警惕顶部' in signal:
        return (
            f"{code}近5日股价上涨{chg5_str}，但成交量仅是前期均量的**{vol_r_str}**（{vol_trend}）。"
            f"**「量缩价涨」是潜在的顶部警告信号**——股价在上涨但没有足够的买单跟进，"
            f"这往往意味着上方空间受限，或主力在缩量拉升出货。"
            f"短期可能继续惯性上涨，但建议谨慎追高，关注量能能否有效放大。"
        )
    elif '放量下跌' in signal:
        return (
            f"{code}近5日股价下跌{chg5_str}，但成交量是前期均量的**{vol_r_str}**（{vol_trend}）。"
            f"**「放量下跌」是较强的抛压信号**——大量持有者在主动卖出，"
            f"说明下跌有实质性的抛售驱动，不是随机波动。"
            f"需警惕是否有利空消息或机构出逃，短期建议观望，等待成交量萎缩后再判断方向。"
        )
    elif '缩量回调' in signal:
        return (
            f"{code}近5日小幅下跌{chg5_str}，成交量仅{vol_r_str}（{vol_trend}）。"
            f"**「缩量回调」是健康整理的表现**——下跌没有大量抛盘跟进，"
            f"说明持有者不愿意低价卖出，这往往是主升浪中途的正常蓄力。"
            f"20日涨幅{chg20_str}，整体趋势仍需结合更长周期判断。"
        )
    elif '缩量下跌' in signal:
        return (
            f"{code}近5日股价下跌{chg5_str}，成交量{vol_r_str}（{vol_trend}），20日{chg20_str}。"
            f"**「缩量下跌」通常意味着无人接盘**——买方兴趣低迷，"
            f"股价在自然下滑，既没有大量抛售（也没有大量承接）。"
            f"这种格局往往是熊市或个股基本面走弱时的特征，建议降低仓位或回避。"
        )
    else:
        return (
            f"{code}近5日股价{chg5_str}，20日{chg20_str}。"
            f"成交量相对前期均量为{vol_r_str}（{vol_trend}），"
            f"量价关系信号为「{pattern}」。"
            f"当前量价配合程度有限，建议持续观察量能变化确认趋势。"
        )


def price_volume_health(ts_code: str, trade_date: str) -> dict:
    """
    量价健康度分析。

    Parameters
    ----------
    ts_code    : tushare 股票代码，如 '688981.SH'
    trade_date : 报告日期，格式 'YYYYMMDD'

    Returns
    -------
    dict with keys:
        signal        : str
        chart_b64     : str (base64 PNG)
        narrative     : str
        price_chg_5d  : float
        price_chg_20d : float
        vol_ratio     : float
        vol_trend     : str
        error         : str | None
    """
    result = {
        'ts_code': ts_code,
        'trade_date': trade_date,
        'signal': '无数据',
        'chart_b64': '',
        'narrative': '',
        'price_chg_5d': 0.0,
        'price_chg_20d': 0.0,
        'vol_ratio': 1.0,
        'vol_trend': '平量',
        'error': None,
    }

    df = _fetch_daily(ts_code, trade_date)
    if len(df) == 0:
        result['error'] = 'No daily data returned'
        result['narrative'] = f'【{ts_code.split(".")[0]}】量价数据暂无。'
        return result

    if len(df) < 6:
        result['error'] = f'Insufficient data ({len(df)} rows, need >= 6)'
        return result

    metrics = _compute_metrics(df)
    if not metrics:
        result['error'] = 'Metric computation failed'
        return result

    result.update({
        'signal': metrics['signal'],
        'price_chg_5d': metrics['price_chg_5d'],
        'price_chg_20d': metrics['price_chg_20d'],
        'vol_ratio': metrics['vol_ratio'],
        'vol_trend': metrics['vol_trend'],
    })

    print(f'  量价：5日{metrics["price_chg_5d"]:+.1f}% '
          f'量比{metrics["vol_ratio"]:.2f}x {metrics["vol_trend"]} '
          f'→ {metrics["signal"]}')

    try:
        result['chart_b64'] = _build_chart(metrics, ts_code)
    except Exception as e:
        print(f'  [WARN] price_volume_health chart: {e}')

    result['narrative'] = _build_narrative(ts_code, metrics)
    return result


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    TEST_DATE = '20260520'
    for code in ['688981.SH', '000880.SZ']:
        print(f'\n{"=" * 55}\n{code}')
        r = price_volume_health(code, TEST_DATE)
        if r['error']:
            print(f'  错误: {r["error"]}')
        else:
            print(f'  信号     : {r["signal"]}')
            print(f'  5日涨跌  : {r["price_chg_5d"]:+.2f}%')
            print(f'  20日涨跌 : {r["price_chg_20d"]:+.2f}%')
            print(f'  量比     : {r["vol_ratio"]:.3f}x  {r["vol_trend"]}')
            print(f'  图表     : {"已生成" if r["chart_b64"] else "无"}')
            print(f'  叙事:\n  {r["narrative"]}')
