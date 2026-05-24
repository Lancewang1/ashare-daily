"""
capital_dashboard.py
====================
资金博弈仪表盘  — 7指标固定雷达版

7个固定信号槽（无数据时用中性50%ile占位，保证雷达始终7点）：
1. 换手率          daily_basic.turnover_rate_f     1-year
2. 融资余额增速     margin_detail.rzye 5d变化       1-year
3. 主力净流入       moneyflow                       60-day
4. 北向持股变化     hk_hold ratio 5d change         60-day
5. 融券/融资比      rqye / rzye                     1-year（空头方向）
6. 大宗成交         block_trade amount              40-day（规模评分）
7. 机构调研强度     stk_surv 近30日 vs 前30日        60-day
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


def _fetch_block_trade(ts_code: str, trade_date: str) -> pd.DataFrame:
    """Recent 40-day block trades — returns discount rate series."""
    try:
        df = pro.block_trade(
            ts_code=ts_code,
            start_date=_lookback(trade_date, 40),
            end_date=trade_date,
        )
        time.sleep(0.2)
        if df is None or len(df) == 0:
            return pd.DataFrame()
        df = df.copy()
        df['trade_date'] = df['trade_date'].astype(str)
        for col in ('price', 'vol', 'amount'):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        return df.sort_values('trade_date').reset_index(drop=True)
    except Exception as e:
        print(f'  [WARN] capital block_trade: {e}')
        return pd.DataFrame()


def _fetch_insider(ts_code: str, trade_date: str) -> pd.DataFrame:
    """Insider trades in past 90 days (stk_holdertrade)."""
    try:
        df = pro.stk_holdertrade(
            ts_code=ts_code,
            start_date=_lookback(trade_date, 90),
            end_date=trade_date,
        )
        time.sleep(0.2)
        if df is None or len(df) == 0:
            return pd.DataFrame()
        df = df.copy()
        for col in ('change_vol', 'change_ratio', 'avg_price'):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        return df.reset_index(drop=True)
    except Exception as e:
        print(f'  [WARN] capital insider: {e}')
        return pd.DataFrame()


def _fetch_inst_survey_score(ts_code: str, trade_date: str) -> dict:
    """Fetch institution survey data; returns pct score + value_str."""
    end_dt   = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=65)
    cut30    = (end_dt - timedelta(days=30)).strftime('%Y%m%d')
    try:
        df = pro.stk_surv(
            ts_code=ts_code,
            start_date=start_dt.strftime('%Y%m%d'),
            end_date=trade_date,
        )
        time.sleep(0.2)
        if df is None or len(df) == 0:
            return {}
        date_col = next((c for c in ['surv_date', 'rece_date'] if c in df.columns), None)
        if date_col is None:
            return {}
        df['_d'] = df[date_col].astype(str)
        recent30 = int((df['_d'] >= cut30).sum())
        prev30   = int((df['_d'] < cut30).sum())
        accel    = recent30 / max(prev30, 1) if prev30 > 0 else (2.5 if recent30 > 0 else 1.0)
        if recent30 >= 5 and accel >= 2:
            pct = 80.0;  val = f'{recent30}次↑×{accel:.1f}'
        elif recent30 >= 3 or (recent30 >= 1 and accel >= 1.5):
            pct = 65.0;  val = f'{recent30}次'
        elif recent30 >= 1:
            pct = 52.0;  val = f'{recent30}次'
        elif prev30 > 0:
            pct = 32.0;  val = '近期减少'
        else:
            pct = 42.0;  val = '暂无'
        return {'pct': pct, 'value_str': val, 'n_recent': recent30, 'n_prev': prev30}
    except Exception as e:
        print(f'  [WARN] capital inst_survey: {e}')
        return {}


# ── 计算各指标的历史百分位 ────────────────────────────────────────────────────

def _pct_of(series: pd.Series, current_val: float) -> float:
    """Return percentile (0-100) of current_val in the series."""
    clean = series.dropna()
    if len(clean) < 5:
        return 50.0
    return float(percentileofscore(clean, current_val, kind='rank'))


def _compute_metrics(ts_code: str, trade_date: str) -> list[dict]:
    """Return exactly 7 metric dicts (neutral 50%ile fallback for missing data)."""

    # Pre-define 7 canonical slots — always returned in this order
    slots = [
        {'name': '换手率',      'value_str': 'N/A', 'pct': 50.0, 'direction': 'bull', 'raw': None},
        {'name': '融资余额增速', 'value_str': 'N/A', 'pct': 50.0, 'direction': 'bull', 'raw': None},
        {'name': '主力净流入',   'value_str': 'N/A', 'pct': 50.0, 'direction': 'bull', 'raw': None},
        {'name': '北向持股变化', 'value_str': 'N/A', 'pct': 50.0, 'direction': 'bull', 'raw': None},
        {'name': '融券/融资比',  'value_str': 'N/A', 'pct': 50.0, 'direction': 'bear', 'raw': None},
        {'name': '大宗成交',     'value_str': 'N/A', 'pct': 50.0, 'direction': 'bull', 'raw': None},
        {'name': '机构调研',     'value_str': 'N/A', 'pct': 50.0, 'direction': 'bull', 'raw': None},
    ]

    # ── 1. 换手率 ─────────────────────────────────────────────────────────────
    db = _fetch_daily_basic(ts_code, trade_date)
    if len(db) >= 10:
        curr_tr = db['turnover_rate_f'].iloc[-1]
        slots[0].update({
            'value_str': f'{curr_tr:.2f}%',
            'pct': _pct_of(db['turnover_rate_f'], curr_tr),
            'raw': curr_tr,
        })

    # ── 2. 融资余额 5日增速 ────────────────────────────────────────────────────
    mg = _fetch_margin(ts_code, trade_date)
    rqye_series = None
    if len(mg) >= 10 and 'rzye' in mg.columns:
        mg_nonan = mg['rzye'].dropna()
        if len(mg_nonan) >= 5:
            rzye_vals = mg['rzye'].dropna().reset_index(drop=True)
            change5 = rzye_vals.diff(5)
            curr_chg = float(change5.iloc[-1]) if not pd.isna(change5.iloc[-1]) else 0.0
            slots[1].update({
                'value_str': f'{curr_chg/1e8:+.1f}亿',
                'pct': _pct_of(change5.dropna(), curr_chg),
                'raw': curr_chg,
            })
        if 'rqye' in mg.columns:
            rqye_series = mg['rqye'].dropna()

    # ── 3. 主力净流入 ─────────────────────────────────────────────────────────
    mf = _fetch_moneyflow(ts_code, trade_date)
    if len(mf) >= 5:
        if 'net_mf_amount' in mf.columns:
            net_series = mf['net_mf_amount']
        else:
            buy  = mf.get('buy_elg_amount', pd.Series(0, index=mf.index)).fillna(0) \
                 + mf.get('buy_lg_amount',  pd.Series(0, index=mf.index)).fillna(0)
            sell = mf.get('sell_elg_amount', pd.Series(0, index=mf.index)).fillna(0) \
                 + mf.get('sell_lg_amount',  pd.Series(0, index=mf.index)).fillna(0)
            net_series = buy - sell
        curr_net = float(net_series.iloc[-1])
        slots[2].update({
            'value_str': f'{curr_net/1e4:+.1f}亿',
            'pct': _pct_of(net_series, curr_net),
            'raw': curr_net,
        })

    # ── 4. 北向持股5日变化 ─────────────────────────────────────────────────────
    hk = _fetch_hk_hold(ts_code, trade_date)
    if len(hk) >= 10 and 'ratio' in hk.columns:
        ratio_nonan = hk['ratio'].dropna()
        if len(ratio_nonan) >= 5:
            ratio_change = ratio_nonan.diff(5)
            curr_rc = float(ratio_change.iloc[-1]) if not pd.isna(ratio_change.iloc[-1]) else 0.0
            slots[3].update({
                'value_str': f'{curr_rc:+.2f}pp',
                'pct': _pct_of(ratio_change.dropna(), curr_rc),
                'raw': curr_rc,
            })

    # ── 5. 融券/融资比（空头压力，无数据保留中性占位） ────────────────────────
    if rqye_series is not None and len(rqye_series) >= 5 and len(mg) >= 5:
        rzye_s = mg['rzye'].dropna()
        if len(rzye_s) >= 5:
            ratio_series = (rqye_series.reset_index(drop=True)
                            / rzye_s.reset_index(drop=True).clip(lower=1e6))
            curr_ratio = float(ratio_series.iloc[-1]) if not ratio_series.empty else float('nan')
            if not np.isnan(curr_ratio):
                slots[4].update({
                    'value_str': f'{curr_ratio*100:.1f}%',
                    'pct': _pct_of(ratio_series.dropna(), curr_ratio),
                    'raw': curr_ratio,
                })

    # ── 6. 大宗交易（最近40日，无大宗则用规模评分中性占位） ──────────────────
    try:
        bt_df = _fetch_block_trade(ts_code, trade_date)
        if not bt_df.empty and 'amount' in bt_df.columns:
            total_amt = float(bt_df['amount'].dropna().sum())
            bt_pct = 68.0 if total_amt > 5e8 else (58.0 if total_amt > 1e8 else
                     (48.0 if total_amt > 0 else 44.0))
            slots[5].update({
                'value_str': f'{total_amt/1e8:.1f}亿',
                'pct': bt_pct,
                'raw': total_amt,
            })
    except Exception as e:
        print(f'  [WARN] capital block_trade metric: {e}')

    # ── 7. 机构调研强度（无调研记录则中性占位） ───────────────────────────────
    try:
        surv = _fetch_inst_survey_score(ts_code, trade_date)
        if surv:
            slots[6].update({
                'value_str': surv['value_str'],
                'pct': surv['pct'],
                'raw': surv.get('n_recent', 0),
            })
    except Exception as e:
        print(f'  [WARN] capital inst_survey metric: {e}')

    return slots


# ── 图表构建（雷达图） ────────────────────────────────────────────────────────

def _build_chart(metrics: list[dict], ts_code: str) -> str:
    n = len(metrics)
    if n < 3:
        return ''

    code = ts_code.split('.')[0]

    # Convert all to bull-direction percentile
    bull_pcts = np.array([
        m['pct'] if m['direction'] == 'bull' else (100 - m['pct'])
        for m in metrics
    ], dtype=float)
    labels     = [m['name']      for m in metrics]
    value_strs = [m['value_str'] for m in metrics]
    raw_pcts   = [m['pct']       for m in metrics]
    composite  = float(np.nanmean(bull_pcts))

    if composite >= 65:
        main_color, comp_label = '#2ca02c', '资金偏多'
    elif composite <= 35:
        main_color, comp_label = '#d62728', '资金偏空'
    else:
        main_color, comp_label = '#e8a500', '资金中性'

    # Angles: evenly spaced, start from top (π/2), counterclockwise
    angles = np.linspace(np.pi / 2, np.pi / 2 + 2 * np.pi, n, endpoint=False)
    vals   = bull_pcts / 100.0

    # Close polygon
    angles_c = np.append(angles, angles[0])
    vals_c   = np.append(vals,   vals[0])

    fig, ax = plt.subplots(figsize=(5.5, 5.0),
                           subplot_kw={'projection': 'polar'},
                           facecolor='white')
    ax.set_facecolor('#fafafa')

    # Reference rings
    theta_ring = np.linspace(0, 2 * np.pi, 300)
    for r, ls, alpha in [(0.25, ':', 0.4), (0.50, '--', 0.42),
                         (0.75, ':', 0.4), (1.0, '-', 0.55)]:
        ax.plot(theta_ring, [r] * 300, color='#ccc', lw=0.8,
                ls=ls, alpha=alpha, zorder=1)

    # Axis spokes
    for angle in angles:
        ax.plot([angle, angle], [0, 1.0], color='#ddd', lw=0.9, zorder=1)

    # Filled polygon
    ax.fill(angles_c, vals_c, alpha=0.20, color=main_color, zorder=2)
    ax.plot(angles_c, vals_c, color=main_color, lw=2.2, zorder=3)
    ax.scatter(angles, vals, s=65, color=main_color,
               edgecolors='white', linewidths=1.5, zorder=4)

    # Clean up ticks / spine
    ax.set_xticks([])
    ax.set_yticks([])
    ax.spines['polar'].set_visible(False)
    ax.set_ylim(0, 1.6)

    # Ring percentage labels near first spoke
    ref_angle = angles[0] + 0.08
    for r, lbl in [(0.25, '25%'), (0.5, '50%'), (0.75, '75%'), (1.0, '100%')]:
        ax.text(ref_angle, r + 0.01, lbl, fontsize=6, color='#bbb',
                ha='left', va='bottom', zorder=5)

    # Metric labels at each vertex
    for angle, label, val_str, pct, bp in zip(
            angles, labels, value_strs, raw_pcts, bull_pcts):
        r_lbl = 1.28
        txt_color = ('#2ca02c' if bp >= 65
                     else ('#d62728' if bp <= 35 else '#555'))
        weight = 'bold' if abs(bp - 50) >= 25 else 'normal'
        ax.text(angle, r_lbl,
                f'{label}\n{val_str}  {pct:.0f}%ile',
                ha='center', va='center', fontsize=8,
                color=txt_color, fontweight=weight, zorder=6)

    # Center composite badge
    ax.text(0, 0,
            f'{composite:.0f}%ile\n{comp_label}',
            ha='center', va='center', fontsize=12, fontweight='bold',
            color=main_color, zorder=7,
            bbox=dict(boxstyle='round,pad=0.35', facecolor='white',
                      edgecolor=main_color, alpha=0.95, linewidth=1.8))

    ax.set_title(f'{code} 资金博弈雷达（历史百分位）',
                 fontsize=10, fontweight='bold', color='#1a1a2e', pad=18)

    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white')
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

    real_metrics = [m for m in metrics if m.get('value_str', 'N/A') != 'N/A']
    na_count     = len(metrics) - len(real_metrics)

    parts = []
    for m in real_metrics:
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
    na_note = f'（{na_count}项无历史数据，以中性50%ile占位）' if na_count > 0 else ''

    return (
        f"{code}7维资金博弈综合百分位**{composite:.0f}%**，{tone}{na_note}。"
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
    # Always 7 slots; sanitise any NaN pct to 50 (neutral)
    for m in metrics:
        if np.isnan(m.get('pct', float('nan'))):
            m['pct'] = 50.0

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
