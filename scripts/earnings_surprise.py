"""
earnings_surprise.py
====================
业绩增速与预期差分析

功能：
1. 拉取近4季度实际财务数据（fina_indicator API）：EPS、ROE、净利润同比、营收同比、毛利率
2. 拉取券商一致预期（report_rc API）：EPS预测值
3. 计算预期差（实际/预期-1），无预期则退化为同比增速信号
4. 信号：业绩大超预期 / 小幅超预期 / 大幅低于预期 / 略低预期 / 高增长 / 正增长 / 利润下滑
5. 生成4季度增速对比柱状图 + 散户叙事
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

_QUARTER_LABEL = {'03': 'Q1', '06': 'Q2', '09': 'Q3', '12': 'Q4'}


def _end_date_label(end_date: str) -> str:
    """Convert '20240930' → '2024Q3'."""
    try:
        yr  = end_date[:4]
        mon = end_date[4:6]
        return f'{yr}{_QUARTER_LABEL.get(mon, mon)}'
    except Exception:
        return end_date[:6]


def _fetch_fina(ts_code: str, trade_date: str) -> pd.DataFrame:
    """Fetch fina_indicator for the stock, going back to 2023-01-01."""
    try:
        df = pro.fina_indicator(
            ts_code=ts_code,
            start_date='20230101',
            end_date=trade_date,
            fields='ts_code,ann_date,end_date,eps,roe,netprofit_yoy,or_yoy,grossprofit_margin',
        )
        time.sleep(0.2)
    except Exception as e:
        print(f'  [WARN] fina_indicator: {e}')
        return pd.DataFrame()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.copy()
    for col in ['eps', 'roe', 'netprofit_yoy', 'or_yoy', 'grossprofit_margin']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    df['end_date'] = df['end_date'].astype(str)
    df = df.sort_values('end_date', ascending=False).reset_index(drop=True)
    return df


def _fetch_estimates(ts_code: str, trade_date: str) -> pd.DataFrame:
    """
    Fetch consensus EPS estimates from report_rc.
    The eps2 / net_profit_e / or_e fields contain forward estimates.
    Only pull the last 180 days to get recent consensus.
    """
    end_dt   = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = (end_dt - timedelta(days=180)).strftime('%Y%m%d')
    try:
        df = pro.report_rc(
            ts_code=ts_code,
            start_date=start_dt,
            end_date=trade_date,
            fields='report_date,eps2,net_profit_e,or_e',
        )
        time.sleep(0.2)
    except Exception as e:
        print(f'  [WARN] report_rc (estimates): {e}')
        return pd.DataFrame()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    return df.copy()


def _match_quarter_estimates(est_df: pd.DataFrame, latest_end_date: str) -> float | None:
    """
    Return median eps2 estimate for the quarter matching latest_end_date.
    Falls back to median of all recent estimates if no quarter-match found.
    """
    if est_df is None or len(est_df) == 0:
        return None

    est_df = est_df.copy()
    est_df.columns = [c.lower() for c in est_df.columns]

    eps_col = next((c for c in ['eps2', 'eps_e', 'eps'] if c in est_df.columns), None)
    if eps_col is None:
        return None

    eps_vals = pd.to_numeric(est_df[eps_col], errors='coerce').dropna()
    if len(eps_vals) == 0:
        return None

    return float(eps_vals.median())


def _compute_metrics(fina_df: pd.DataFrame, est_df: pd.DataFrame) -> dict:
    # Take latest 4 distinct quarters
    quarters = fina_df.drop_duplicates('end_date').head(4).copy()
    quarters = quarters.sort_values('end_date').reset_index(drop=True)   # chrono order

    if len(quarters) == 0:
        return {}

    latest = quarters.iloc[-1]
    actual_eps    = float(latest['eps']) if not pd.isna(latest.get('eps')) else None
    netprofit_yoy = float(latest['netprofit_yoy']) if not pd.isna(latest.get('netprofit_yoy')) else None
    revenue_yoy   = float(latest['or_yoy'])    if not pd.isna(latest.get('or_yoy'))    else None
    roe           = float(latest['roe'])        if not pd.isna(latest.get('roe'))        else None
    gpm           = float(latest['grossprofit_margin']) if not pd.isna(latest.get('grossprofit_margin')) else None
    latest_end    = str(latest['end_date'])

    # Estimate
    est_eps     = _match_quarter_estimates(est_df, latest_end)
    surprise_pct = None
    if actual_eps is not None and est_eps is not None and est_eps != 0:
        surprise_pct = (actual_eps / est_eps - 1) * 100

    # Signal logic
    if surprise_pct is not None:
        if surprise_pct > 5:
            signal = f'业绩大超预期（超预期{surprise_pct:.1f}%）'
        elif surprise_pct > 0:
            signal = '业绩小幅超预期'
        elif surprise_pct < -5:
            signal = f'业绩大幅低预期（预期差{surprise_pct:.1f}%）'
        else:
            signal = '业绩略低预期'
    else:
        # Fallback to YoY growth
        yoy = netprofit_yoy if netprofit_yoy is not None else 0.0
        if yoy > 20:
            signal = f'净利润高增长（同比+{yoy:.1f}%）'
        elif yoy > 0:
            signal = f'净利润正增长（同比+{yoy:.1f}%）'
        else:
            signal = f'净利润下滑（同比{yoy:.1f}%）'

    # Build time-series arrays for chart (4 quarters)
    labels        = [_end_date_label(str(r['end_date'])) for _, r in quarters.iterrows()]
    np_yoy_vals   = [float(r['netprofit_yoy']) if not pd.isna(r['netprofit_yoy']) else 0.0
                     for _, r in quarters.iterrows()]
    rev_yoy_vals  = [float(r['or_yoy']) if not pd.isna(r['or_yoy']) else 0.0
                     for _, r in quarters.iterrows()]

    return {
        'quarters':      quarters,
        'labels':        labels,
        'np_yoy_vals':   np_yoy_vals,
        'rev_yoy_vals':  rev_yoy_vals,
        'actual_eps':    round(actual_eps, 4) if actual_eps is not None else None,
        'est_eps':       round(est_eps, 4)    if est_eps is not None    else None,
        'surprise_pct':  round(surprise_pct, 2) if surprise_pct is not None else None,
        'netprofit_yoy': round(netprofit_yoy, 2) if netprofit_yoy is not None else None,
        'revenue_yoy':   round(revenue_yoy, 2)   if revenue_yoy is not None   else None,
        'roe':           round(roe, 2)            if roe is not None            else None,
        'gpm':           round(gpm, 2)            if gpm is not None            else None,
        'latest_end':    latest_end,
        'signal':        signal,
    }


def _build_chart(metrics: dict, ts_code: str) -> str:
    labels       = metrics['labels']
    np_yoy       = metrics['np_yoy_vals']
    rev_yoy      = metrics['rev_yoy_vals']
    actual_eps   = metrics['actual_eps']
    est_eps      = metrics['est_eps']
    surprise_pct = metrics['surprise_pct']
    code_short   = ts_code.split('.')[0]
    n            = len(labels)

    fig, ax = plt.subplots(figsize=(8, 3.0), facecolor='white')
    fig.subplots_adjust(left=0.09, right=0.97, top=0.82, bottom=0.16)

    x      = np.arange(n)
    width  = 0.36

    # ── Grouped bars: net profit YoY (blue) + revenue YoY (orange) ──
    bars_np  = ax.bar(x - width / 2, np_yoy,  width, label='净利润同比(%)', color='#1f77b4', alpha=0.85)
    bars_rev = ax.bar(x + width / 2, rev_yoy, width, label='营收同比(%)',   color='#ff7f0e', alpha=0.85)

    ax.axhline(0, color='#555', linewidth=0.9)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=7.5)
    ax.set_ylabel('同比增速 (%)', fontsize=7)
    ax.tick_params(axis='both', labelsize=7)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.set_title(f'{code_short} 业绩增速 & 预期差（近{n}季）',
                 fontsize=8.5, fontweight='bold', pad=5)

    # Value labels on bars
    for bar in list(bars_np) + list(bars_rev):
        h = bar.get_height()
        if abs(h) > 0.5:
            ax.text(bar.get_x() + bar.get_width() / 2, h + (1.5 if h >= 0 else -3.5),
                    f'{h:.0f}%', ha='center', va='bottom', fontsize=5.5, color='#444')

    # Surprise annotation on latest quarter bar (top of net-profit bar)
    if surprise_pct is not None and actual_eps is not None and est_eps is not None:
        ann_x = x[-1] - width / 2
        ann_y = np_yoy[-1]
        color_surp = '#2ca02c' if surprise_pct >= 0 else '#d62728'
        sign  = '+' if surprise_pct >= 0 else ''
        ax.annotate(
            f'预期差 {sign}{surprise_pct:.1f}%\n实际EPS ¥{actual_eps:.3f}\n预期EPS ¥{est_eps:.3f}',
            xy=(ann_x, ann_y),
            xytext=(ann_x + 0.05, ann_y + max(12, abs(ann_y) * 0.25 + 10)),
            fontsize=5.8,
            color=color_surp,
            arrowprops=dict(arrowstyle='->', color=color_surp, lw=0.8),
            bbox=dict(boxstyle='round,pad=0.3', facecolor='white',
                      edgecolor=color_surp, alpha=0.85),
        )

    ax.legend(fontsize=6.5, loc='upper left', framealpha=0.7)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def _build_narrative(ts_code: str, metrics: dict) -> str:
    code         = ts_code.split('.')[0]
    signal       = metrics['signal']
    np_yoy       = metrics['netprofit_yoy']
    rev_yoy      = metrics['revenue_yoy']
    actual_eps   = metrics['actual_eps']
    est_eps      = metrics['est_eps']
    surprise_pct = metrics['surprise_pct']
    roe          = metrics['roe']
    gpm          = metrics['gpm']
    latest_end   = _end_date_label(metrics['latest_end'])

    # Trend descriptor
    if np_yoy is not None:
        if np_yoy > 30:
            trend_desc = f'净利润高速增长（同比**+{np_yoy:.1f}%**）'
        elif np_yoy > 0:
            trend_desc = f'净利润正增长（同比+{np_yoy:.1f}%）'
        else:
            trend_desc = f'净利润下滑（同比**{np_yoy:.1f}%**）'
    else:
        trend_desc = '净利润同比数据暂缺'

    if rev_yoy is not None:
        rev_desc = f'营收同比{"+" if rev_yoy >= 0 else ""}{rev_yoy:.1f}%'
    else:
        rev_desc = ''

    # Surprise portion
    if surprise_pct is not None and actual_eps is not None and est_eps is not None:
        surp_sign = '+' if surprise_pct >= 0 else ''
        surp_desc = (
            f'{latest_end}实际EPS ¥{actual_eps:.3f}，一致预期EPS ¥{est_eps:.3f}，'
            f'**预期差{surp_sign}{surprise_pct:.1f}%**。'
        )
        if surprise_pct > 5:
            surp_insight = (
                '大幅超预期是最强的短期股价催化剂——'
                '机构会立刻上调目标价并追高，散户应注意追高风险，'
                '等待第一波拉升后的回调再考虑介入。'
                '关键看下季度能否持续超预期（"预期差持续性"）。'
            )
        elif surprise_pct > 0:
            surp_insight = (
                '小幅超预期是正面信号，但驱动力较弱。'
                '股价短期或有温和上涨，但空间取决于下季度预期能否进一步上修。'
            )
        elif surprise_pct < -5:
            surp_insight = (
                '大幅低预期可能导致机构下调评级和目标价，'
                '短期存在"杀估值"压力。需重点关注公司是否发出业绩预警，'
                '以及管理层对下季度的指引是否乐观。'
            )
        else:
            surp_insight = (
                '略低预期影响有限，但需警惕是否存在"趋势性低于预期"的连续情况——'
                '若连续两季低于预期，机构往往持续下修，形成负向预期螺旋。'
            )
    else:
        surp_desc = f'{latest_end}暂无一致预期对比数据，以同比增速作为判断依据。'
        if np_yoy is not None and np_yoy > 20:
            surp_insight = (
                '高增速本身就是强信号——即使没有预期差锚点，'
                '20%+的净利润增速在A股所有上市公司中属于TOP分位，'
                '往往能获得估值溢价。'
            )
        elif np_yoy is not None and np_yoy > 0:
            surp_insight = (
                '正增长但增速平淡——关键看市场是否已充分定价这个增速，'
                '若当前PE较高则上涨空间有限。'
            )
        else:
            surp_insight = (
                '净利润下滑意味着公司正在经历盈利压缩。'
                '需分析是行业性周期底部（可能反转）还是结构性恶化（慎入）。'
            )

    quality_str = ''
    if roe is not None:
        quality_str += f'ROE {roe:.1f}%，'
    if gpm is not None:
        quality_str += f'毛利率 {gpm:.1f}%。'

    return (
        f'{code} {latest_end}季报：{trend_desc}，{rev_desc}。'
        f'{quality_str}'
        f'{surp_desc}'
        f'{surp_insight}'
        f'**下次关注节点**：下季度业绩预告/正式财报，验证增速是否持续。'
    )


def earnings_surprise(ts_code: str, trade_date: str) -> dict:
    """
    Analyse quarterly earnings vs analyst consensus for a single A-share.

    Returns
    -------
    dict with keys: signal, chart_b64, narrative, netprofit_yoy, revenue_yoy,
                    surprise_pct, actual_eps, est_eps, error
    """
    result: dict = {
        'signal':        '无数据',
        'chart_b64':     '',
        'narrative':     '',
        'netprofit_yoy': None,
        'revenue_yoy':   None,
        'surprise_pct':  None,
        'actual_eps':    None,
        'est_eps':       None,
        'error':         None,
    }

    fina_df = _fetch_fina(ts_code, trade_date)
    if fina_df is None or len(fina_df) == 0:
        result['signal']    = '暂无财务数据'
        result['narrative'] = (
            f'【{ts_code.split(".")[0]}】财务数据暂时无法获取，'
            f'可能是数据接口限流或该股财报尚未披露。'
        )
        return result

    est_df = _fetch_estimates(ts_code, trade_date)

    metrics = _compute_metrics(fina_df, est_df)
    if not metrics:
        result['error']     = 'compute_metrics returned empty'
        result['narrative'] = f'【{ts_code.split(".")[0]}】财务指标计算失败，数据可能不完整。'
        return result

    result.update({
        'signal':        metrics['signal'],
        'netprofit_yoy': metrics['netprofit_yoy'],
        'revenue_yoy':   metrics['revenue_yoy'],
        'surprise_pct':  metrics['surprise_pct'],
        'actual_eps':    metrics['actual_eps'],
        'est_eps':       metrics['est_eps'],
    })

    print(f'  业绩：净利同比{metrics["netprofit_yoy"]}% 营收同比{metrics["revenue_yoy"]}% '
          f'实际EPS {metrics["actual_eps"]} 预期EPS {metrics["est_eps"]} '
          f'预期差{metrics["surprise_pct"]}% → {metrics["signal"]}')

    try:
        result['chart_b64'] = _build_chart(metrics, ts_code)
    except Exception as e:
        print(f'  [WARN] earnings chart: {e}')
        result['error'] = str(e)

    result['narrative'] = _build_narrative(ts_code, metrics)
    return result


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    for code in ['688981.SH']:
        print(f'\n{"=" * 55}\n{code}')
        r = earnings_surprise(code, '20260520')
        print(f'  信号:       {r["signal"]}')
        print(f'  净利同比:   {r["netprofit_yoy"]}%')
        print(f'  营收同比:   {r["revenue_yoy"]}%')
        print(f'  实际EPS:    {r["actual_eps"]}')
        print(f'  预期EPS:    {r["est_eps"]}')
        print(f'  预期差:     {r["surprise_pct"]}%')
        print(f'  叙事: {(r["narrative"] or "")[:120]}...')
        print(f'  错误: {r["error"]}')
