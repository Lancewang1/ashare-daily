"""
northbound_flow.py
==================
北向资金持股变化分析

功能：
1. 拉取个股沪深港通北向持仓数据（hk_hold API）
2. 计算近60日持仓量、占流通比变化趋势
3. 信号：北向持续加仓 / 小幅增持 / 稳定 / 小幅减仓 / 明显减仓
4. 生成双轴图表（持仓量柱状 + 持股比例折线）+ 散户叙事
"""

from __future__ import annotations
import io, time, base64
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


def _fetch_hk_hold(ts_code: str, start_dt: str, trade_date: str) -> pd.DataFrame:
    """Fetch northbound holding data, with fallback if ts_code filter returns nothing."""
    try:
        df = pro.hk_hold(ts_code=ts_code, start_date=start_dt, end_date=trade_date)
        time.sleep(0.2)
    except Exception as e:
        print(f'  [WARN] hk_hold (with ts_code): {e}')
        df = pd.DataFrame()

    if df is not None and len(df) > 0:
        return df

    # Fallback: some API versions need exchange field; try without ts_code
    try:
        df_sh = pro.hk_hold(exchange='SH', start_date=start_dt, end_date=trade_date)
        time.sleep(0.2)
        df_sz = pro.hk_hold(exchange='SZ', start_date=start_dt, end_date=trade_date)
        time.sleep(0.2)
        df_all = pd.concat([df_sh or pd.DataFrame(), df_sz or pd.DataFrame()], ignore_index=True)
        if len(df_all) > 0:
            code_stripped = ts_code.split('.')[0]
            mask = df_all['ts_code'].astype(str).str.startswith(code_stripped)
            df_filtered = df_all[mask].copy()
            if len(df_filtered) > 0:
                return df_filtered
    except Exception as e:
        print(f'  [WARN] hk_hold fallback: {e}')

    return pd.DataFrame()


def _compute_metrics(df: pd.DataFrame) -> dict:
    """Compute northbound holding metrics from raw dataframe."""
    df = df.copy()
    df['trade_date'] = df['trade_date'].astype(str)
    for col in ['vol', 'ratio', 'close', 'market_cap']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    df = df.sort_values('trade_date').reset_index(drop=True)

    # Take last 60 trading-day rows (API may return more calendar days)
    if len(df) > 60:
        df = df.tail(60).reset_index(drop=True)

    if len(df) < 2:
        return {}

    vol_series   = df['vol'].fillna(method='ffill')
    ratio_series = df['ratio'].fillna(method='ffill') if 'ratio' in df.columns else pd.Series([np.nan] * len(df))

    vol_now   = float(vol_series.iloc[-1])
    ratio_now = float(ratio_series.iloc[-1]) if not pd.isna(ratio_series.iloc[-1]) else 0.0

    # 30-row-ago value (≈30 trading days)
    idx_30 = max(0, len(df) - 1 - 30)
    vol_30d_ago = float(vol_series.iloc[idx_30])
    chg_30d = (vol_now / vol_30d_ago - 1) * 100 if vol_30d_ago > 0 else 0.0

    # 5-day ratio trend
    if len(ratio_series.dropna()) >= 6:
        ratio_trend_5d = float(ratio_series.iloc[-1]) - float(ratio_series.iloc[-6])
    else:
        ratio_trend_5d = 0.0

    # Signal
    if ratio_trend_5d > 0.5:
        signal = '北向持续加仓'
    elif ratio_trend_5d > 0.1:
        signal = '北向小幅增持'
    elif ratio_trend_5d < -0.5:
        signal = '北向明显减仓'
    elif ratio_trend_5d < -0.1:
        signal = '北向小幅减仓'
    else:
        signal = '北向持仓稳定'

    return {
        'df': df,
        'vol_series': vol_series,
        'ratio_series': ratio_series,
        'vol_now': round(vol_now, 2),
        'vol_30d_ago': round(vol_30d_ago, 2),
        'chg_30d': round(chg_30d, 2),
        'ratio_now': round(ratio_now, 4),
        'ratio_trend_5d': round(ratio_trend_5d, 4),
        'signal': signal,
    }


def _build_chart(metrics: dict, ts_code: str) -> str:
    df          = metrics['df']
    vol_series  = metrics['vol_series']
    ratio_series = metrics['ratio_series']
    dates       = df['trade_date'].tolist()
    n           = len(dates)
    x           = list(range(n))

    # Bar colour: red if vol >= prev day, blue otherwise
    vol_vals = vol_series.tolist()
    colors = []
    for i, v in enumerate(vol_vals):
        if i == 0:
            colors.append('#d62728')
        else:
            colors.append('#d62728' if v >= vol_vals[i - 1] else '#1f77b4')

    fig, ax1 = plt.subplots(figsize=(8, 3.0), facecolor='white')
    fig.subplots_adjust(left=0.10, right=0.88, top=0.82, bottom=0.14)

    # ── Left y-axis: vol bars ────────────────────────────────────
    ax1.bar(x, vol_vals, color=colors, alpha=0.75, width=0.8, label='持仓量(万股)')
    ax1.set_ylabel('北向持仓量 (万股)', fontsize=7)
    ax1.tick_params(axis='y', labelsize=6.5)

    step = max(1, n // 8)
    ax1.set_xticks(x[::step])
    ax1.set_xticklabels([dates[i][4:] for i in x[::step]], fontsize=6, rotation=20)
    ax1.spines['top'].set_visible(False)

    code_short = ts_code.split('.')[0]
    ax1.set_title(f'{code_short} 北向资金持股变化（近{n}日）',
                  fontsize=8.5, fontweight='bold', pad=5)

    # ── Right y-axis: ratio line ─────────────────────────────────
    ratio_vals = ratio_series.tolist()
    if any(not pd.isna(v) for v in ratio_vals):
        ax2 = ax1.twinx()
        ax2.plot(x, ratio_vals, color='#ff7f0e', linewidth=1.8,
                 marker='', zorder=3, label='持股比例(%)')
        ax2.set_ylabel('持股占流通比 (%)', fontsize=7, color='#ff7f0e')
        ax2.tick_params(axis='y', labelsize=6.5, colors='#ff7f0e')
        ax2.spines['top'].set_visible(False)

        # Legend
        ax2.legend(loc='upper right', fontsize=6, framealpha=0.7)

    in_p  = mpatches.Patch(color='#d62728', alpha=0.75, label='持仓增加')
    out_p = mpatches.Patch(color='#1f77b4', alpha=0.75, label='持仓减少')
    ax1.legend(handles=[in_p, out_p], fontsize=6, loc='upper left', framealpha=0.7)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def _build_narrative(ts_code: str, metrics: dict) -> str:
    code           = ts_code.split('.')[0]
    vol_now        = metrics['vol_now']
    ratio_now      = metrics['ratio_now']
    chg_30d        = metrics['chg_30d']
    ratio_trend_5d = metrics['ratio_trend_5d']
    signal         = metrics['signal']

    trend_desc = (
        f'近5日持股比例{"上升" if ratio_trend_5d >= 0 else "下降"}'
        f'**{abs(ratio_trend_5d):.2f}个百分点**'
    )
    direction_30d = '增加' if chg_30d >= 0 else '减少'

    base = (
        f'北向资金（沪深港通外资）目前持有**{code} {vol_now:,.0f}万股**，'
        f'占流通盘**{ratio_now:.2f}%**。'
        f'近30日持仓{direction_30d}了**{abs(chg_30d):.1f}%**，{trend_desc}。'
    )

    if signal == '北向持续加仓':
        insight = (
            '外资连续加仓是较强的看多信号——北向资金以机构为主，'
            '信息优势明显。若此时AH溢价偏高而外资仍在买入，'
            '说明他们愿意为A股支付溢价，认可基本面价值。'
            '历史上北向持续流入往往对应股价中期上升通道。'
        )
    elif signal == '北向小幅增持':
        insight = (
            '外资在小幅增持，温和偏多。建议结合主力资金流和基本面信号综合判断，'
            '单纯小幅增持尚不足以单独构成强买入依据。'
        )
    elif signal == '北向明显减仓':
        insight = (
            '外资明显减仓需警惕——北向资金通常领先A股散户做出反应。'
            '大幅减仓可能反映海外机构对公司基本面或行业前景的担忧，'
            '或是全球资金风险偏好下降的反映。建议暂缓追高。'
        )
    elif signal == '北向小幅减仓':
        insight = (
            '外资有所减仓，但幅度较小，可能属于正常调仓而非趋势性退出。'
            '需持续观察未来1-2周方向。'
        )
    else:
        insight = (
            '外资持仓稳定，未有明显方向性动作。这本身是中性信号，'
            '意味着外资对当前价位既不急于追买也不急于卖出，可视为"观望"。'
        )

    return base + insight


def northbound_flow(ts_code: str, trade_date: str, lookback_days: int = 60) -> dict:
    """
    Analyse northbound (Stock Connect) holding changes for a single A-share.

    Returns
    -------
    dict with keys: signal, chart_b64, narrative, vol_now, ratio_now,
                    ratio_trend_5d, chg_30d, error
    """
    result: dict = {
        'signal': '无数据',
        'chart_b64': '',
        'narrative': '',
        'vol_now': None,
        'ratio_now': None,
        'ratio_trend_5d': None,
        'chg_30d': None,
        'error': None,
    }

    end_dt   = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = (end_dt - timedelta(days=lookback_days + 10)).strftime('%Y%m%d')

    df = _fetch_hk_hold(ts_code, start_dt, trade_date)

    if df is None or len(df) == 0:
        result['signal']    = '暂无北向持仓数据'
        result['narrative'] = (
            f'【{ts_code.split(".")[0]}】北向持仓数据暂时无法获取，'
            f'可能该股暂未纳入沪深港通标的，或数据接口返回为空。'
        )
        return result

    metrics = _compute_metrics(df)
    if not metrics:
        result['error']     = 'Insufficient data after processing'
        result['narrative'] = f'【{ts_code.split(".")[0]}】北向数据行数不足，无法计算趋势。'
        return result

    result.update({
        'signal':         metrics['signal'],
        'vol_now':        metrics['vol_now'],
        'ratio_now':      metrics['ratio_now'],
        'ratio_trend_5d': metrics['ratio_trend_5d'],
        'chg_30d':        metrics['chg_30d'],
    })

    print(f'  北向持仓：{metrics["vol_now"]:,.0f}万股 '
          f'占流通{metrics["ratio_now"]:.2f}% '
          f'5日比例变化{metrics["ratio_trend_5d"]:+.2f}pp → {metrics["signal"]}')

    try:
        result['chart_b64'] = _build_chart(metrics, ts_code)
    except Exception as e:
        print(f'  [WARN] northbound chart: {e}')
        result['error'] = str(e)

    result['narrative'] = _build_narrative(ts_code, metrics)
    return result


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    for code in ['688981.SH']:
        print(f'\n{"=" * 55}\n{code}')
        r = northbound_flow(code, '20260520')
        print(f'  信号:       {r["signal"]}')
        print(f'  持仓量:     {r["vol_now"]} 万股')
        print(f'  占流通比:   {r["ratio_now"]} %')
        print(f'  5日比例变化: {r["ratio_trend_5d"]} pp')
        print(f'  30日持仓变化: {r["chg_30d"]} %')
        print(f'  叙事: {(r["narrative"] or "")[:120]}...')
        print(f'  错误: {r["error"]}')
