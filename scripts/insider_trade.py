"""
insider_trade.py
================
股东增减持追踪

功能：
1. 拉取个股近90日股东增减持记录（stk_holdertrade API）
2. 按方向统计净变动金额、笔数，区分高管/大股东
3. 信号分类：高管净增持 / 高管净减持 / 仅大股东减持 / 无动作
4. 生成瀑布图 + 散户叙事
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


def _fetch_insider_trades(ts_code: str, end_date: str, lookback_days: int = 90) -> pd.DataFrame:
    end_dt   = datetime.strptime(end_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=lookback_days)
    try:
        df = pro.stk_holdertrade(
            ts_code=ts_code,
            start_date=start_dt.strftime('%Y%m%d'),
            end_date=end_date,
        )
        time.sleep(0.2)
    except Exception as e:
        print(f'  [WARN] stk_holdertrade: {e}')
        return pd.DataFrame()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.copy()
    for col in ['ann_date', 'in_date', 'out_date']:
        if col in df.columns:
            df[col] = df[col].astype(str)
    for col in ['change_vol', 'change_ratio', 'avg_price', 'total_share', 'begin_share', 'after_share']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    # Use ann_date as primary sort date
    df['date'] = df.get('ann_date', df.get('in_date', pd.Series([''] * len(df))))
    df = df.sort_values('date').reset_index(drop=True)
    return df


def _compute_metrics(df: pd.DataFrame) -> dict:
    if len(df) == 0:
        return {}

    # Determine direction from 'in_de' column (增持/减持) or change_vol sign
    if 'in_de' in df.columns:
        df['is_buy'] = df['in_de'].astype(str).str.contains('增|买|IN', case=False, na=False)
    else:
        df['is_buy'] = df.get('change_vol', 0) > 0

    # Estimate amount: avg_price * change_vol (shares) / 1e8
    if 'avg_price' in df.columns and 'change_vol' in df.columns:
        df['amount_wan'] = (df['avg_price'].fillna(0) * df['change_vol'].abs().fillna(0)) / 1e4
    else:
        df['amount_wan'] = 0.0

    buys  = df[df['is_buy']]
    sells = df[~df['is_buy']]

    buy_amt  = float(buys['amount_wan'].sum()) / 1e4    # → 亿元
    sell_amt = float(sells['amount_wan'].sum()) / 1e4
    net_amt  = buy_amt - sell_amt

    n_buy  = len(buys)
    n_sell = len(sells)

    # Is it executives (高管) or major shareholders (大股东)?
    # holder_type or holder_name heuristic
    exec_keywords = ['董事', '监事', '高管', '总经理', '总裁', '副总', '财务', '秘书', 'CEO', 'CFO']

    def is_exec(row):
        name = str(row.get('holder_name', '') or '')
        htype = str(row.get('holder_type', '') or '')
        return any(k in name or k in htype for k in exec_keywords)

    df['is_exec'] = df.apply(is_exec, axis=1)
    exec_df   = df[df['is_exec']]
    exec_net  = float(exec_df[exec_df['is_buy']]['amount_wan'].sum() -
                      exec_df[~exec_df['is_buy']]['amount_wan'].sum()) / 1e4

    # Signal
    if n_buy == 0 and n_sell == 0:
        signal = '近期无增减持'
    elif net_amt > 0 and n_buy > 0:
        signal = '高管净增持' if exec_net > 0 else '大股东净增持'
    else:
        signal = '高管净减持' if exec_net < 0 and len(exec_df) > 0 else '大股东减持'

    return {
        'df': df,
        'n_buy': n_buy, 'n_sell': n_sell,
        'buy_amt': round(buy_amt, 4),
        'sell_amt': round(sell_amt, 4),
        'net_amt': round(net_amt, 4),
        'exec_net': round(exec_net, 4),
        'signal': signal,
    }


def _build_chart(metrics: dict, ts_code: str) -> str:
    df = metrics['df']

    # Group by month for bar chart
    df2 = df.copy()
    df2['month'] = df2['date'].str[:7]
    df2['signed_amt'] = df2.apply(
        lambda r: r['amount_wan'] / 1e4 if r['is_buy'] else -r['amount_wan'] / 1e4, axis=1)
    monthly = df2.groupby('month')['signed_amt'].sum().reset_index()

    fig, (ax_left, ax_right) = plt.subplots(
        1, 2, figsize=(8, 2.8),
        gridspec_kw={'width_ratios': [3, 1]},
        facecolor='white'
    )
    fig.subplots_adjust(left=0.07, right=0.97, top=0.80, bottom=0.22, wspace=0.4)

    # ── 左图：月度净变动柱 ──────────────────────────────────────
    x = list(range(len(monthly)))
    vals = monthly['signed_amt'].tolist()
    colors = ['#2ca02c' if v >= 0 else '#d62728' for v in vals]
    ax_left.bar(x, vals, color=colors, alpha=0.85, width=0.65)
    ax_left.axhline(0, color='#333', linewidth=0.8)
    ax_left.set_xticks(x)
    ax_left.set_xticklabels(monthly['month'].str[5:].tolist(), fontsize=6.5, rotation=30)
    ax_left.set_ylabel('净变动 (亿元)', fontsize=6.5)
    ax_left.set_title(f'{ts_code.split(".")[0]} 增减持月度净额', fontsize=8, fontweight='bold', pad=4)
    ax_left.spines['top'].set_visible(False); ax_left.spines['right'].set_visible(False)

    buy_patch  = mpatches.Patch(color='#2ca02c', alpha=0.7, label='增持')
    sell_patch = mpatches.Patch(color='#d62728', alpha=0.7, label='减持')
    ax_left.legend(handles=[buy_patch, sell_patch], fontsize=6, loc='upper left', framealpha=0.7)

    # ── 右图：信号汇总 ──────────────────────────────────────────
    signal = metrics['signal']
    color_map = {
        '高管净增持': '#2ca02c', '大股东净增持': '#52b788',
        '高管净减持': '#d62728', '大股东减持': '#ff7f0e',
        '近期无增减持': '#aaa',
    }
    sig_color = color_map.get(signal, '#888')

    ax_right.set_xlim(0, 1); ax_right.set_ylim(0, 1); ax_right.axis('off')
    ax_right.add_patch(mpatches.FancyBboxPatch(
        (0.05, 0.42), 0.9, 0.22, boxstyle='round,pad=0.05',
        facecolor=sig_color, alpha=0.15, edgecolor=sig_color, linewidth=1.5))
    ax_right.text(0.5, 0.53, signal, ha='center', va='center',
                  fontsize=10, fontweight='bold', color=sig_color)
    ax_right.text(0.5, 0.78, f'增{metrics["n_buy"]}笔 减{metrics["n_sell"]}笔',
                  ha='center', va='center', fontsize=7, color='#555')
    net = metrics['net_amt']
    ax_right.text(0.5, 0.25, f'净额 {net:+.3f}亿', ha='center', va='center',
                  fontsize=7.5, fontweight='bold', color=sig_color)
    ax_right.set_title('信号', fontsize=8, fontweight='bold', pad=4)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def _build_narrative(ts_code: str, metrics: dict) -> str:
    code   = ts_code.split('.')[0]
    signal = metrics['signal']
    net    = metrics['net_amt']
    nb     = metrics['n_buy']
    ns     = metrics['n_sell']
    exec_n = metrics['exec_net']

    if signal == '近期无增减持':
        return (f'近90日内{code}无任何股东增减持公告。'
                f'内部人保持沉默——既不是买入信号，也不是卖出压力。')

    if '增持' in signal:
        who = '高管' if '高管' in signal else '大股东'
        return (
            f'近90日内{code}共有**{nb}笔增持、{ns}笔减持**，净增持**{net:+.3f}亿元**。'
            f'{who}选择在二级市场买入自家股票——这是最直接的信心信号，'
            f'因为他们比任何人都更了解公司真实状况。'
            f'内部人增持历史上往往领先股价6-12个月，是中长线布局的重要参考。'
        )
    else:
        who = '高管' if '高管' in signal else '大股东'
        return (
            f'近90日内{code}共有**{nb}笔增持、{ns}笔减持**，净减持**{abs(net):.3f}亿元**。'
            f'{who}正在通过二级市场减持——需要区分是"正常税务/理财需要"还是"对公司失去信心"。'
            f'若减持规模较大（超过总市值1%）且无明确理由，通常是中期压制。'
            f'建议关注减持原因公告。'
        )


def insider_trade(ts_code: str, trade_date: str, lookback_days: int = 90) -> dict:
    result = {
        'ts_code': ts_code, 'trade_date': trade_date,
        'n_buy': 0, 'n_sell': 0, 'net_amt': 0.0,
        'signal': '无数据', 'chart_b64': '', 'narrative': '', 'error': None,
    }

    df = _fetch_insider_trades(ts_code, trade_date, lookback_days=lookback_days)

    if len(df) == 0:
        result['narrative'] = f'【{ts_code.split(".")[0]}】近{lookback_days}日无股东增减持公告记录。'
        result['signal'] = '近期无增减持'
        return result

    metrics = _compute_metrics(df)
    if not metrics:
        result['error'] = 'compute failed'
        return result

    result.update({
        'n_buy': metrics['n_buy'],
        'n_sell': metrics['n_sell'],
        'net_amt': metrics['net_amt'],
        'signal': metrics['signal'],
    })

    print(f'  股东增减持：增{metrics["n_buy"]}笔 减{metrics["n_sell"]}笔 '
          f'净额{metrics["net_amt"]:+.3f}亿 → {metrics["signal"]}')

    try:
        result['chart_b64'] = _build_chart(metrics, ts_code)
    except Exception as e:
        print(f'  [WARN] insider chart: {e}')

    result['narrative'] = _build_narrative(ts_code, metrics)
    return result


if __name__ == '__main__':
    for code in ['688981.SH', '000880.SZ']:
        print(f'\n{"="*50}\n{code}')
        r = insider_trade(code, '20260520')
        print(f'  信号: {r["signal"]}  净额: {r["net_amt"]:+.3f}亿')
        print(f'  叙事: {r["narrative"][:100]}...')
