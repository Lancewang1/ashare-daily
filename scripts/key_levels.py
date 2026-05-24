"""
key_levels.py
=============
关键价位综合图（支撑/压力/行权价/解禁）

功能：
1. 拉取个股120个交易日收盘价历史
2. 通过局部极值 + 聚类计算支撑/压力位（最近2个）
3. 叠加股权激励行权价（活跃方案）
4. 叠加未来90日解禁日期（纵向标记）
5. 生成单一图表 + 60字以内散户叙事
6. 输出信号标签：近支撑位 / 近压力位 / 行权价锚定 / 解禁压力 / 价格中性区间
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

import tushare as ts
pro = ts.pro_api()


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def _fetch_daily(ts_code: str, trade_date: str) -> pd.DataFrame:
    """Fetch 120 trading days of price history (use 180-day calendar lookback)."""
    end_dt   = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=180)
    try:
        df = pro.daily(
            ts_code=ts_code,
            start_date=start_dt.strftime('%Y%m%d'),
            end_date=trade_date,
            fields='trade_date,open,close,high,low,vol',
        )
        time.sleep(0.2)
    except Exception as e:
        print(f'  [WARN] key_levels daily: {e}')
        return pd.DataFrame()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.copy()
    df['trade_date'] = df['trade_date'].astype(str)
    for col in ('open', 'close', 'high', 'low', 'vol'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['close'])
    df = df.sort_values('trade_date').tail(120).reset_index(drop=True)
    return df


def _fetch_incentive_prices(ts_code: str, trade_date: str) -> list[float]:
    """Return list of active exercise/grant/fair-value prices from stk_sf_cpn."""
    try:
        df = pro.stk_sf_cpn(ts_code=ts_code)
        time.sleep(0.2)
    except Exception as e:
        print(f'  [WARN] key_levels stk_sf_cpn: {e}')
        return []
    if df is None or len(df) == 0:
        return []
    df = df.copy()
    for col in ('ann_date', 'end_date'):
        if col in df.columns:
            df[col] = df[col].astype(str)
    for col in ('exercise_price', 'grant_price', 'fv_price'):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    prices = []
    for _, row in df.iterrows():
        end = str(row.get('end_date', '') or '')
        if end in ('', 'nan', 'None') or end >= trade_date:
            ep = None
            for field in ('exercise_price', 'grant_price', 'fv_price'):
                val = row.get(field)
                if pd.notna(val) and float(val) > 0:
                    ep = float(val)
                    break
            if ep is not None:
                prices.append(ep)
    return sorted(set(round(p, 2) for p in prices))


def _fetch_unlock_events(ts_code: str, trade_date: str) -> list[dict]:
    """Return upcoming unlock events within 90 days."""
    end_dt   = datetime.strptime(trade_date, '%Y%m%d')
    ahead_dt = end_dt + timedelta(days=90)
    try:
        df = pro.share_float(
            ts_code=ts_code,
            start_date=trade_date,
            end_date=ahead_dt.strftime('%Y%m%d'),
        )
        time.sleep(0.2)
    except Exception as e:
        print(f'  [WARN] key_levels share_float: {e}')
        return []
    if df is None or len(df) == 0:
        return []
    df = df.copy()
    df['float_date'] = df['float_date'].astype(str)
    for col in ('float_share', 'float_ratio'):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.sort_values('float_date').reset_index(drop=True)

    events = []
    for _, row in df.iterrows():
        fdate = str(row['float_date'])
        try:
            dt = datetime.strptime(fdate, '%Y%m%d')
        except Exception:
            continue
        days_to = (dt - end_dt).days
        yi = float(row['float_share']) / 1e4 if pd.notna(row.get('float_share')) else 0.0
        events.append({'date': fdate, 'days_to': days_to, 'yi': round(yi, 4)})
    return events


def _fetch_float_share(ts_code: str) -> float:
    """Return current float share count in 万股 (for ratio denominator)."""
    try:
        df = pro.stock_basic(ts_code=ts_code, fields='ts_code,float_share')
        time.sleep(0.15)
        if df is not None and len(df) > 0:
            v = pd.to_numeric(df.iloc[0].get('float_share', None), errors='coerce')
            if pd.notna(v):
                return float(v)
    except Exception:
        pass
    return 0.0


# ---------------------------------------------------------------------------
# Support / resistance calculation
# ---------------------------------------------------------------------------

def _local_extrema(close: np.ndarray, dates: list[str],
                   window: int = 5) -> tuple[list[tuple], list[tuple]]:
    """Return (lows, highs) as lists of (price, date, index) at local extrema."""
    n = len(close)
    lows: list[tuple] = []
    highs: list[tuple] = []
    for i in range(window, n - window):
        lo = close[i - window: i + window + 1]
        if close[i] == lo.min():
            lows.append((float(close[i]), dates[i], i))
        if close[i] == lo.max():
            highs.append((float(close[i]), dates[i], i))
    return lows, highs


def _cluster_levels(extrema: list[tuple], tol_pct: float = 1.5) -> list[dict]:
    """Cluster extrema (price, date, idx) within tol_pct% tolerance.
    Returns list of {price, dates, count} dicts sorted by price."""
    if not extrema:
        return []
    sorted_ex = sorted(extrema, key=lambda x: x[0])
    clusters: list[list[tuple]] = []
    for item in sorted_ex:
        p = item[0]
        merged = False
        for cluster in clusters:
            mean_p = np.mean([c[0] for c in cluster])
            if abs(p - mean_p) / mean_p * 100 <= tol_pct:
                cluster.append(item)
                merged = True
                break
        if not merged:
            clusters.append([item])
    result = []
    for cl in clusters:
        mean_p = round(float(np.mean([c[0] for c in cl])), 2)
        dates  = sorted(set(c[1] for c in cl))
        result.append({'price': mean_p, 'dates': dates, 'count': len(cl)})
    return result


def _compute_sr_levels(df: pd.DataFrame, current_price: float) -> dict:
    """Compute support/resistance levels from 120-day close, with originating dates."""
    close  = df['close'].values
    dates  = df['trade_date'].tolist()
    lows, highs = _local_extrema(close, dates, window=5)

    all_lows_cl  = _cluster_levels(lows)
    all_highs_cl = _cluster_levels(highs)

    supports    = sorted([c for c in all_lows_cl  if c['price'] < current_price],
                         key=lambda x: -x['price'])[:2]
    resistances = sorted([c for c in all_highs_cl if c['price'] > current_price],
                         key=lambda x: x['price'])[:2]

    # Fill if sparse
    if len(supports) < 2:
        extras = sorted([c for c in all_highs_cl if c['price'] < current_price],
                        key=lambda x: -x['price'])
        for c in extras:
            if len(supports) >= 2:
                break
            if not any(abs(c['price'] - s['price']) / current_price * 100 < 1.5
                       for s in supports):
                supports.append(c)
    if len(resistances) < 2:
        extras = sorted([c for c in all_lows_cl if c['price'] > current_price],
                        key=lambda x: x['price'])
        for c in extras:
            if len(resistances) >= 2:
                break
            if not any(abs(c['price'] - r['price']) / current_price * 100 < 1.5
                       for r in resistances):
                resistances.append(c)

    return {
        'support_clusters':    supports[:2],
        'resistance_clusters': resistances[:2],
        'support_levels':    [c['price'] for c in supports[:2]],
        'resistance_levels': [c['price'] for c in resistances[:2]],
    }


# ---------------------------------------------------------------------------
# Signal classification
# ---------------------------------------------------------------------------

def _classify_signal(current_price: float, supports: list[float], resistances: list[float],
                     exercise_prices: list[float], unlock_events: list[dict],
                     float_share_wan: float) -> str:
    # Near support (within 3%)
    for s in supports:
        if abs(current_price - s) / current_price <= 0.03:
            return '近支撑位'
    # Near resistance (within 3%)
    for r in resistances:
        if abs(current_price - r) / current_price <= 0.03:
            return '近压力位'
    # Exercise price anchor (within 5%)
    for ep in exercise_prices:
        if abs(current_price - ep) / current_price <= 0.05:
            return '行权价锚定'
    # Unlock pressure (within 30 days, ratio > 2%)
    float_yi = float_share_wan / 1e4 if float_share_wan > 0 else 0.0
    for ev in unlock_events:
        if ev['days_to'] <= 30:
            ratio_pct = (ev['yi'] / float_yi * 100) if float_yi > 0 else 0.0
            if ratio_pct > 2.0:
                return '解禁压力'
    return '价格中性区间'


# ---------------------------------------------------------------------------
# Chart
# ---------------------------------------------------------------------------

def _build_chart(df: pd.DataFrame, current_price: float,
                 support_clusters: list[dict], resistance_clusters: list[dict],
                 exercise_prices: list[float], unlock_events: list[dict],
                 ts_code: str) -> str:
    dates   = df['trade_date'].tolist()
    opens   = df['open'].tolist()
    closes  = df['close'].tolist()
    highs   = df['high'].tolist()
    lows_   = df['low'].tolist()
    n       = len(dates)

    supports    = [c['price'] for c in support_clusters]
    resistances = [c['price'] for c in resistance_clusters]

    fig, ax = plt.subplots(figsize=(9, 4.0), facecolor='white')
    fig.subplots_adjust(left=0.07, right=0.72, top=0.88, bottom=0.12)

    # ── Candlestick bars ──────────────────────────────────────────────────────
    bar_w = 0.6
    for i, (o, c, h, lo) in enumerate(zip(opens, closes, highs, lows_)):
        if any(v is None or (isinstance(v, float) and np.isnan(v)) for v in (o, c, h, lo)):
            continue
        color = '#2ca02c' if c >= o else '#d62728'
        # Wick (high-low)
        ax.plot([i, i], [lo, h], color=color, linewidth=0.8, zorder=2)
        # Body (open-close)
        body_lo = min(o, c)
        body_hi = max(o, c)
        ax.bar(i, body_hi - body_lo, bottom=body_lo, width=bar_w,
               color=color, alpha=0.85, zorder=3, linewidth=0)

    # ── Determine y range ────────────────────────────────────────────────────
    all_prices = [v for v in closes + supports + resistances + exercise_prices
                  if v is not None and not (isinstance(v, float) and np.isnan(v))]
    y_lo = min(all_prices) * 0.96
    y_hi = max(all_prices) * 1.04
    y_span = y_hi - y_lo if y_hi != y_lo else 1.0
    ax_right = n + 0.8

    # ── Support levels ───────────────────────────────────────────────────────
    for cl in support_clusters:
        s = cl['price']
        gap_pct = (s / current_price - 1) * 100
        date_lbl = cl['dates'][0][4:] if cl['dates'] else ''
        cnt = cl['count']
        ax.axhline(s, color='#2ca02c', linewidth=1.2, linestyle='--', alpha=0.85, zorder=2)
        ax.text(ax_right, s,
                f'支撑 {s:.2f} ({gap_pct:+.1f}%)\n触碰{cnt}次·{date_lbl}低',
                va='center', ha='left', fontsize=6.0, color='#2ca02c',
                clip_on=False, linespacing=1.4)

    # ── Resistance levels ────────────────────────────────────────────────────
    for cl in resistance_clusters:
        r = cl['price']
        gap_pct = (r / current_price - 1) * 100
        date_lbl = cl['dates'][-1][4:] if cl['dates'] else ''
        cnt = cl['count']
        ax.axhline(r, color='#d62728', linewidth=1.2, linestyle='--', alpha=0.85, zorder=2)
        ax.text(ax_right, r,
                f'压力 {r:.2f} ({gap_pct:+.1f}%)\n{cnt}次高点·{date_lbl}顶',
                va='center', ha='left', fontsize=6.0, color='#d62728',
                clip_on=False, linespacing=1.4)

    # ── Exercise prices ──────────────────────────────────────────────────────
    if exercise_prices:
        ep_avg = float(np.mean(exercise_prices))
        for ep in exercise_prices:
            ax.axhline(ep, color='#ff7f0e', linewidth=1.3, linestyle='-', alpha=0.88, zorder=4)
        ax.text(ax_right, ep_avg, f'行权价 {ep_avg:.2f}',
                va='center', ha='left', fontsize=6.0, color='#ff7f0e', clip_on=False)

    # ── Unlock events ────────────────────────────────────────────────────────
    date_to_x = {d: i for i, d in enumerate(dates)}
    mid_price = float(np.median(closes))
    for idx_ev, ev in enumerate(unlock_events):
        fdate = ev['date']
        if fdate in date_to_x:
            xi = date_to_x[fdate]
            ax.axvline(xi, color='#9467bd', linewidth=0.9, linestyle=':', alpha=0.75, zorder=2)
            ax.text(xi + 0.3, y_hi * 0.98,
                    f'+{ev["days_to"]}天\n{ev["yi"]:.2f}亿',
                    ha='left', va='top', fontsize=5.5, color='#9467bd',
                    clip_on=False, linespacing=1.3)
        else:
            ax.text(n + 0.5,
                    mid_price + y_span * 0.12 * idx_ev,
                    f'解禁{fdate[4:6]}-{fdate[6:]} {ev["yi"]:.2f}亿',
                    ha='left', va='center', fontsize=5.5, color='#9467bd',
                    clip_on=False)

    # ── Current price line ────────────────────────────────────────────────────
    ax.axhline(current_price, color='#555', linewidth=0.8, linestyle=':', alpha=0.6, zorder=1)

    # ── X-axis ───────────────────────────────────────────────────────────────
    step = max(1, n // 6)
    xtick_idx = list(range(0, n, step))
    ax.set_xticks(xtick_idx)
    ax.set_xticklabels([dates[i][4:] for i in xtick_idx], fontsize=6, rotation=20)
    ax.set_xlim(-0.5, n - 0.5)
    ax.set_ylim(y_lo, y_hi)

    code_disp = ts_code.split('.')[0]
    ax.set_title(f'{code_disp} 关键价位 K 线图（支撑/压力/行权价）',
                 fontsize=9, fontweight='bold', pad=6)
    ax.tick_params(axis='y', labelsize=6)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------

def _build_narrative(ts_code: str, current_price: float,
                     support_clusters: list[dict], resistance_clusters: list[dict],
                     exercise_prices: list[float], unlock_events: list[dict],
                     signal: str) -> str:
    code = ts_code.split('.')[0]
    parts = [f'{code}当前价{current_price:.2f}元。']

    for cl in support_clusters[:2]:
        s = cl['price']
        gap = (s / current_price - 1) * 100
        cnt = cl['count']
        date_str = cl['dates'][0][4:] if cl['dates'] else ''
        why = f'（{date_lbl}附近触底{cnt}次确认）' if (date_lbl := date_str) else f'（触碰{cnt}次）'
        parts.append(f'**支撑{s:.2f}**{gap:+.1f}%{why}。')

    for cl in resistance_clusters[:2]:
        r = cl['price']
        gap = (r / current_price - 1) * 100
        cnt = cl['count']
        date_str = cl['dates'][-1][4:] if cl['dates'] else ''
        why = f'（{date_lbl}压制{cnt}次）' if (date_lbl := date_str) else f'（压制{cnt}次）'
        parts.append(f'**压力{r:.2f}**{gap:+.1f}%{why}。')

    if exercise_prices:
        ep_avg = float(np.mean(exercise_prices))
        if abs(ep_avg / current_price - 1) <= 0.20:
            dist = (ep_avg / current_price - 1) * 100
            parts.append(f'股权激励行权价{ep_avg:.2f}（{dist:+.1f}%）形成价格锚定。')

    if unlock_events:
        near = [e for e in unlock_events if e['days_to'] <= 60]
        if near:
            ev = near[0]
            parts.append(f'{ev["days_to"]}天后解禁{ev["yi"]:.2f}亿股，关注供给压力。')

    return ''.join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def key_levels(ts_code: str, trade_date: str) -> dict:
    """
    综合关键价位分析。

    Parameters
    ----------
    ts_code    : tushare 股票代码，如 '688981.SH'
    trade_date : 报告日期，格式 'YYYYMMDD'

    Returns
    -------
    dict with keys:
        ts_code, trade_date, current_price,
        support_levels, resistance_levels,
        exercise_prices, unlock_dates,
        signal, chart_b64, narrative, error
    """
    result: dict = {
        'ts_code':           ts_code,
        'trade_date':        trade_date,
        'current_price':     None,
        'support_levels':    [],
        'resistance_levels': [],
        'exercise_prices':   [],
        'unlock_dates':      [],
        'signal':            '价格中性区间',
        'chart_b64':         '',
        'narrative':         '',
        'error':             None,
    }

    # 1. Price history
    df = _fetch_daily(ts_code, trade_date)
    if len(df) < 10:
        result['error'] = f'价格数据不足（{len(df)} 行）'
        result['narrative'] = f'【{ts_code.split(".")[0]}】历史行情数据不足，无法生成关键价位图。'
        return result

    current_price = float(df['close'].iloc[-1])
    result['current_price'] = round(current_price, 2)

    # 2. Support / resistance
    sr = _compute_sr_levels(df, current_price)
    support_clusters    = sr['support_clusters']
    resistance_clusters = sr['resistance_clusters']
    supports    = sr['support_levels']
    resistances = sr['resistance_levels']
    result['support_levels']    = supports
    result['resistance_levels'] = resistances

    # 3. Exercise prices
    exercise_prices = _fetch_incentive_prices(ts_code, trade_date)
    result['exercise_prices'] = exercise_prices

    # 4. Unlock events
    unlock_events = _fetch_unlock_events(ts_code, trade_date)
    result['unlock_dates'] = unlock_events

    # 5. Float share for ratio
    float_share_wan = _fetch_float_share(ts_code)

    # 6. Signal
    signal = _classify_signal(
        current_price, supports, resistances,
        exercise_prices, unlock_events, float_share_wan,
    )
    result['signal'] = signal

    print(
        f'  关键价位：当前{current_price:.2f} '
        f'支撑{supports} 压力{resistances} '
        f'行权{exercise_prices} 解禁{len(unlock_events)}批 '
        f'→ {signal}'
    )

    # 7. Chart (candlestick)
    try:
        result['chart_b64'] = _build_chart(
            df, current_price, support_clusters, resistance_clusters,
            exercise_prices, unlock_events, ts_code,
        )
    except Exception as e:
        print(f'  [WARN] key_levels chart: {e}')
        result['error'] = str(e)

    # 8. Narrative
    result['narrative'] = _build_narrative(
        ts_code, current_price, support_clusters, resistance_clusters,
        exercise_prices, unlock_events, signal,
    )

    return result


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')

    TEST_CODE = '688981.SH'
    TEST_DATE = '20260520'

    print(f'\n{"=" * 55}')
    print(f'Testing key_levels: {TEST_CODE}  {TEST_DATE}')
    print('=' * 55)

    r = key_levels(TEST_CODE, TEST_DATE)

    print(f'  当前价格    : {r["current_price"]}')
    print(f'  支撑位      : {r["support_levels"]}')
    print(f'  压力位      : {r["resistance_levels"]}')
    print(f'  行权价      : {r["exercise_prices"]}')
    print(f'  解禁事件    : {len(r["unlock_dates"])} 批')
    for ev in r['unlock_dates']:
        print(f'    {ev["date"]}  +{ev["days_to"]}天  {ev["yi"]:.4f}亿股')
    print(f'  信号        : {r["signal"]}')
    print(f'  叙事        : {r["narrative"]}')
    print(f'  图表        : {"已生成 (" + str(len(r["chart_b64"])) + " bytes base64)" if r["chart_b64"] else "无"}')
    if r['error']:
        print(f'  错误        : {r["error"]}')
