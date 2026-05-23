"""
equity_incentive.py
===================
股权激励行权价锚定分析

功能：
1. 拉取个股有效股权激励方案（stk_sf_cpn API）
2. 找到行权价（exercise price）和当前股价的距离
3. 识别管理层利益绑定价位，计算"激励水位"
4. 信号：强激励支撑 / 激励已废弃 / 无有效激励
5. 生成价格+行权价区间图 + 散户叙事
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


def _fetch_incentive_plans(ts_code: str) -> pd.DataFrame:
    try:
        df = pro.stk_sf_cpn(ts_code=ts_code)
        time.sleep(0.2)
    except Exception as e:
        print(f'  [WARN] stk_sf_cpn: {e}')
        return pd.DataFrame()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.copy()
    for col in ['ann_date', 'end_date']:
        if col in df.columns:
            df[col] = df[col].astype(str)
    for col in ['exercise_price', 'total_num', 'grant_price', 'fv_price']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df


def _fetch_recent_prices(ts_code: str, trade_date: str, lookback: int = 60) -> pd.DataFrame:
    end_dt   = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=lookback + 10)
    try:
        df = pro.daily(ts_code=ts_code,
                       start_date=start_dt.strftime('%Y%m%d'),
                       end_date=trade_date,
                       fields='trade_date,close,high,low')
        time.sleep(0.15)
        if df is None or len(df) == 0:
            return pd.DataFrame()
        df['trade_date'] = df['trade_date'].astype(str)
        for c in ['close', 'high', 'low']:
            df[c] = pd.to_numeric(df[c], errors='coerce')
        return df.sort_values('trade_date').tail(lookback).reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def _compute_metrics(plans: pd.DataFrame, prices: pd.DataFrame,
                     trade_date: str) -> dict:
    if len(plans) == 0:
        return {'has_plan': False}

    current_price = float(prices['close'].iloc[-1]) if len(prices) > 0 else None

    # Find active plans (end_date >= trade_date or null)
    active = []
    for _, row in plans.iterrows():
        end = str(row.get('end_date', '') or '')
        if end == '' or end == 'nan' or end >= trade_date:
            ep = row.get('exercise_price') or row.get('grant_price') or row.get('fv_price')
            if pd.notna(ep) and ep > 0:
                active.append({
                    'exercise_price': float(ep),
                    'end_date': end,
                    'ann_date': str(row.get('ann_date', '') or ''),
                })

    if not active:
        return {'has_plan': False, 'reason': '无有效激励方案或行权价数据缺失'}

    # Sort by exercise price
    active.sort(key=lambda r: r['exercise_price'])
    prices_only = [a['exercise_price'] for a in active]
    ep_min = min(prices_only)
    ep_max = max(prices_only)
    ep_avg = float(np.mean(prices_only))

    if current_price is None:
        return {'has_plan': False, 'reason': '无法获取当前股价'}

    dist_pct = (current_price / ep_avg - 1) * 100  # positive = above exercise price

    # Signal
    if dist_pct < -20:
        signal = '激励已废弃（深度破发）'
    elif dist_pct < -5:
        signal = '激励水下（轻微破发）'
    elif dist_pct < 10:
        signal = '强激励支撑（接近行权价）'
    elif dist_pct < 30:
        signal = '激励有效（小幅高于行权价）'
    else:
        signal = '激励已充分（大幅高于行权价）'

    return {
        'has_plan': True,
        'active': active,
        'ep_min': round(ep_min, 2),
        'ep_max': round(ep_max, 2),
        'ep_avg': round(ep_avg, 2),
        'current_price': round(current_price, 2),
        'dist_pct': round(dist_pct, 1),
        'signal': signal,
        'prices': prices,
    }


def _build_chart(metrics: dict, ts_code: str) -> str:
    prices = metrics['prices']
    ep_min = metrics['ep_min']
    ep_max = metrics['ep_max']
    ep_avg = metrics['ep_avg']
    current = metrics['current_price']

    fig, ax = plt.subplots(figsize=(8, 2.8), facecolor='white')
    fig.subplots_adjust(left=0.08, right=0.97, top=0.80, bottom=0.18)

    # Price line
    x = list(range(len(prices)))
    closes = prices['close'].tolist()
    ax.plot(x, closes, color='#1f77b4', linewidth=1.5, label='收盘价', zorder=3)
    ax.fill_between(x, closes, min(closes) * 0.98, color='#1f77b4', alpha=0.08)

    # Exercise price bands
    y_lo = min(min(closes), ep_min) * 0.97
    y_hi = max(max(closes), ep_max) * 1.03

    if ep_min != ep_max:
        ax.axhspan(ep_min, ep_max, alpha=0.12, color='#ff7f0e', label=f'行权价区间 {ep_min:.1f}–{ep_max:.1f}')
    ax.axhline(ep_avg, color='#ff7f0e', linewidth=1.5, linestyle='--',
               label=f'平均行权价 {ep_avg:.2f}', zorder=4)

    # Current price annotation
    ax.annotate(f'当前 {current:.2f}',
                xy=(x[-1], current), xytext=(x[-1] - 5, current + (y_hi - y_lo) * 0.1),
                fontsize=7, color='#1f77b4', fontweight='bold',
                arrowprops=dict(arrowstyle='->', color='#1f77b4', lw=0.8))

    # X-axis
    dates = prices['trade_date'].tolist()
    step = max(1, len(x) // 6)
    ax.set_xticks(x[::step])
    ax.set_xticklabels([d[4:] for d in dates[::step]], fontsize=6, rotation=30)
    ax.set_ylim(y_lo, y_hi)
    ax.set_ylabel('股价 (元)', fontsize=6.5)
    ax.set_title(f'{ts_code.split(".")[0]} 股权激励行权价锚定', fontsize=8, fontweight='bold', pad=4)
    ax.legend(fontsize=6, loc='upper left', framealpha=0.7)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def _build_narrative(ts_code: str, metrics: dict) -> str:
    code = ts_code.split('.')[0]
    if not metrics.get('has_plan'):
        reason = metrics.get('reason', '无有效股权激励方案')
        return f'【{code}】{reason}，无法进行行权价锚定分析。'

    signal  = metrics['signal']
    ep_avg  = metrics['ep_avg']
    current = metrics['current_price']
    dist    = metrics['dist_pct']
    n_plans = len(metrics['active'])

    if '废弃' in signal:
        return (
            f"{code}共有**{n_plans}个**有效股权激励方案，平均行权价**{ep_avg:.2f}元**，"
            f"当前股价{current:.2f}元，**低于行权价{abs(dist):.1f}%**（深度破发）。"
            f"行权价破发意味着管理层的期权已经毫无价值，激励效果完全失效。"
            f"这种情况下管理层的个人利益与股价脱钩，对股东而言是负面信号。"
        )
    elif '水下' in signal:
        return (
            f"{code}平均行权价**{ep_avg:.2f}元**，当前股价{current:.2f}元，"
            f"**低于行权价{abs(dist):.1f}%**（轻微破发）。"
            f"管理层期权处于轻微亏损状态，他们有强烈动力推动股价回到行权价以上。"
            f"这反而构成一个潜在的做多动力——管理层会积极争取业绩以挽救期权价值。"
        )
    elif '强激励支撑' in signal:
        return (
            f"{code}平均行权价**{ep_avg:.2f}元**，当前股价{current:.2f}元（高出{dist:.1f}%）。"
            f"当前股价紧贴行权价——管理层的身家与股价在这个关键位置高度绑定。"
            f"**行权价是最天然的心理支撑位**：管理层有强烈动力阻止股价跌破，这比任何技术支撑都真实。"
            f"共{n_plans}个激励计划处于有效期内。"
        )
    elif '有效' in signal:
        return (
            f"{code}平均行权价**{ep_avg:.2f}元**，当前股价{current:.2f}元（高出{dist:.1f}%）。"
            f"激励方案处于有效状态，管理层持有的期权目前盈利{dist:.1f}%。"
            f"激励到位的管理层与外部股东利益一致，是公司治理健康的信号。"
            f"行权价附近若调整则是较好的买入参考位。"
        )
    else:
        return (
            f"{code}平均行权价**{ep_avg:.2f}元**，当前股价{current:.2f}元（高出{dist:.1f}%）。"
            f"股价已大幅超越行权价，激励方案的期权价值已充分兑现。"
            f"此时管理层有更强的行权套现动机，需关注是否会有期权行权带来的股权稀释压力。"
        )


def equity_incentive(ts_code: str, trade_date: str) -> dict:
    result = {
        'ts_code': ts_code, 'trade_date': trade_date,
        'ep_avg': None, 'current_price': None, 'dist_pct': None,
        'signal': '无数据', 'chart_b64': '', 'narrative': '', 'error': None,
    }

    plans  = _fetch_incentive_plans(ts_code)
    prices = _fetch_recent_prices(ts_code, trade_date, lookback=60)

    if len(plans) == 0:
        result['signal'] = '无有效激励'
        result['narrative'] = f'【{ts_code.split(".")[0]}】暂无股权激励方案数据。'
        return result

    metrics = _compute_metrics(plans, prices, trade_date)

    if not metrics.get('has_plan'):
        result['signal'] = '无有效激励'
        result['narrative'] = _build_narrative(ts_code, metrics)
        return result

    result.update({
        'ep_avg': metrics['ep_avg'],
        'current_price': metrics['current_price'],
        'dist_pct': metrics['dist_pct'],
        'signal': metrics['signal'],
    })

    print(f'  股权激励：行权价均值{metrics["ep_avg"]:.2f} 当前{metrics["current_price"]:.2f} '
          f'距离{metrics["dist_pct"]:+.1f}% → {metrics["signal"]}')

    if len(prices) > 0:
        try:
            result['chart_b64'] = _build_chart(metrics, ts_code)
        except Exception as e:
            print(f'  [WARN] equity_incentive chart: {e}')

    result['narrative'] = _build_narrative(ts_code, metrics)
    return result


if __name__ == '__main__':
    for code in ['688981.SH', '000880.SZ']:
        print(f'\n{"="*50}\n{code}')
        r = equity_incentive(code, '20260520')
        print(f'  信号: {r["signal"]}')
        print(f'  叙事: {r["narrative"][:100]}...')
