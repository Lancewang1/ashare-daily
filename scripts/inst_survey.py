"""
inst_survey.py
==============
机构调研频次分析

功能：
1. 拉取个股近90日机构调研记录（stk_surv API）
2. 统计调研机构数、调研次数、机构类型构成
3. 计算近30日 vs 前30日调研频次变化
4. 信号：机构密集调研（加速）/ 正常 / 冷清
5. 生成图表 + 散户叙事
"""

from __future__ import annotations
import sys, io, time, base64
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from collections import Counter

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


def _fetch_surveys(ts_code: str, end_date: str, lookback_days: int = 90) -> pd.DataFrame:
    end_dt   = datetime.strptime(end_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=lookback_days)
    try:
        df = pro.stk_surv(
            ts_code=ts_code,
            start_date=start_dt.strftime('%Y%m%d'),
            end_date=end_date,
        )
        time.sleep(0.2)
    except Exception as e:
        print(f'  [WARN] stk_surv: {e}')
        return pd.DataFrame()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.copy()
    for col in ['surv_date', 'rece_date']:
        if col in df.columns:
            df[col] = df[col].astype(str)
    df['date'] = df.get('surv_date', df.get('rece_date', pd.Series([''] * len(df))))
    return df.sort_values('date').reset_index(drop=True)


def _compute_metrics(df: pd.DataFrame, end_date: str) -> dict:
    if len(df) == 0:
        return {'has_data': False}

    end_dt   = datetime.strptime(end_date, '%Y%m%d')
    cut30    = (end_dt - timedelta(days=30)).strftime('%Y%m%d')
    cut60    = (end_dt - timedelta(days=60)).strftime('%Y%m%d')

    recent30 = df[df['date'] >= cut30]
    prev30   = df[(df['date'] >= cut60) & (df['date'] < cut30)]

    n_total   = len(df)
    n_recent  = len(recent30)
    n_prev    = len(prev30)

    # Unique institutions
    inst_col = None
    for c in ['institution', 'comp_name', 'org_name']:
        if c in df.columns:
            inst_col = c; break
    n_inst = df[inst_col].nunique() if inst_col else 0

    # Institution type breakdown
    type_col = None
    for c in ['comp_type', 'inst_type', 'rece_org_type']:
        if c in df.columns:
            type_col = c; break
    type_dist = {}
    if type_col:
        vc = df[type_col].value_counts()
        type_dist = vc.head(4).to_dict()

    # Weekly grouping
    df['week'] = df['date'].apply(
        lambda d: (datetime.strptime(d, '%Y%m%d') - timedelta(days=datetime.strptime(d, '%Y%m%d').weekday())).strftime('%Y%m%d')
        if len(d) == 8 else d
    )
    weekly = df.groupby('week').size().reset_index(name='count').sort_values('week')

    # Acceleration
    if n_prev > 0:
        accel = n_recent / n_prev
    elif n_recent > 0:
        accel = float('inf')
    else:
        accel = 1.0

    # Signal
    if n_recent >= 5 and accel >= 2:
        signal = '机构密集调研（加速）'
    elif n_recent >= 3:
        signal = '机构持续关注'
    elif n_recent >= 1:
        signal = '有机构调研'
    else:
        signal = '近期无机构调研'

    return {
        'has_data': True,
        'weekly': weekly,
        'n_total': n_total,
        'n_recent': n_recent,
        'n_prev': n_prev,
        'n_inst': n_inst,
        'type_dist': type_dist,
        'accel': round(accel, 1) if accel != float('inf') else 99,
        'signal': signal,
    }


def _build_chart(metrics: dict, ts_code: str) -> str:
    weekly = metrics['weekly']
    type_dist = metrics['type_dist']

    n_cols = 2 if type_dist else 1
    fig, axes = plt.subplots(1, n_cols, figsize=(8, 2.6),
                             gridspec_kw={'width_ratios': [3, 1]} if n_cols == 2 else None,
                             facecolor='white')
    fig.subplots_adjust(left=0.08, right=0.97, top=0.80, bottom=0.20, wspace=0.4)

    ax_left = axes[0] if n_cols == 2 else axes
    ax_right = axes[1] if n_cols == 2 else None

    # ── 左图：周度调研次数 ─────────────────────────────────────
    x = list(range(len(weekly)))
    counts = weekly['count'].tolist()
    weeks  = weekly['week'].tolist()

    colors = ['#d62728' if c >= 3 else '#ff7f0e' if c >= 2 else '#aec7e8' for c in counts]
    ax_left.bar(x, counts, color=colors, alpha=0.85, width=0.7)
    ax_left.axhline(np.mean(counts), color='#1f77b4', linewidth=1, linestyle='--', alpha=0.7,
                    label=f'均值{np.mean(counts):.1f}次/周')

    step = max(1, len(x) // 5)
    ax_left.set_xticks(x[::step])
    ax_left.set_xticklabels([w[4:] for w in weeks[::step]], fontsize=6, rotation=30)
    ax_left.set_ylabel('调研次数', fontsize=6.5)
    ax_left.set_title(f'{ts_code.split(".")[0]} 机构调研频次（近90日）',
                      fontsize=8, fontweight='bold', pad=4)
    ax_left.legend(fontsize=6, loc='upper left', framealpha=0.7)
    ax_left.spines['top'].set_visible(False); ax_left.spines['right'].set_visible(False)

    # ── 右图：机构类型饼图 ────────────────────────────────────
    if ax_right and type_dist:
        labels = list(type_dist.keys())
        sizes  = list(type_dist.values())
        colors_pie = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd']
        ax_right.pie(sizes, labels=labels, autopct='%1.0f%%',
                     colors=colors_pie[:len(labels)],
                     textprops={'fontsize': 6}, startangle=90)
        ax_right.set_title('机构类型', fontsize=7.5, fontweight='bold', pad=4)
    elif ax_right:
        ax_right.axis('off')
        ax_right.text(0.5, 0.5, f'共{metrics["n_inst"]}家机构\n调研{metrics["n_total"]}次',
                      ha='center', va='center', fontsize=9, color='#333')

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def _build_narrative(ts_code: str, metrics: dict) -> str:
    code = ts_code.split('.')[0]
    if not metrics.get('has_data') or metrics['signal'] == '近期无机构调研':
        return (f'【{code}】近90日内无机构调研记录。'
                f'机构对该股暂时沉默——可能是因为行情平淡，也可能是进入静默期。')

    signal  = metrics['signal']
    n_total = metrics['n_total']
    n_inst  = metrics['n_inst']
    n30     = metrics['n_recent']
    n_prev  = metrics['n_prev']
    accel   = metrics['accel']

    if '密集' in signal:
        return (
            f"{code}近90日内共有**{n_inst}家机构**调研，合计**{n_total}次**。"
            f"特别是近30日调研**{n30}次**，是前30日({n_prev}次)的**{accel:.1f}倍**——机构调研明显加速。"
            f"机构调研激增往往发生在布局阶段——他们在评估是否值得建仓前会密集尽调。"
            f"历史上调研频次激增后3-6个月内，股价有较高概率跑赢大盘。"
        )
    elif '持续' in signal:
        return (
            f"{code}近90日内共有**{n_inst}家机构**调研，合计**{n_total}次**，近30日{n30}次。"
            f"机构保持持续关注，说明这只股票在机构研究员的视野内。"
            f"调研后机构会内部讨论研究报告，频繁调研通常预示着后续会有配置动作。"
        )
    else:
        return (
            f"{code}近90日内有**{n_inst}家机构**调研共{n_total}次，近30日仅{n30}次。"
            f"机构调研频次一般，关注度有限。"
            f"若后续有业绩超预期或行业催化剂，可能会触发机构重新关注并加大调研力度。"
        )


def inst_survey(ts_code: str, trade_date: str, lookback_days: int = 90) -> dict:
    result = {
        'ts_code': ts_code, 'trade_date': trade_date,
        'n_total': 0, 'n_inst': 0,
        'signal': '无数据', 'chart_b64': '', 'narrative': '', 'error': None,
    }

    df = _fetch_surveys(ts_code, trade_date, lookback_days=lookback_days)

    if len(df) == 0:
        result['signal'] = '近期无机构调研'
        result['narrative'] = f'【{ts_code.split(".")[0]}】近{lookback_days}日无机构调研记录。'
        return result

    metrics = _compute_metrics(df, trade_date)

    result.update({
        'n_total': metrics.get('n_total', 0),
        'n_inst': metrics.get('n_inst', 0),
        'signal': metrics.get('signal', '无数据'),
    })

    print(f'  机构调研：{metrics["n_total"]}次 {metrics["n_inst"]}家 '
          f'近30日{metrics["n_recent"]}次 → {metrics["signal"]}')

    try:
        result['chart_b64'] = _build_chart(metrics, ts_code)
    except Exception as e:
        print(f'  [WARN] inst_survey chart: {e}')

    result['narrative'] = _build_narrative(ts_code, metrics)
    return result


if __name__ == '__main__':
    for code in ['688981.SH', '000880.SZ']:
        print(f'\n{"="*50}\n{code}')
        r = inst_survey(code, '20260520')
        print(f'  信号: {r["signal"]}  调研: {r["n_total"]}次 {r["n_inst"]}家')
