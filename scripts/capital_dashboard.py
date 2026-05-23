"""
capital_dashboard.py
====================
资金博弈仪表盘

功能：
拉取 5 类资金指标的近 1 年历史，用历史百分位将每个指标标准化后，
在单张图内以渐变色 gauge 条可视化——左侧偏空、右侧偏多。

指标清单（按历史百分位排列）：
1. 换手率          daily_basic.turnover_rate_f     1-year
2. 融资增速(5日)    margin_detail.rzye              1-year
3. 主力净流入       moneyflow                       60-day
4. 北向净买入       hk_hold ratio 5d change         60-day  （无数据则跳过）
5. 融券/融资比      rqye / rzye                     1-year  （有数据则加入）
"""

from __future__ import annotations
import io, time, base64
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from scipy.stats import percentileofscore

import matplotlib
matplotlib.use('Agg')
matplotlib.rcParams.update({
    'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'DejaVu Sans'],
    'axes.unicode_minus': False,
})
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

import tushare as ts
pro = ts.pro_api()

# 渐变色：偏空(红) → 中性(黄) → 偏多(绿)
_CMAP_BULL = LinearSegmentedColormap.from_list(
    'bull', ['#d62728', '#f7dc6f', '#2ca02c'])
_CMAP_BEAR = LinearSegmentedColormap.from_list(
    'bear', ['#2ca02c', '#f7dc6f', '#d62728'])   # inverted: high = bearish

_GRAD = np.linspace(0, 1, 256).reshape(1, -1)


# ── 数据拉取 ──────────────────────────────────────────────────────────────────

def _lookback(trade_date: str, days: int) -> str:
    dt = datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=days)
    return dt.strftime('%Y%m%d')


def _fetch_daily_basic(ts_code: str, trade_date: str) -> pd.DataFrame:
    try:
        df = pro.daily_basic(
            ts_code=ts_code,
            start_date=_lookback(trade_date, 380),
            end_date=trade_date,
            fields='trade_date,turnover_rate_f,volume_ratio'
        )
        time.sleep(0.2)
        if df is None or len(df) == 0:
            return pd.DataFrame()
        df['trade_date'] = df['trade_date'].astype(str)
        df['turnover_rate_f'] = pd.to_numeric(df['turnover_rate_f'], errors='coerce')
        return df.sort_values('trade_date').reset_index(drop=True)
    except Exception as e:
        print(f'  [WARN] capital daily_basic: {e}')
        return pd.DataFrame()


def _fetch_margin(ts_code: str, trade_date: str) -> pd.DataFrame:
    try:
        df = pro.margin_detail(
            ts_code=ts_code,
            start_date=_lookback(trade_date, 380),
            end_date=trade_date,
        )
        time.sleep(0.2)
        if df is None or len(df) == 0:
            return pd.DataFrame()
        df['trade_date'] = df['trade_date'].astype(str)
        for col in ['rzye', 'rqye', 'rzmre']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        return df.sort_values('trade_date').reset_index(drop=True)
    except Exception as e:
        print(f'  [WARN] capital margin: {e}')
        return pd.DataFrame()


def _fetch_moneyflow(ts_code: str, trade_date: str) -> pd.DataFrame:
    try:
        df = pro.moneyflow(
            ts_code=ts_code,
            start_date=_lookback(trade_date, 100),
            end_date=trade_date,
        )
        time.sleep(0.2)
        if df is None or len(df) == 0:
            return pd.DataFrame()
        df['trade_date'] = df['trade_date'].astype(str)
        for col in df.columns:
            if col not in ('ts_code', 'trade_date'):
                df[col] = pd.to_numeric(df[col], errors='coerce')
        return df.sort_values('trade_date').reset_index(drop=True)
    except Exception as e:
        print(f'  [WARN] capital moneyflow: {e}')
        return pd.DataFrame()


def _fetch_hk_hold(ts_code: str, trade_date: str) -> pd.DataFrame:
    try:
        df = pro.hk_hold(
            ts_code=ts_code,
            start_date=_lookback(trade_date, 100),
            end_date=trade_date,
        )
        time.sleep(0.2)
        if df is None or len(df) == 0:
            return pd.DataFrame()
        df['trade_date'] = df['trade_date'].astype(str)
        df['ratio'] = pd.to_numeric(df.get('ratio', df.get('hold_ratio', None)), errors='coerce')
        return df.sort_values('trade_date').reset_index(drop=True)
    except Exception as e:
        return pd.DataFrame()


# ── 计算各指标的历史百分位 ────────────────────────────────────────────────────

def _pct_of(series: pd.Series, current_val: float) -> float:
    """Return percentile (0-100) of current_val in the series."""
    clean = series.dropna()
    if len(clean) < 5:
        return 50.0
    return float(percentileofscore(clean, current_val, kind='rank'))


def _compute_metrics(ts_code: str, trade_date: str) -> list[dict]:
    """Return list of metric dicts: {name, value_str, pct, direction, color}"""
    metrics = []

    # ── 1. 换手率 ─────────────────────────────────────────────────────────────
    db = _fetch_daily_basic(ts_code, trade_date)
    if len(db) >= 10:
        curr_tr = db['turnover_rate_f'].iloc[-1]
        pct_tr  = _pct_of(db['turnover_rate_f'], curr_tr)
        metrics.append({
            'name': '换手率',
            'value_str': f'{curr_tr:.2f}%',
            'pct': pct_tr,
            'direction': 'bull',  # high turnover = active = mild bullish
            'raw': curr_tr,
        })

    # ── 2. 融资余额 5日增速 ────────────────────────────────────────────────────
    mg = _fetch_margin(ts_code, trade_date)
    rqye_series = None
    if len(mg) >= 10 and 'rzye' in mg.columns:
        mg_nonan = mg['rzye'].dropna()
        if len(mg_nonan) >= 5:
            # 5日变化率
            rzye_vals = mg['rzye'].dropna().reset_index(drop=True)
            change5 = rzye_vals.diff(5)
            curr_chg = float(change5.iloc[-1]) if not pd.isna(change5.iloc[-1]) else 0.0
            pct_mg   = _pct_of(change5.dropna(), curr_chg)
            curr_val_yi = float(mg_nonan.iloc[-1]) / 1e8  # 元 → 亿
            metrics.append({
                'name': '融资余额增速',
                'value_str': f'{curr_chg/1e8:+.1f}亿',
                'pct': pct_mg,
                'direction': 'bull',   # more margin debt = bullish sentiment
                'raw': curr_chg,
            })
        # also store rqye series for metric 5
        if 'rqye' in mg.columns:
            rqye_series = mg['rqye'].dropna()

    # ── 3. 主力净流入 ─────────────────────────────────────────────────────────
    mf = _fetch_moneyflow(ts_code, trade_date)
    if len(mf) >= 5:
        # prefer net_mf_amount; fallback to computing from buy/sell
        if 'net_mf_amount' in mf.columns:
            net_series = mf['net_mf_amount']
        else:
            buy  = mf.get('buy_elg_amount', pd.Series(0, index=mf.index)).fillna(0) \
                 + mf.get('buy_lg_amount',  pd.Series(0, index=mf.index)).fillna(0)
            sell = mf.get('sell_elg_amount', pd.Series(0, index=mf.index)).fillna(0) \
                 + mf.get('sell_lg_amount',  pd.Series(0, index=mf.index)).fillna(0)
            net_series = buy - sell
        curr_net = float(net_series.iloc[-1])
        pct_net  = _pct_of(net_series, curr_net)
        metrics.append({
            'name': '主力净流入',
            'value_str': f'{curr_net/1e4:+.1f}亿',
            'pct': pct_net,
            'direction': 'bull',
            'raw': curr_net,
        })

    # ── 4. 北向持股5日变化 ─────────────────────────────────────────────────────
    hk = _fetch_hk_hold(ts_code, trade_date)
    if len(hk) >= 10 and 'ratio' in hk.columns:
        ratio_nonan = hk['ratio'].dropna()
        if len(ratio_nonan) >= 5:
            ratio_change = ratio_nonan.diff(5)
            curr_rc = float(ratio_change.iloc[-1]) if not pd.isna(ratio_change.iloc[-1]) else 0.0
            pct_rc  = _pct_of(ratio_change.dropna(), curr_rc)
            metrics.append({
                'name': '北向持股变化',
                'value_str': f'{curr_rc:+.2f}pp',
                'pct': pct_rc,
                'direction': 'bull',
                'raw': curr_rc,
            })

    # ── 5. 融券/融资比（空头压力） ─────────────────────────────────────────────
    if rqye_series is not None and len(rqye_series) >= 5 and len(mg) >= 5:
        rzye_s = mg['rzye'].dropna()
        if len(rzye_s) >= 5:
            ratio_series = (rqye_series.reset_index(drop=True)
                            / rzye_s.reset_index(drop=True).clip(lower=1e6))
            curr_ratio = float(ratio_series.iloc[-1]) if not ratio_series.empty else 0.0
            pct_ratio  = _pct_of(ratio_series.dropna(), curr_ratio)
            metrics.append({
                'name': '融券/融资比',
                'value_str': f'{curr_ratio*100:.1f}%',
                'pct': pct_ratio,
                'direction': 'bear',  # high short ratio = bearish pressure
                'raw': curr_ratio,
            })

    return metrics


# ── 图表构建 ──────────────────────────────────────────────────────────────────

def _build_chart(metrics: list[dict], ts_code: str) -> str:
    n = len(metrics)
    if n == 0:
        return ''

    code = ts_code.split('.')[0]

    # Layout constants
    bar_h      = 0.52          # height of each gauge bar
    row_gap    = 1.0           # vertical spacing between rows
    fig_h      = max(3.0, n * row_gap + 1.2)
    left_pad   = 0.22          # fraction for metric name
    right_pad  = 0.18          # fraction for value/percentile text

    fig_w = 8.0
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), facecolor='white')
    ax.set_xlim(-0.05, 1.05)
    ax.set_ylim(-0.6, n * row_gap)
    ax.axis('off')

    x0 = left_pad          # bar start (in ax coords)
    x1 = 1.0 - right_pad   # bar end

    # Composite score (average of bullish-direction percentiles)
    bull_pcts = []
    for m in metrics:
        p = m['pct'] if m['direction'] == 'bull' else (100 - m['pct'])
        if not np.isnan(p):
            bull_pcts.append(p)
    composite = float(np.mean(bull_pcts)) if bull_pcts else 50.0

    for i, m in enumerate(metrics):
        y = (n - 1 - i) * row_gap   # top metric = highest y

        # ── 渐变 gauge bar via imshow ──────────────────────────────────────
        cmap = _CMAP_BULL if m['direction'] == 'bull' else _CMAP_BEAR
        ax.imshow(
            _GRAD,
            aspect='auto',
            extent=[x0, x1, y - bar_h/2, y + bar_h/2],
            cmap=cmap,
            alpha=0.72,
            zorder=1,
            transform=ax.transData,
        )
        # border
        ax.add_patch(plt.Rectangle(
            (x0, y - bar_h/2), x1 - x0, bar_h,
            fill=False, edgecolor='#bbb', linewidth=0.8, zorder=3,
            transform=ax.transData
        ))

        # ── 当前百分位标记 (倒三角) ─────────────────────────────────────────
        marker_x = x0 + (m['pct'] / 100) * (x1 - x0)
        ax.plot([marker_x], [y],
                marker='v', markersize=10, color='#111',
                markeredgecolor='white', markeredgewidth=0.6,
                zorder=5, transform=ax.transData)

        # ── 左侧：指标名 ─────────────────────────────────────────────────────
        ax.text(x0 - 0.015, y, m['name'],
                ha='right', va='center', fontsize=8.5, color='#222',
                transform=ax.transData)

        # ── 右侧：当前值 + 百分位 ─────────────────────────────────────────────
        pct_color = (_CMAP_BULL(m['pct'] / 100)
                     if m['direction'] == 'bull'
                     else _CMAP_BEAR(m['pct'] / 100))
        ax.text(x1 + 0.015, y + 0.14,
                m['value_str'],
                ha='left', va='center', fontsize=7.5, color='#333',
                transform=ax.transData)
        ax.text(x1 + 0.015, y - 0.14,
                f'{m["pct"]:.0f}%ile',
                ha='left', va='center', fontsize=7,
                color=pct_color, fontweight='bold',
                transform=ax.transData)

        # ── 零刻度线（百分位50处） ────────────────────────────────────────────
        mid_x = x0 + 0.5 * (x1 - x0)
        ax.plot([mid_x, mid_x], [y - bar_h/2, y + bar_h/2],
                color='white', linewidth=1.0, alpha=0.7, zorder=4,
                transform=ax.transData)

    # ── 底部综合评分条 ───────────────────────────────────────────────────────────
    y_comp = -0.48
    ax.text(x0 - 0.015, y_comp, '综合资金',
            ha='right', va='center', fontsize=8.5, color='#222',
            fontweight='bold', transform=ax.transData)
    ax.imshow(
        _GRAD,
        aspect='auto',
        extent=[x0, x1, y_comp - bar_h/2 * 0.8, y_comp + bar_h/2 * 0.8],
        cmap=_CMAP_BULL,
        alpha=0.55,
        zorder=1,
        transform=ax.transData,
    )
    ax.add_patch(plt.Rectangle(
        (x0, y_comp - bar_h/2 * 0.8), x1 - x0, bar_h * 0.8,
        fill=False, edgecolor='#888', linewidth=1.2, zorder=3,
        transform=ax.transData
    ))
    comp_marker_x = x0 + (composite / 100) * (x1 - x0)
    ax.plot([comp_marker_x], [y_comp],
            marker='D', markersize=9, color='#111',
            markeredgecolor='white', markeredgewidth=0.7,
            zorder=5, transform=ax.transData)

    if composite >= 65:
        comp_label = '资金偏多'
        comp_color = '#2ca02c'
    elif composite <= 35:
        comp_label = '资金偏空'
        comp_color = '#d62728'
    else:
        comp_label = '资金中性'
        comp_color = '#8c6d31'

    ax.text(x1 + 0.015, y_comp,
            f'{composite:.0f}%ile  {comp_label}',
            ha='left', va='center', fontsize=8,
            color=comp_color, fontweight='bold',
            transform=ax.transData)

    # ── 图例说明 ─────────────────────────────────────────────────────────────
    ax.text(0.5, n * row_gap - 0.05,
            f'{code} 资金博弈仪表盘（历史百分位）',
            ha='center', va='bottom', fontsize=9, fontweight='bold',
            color='#1a1a2e', transform=ax.transData)
    ax.text(0.5, n * row_gap - 0.28,
            '<< 历史偏空（0%ile）          中性（50%ile）          历史偏多（100%ile） >>',
            ha='center', va='bottom', fontsize=6.5,
            color='#666', transform=ax.transData)

    fig.subplots_adjust(left=0.02, right=0.98, top=0.96, bottom=0.04)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=140, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


# ── 叙事 ──────────────────────────────────────────────────────────────────────

def _build_narrative(ts_code: str, metrics: list[dict]) -> str:
    code = ts_code.split('.')[0]
    if not metrics:
        return f'【{code}】资金指标数据不足，无法生成仪表盘。'

    bull_pcts = [m['pct'] if m['direction'] == 'bull' else 100 - m['pct']
                 for m in metrics]
    composite = float(np.mean(bull_pcts))

    parts = []
    for m in metrics:
        bp = m['pct'] if m['direction'] == 'bull' else 100 - m['pct']
        if bp >= 70:
            parts.append(f"{m['name']}偏多（{m['pct']:.0f}%ile）")
        elif bp <= 30:
            parts.append(f"{m['name']}偏空（{m['pct']:.0f}%ile）")

    if composite >= 65:
        tone = '**整体资金偏多**'
    elif composite <= 35:
        tone = '**整体资金偏空**'
    else:
        tone = '资金博弈分歧'

    highlights = '；'.join(parts) if parts else '各指标均处历史中性区间'

    return (
        f"{code}各维度资金博弈综合百分位**{composite:.0f}%**，{tone}。"
        f"显著信号：{highlights}。"
        f"百分位越高代表该指标相对过去1年处于越强势位置——"
        f"高换手+高融资+高主力净流入同时出现时，短期上涨动能最强。"
    )


# ── 主入口 ────────────────────────────────────────────────────────────────────

def capital_dashboard(ts_code: str, trade_date: str) -> dict:
    result = {
        'ts_code': ts_code, 'trade_date': trade_date,
        'composite_pct': None, 'n_metrics': 0,
        'signal': '无数据', 'chart_b64': '', 'narrative': '', 'error': None,
    }

    metrics = _compute_metrics(ts_code, trade_date)
    # drop metrics where pct is NaN
    metrics = [m for m in metrics if not np.isnan(m.get('pct', float('nan')))]

    if not metrics:
        result['narrative'] = f'【{ts_code.split(".")[0]}】资金指标数据不足。'
        return result

    bull_pcts = [m['pct'] if m['direction'] == 'bull' else 100 - m['pct']
                 for m in metrics]
    composite = float(np.mean(bull_pcts))

    result['n_metrics']     = len(metrics)
    result['composite_pct'] = round(composite, 1)

    if composite >= 65:
        result['signal'] = '资金偏多'
    elif composite <= 35:
        result['signal'] = '资金偏空'
    else:
        result['signal'] = '资金中性'

    print(f'  资金仪表盘：{len(metrics)}项指标 综合{composite:.1f}%ile → {result["signal"]}')
    for m in metrics:
        print(f'    {m["name"]:8s} {m["value_str"]:>10s}  {m["pct"]:.0f}%ile')

    try:
        result['chart_b64'] = _build_chart(metrics, ts_code)
    except Exception as e:
        print(f'  [WARN] capital_dashboard chart: {e}')

    result['narrative'] = _build_narrative(ts_code, metrics)
    return result


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    for code in ['688981.SH', '000880.SZ']:
        print(f'\n{"="*50}\n{code}')
        r = capital_dashboard(code, '20260520')
        print(f'  信号: {r["signal"]}  综合: {r["composite_pct"]}%ile  指标数: {r["n_metrics"]}')
        print(f'  chart: {len(r["chart_b64"])} chars')
        print(f'  叙事: {r["narrative"][:120]}...')
