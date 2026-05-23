"""
share_unlock.py
===============
解禁倒计时日历

功能：
1. 拉取个股未来90日限售股解禁计划（share_float API）
2. 计算解禁量（亿股/亿元）、占流通盘比例
3. 信号分类：重大解禁压力 / 中等压力 / 无近期解禁
4. 生成时间轴图表 + 散户叙事
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


def _fetch_share_float(ts_code: str, trade_date: str, lookahead_days: int = 90) -> pd.DataFrame:
    start_dt = datetime.strptime(trade_date, '%Y%m%d')
    end_dt   = start_dt + timedelta(days=lookahead_days)
    try:
        df = pro.share_float(
            ts_code=ts_code,
            start_date=trade_date,
            end_date=end_dt.strftime('%Y%m%d'),
        )
        time.sleep(0.2)
    except Exception as e:
        print(f'  [WARN] share_float: {e}')
        return pd.DataFrame()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.copy()
    df['float_date'] = df['float_date'].astype(str)
    for col in ['float_share', 'float_ratio']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.sort_values('float_date').reset_index(drop=True)


def _fetch_basic_info(ts_code: str) -> dict:
    """Get total share count for context."""
    try:
        df = pro.stock_basic(ts_code=ts_code, fields='ts_code,total_share,float_share,circ_mv')
        time.sleep(0.15)
        if df is not None and len(df) > 0:
            row = df.iloc[0]
            return {
                'total_share': float(row.get('total_share', 0) or 0),
                'float_share': float(row.get('float_share', 0) or 0),
            }
    except Exception:
        pass
    return {}


def _compute_metrics(df: pd.DataFrame, basic: dict, trade_date: str) -> dict:
    if len(df) == 0:
        return {'has_unlock': False}

    today = datetime.strptime(trade_date, '%Y%m%d')
    df['days_to'] = df['float_date'].apply(
        lambda d: (datetime.strptime(d, '%Y%m%d') - today).days)

    # Total float shares (万股 → 亿股)
    if 'float_share' in df.columns:
        df['float_share_yi'] = df['float_share'] / 1e4   # 万股 → 亿股
    else:
        df['float_share_yi'] = 0.0

    total_unlock_yi = float(df['float_share_yi'].sum())

    # Compare with existing float shares
    float_share_total = basic.get('float_share', 0)  # 万股
    float_share_total_yi = float_share_total / 1e4 if float_share_total else 0

    unlock_ratio_pct = (total_unlock_yi / float_share_total_yi * 100) if float_share_total_yi > 0 else 0

    # Nearest event
    nearest = df.iloc[0]
    nearest_days = int(nearest['days_to'])
    nearest_date = nearest['float_date']
    nearest_yi   = float(nearest['float_share_yi'])

    # 30-day pressure
    near30 = df[df['days_to'] <= 30]
    pressure30_yi = float(near30['float_share_yi'].sum())
    pressure30_ratio = (pressure30_yi / float_share_total_yi * 100) if float_share_total_yi > 0 else 0

    # Signal
    if pressure30_ratio > 5:
        signal = '重大解禁压力'
    elif pressure30_ratio > 2:
        signal = '中等解禁压力'
    elif total_unlock_yi > 0:
        signal = '近期有解禁'
    else:
        signal = '无近期解禁'

    return {
        'has_unlock': True,
        'df': df,
        'total_unlock_yi': round(total_unlock_yi, 4),
        'unlock_ratio_pct': round(unlock_ratio_pct, 2),
        'nearest_date': nearest_date,
        'nearest_days': nearest_days,
        'nearest_yi': round(nearest_yi, 4),
        'pressure30_ratio': round(pressure30_ratio, 2),
        'n_events': len(df),
        'signal': signal,
    }


def _build_chart(metrics: dict, ts_code: str) -> str:
    df = metrics['df']

    fig, ax = plt.subplots(figsize=(8, 2.6), facecolor='white')
    fig.subplots_adjust(left=0.10, right=0.97, top=0.78, bottom=0.20)

    days  = df['days_to'].tolist()
    sizes = df['float_share_yi'].tolist()
    types = df.get('holder_name', pd.Series(['未知'] * len(df))).tolist() \
            if 'holder_name' in df.columns else [''] * len(df)

    # Color by pressure level
    max_size = max(sizes) if sizes else 1
    colors = []
    for s in sizes:
        ratio = s / max_size
        if ratio > 0.5:
            colors.append('#d62728')
        elif ratio > 0.2:
            colors.append('#ff7f0e')
        else:
            colors.append('#aec7e8')

    # Timeline: x = days, y = size, scatter
    sc = ax.scatter(days, sizes, s=[max(20, min(300, s / max_size * 200)) for s in sizes],
                    c=colors, alpha=0.85, zorder=3)
    for i, (d, s) in enumerate(zip(days, sizes)):
        ax.annotate(f'+{d}天\n{s:.2f}亿股', (d, s),
                    textcoords='offset points', xytext=(0, 8),
                    ha='center', fontsize=5.5, color='#333')

    # 30-day warning zone
    ax.axvspan(0, 30, alpha=0.07, color='#d62728', label='30天内')
    ax.axvline(30, color='#d62728', linewidth=0.8, linestyle='--', alpha=0.6)

    ax.set_xlabel('距今天数', fontsize=6.5)
    ax.set_ylabel('解禁股数 (亿股)', fontsize=6.5)
    ax.set_title(f'{ts_code.split(".")[0]} 解禁倒计时（未来90日）', fontsize=8, fontweight='bold', pad=4)
    ax.tick_params(axis='both', labelsize=6)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)

    red_p   = mpatches.Patch(color='#d62728', alpha=0.7, label='大量解禁')
    ora_p   = mpatches.Patch(color='#ff7f0e', alpha=0.7, label='中等解禁')
    blue_p  = mpatches.Patch(color='#aec7e8', alpha=0.7, label='小量解禁')
    ax.legend(handles=[red_p, ora_p, blue_p], fontsize=5.5, loc='upper right', framealpha=0.7)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def _build_narrative(ts_code: str, metrics: dict) -> str:
    code = ts_code.split('.')[0]

    if not metrics.get('has_unlock') or metrics.get('signal') == '无近期解禁':
        return (f'【{code}】未来90日内无限售股解禁计划。'
                f'短期内不存在大股东被动减仓压力，这是一个正面的技术面条件。')

    signal   = metrics['signal']
    n        = metrics['n_events']
    total    = metrics['total_unlock_yi']
    ratio    = metrics['unlock_ratio_pct']
    nearest  = metrics['nearest_date']
    nd       = metrics['nearest_days']
    p30      = metrics['pressure30_ratio']

    nearest_fmt = f'{nearest[:4]}-{nearest[4:6]}-{nearest[6:]}'

    if signal == '重大解禁压力':
        return (
            f"⚠️ {code}在未来90日内有**{n}批**限售股解禁，共**{total:.2f}亿股**（占流通盘约{ratio:.1f}%）。"
            f"最近一批在**{nearest_fmt}**（还有{nd}天），**30日内解禁占流通盘{p30:.1f}%**。"
            f"解禁≠立即减持，但解禁股股东的持股成本极低，一旦解锁就有强烈的套现冲动。"
            f"历史数据显示大规模解禁前后1-2个月，股价受压概率显著偏高。请密切关注。"
        )
    elif signal == '中等解禁压力':
        return (
            f"{code}在未来90日内有**{n}批**限售股解禁，共约**{total:.2f}亿股**（流通盘{ratio:.1f}%）。"
            f"最近一批{nearest_fmt}（{nd}天后），30日内压力约占流通盘{p30:.1f}%。"
            f"解禁规模属中等，若股价届时处于高位，套现压力会更明显；低位则减持意愿相对弱。"
        )
    else:
        return (
            f"{code}未来90日内将有**{n}批**限售股解禁（共{total:.2f}亿股），"
            f"最近一批在{nearest_fmt}（{nd}天后），规模较小。"
            f"解禁压力不大，但仍建议在解禁日前后观察成交量和大宗交易数据。"
        )


def share_unlock(ts_code: str, trade_date: str, lookahead_days: int = 90) -> dict:
    result = {
        'ts_code': ts_code, 'trade_date': trade_date,
        'total_unlock_yi': 0.0, 'nearest_days': None,
        'signal': '无近期解禁', 'chart_b64': '', 'narrative': '', 'error': None,
    }

    df    = _fetch_share_float(ts_code, trade_date, lookahead_days=lookahead_days)
    basic = _fetch_basic_info(ts_code)

    if len(df) == 0:
        result['narrative'] = f'【{ts_code.split(".")[0]}】未来{lookahead_days}日内无解禁计划。'
        return result

    metrics = _compute_metrics(df, basic, trade_date)

    result['signal']         = metrics.get('signal', '无近期解禁')
    result['total_unlock_yi'] = metrics.get('total_unlock_yi', 0.0)
    result['nearest_days']   = metrics.get('nearest_days')

    print(f'  解禁倒计时：{metrics.get("n_events", 0)}批 '
          f'共{metrics.get("total_unlock_yi", 0):.2f}亿股 '
          f'最近{metrics.get("nearest_days", "N/A")}天 → {metrics.get("signal")}')

    if metrics.get('has_unlock'):
        try:
            result['chart_b64'] = _build_chart(metrics, ts_code)
        except Exception as e:
            print(f'  [WARN] unlock chart: {e}')

    result['narrative'] = _build_narrative(ts_code, metrics)
    return result


if __name__ == '__main__':
    for code in ['688981.SH', '000880.SZ']:
        print(f'\n{"="*50}\n{code}')
        r = share_unlock(code, '20260520')
        print(f'  信号: {r["signal"]}')
        print(f'  叙事: {r["narrative"][:100]}...')
