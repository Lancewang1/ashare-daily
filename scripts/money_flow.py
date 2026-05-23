"""
money_flow.py
=============
主力资金分时流向分析

功能：
1. 拉取个股近20日每日主力净流入（moneyflow API）
2. 区分超大单/大单（主力）vs 中小单（散户）
3. 计算累计净流入、近5日趋势
4. 信号：主力持续净流入 / 主力出逃 / 震荡分歧
5. 生成条形+累积线图表 + 散户叙事
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


def _fetch_moneyflow(ts_code: str, end_date: str, lookback_days: int = 30) -> pd.DataFrame:
    end_dt   = datetime.strptime(end_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=lookback_days)
    try:
        df = pro.moneyflow(
            ts_code=ts_code,
            start_date=start_dt.strftime('%Y%m%d'),
            end_date=end_date,
        )
        time.sleep(0.2)
    except Exception as e:
        print(f'  [WARN] moneyflow: {e}')
        return pd.DataFrame()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.copy()
    df['trade_date'] = df['trade_date'].astype(str)
    for col in df.columns:
        if col not in ('ts_code', 'trade_date'):
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.sort_values('trade_date').reset_index(drop=True)


def _compute_metrics(df: pd.DataFrame) -> dict:
    if len(df) < 3:
        return {}

    df = df.tail(20).reset_index(drop=True)

    # Main money = 超大单 + 大单 net inflow
    # Fields: buy_elg_vol / sell_elg_vol (超大单), buy_lg_vol / sell_lg_vol (大单)
    # Alternatively: net_mf_amount directly if available, or compute from fields
    # Try net_mf_amount first (直接净流入额)
    if 'net_mf_amount' in df.columns:
        df['main_net_wan'] = df['net_mf_amount']
    else:
        # Compute from buy/sell amounts (万元)
        main_buy  = df.get('buy_elg_amount', 0).fillna(0) + df.get('buy_lg_amount', 0).fillna(0)
        main_sell = df.get('sell_elg_amount', 0).fillna(0) + df.get('sell_lg_amount', 0).fillna(0)
        df['main_net_wan'] = main_buy - main_sell

    # Retail net = small + medium orders
    if 'net_sm_amount' in df.columns:
        df['retail_net_wan'] = df['net_sm_amount'] + df.get('net_md_amount', 0).fillna(0)
    else:
        retail_buy  = df.get('buy_sm_amount', 0).fillna(0) + df.get('buy_md_amount', 0).fillna(0)
        retail_sell = df.get('sell_sm_amount', 0).fillna(0) + df.get('sell_md_amount', 0).fillna(0)
        df['retail_net_wan'] = retail_buy - retail_sell

    main_net_total   = float(df['main_net_wan'].sum())    # 万元
    main_net_5d      = float(df['main_net_wan'].tail(5).sum())
    retail_net_total = float(df['retail_net_wan'].sum())

    # Cumulative
    df['cum_main'] = df['main_net_wan'].cumsum()

    pos_days = int((df['main_net_wan'] > 0).sum())
    neg_days = int((df['main_net_wan'] < 0).sum())

    # Signal
    if main_net_5d > 0 and pos_days >= len(df) * 0.6:
        signal = '主力持续净流入'
    elif main_net_5d < 0 and neg_days >= len(df) * 0.6:
        signal = '主力持续出逃'
    elif main_net_total > 0:
        signal = '主力净流入（震荡）'
    else:
        signal = '主力净流出（震荡）'

    return {
        'df': df,
        'main_net_total_yi': round(main_net_total / 1e4, 4),
        'main_net_5d_wan': round(main_net_5d, 2),
        'retail_net_total_yi': round(retail_net_total / 1e4, 4),
        'pos_days': pos_days,
        'neg_days': neg_days,
        'signal': signal,
    }


def _build_chart(metrics: dict, ts_code: str) -> str:
    df = metrics['df']
    dates = df['trade_date'].tolist()
    main  = df['main_net_wan'].tolist()
    cum   = df['cum_main'].tolist()
    n     = len(dates)
    x     = list(range(n))

    fig, (ax_top, ax_bot) = plt.subplots(
        2, 1, figsize=(8, 3.2),
        gridspec_kw={'height_ratios': [3, 1.5]},
        facecolor='white'
    )
    fig.subplots_adjust(left=0.09, right=0.97, top=0.82, bottom=0.14, hspace=0.4)

    # ── 上图：每日净流入条形 ─────────────────────────────────────
    colors = ['#d62728' if v > 0 else '#1f77b4' for v in main]
    ax_top.bar(x, [v / 1e4 for v in main], color=colors, alpha=0.85, width=0.7)
    ax_top.axhline(0, color='#333', linewidth=0.8)

    step = max(1, n // 5)
    ax_top.set_xticks(x[::step])
    ax_top.set_xticklabels([dates[i][4:] for i in x[::step]], fontsize=6)
    ax_top.set_ylabel('日净流入 (亿元)', fontsize=6.5)
    ax_top.set_title(f'{ts_code.split(".")[0]} 主力资金流向（近{n}日）',
                     fontsize=8, fontweight='bold', pad=4)
    ax_top.spines['top'].set_visible(False); ax_top.spines['right'].set_visible(False)

    in_p  = mpatches.Patch(color='#d62728', alpha=0.7, label='主力净流入')
    out_p = mpatches.Patch(color='#1f77b4', alpha=0.7, label='主力净流出')
    ax_top.legend(handles=[in_p, out_p], fontsize=6, loc='upper left', framealpha=0.7)

    # ── 下图：累积净流入线 ────────────────────────────────────────
    cum_yi = [c / 1e4 for c in cum]
    color_cum = '#d62728' if cum_yi[-1] > 0 else '#1f77b4'
    ax_bot.plot(x, cum_yi, color=color_cum, linewidth=1.5)
    ax_bot.fill_between(x, cum_yi, 0,
                        where=[v > 0 for v in cum_yi], color='#d62728', alpha=0.12)
    ax_bot.fill_between(x, cum_yi, 0,
                        where=[v <= 0 for v in cum_yi], color='#1f77b4', alpha=0.12)
    ax_bot.axhline(0, color='#333', linewidth=0.8)
    ax_bot.set_xticks(x[::step])
    ax_bot.set_xticklabels([dates[i][4:] for i in x[::step]], fontsize=6)
    ax_bot.set_ylabel('累积 (亿元)', fontsize=6.5)
    ax_bot.tick_params(axis='both', labelsize=6)
    ax_bot.spines['top'].set_visible(False); ax_bot.spines['right'].set_visible(False)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def _build_narrative(ts_code: str, metrics: dict) -> str:
    code   = ts_code.split('.')[0]
    signal = metrics['signal']
    total  = metrics['main_net_total_yi']
    n5     = metrics['main_net_5d_wan'] / 1e4   # → 亿
    pos    = metrics['pos_days']
    neg    = metrics['neg_days']

    if '持续净流入' in signal:
        return (
            f"{code}近20日主力资金（超大单+大单）**累计净流入{total:+.2f}亿元**，"
            f"近5日净流入{n5:+.2f}亿，**{pos}天净流入 vs {neg}天净流出**。"
            f"大单是机构、游资和大户的行为代理——他们的钱在悄悄流入，"
            f"而小散（小单+中单）往往是反向指标。持续净流入是较强的趋势确认信号。"
        )
    elif '持续出逃' in signal:
        return (
            f"{code}近20日主力资金**累计净流出{abs(total):.2f}亿元**，"
            f"近5日净流出{abs(n5):.2f}亿，{neg}天流出 vs {pos}天流入。"
            f"主力资金持续出逃而散户接盘，这种格局历史上往往是股价短期顶部的特征。"
            f"需警惕是否有主力出货的迹象。"
        )
    else:
        direction = '净流入' if total > 0 else '净流出'
        return (
            f"{code}近20日主力资金累计{direction}{abs(total):.2f}亿元，"
            f"但{pos}天流入 vs {neg}天流出，方向不稳定。"
            f"震荡分歧格局下，主力自身也在博弈，短期方向不明朗。"
            f"建议等待连续3日以上的方向性信号再判断。"
        )


def money_flow(ts_code: str, trade_date: str, lookback_days: int = 30) -> dict:
    result = {
        'ts_code': ts_code, 'trade_date': trade_date,
        'main_net_total_yi': 0.0,
        'signal': '无数据', 'chart_b64': '', 'narrative': '', 'error': None,
    }

    df = _fetch_moneyflow(ts_code, trade_date, lookback_days=lookback_days)
    if len(df) == 0:
        result['error'] = 'No moneyflow data'
        result['narrative'] = f'【{ts_code.split(".")[0]}】资金流向数据暂无。'
        return result

    metrics = _compute_metrics(df)
    if not metrics:
        result['error'] = 'Insufficient data'
        return result

    result.update({
        'main_net_total_yi': metrics['main_net_total_yi'],
        'signal': metrics['signal'],
    })

    print(f'  主力资金：累计净{metrics["main_net_total_yi"]:+.2f}亿 '
          f'近5日{metrics["main_net_5d_wan"]/1e4:+.2f}亿 → {metrics["signal"]}')

    try:
        result['chart_b64'] = _build_chart(metrics, ts_code)
    except Exception as e:
        print(f'  [WARN] money_flow chart: {e}')

    result['narrative'] = _build_narrative(ts_code, metrics)
    return result


if __name__ == '__main__':
    for code in ['688981.SH', '000880.SZ']:
        print(f'\n{"="*50}\n{code}')
        r = money_flow(code, '20260520')
        print(f'  信号: {r["signal"]}  净额: {r["main_net_total_yi"]:+.2f}亿')
