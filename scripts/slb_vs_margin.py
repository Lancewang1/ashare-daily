"""
slb_vs_margin.py
================
转融通出借量 vs 融资余额 — 多空博弈双视角

功能：
1. 拉取个股近30日转融通出借余额（slb_len API）+ 融资余额（margin_detail API）
2. 归一化对比，计算多空比（融资/转融通）
3. 识别博弈格局：纯多 / 多强空弱 / 多空均衡 / 空强多弱
4. 生成双线图表 + 散户叙事
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


def _fetch_slb(ts_code: str, end_date: str, lookback_days: int = 50) -> pd.DataFrame:
    end_dt   = datetime.strptime(end_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=lookback_days)
    try:
        df = pro.slb_len(
            ts_code=ts_code,
            start_date=start_dt.strftime('%Y%m%d'),
            end_date=end_date,
        )
        time.sleep(0.2)
    except Exception as e:
        print(f'  [WARN] slb_len: {e}')
        return pd.DataFrame()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.copy()
    df['trade_date'] = df['trade_date'].astype(str)
    # slb_len returns 'len_bal' (出借余额元) or 'slen_bal'
    bal_col = None
    for c in ['slen_bal', 'len_bal', 'fin_bal']:
        if c in df.columns:
            bal_col = c; break
    if bal_col is None:
        # Try first numeric column besides date
        for c in df.columns:
            if c != 'trade_date' and c != 'ts_code':
                try:
                    pd.to_numeric(df[c], errors='raise')
                    bal_col = c; break
                except Exception:
                    pass
    if bal_col is None:
        return pd.DataFrame()
    df['slb_yi'] = pd.to_numeric(df[bal_col], errors='coerce') / 1e8
    return df[['trade_date', 'slb_yi']].dropna().sort_values('trade_date').reset_index(drop=True)


def _fetch_margin(ts_code: str, end_date: str, lookback_days: int = 50) -> pd.DataFrame:
    end_dt   = datetime.strptime(end_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=lookback_days)
    try:
        df = pro.margin_detail(
            ts_code=ts_code,
            start_date=start_dt.strftime('%Y%m%d'),
            end_date=end_date,
        )
        time.sleep(0.2)
    except Exception as e:
        print(f'  [WARN] margin_detail (slb_vs): {e}')
        return pd.DataFrame()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.copy()
    df['trade_date'] = df['trade_date'].astype(str)
    df['margin_yi'] = pd.to_numeric(df['rzye'], errors='coerce') / 1e8
    return df[['trade_date', 'margin_yi']].dropna().sort_values('trade_date').reset_index(drop=True)


def _compute_metrics(slb_df: pd.DataFrame, margin_df: pd.DataFrame) -> dict:
    merged = pd.merge(margin_df, slb_df, on='trade_date', how='inner')
    if len(merged) < 3:
        return {}

    merged = merged.tail(25).reset_index(drop=True)

    margin_cur  = float(merged['margin_yi'].iloc[-1])
    slb_cur     = float(merged['slb_yi'].iloc[-1])
    margin_5d   = float(merged['margin_yi'].iloc[-6]) if len(merged) >= 6 else float(merged['margin_yi'].iloc[0])
    slb_5d      = float(merged['slb_yi'].iloc[-6]) if len(merged) >= 6 else float(merged['slb_yi'].iloc[0])

    margin_chg5 = (margin_cur - margin_5d) / abs(margin_5d) * 100 if margin_5d else 0
    slb_chg5    = (slb_cur - slb_5d) / abs(slb_5d) * 100 if slb_5d else 0

    # Long/short ratio (融资/转融通); if slb very small use floor
    ls_ratio = margin_cur / max(slb_cur, 0.001)

    # Signal
    if slb_cur < 0.01:
        signal = '纯多无空'
    elif ls_ratio > 10:
        signal = '多强空弱'
    elif ls_ratio > 3:
        signal = '多占优势'
    elif ls_ratio > 1:
        signal = '多空均衡'
    else:
        signal = '空强多弱'

    return {
        'merged': merged,
        'margin_cur': round(margin_cur, 4),
        'slb_cur': round(slb_cur, 4),
        'margin_chg5': round(margin_chg5, 2),
        'slb_chg5': round(slb_chg5, 2),
        'ls_ratio': round(ls_ratio, 1),
        'signal': signal,
    }


def _build_chart(metrics: dict, ts_code: str) -> str:
    merged = metrics['merged']
    dates  = merged['trade_date'].tolist()
    margin = merged['margin_yi'].tolist()
    slb    = merged['slb_yi'].tolist()
    n = len(dates)
    x = list(range(n))

    fig, (ax_top, ax_bot) = plt.subplots(
        2, 1, figsize=(8, 3.2),
        gridspec_kw={'height_ratios': [3, 1]},
        facecolor='white'
    )
    fig.subplots_adjust(left=0.09, right=0.97, top=0.82, bottom=0.14, hspace=0.35)

    # ── 上图：双线图 ─────────────────────────────────────────────
    ax1 = ax_top
    ax2 = ax1.twinx()

    l1, = ax1.plot(x, margin, color='#d62728', linewidth=1.6, label='融资余额(亿)', zorder=3)
    ax1.fill_between(x, margin, min(margin)*0.995, color='#d62728', alpha=0.10)

    l2, = ax2.plot(x, slb, color='#1f77b4', linewidth=1.4, linestyle='--',
                   label='转融通出借(亿)', zorder=3)
    ax2.fill_between(x, slb, 0, color='#1f77b4', alpha=0.08)

    ax1.set_ylabel('融资余额 (亿元)', fontsize=6.5, color='#d62728')
    ax2.set_ylabel('转融通出借 (亿元)', fontsize=6.5, color='#1f77b4')
    ax1.tick_params(axis='y', labelsize=6, colors='#d62728')
    ax2.tick_params(axis='y', labelsize=6, colors='#1f77b4')

    step = max(1, n // 5)
    ax1.set_xticks(x[::step])
    ax1.set_xticklabels([dates[i][4:] for i in x[::step]], fontsize=6)
    ax1.set_title(f'{ts_code.split(".")[0]} 多空博弈：融资 vs 转融通', fontsize=8, fontweight='bold', pad=4)
    lines = [l1, l2]
    ax1.legend(lines, [l.get_label() for l in lines], fontsize=6, loc='upper left', framealpha=0.7)
    ax1.spines['top'].set_visible(False)

    # ── 下图：多空比 ─────────────────────────────────────────────
    ls_ratio_series = [m / max(s, 0.001) for m, s in zip(margin, slb)]
    ax_bot.plot(x, ls_ratio_series, color='#9467bd', linewidth=1.2)
    ax_bot.axhline(1.0, color='#888', linestyle='--', linewidth=0.8, alpha=0.7)
    ax_bot.fill_between(x, ls_ratio_series, 1.0,
                        where=[v > 1 for v in ls_ratio_series],
                        color='#d62728', alpha=0.12, label='多占优')
    ax_bot.fill_between(x, ls_ratio_series, 1.0,
                        where=[v <= 1 for v in ls_ratio_series],
                        color='#1f77b4', alpha=0.12, label='空占优')
    ax_bot.set_xticks(x[::step])
    ax_bot.set_xticklabels([dates[i][4:] for i in x[::step]], fontsize=6)
    ax_bot.set_ylabel('多/空比', fontsize=6.5)
    ax_bot.tick_params(axis='both', labelsize=6)
    ax_bot.spines['top'].set_visible(False); ax_bot.spines['right'].set_visible(False)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def _build_narrative(ts_code: str, metrics: dict) -> str:
    code = ts_code.split('.')[0]
    signal   = metrics['signal']
    margin   = metrics['margin_cur']
    slb      = metrics['slb_cur']
    mc5      = metrics['margin_chg5']
    sc5      = metrics['slb_chg5']
    ratio    = metrics['ls_ratio']

    dir_margin = '增加' if mc5 > 0 else '减少'
    dir_slb    = '增加' if sc5 > 0 else '减少'

    if signal in ('纯多无空', '多强空弱'):
        return (
            f"{code}当前融资余额**{margin:.2f}亿元**，近5日{dir_margin}{abs(mc5):.1f}%；"
            f"转融通出借余额**{slb:.3f}亿元**，几乎没有机构在借券做空。"
            f"多空比高达**{ratio:.0f}:1**——场内完全是多头的天下，空头存在感极低。"
            f"纯多格局下股价上涨阻力小，但也要警惕缺乏对手盘时流动性风险。"
        )
    elif signal == '多占优势':
        return (
            f"{code}融资余额**{margin:.2f}亿元**（近5日{dir_margin}{abs(mc5):.1f}%），"
            f"转融通出借**{slb:.2f}亿元**（近5日{dir_margin}{abs(sc5):.1f}%），"
            f"多空比为**{ratio:.1f}:1**。"
            f"多头明显占据优势，但空头也有一定规模——机构之间存在方向分歧。"
            f"融资持续增加而借券不变，是典型的多头加仓信号。"
        )
    elif signal == '多空均衡':
        return (
            f"{code}融资余额**{margin:.2f}亿元**，转融通出借**{slb:.2f}亿元**，"
            f"多空比约**{ratio:.1f}:1**，双方势力相近。"
            f"近5日融资{dir_margin}{abs(mc5):.1f}%，借券{dir_slb}{abs(sc5):.1f}%。"
            f"多空均衡阶段往往是股价震荡整理的区间，方向突破前需等待一方明显占优。"
        )
    else:
        return (
            f"{code}融资余额**{margin:.2f}亿元**，而转融通出借高达**{slb:.2f}亿元**，"
            f"多空比仅**{ratio:.1f}:1**——机构空头力量不容小觑。"
            f"大量借券做空意味着有机构看跌并押注下行，是不可忽视的压力信号。"
            f"需关注是否有催化剂（业绩/政策）能让空头平仓，否则压制可能持续。"
        )


def slb_vs_margin(ts_code: str, trade_date: str) -> dict:
    result = {
        'ts_code': ts_code, 'trade_date': trade_date,
        'margin_cur': 0.0, 'slb_cur': 0.0, 'ls_ratio': 0.0,
        'signal': '无数据', 'chart_b64': '', 'narrative': '', 'error': None,
    }

    slb_df    = _fetch_slb(ts_code, trade_date)
    margin_df = _fetch_margin(ts_code, trade_date)

    if len(slb_df) == 0:
        result['error'] = 'No SLB data'
        result['narrative'] = f'【{ts_code.split(".")[0]}】转融通数据暂无（该股可能不在转融通标的范围内）。'
        return result

    metrics = _compute_metrics(slb_df, margin_df)
    if not metrics:
        result['error'] = 'Insufficient merged data'
        return result

    result.update({
        'margin_cur': metrics['margin_cur'],
        'slb_cur': metrics['slb_cur'],
        'ls_ratio': metrics['ls_ratio'],
        'signal': metrics['signal'],
    })

    print(f'  多空博弈：融资{metrics["margin_cur"]:.2f}亿 / 转融通{metrics["slb_cur"]:.3f}亿 '
          f'比={metrics["ls_ratio"]:.1f}:1 → {metrics["signal"]}')

    try:
        result['chart_b64'] = _build_chart(metrics, ts_code)
    except Exception as e:
        print(f'  [WARN] slb chart: {e}')

    result['narrative'] = _build_narrative(ts_code, metrics)
    return result


if __name__ == '__main__':
    for code in ['688981.SH', '000880.SZ']:
        print(f'\n{"="*50}\n{code}')
        r = slb_vs_margin(code, '20260520')
        print(f'  信号: {r["signal"]}  多空比: {r["ls_ratio"]}:1')
        if r.get('error'):
            print(f'  错误: {r["error"]}')
