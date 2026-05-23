"""
block_trade_signal.py
=====================
大宗交易折价率分析

功能：
1. 拉取个股近30日大宗交易记录（block_trade API）
2. 计算每笔成交的折价率（vs 当日收盘价）
3. 统计：平均折价率、近5日趋势、买卖方席位特征
4. 信号分类：机构甩货 / 战略接盘 / 正常换手
5. 生成可视化图表 + 散户叙事
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


def _fetch_block_trades(ts_code: str, end_date: str, lookback_days: int = 40) -> pd.DataFrame:
    end_dt  = datetime.strptime(end_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=lookback_days)
    try:
        df = pro.block_trade(
            ts_code=ts_code,
            start_date=start_dt.strftime('%Y%m%d'),
            end_date=end_date,
        )
        time.sleep(0.2)
    except Exception as e:
        print(f'  [WARN] block_trade: {e}')
        return pd.DataFrame()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.copy()
    df['trade_date'] = df['trade_date'].astype(str)
    for col in ['price', 'vol', 'amount']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.sort_values('trade_date').reset_index(drop=True)


def _fetch_close_prices(ts_code: str, start_date: str, end_date: str) -> dict:
    try:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date,
                       fields='trade_date,close')
        time.sleep(0.15)
        if df is None or len(df) == 0:
            return {}
        return dict(zip(df['trade_date'].astype(str), pd.to_numeric(df['close'], errors='coerce')))
    except Exception:
        return {}


def _compute_metrics(bt: pd.DataFrame, close_map: dict) -> dict:
    """Compute discount rates and summary metrics."""
    rows = []
    for _, r in bt.iterrows():
        d = r['trade_date']
        close = close_map.get(d)
        if close and close > 0 and pd.notna(r['price']) and r['price'] > 0:
            discount = (r['price'] / close - 1) * 100   # negative = discount
            rows.append({
                'trade_date': d,
                'price': r['price'],
                'close': close,
                'discount_pct': round(discount, 2),
                'amount_yi': round(r['amount'] / 1e4, 4) if pd.notna(r.get('amount')) else 0.0,
                'buyer': str(r.get('buyer', '') or ''),
                'seller': str(r.get('seller', '') or ''),
            })

    if not rows:
        return {}

    df = pd.DataFrame(rows)
    avg_discount = float(df['discount_pct'].mean())
    recent5 = df[df['trade_date'] >= df['trade_date'].iloc[-1]].head(5) if len(df) >= 2 else df
    avg_recent5 = float(recent5['discount_pct'].mean())
    total_amount = float(df['amount_yi'].sum())
    n_deals = len(df)

    # Signal classification
    if avg_discount < -3:
        signal = '机构甩货'
    elif avg_discount > -0.5:
        signal = '战略接盘'
    else:
        signal = '正常换手'

    return {
        'deals': rows,
        'avg_discount': round(avg_discount, 2),
        'avg_recent5': round(avg_recent5, 2),
        'total_amount_yi': round(total_amount, 4),
        'n_deals': n_deals,
        'signal': signal,
    }


def _build_chart(metrics: dict, ts_code: str) -> str:
    deals = metrics['deals']
    df = pd.DataFrame(deals)

    fig, (ax_left, ax_right) = plt.subplots(
        1, 2, figsize=(8, 2.8),
        gridspec_kw={'width_ratios': [5, 2]},
        facecolor='white'
    )
    fig.subplots_adjust(left=0.07, right=0.97, top=0.80, bottom=0.22, wspace=0.4)

    # ── 左图：折价率时序条形图 ──────────────────────────────────
    dates = df['trade_date'].tolist()
    discounts = df['discount_pct'].tolist()
    amounts = df['amount_yi'].tolist()
    x = list(range(len(dates)))

    bar_colors = ['#d62728' if d < -2 else ('#ff7f0e' if d < -0.5 else '#2ca02c')
                  for d in discounts]
    ax_left.bar(x, discounts, color=bar_colors, alpha=0.85, width=0.7)
    ax_left.axhline(0, color='#333', linewidth=0.8, linestyle='--', alpha=0.6)
    ax_left.axhline(metrics['avg_discount'], color='#1f77b4', linewidth=1.2,
                    linestyle=':', alpha=0.9, label=f'均值{metrics["avg_discount"]:+.1f}%')

    # Amount annotation on tallest bars
    for i, (d, a) in enumerate(zip(discounts, amounts)):
        if a > 0.05:
            ax_left.text(i, d - 0.15 if d < 0 else d + 0.1,
                         f'{a:.2f}亿', ha='center', va='top' if d < 0 else 'bottom',
                         fontsize=5.5, color='#333')

    step = max(1, len(dates) // 5)
    ax_left.set_xticks(x[::step])
    ax_left.set_xticklabels([d[4:] for d in dates[::step]], fontsize=6, rotation=30)
    ax_left.set_ylabel('折价率 (%)', fontsize=6.5)
    ax_left.set_title(f'{ts_code.split(".")[0]} 大宗交易折价率', fontsize=8, fontweight='bold', pad=4)
    ax_left.legend(fontsize=6, loc='lower left', framealpha=0.6)
    ax_left.spines['top'].set_visible(False)
    ax_left.spines['right'].set_visible(False)

    # ── 右图：信号仪表 ──────────────────────────────────────────
    signal = metrics['signal']
    color_map = {'机构甩货': '#d62728', '战略接盘': '#2ca02c', '正常换手': '#ff7f0e'}
    sig_color = color_map.get(signal, '#888')

    ax_right.set_xlim(0, 1); ax_right.set_ylim(0, 1)
    ax_right.add_patch(mpatches.FancyBboxPatch(
        (0.05, 0.35), 0.9, 0.3, boxstyle='round,pad=0.05',
        facecolor=sig_color, alpha=0.15, edgecolor=sig_color, linewidth=1.5))
    ax_right.text(0.5, 0.50, signal, ha='center', va='center',
                  fontsize=12, fontweight='bold', color=sig_color)
    ax_right.text(0.5, 0.80, f'{metrics["n_deals"]}笔  共{metrics["total_amount_yi"]:.2f}亿',
                  ha='center', va='center', fontsize=7, color='#555')
    ax_right.text(0.5, 0.18, f'均值折价 {metrics["avg_discount"]:+.1f}%',
                  ha='center', va='center', fontsize=7.5, color=sig_color, fontweight='bold')
    ax_right.set_title('信号判断', fontsize=8, fontweight='bold', pad=4)
    ax_right.axis('off')

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def _build_narrative(ts_code: str, metrics: dict) -> str:
    code = ts_code.split('.')[0]
    signal = metrics['signal']
    avg_d = metrics['avg_discount']
    n = metrics['n_deals']
    amt = metrics['total_amount_yi']

    if signal == '机构甩货':
        return (
            f"近30日内{code}累计发生**{n}笔大宗交易**，合计{amt:.2f}亿元，"
            f"平均折价率**{avg_d:+.1f}%**——卖方在大幅让利求快速成交。"
            f"大折价往往意味着大股东或机构急于退出，而非正常换手。"
            f"需警惕解禁套现或机构降低仓位，这类压制往往会持续到大宗交易消化完毕。"
        )
    elif signal == '战略接盘':
        return (
            f"近30日内{code}累计发生**{n}笔大宗交易**，合计{amt:.2f}亿元，"
            f"平均折价率仅**{avg_d:+.1f}%**（接近市价甚至溢价）。"
            f"以接近全价买入的机构通常是战略配置需求，而非短线投机。"
            f"大宗买家锁定成本与二级市场相近，说明他们对中长期持有有信心。"
        )
    else:
        return (
            f"近30日内{code}发生**{n}笔大宗交易**，合计{amt:.2f}亿元，"
            f"平均折价**{avg_d:+.1f}%**，属正常换手区间。"
            f"大宗交易是机构之间的场外协议转让，折价率是衡量卖方急迫程度的温度计。"
            f"当前折价幅度温和，暂无明显甩货或抢筹信号。"
        )


def block_trade_signal(ts_code: str, trade_date: str, lookback_days: int = 30) -> dict:
    result = {
        'ts_code': ts_code, 'trade_date': trade_date,
        'n_deals': 0, 'avg_discount': 0.0, 'total_amount_yi': 0.0,
        'signal': '无数据', 'chart_b64': '', 'narrative': '', 'error': None,
    }
    bt = _fetch_block_trades(ts_code, trade_date, lookback_days=lookback_days + 10)
    if len(bt) == 0:
        result['narrative'] = f'【{ts_code.split(".")[0]}】近{lookback_days}日无大宗交易记录。'
        return result

    end_dt   = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=lookback_days + 10)
    close_map = _fetch_close_prices(ts_code, start_dt.strftime('%Y%m%d'), trade_date)

    metrics = _compute_metrics(bt, close_map)
    if not metrics:
        result['narrative'] = f'【{ts_code.split(".")[0]}】大宗交易数据不完整，无法计算折价率。'
        return result

    result.update({
        'n_deals': metrics['n_deals'],
        'avg_discount': metrics['avg_discount'],
        'total_amount_yi': metrics['total_amount_yi'],
        'signal': metrics['signal'],
    })

    print(f'  大宗交易：{metrics["n_deals"]}笔 均值折价{metrics["avg_discount"]:+.1f}% '
          f'合计{metrics["total_amount_yi"]:.2f}亿 → {metrics["signal"]}')

    try:
        result['chart_b64'] = _build_chart(metrics, ts_code)
    except Exception as e:
        print(f'  [WARN] block_trade chart: {e}')

    result['narrative'] = _build_narrative(ts_code, metrics)
    return result


if __name__ == '__main__':
    for code in ['688981.SH', '000880.SZ']:
        print(f'\n{"="*50}\n{code}')
        r = block_trade_signal(code, '20260520')
        print(f'  信号: {r["signal"]}  折价: {r["avg_discount"]:+.1f}%  笔数: {r["n_deals"]}')
        print(f'  叙事: {r["narrative"][:80]}...')
