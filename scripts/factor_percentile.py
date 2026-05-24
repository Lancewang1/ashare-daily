"""
factor_percentile.py
====================
量化因子百分位图

功能：
1. 拉取个股近1年 daily + daily_basic 数据
2. 计算6个核心技术/量能因子的历史百分位（自身历史，非横截面）
3. 生成水平条形图（0-100%ile），直观展示当前因子强弱
4. 输出说明性文字

因子清单：
1. 昨日涨幅       → pct_chg 1-year 百分位
2. 5日累计涨幅    → 5d momentum 1-year 百分位
3. 20日累计涨幅   → 20d momentum 1-year 百分位
4. 换手率         → turnover_rate_f 1-year 百分位
5. 量比           → volume_ratio 1-year 百分位
6. RSI(14)        → 14-day RSI 1-year 百分位
"""

from __future__ import annotations
import io, base64, time
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

import tushare as ts
pro = ts.pro_api()


# ── Data fetchers ─────────────────────────────────────────────────────────────

def _fetch_daily(ts_code: str, trade_date: str) -> pd.DataFrame:
    end_dt   = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=400)  # 1-year trading days ≈ 250
    try:
        df = pro.daily(
            ts_code=ts_code,
            start_date=start_dt.strftime('%Y%m%d'),
            end_date=trade_date,
            fields='trade_date,close,pct_chg',
        )
        time.sleep(0.2)
    except Exception as e:
        print(f'  [WARN] factor_pct daily: {e}')
        return pd.DataFrame()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df['trade_date'] = df['trade_date'].astype(str)
    for col in ('close', 'pct_chg'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.sort_values('trade_date').reset_index(drop=True)


def _fetch_daily_basic(ts_code: str, trade_date: str) -> pd.DataFrame:
    end_dt   = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=400)
    try:
        df = pro.daily_basic(
            ts_code=ts_code,
            start_date=start_dt.strftime('%Y%m%d'),
            end_date=trade_date,
            fields='trade_date,turnover_rate_f,volume_ratio',
        )
        time.sleep(0.2)
    except Exception as e:
        print(f'  [WARN] factor_pct daily_basic: {e}')
        return pd.DataFrame()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df['trade_date'] = df['trade_date'].astype(str)
    for col in ('turnover_rate_f', 'volume_ratio'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.sort_values('trade_date').reset_index(drop=True)


# ── Factor computation ────────────────────────────────────────────────────────

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_l = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _pct_rank(series: pd.Series, current_val: float) -> float:
    clean = series.dropna()
    if len(clean) < 10:
        return 50.0
    return round(float(percentileofscore(clean, current_val, kind='rank')), 1)


def _compute_factors(daily: pd.DataFrame, basic: pd.DataFrame) -> list[dict]:
    if len(daily) < 25:
        return []

    merged = daily.merge(basic, on='trade_date', how='left')
    merged = merged.sort_values('trade_date').reset_index(drop=True)

    close = merged['close']

    # Rolling momentum
    ret5  = close.pct_change(5) * 100
    ret20 = close.pct_change(20) * 100
    rsi14 = _rsi(close, 14)

    factors = []

    # 1. 昨日涨幅
    pchg = merged['pct_chg']
    if not pd.isna(pchg.iloc[-1]):
        v = float(pchg.iloc[-1])
        factors.append({
            'name': '昨日涨幅',
            'value': v,
            'value_str': f'{v:+.2f}%',
            'pct': _pct_rank(pchg, v),
            'direction': 'bull',
        })

    # 2. 5日动量
    if not pd.isna(ret5.iloc[-1]):
        v = float(ret5.iloc[-1])
        factors.append({
            'name': '5日累计涨幅',
            'value': v,
            'value_str': f'{v:+.1f}%',
            'pct': _pct_rank(ret5.dropna(), v),
            'direction': 'bull',
        })

    # 3. 20日动量
    if not pd.isna(ret20.iloc[-1]):
        v = float(ret20.iloc[-1])
        factors.append({
            'name': '20日累计涨幅',
            'value': v,
            'value_str': f'{v:+.1f}%',
            'pct': _pct_rank(ret20.dropna(), v),
            'direction': 'bull',
        })

    # 4. 换手率
    tr = merged.get('turnover_rate_f', pd.Series(dtype=float))
    if tr is not None and not pd.isna(tr.iloc[-1]):
        v = float(tr.iloc[-1])
        factors.append({
            'name': '换手率',
            'value': v,
            'value_str': f'{v:.2f}%',
            'pct': _pct_rank(tr.dropna(), v),
            'direction': 'bull',
        })

    # 5. 量比
    vr = merged.get('volume_ratio', pd.Series(dtype=float))
    if vr is not None and not pd.isna(vr.iloc[-1]):
        v = float(vr.iloc[-1])
        factors.append({
            'name': '量比',
            'value': v,
            'value_str': f'{v:.2f}x',
            'pct': _pct_rank(vr.dropna(), v),
            'direction': 'bull',
        })

    # 6. RSI(14)
    if not pd.isna(rsi14.iloc[-1]):
        v = float(rsi14.iloc[-1])
        factors.append({
            'name': 'RSI(14)',
            'value': v,
            'value_str': f'{v:.1f}',
            'pct': _pct_rank(rsi14.dropna(), v),
            'direction': 'bull',
        })

    return factors


# ── Chart builder (radar) ─────────────────────────────────────────────────────

def _build_chart(factors: list[dict], ts_code: str) -> str:
    """Build a radar (spider) chart for factor percentiles, matching capital_dashboard style."""
    if not factors:
        return ''

    code       = ts_code.split('.')[0]
    n          = len(factors)
    labels     = [f['name'] for f in factors]
    pcts       = np.array([f['pct'] for f in factors], dtype=float)
    value_strs = [f['value_str'] for f in factors]
    composite  = float(np.nanmean(pcts))

    if composite >= 65:
        main_color, comp_label = '#2ca02c', '因子偏强'
    elif composite <= 35:
        main_color, comp_label = '#d62728', '因子偏弱'
    else:
        main_color, comp_label = '#e8a500', '因子中性'

    vals = pcts / 100.0
    angles = np.linspace(np.pi / 2, np.pi / 2 + 2 * np.pi, n, endpoint=False)
    angles_c = np.append(angles, angles[0])
    vals_c   = np.append(vals, vals[0])

    fig, ax = plt.subplots(figsize=(5.5, 5.0),
                           subplot_kw={'projection': 'polar'},
                           facecolor='white')
    ax.set_facecolor('#fafafa')

    theta_ring = np.linspace(0, 2 * np.pi, 300)
    for r, ls, alpha in [(0.25, ':', 0.4), (0.50, '--', 0.42),
                         (0.75, ':', 0.4), (1.0, '-', 0.55)]:
        ax.plot(theta_ring, [r] * 300, color='#ccc', lw=0.8,
                ls=ls, alpha=alpha, zorder=1)
    for angle in angles:
        ax.plot([angle, angle], [0, 1.0], color='#ddd', lw=0.9, zorder=1)

    ax.fill(angles_c, vals_c, alpha=0.20, color=main_color, zorder=2)
    ax.plot(angles_c, vals_c, color=main_color, lw=2.2, zorder=3)
    ax.scatter(angles, vals, s=65, color=main_color,
               edgecolors='white', linewidths=1.5, zorder=4)

    ax.set_xticks([])
    ax.set_yticks([])
    ax.spines['polar'].set_visible(False)
    ax.set_ylim(0, 1.6)

    ref_angle = angles[0] + 0.08
    for r, lbl in [(0.25, '25%'), (0.5, '50%'), (0.75, '75%'), (1.0, '100%')]:
        ax.text(ref_angle, r + 0.01, lbl, fontsize=6, color='#bbb',
                ha='left', va='bottom', zorder=5)

    for angle, label, val_str, pct in zip(angles, labels, value_strs, pcts):
        r_lbl = 1.28
        txt_color = ('#2ca02c' if pct >= 65 else ('#d62728' if pct <= 35 else '#555'))
        weight = 'bold' if abs(pct - 50) >= 25 else 'normal'
        ax.text(angle, r_lbl,
                f'{label}\n{val_str}  {pct:.0f}%ile',
                ha='center', va='center', fontsize=8,
                color=txt_color, fontweight=weight, zorder=6)

    ax.text(0, 0,
            f'{composite:.0f}%ile\n{comp_label}',
            ha='center', va='center', fontsize=12, fontweight='bold',
            color=main_color, zorder=7,
            bbox=dict(boxstyle='round,pad=0.35', facecolor='white',
                      edgecolor=main_color, alpha=0.95, linewidth=1.8))

    ax.set_title(f'{code} 量化因子雷达（自身历史1年）',
                 fontsize=10, fontweight='bold', color='#1a1a2e', pad=18)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


# ── Narrative ─────────────────────────────────────────────────────────────────

def _build_narrative(ts_code: str, factors: list[dict]) -> str:
    code = ts_code.split('.')[0]
    if not factors:
        return f'【{code}】因子数据不足。'

    strong  = [f for f in factors if f['pct'] >= 65]
    weak    = [f for f in factors if f['pct'] <= 35]
    avg_pct = float(np.mean([f['pct'] for f in factors]))

    if avg_pct >= 65:
        tone = '**量化因子综合极强**'
    elif avg_pct <= 35:
        tone = '**量化因子综合偏弱**'
    else:
        tone = '量化因子综合中性'

    s_names = '、'.join(f['name'] for f in strong) or '无'
    w_names = '、'.join(f['name'] for f in weak) or '无'
    return (
        f'{code}量化因子综合百分位**{avg_pct:.0f}%**，{tone}。'
        f'强势因子：{s_names}；弱势因子：{w_names}。'
        f'百分位代表当前值在过去1年自身历史中的排位，越高代表越强。'
    )


# ── Public API ────────────────────────────────────────────────────────────────

def factor_percentile(ts_code: str, trade_date: str) -> dict:
    result = {
        'ts_code': ts_code, 'trade_date': trade_date,
        'factors': [], 'chart_b64': '', 'narrative': '', 'error': None,
    }

    daily = _fetch_daily(ts_code, trade_date)
    basic = _fetch_daily_basic(ts_code, trade_date)

    if len(daily) < 25:
        result['error'] = f'日线数据不足({len(daily)}行)'
        result['narrative'] = f'【{ts_code.split(".")[0]}】历史数据不足，无法生成因子百分位图。'
        return result

    factors = _compute_factors(daily, basic)
    result['factors'] = factors

    if not factors:
        result['narrative'] = f'【{ts_code.split(".")[0]}】因子计算失败。'
        return result

    for f in factors:
        print(f'    {f["name"]:10s} {f["value_str"]:>10s}  {f["pct"]:.0f}%ile')

    try:
        result['chart_b64'] = _build_chart(factors, ts_code)
    except Exception as e:
        print(f'  [WARN] factor_percentile chart: {e}')
        result['error'] = str(e)

    result['narrative'] = _build_narrative(ts_code, factors)
    return result


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    r = factor_percentile('688981.SH', '20260520')
    print(f'  因子数: {len(r["factors"])}')
    print(f'  叙事: {r["narrative"]}')
    print(f'  图表: {"已生成 " + str(len(r["chart_b64"])) + " bytes" if r["chart_b64"] else "无"}')
