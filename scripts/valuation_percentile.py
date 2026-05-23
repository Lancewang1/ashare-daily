"""
valuation_percentile.py
=======================
估值历史分位分析

功能：
1. 拉取个股近3年日频基本面数据（PE TTM、PB、PS TTM、股息率）
2. 计算当前PE/PB处于历史分位（0-100）
3. 判断估值水位信号：历史低位 / 偏低 / 合理 / 偏高 / 高位
4. 生成双子图（PE+PB 历史走势 + 百分位标注）+ 散户叙事
"""

from __future__ import annotations
import sys, io, time, base64
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from scipy import stats as scipy_stats

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


def _fetch_daily_basic(ts_code: str, trade_date: str, lookback_years: int) -> pd.DataFrame:
    end_dt   = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=lookback_years * 365 + 30)
    try:
        df = pro.daily_basic(
            ts_code=ts_code,
            start_date=start_dt.strftime('%Y%m%d'),
            end_date=trade_date,
            fields='trade_date,pe_ttm,pb,ps_ttm,dv_ratio',
        )
        time.sleep(0.2)
    except Exception as e:
        print(f'  [WARN] valuation_percentile fetch: {e}')
        return pd.DataFrame()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.copy()
    df['trade_date'] = df['trade_date'].astype(str)
    for col in ('pe_ttm', 'pb', 'ps_ttm', 'dv_ratio'):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.sort_values('trade_date').reset_index(drop=True)
    return df


def _pct_rank(series: pd.Series, current_val: float) -> float | None:
    """计算 current_val 在 series（已去NaN、去负）中的百分位（0-100）。"""
    valid = series.dropna()
    valid = valid[valid > 0]
    if len(valid) < 10 or pd.isna(current_val) or current_val <= 0:
        return None
    rank = float(scipy_stats.percentileofscore(valid.values, current_val, kind='rank'))
    return round(rank, 1)


def _signal_from_pct(pct: float | None, is_loss: bool = False) -> str:
    if is_loss:
        return '公司亏损，PE无意义，看PB'
    if pct is None:
        return '估值数据不足'
    if pct < 20:
        return '历史估值低位（便宜）'
    elif pct < 40:
        return '估值偏低'
    elif pct < 60:
        return '估值合理'
    elif pct < 80:
        return '估值偏高'
    else:
        return '历史估值高位（贵）'


def _compute_metrics(df: pd.DataFrame, lookback_years: int) -> dict:
    if len(df) < 20:
        return {}

    pe_series = df['pe_ttm']
    pb_series = df['pb']

    pe_now = float(pe_series.iloc[-1]) if not pd.isna(pe_series.iloc[-1]) else None
    pb_now = float(pb_series.iloc[-1]) if not pd.isna(pb_series.iloc[-1]) else None

    pe_negative = (pe_now is not None) and (pe_now <= 0)
    pb_negative = (pb_now is not None) and (pb_now <= 0)

    pe_pct = _pct_rank(pe_series, pe_now) if not pe_negative else None
    pb_pct = _pct_rank(pb_series, pb_now) if not pb_negative else None

    # Primary signal: use PE unless loss-making; fallback to PB
    if pe_negative:
        primary_pct = pb_pct
        signal = _signal_from_pct(pb_pct, is_loss=True)
    else:
        primary_pct = pe_pct
        signal = _signal_from_pct(pe_pct)

    # 20th/80th percentile thresholds for chart bands
    pe_valid = pe_series.dropna()
    pe_valid = pe_valid[pe_valid > 0]
    pb_valid = pb_series.dropna()
    pb_valid = pb_valid[pb_valid > 0]

    pe_p20 = float(np.percentile(pe_valid, 20)) if len(pe_valid) >= 10 else None
    pe_p80 = float(np.percentile(pe_valid, 80)) if len(pe_valid) >= 10 else None
    pb_p20 = float(np.percentile(pb_valid, 20)) if len(pb_valid) >= 10 else None
    pb_p80 = float(np.percentile(pb_valid, 80)) if len(pb_valid) >= 10 else None

    return {
        'df': df,
        'pe_now': pe_now,
        'pb_now': pb_now,
        'pe_pct': pe_pct,
        'pb_pct': pb_pct,
        'pe_negative': pe_negative,
        'pb_negative': pb_negative,
        'primary_pct': primary_pct,
        'signal': signal,
        'pe_p20': pe_p20,
        'pe_p80': pe_p80,
        'pb_p20': pb_p20,
        'pb_p80': pb_p80,
        'lookback_years': lookback_years,
    }


def _build_chart(metrics: dict, ts_code: str) -> str:
    df      = metrics['df']
    dates   = df['trade_date'].tolist()
    pe      = df['pe_ttm'].tolist()
    pb      = df['pb'].tolist()
    n       = len(dates)

    # ── X轴刻度：每隔约90个交易日（约4个月）显示一次 ─────────────
    step = max(1, n // 6)
    tick_x = list(range(0, n, step))
    tick_labels = [dates[i][:7].replace('20', '', 1)  # YYMM
                   if i < n else '' for i in tick_x]

    def _subplot_valuation(ax, values, p20, p80, current_val,
                           pct_val, label, neg_flag):
        x = list(range(len(values)))
        # Filter to positive values for display; keep NaN for gaps
        y = [v if (v is not None and not np.isnan(v) and v > 0) else np.nan
             for v in values]

        # Historical line
        ax.plot(x, y, color='#555555', linewidth=0.9, zorder=3, alpha=0.8)

        # 20-80 percentile band
        if p20 is not None and p80 is not None:
            ax.axhspan(p20, p80, color='#aec7e8', alpha=0.20, zorder=1,
                       label=f'20-80分位区间')
            ax.axhline(p20, color='#2166ac', linewidth=0.8, linestyle='--',
                       alpha=0.6)
            ax.axhline(p80, color='#d62728', linewidth=0.8, linestyle='--',
                       alpha=0.6)

        # Current value line + annotation
        if current_val is not None and not np.isnan(current_val) and current_val > 0:
            ax.axhline(current_val, color='#ff7f0e', linewidth=1.3,
                       linestyle='-', zorder=4, alpha=0.9)
            pct_str = f'{pct_val:.0f}分位' if pct_val is not None else 'N/A'
            ax.text(0.98, 0.96,
                    f'当前:{current_val:.1f}x\n{pct_str}',
                    transform=ax.transAxes,
                    ha='right', va='top', fontsize=6.5,
                    color='#ff7f0e', fontweight='bold',
                    bbox=dict(boxstyle='round,pad=0.25',
                              facecolor='white', alpha=0.8, edgecolor='#ff7f0e'))

        if neg_flag:
            ax.text(0.5, 0.5, '亏损期 PE无意义',
                    transform=ax.transAxes, ha='center', va='center',
                    fontsize=8, color='#999999', style='italic')

        ax.set_xticks(tick_x)
        ax.set_xticklabels(tick_labels, fontsize=5.5, rotation=30)
        ax.set_ylabel(label, fontsize=6.5)
        ax.tick_params(axis='y', labelsize=6)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.set_xlim(-1, len(values) + 1)

    fig, (ax_pe, ax_pb) = plt.subplots(
        1, 2, figsize=(8, 3.0), facecolor='white'
    )
    fig.subplots_adjust(left=0.09, right=0.97, top=0.84, bottom=0.18, wspace=0.40)

    code_disp = ts_code.split('.')[0]
    fig.suptitle(f'{code_disp} 估值历史分位（近{metrics["lookback_years"]}年）',
                 fontsize=8.5, fontweight='bold', y=0.97)

    _subplot_valuation(ax_pe, pe, metrics['pe_p20'], metrics['pe_p80'],
                       metrics['pe_now'], metrics['pe_pct'], 'PE TTM (倍)',
                       metrics['pe_negative'])
    ax_pe.set_title('市盈率 PE TTM', fontsize=7.5, pad=3)

    _subplot_valuation(ax_pb, pb, metrics['pb_p20'], metrics['pb_p80'],
                       metrics['pb_now'], metrics['pb_pct'], 'PB (倍)',
                       metrics['pb_negative'])
    ax_pb.set_title('市净率 PB', fontsize=7.5, pad=3)

    # Shared legend
    band_p = mpatches.Patch(color='#aec7e8', alpha=0.5, label='20-80分位区间')
    cur_p  = mpatches.Patch(color='#ff7f0e', alpha=0.9, label='当前值')
    ax_pe.legend(handles=[band_p, cur_p], fontsize=5.5,
                 loc='upper left', framealpha=0.7)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def _build_narrative(ts_code: str, metrics: dict) -> str:
    code    = ts_code.split('.')[0]
    pe_now  = metrics['pe_now']
    pb_now  = metrics['pb_now']
    pe_pct  = metrics['pe_pct']
    pb_pct  = metrics['pb_pct']
    signal  = metrics['signal']
    yrs     = metrics['lookback_years']
    pe_neg  = metrics['pe_negative']

    if pe_neg:
        pb_str = f'{pb_now:.2f}' if pb_now else 'N/A'
        pb_pct_str = f'{pb_pct:.0f}' if pb_pct else 'N/A'
        if pb_pct is not None and pb_pct < 30:
            val_comment = '从PB角度看，当前价格处于历史低位，但需确认亏损是否周期性而非结构性。'
        elif pb_pct is not None and pb_pct > 70:
            val_comment = 'PB已处于历史高位，亏损期高PB往往意味着市场对未来盈利复苏有较高预期，存在估值泡沫风险。'
        else:
            val_comment = '估值中等，具体吸引力取决于盈利修复的速度。'
        return (
            f"{code}当前处于**亏损状态，PE TTM无实际参考价值**。"
            f"市净率PB为**{pb_str}倍**，处于近{yrs}年历史**{pb_pct_str}百分位**"
            f"——意味着过去{yrs}年中有{pb_pct_str}%的时间比现在的PB更高（更贵）。{val_comment}"
        )

    pe_str  = f'{pe_now:.1f}' if pe_now else 'N/A'
    pb_str  = f'{pb_now:.2f}' if pb_now else 'N/A'
    pe_pct_str = f'{pe_pct:.0f}' if pe_pct is not None else 'N/A'
    pb_pct_str = f'{pb_pct:.0f}' if pb_pct is not None else 'N/A'

    if signal == '历史估值低位（便宜）':
        val_comment = (
            f'处于**历史低估区间**——过去{yrs}年中有{pe_pct_str}%的时间比现在贵。'
            f'低估不等于立刻上涨，但安全边际较高，中长期性价比突出。'
        )
    elif signal == '估值偏低':
        val_comment = (
            f'处于**历史偏低区间**（{pe_pct_str}分位），估值有一定吸引力，'
            f'但需结合成长性判断是否值得布局。'
        )
    elif signal == '估值合理':
        val_comment = (
            f'处于**历史合理区间**（{pe_pct_str}分位），估值不贵也不便宜，'
            f'股价涨跌更多取决于业绩兑现和市场情绪。'
        )
    elif signal == '估值偏高':
        val_comment = (
            f'处于**历史偏高区间**（{pe_pct_str}分位），需要更强的业绩成长来支撑，'
            f'存在估值收缩风险，追高需谨慎。'
        )
    else:  # 高位
        val_comment = (
            f'处于**历史高估区间**——过去{yrs}年中只有{100 - float(pe_pct_str):.0f}%的时间比现在贵。'
            f'高估值意味着市场已充分甚至过度定价了乐观预期，安全边际较低。'
        )

    return (
        f"{code}当前PE TTM为**{pe_str}倍**，PB为**{pb_str}倍**。"
        f"PE处于近{yrs}年历史**{pe_pct_str}百分位**，PB处于**{pb_pct_str}百分位**。"
        f"{val_comment}"
    )


def valuation_percentile(ts_code: str, trade_date: str, lookback_years: int = 3) -> dict:
    """
    估值历史分位分析。

    Parameters
    ----------
    ts_code        : tushare 股票代码，如 '688981.SH'
    trade_date     : 报告日期，格式 'YYYYMMDD'
    lookback_years : 历史回溯年数（默认3年）

    Returns
    -------
    dict with keys:
        signal     : str
        chart_b64  : str (base64 PNG)
        narrative  : str
        pe_now     : float | None
        pb_now     : float | None
        pe_pct     : float | None  (0-100)
        pb_pct     : float | None  (0-100)
        error      : str | None
    """
    result = {
        'ts_code': ts_code,
        'trade_date': trade_date,
        'signal': '无数据',
        'chart_b64': '',
        'narrative': '',
        'pe_now': None,
        'pb_now': None,
        'pe_pct': None,
        'pb_pct': None,
        'error': None,
    }

    df = _fetch_daily_basic(ts_code, trade_date, lookback_years)
    if len(df) == 0:
        result['error'] = 'No daily_basic data returned'
        result['narrative'] = f'【{ts_code.split(".")[0]}】估值数据暂无。'
        return result

    if len(df) < 20:
        result['error'] = f'Insufficient data ({len(df)} rows, need >= 20)'
        return result

    metrics = _compute_metrics(df, lookback_years)
    if not metrics:
        result['error'] = 'Metric computation failed'
        return result

    result.update({
        'signal': metrics['signal'],
        'pe_now': metrics['pe_now'],
        'pb_now': metrics['pb_now'],
        'pe_pct': metrics['pe_pct'],
        'pb_pct': metrics['pb_pct'],
    })

    pe_disp = f'{metrics["pe_now"]:.1f}' if metrics['pe_now'] else '亏损'
    print(f'  估值：PE={pe_disp}倍({metrics["pe_pct"]}分位) '
          f'PB={metrics["pb_now"]:.2f}倍({metrics["pb_pct"]}分位) '
          f'→ {metrics["signal"]}')

    try:
        result['chart_b64'] = _build_chart(metrics, ts_code)
    except Exception as e:
        print(f'  [WARN] valuation_percentile chart: {e}')

    result['narrative'] = _build_narrative(ts_code, metrics)
    return result


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    TEST_DATE = '20260520'
    for code in ['688981.SH', '000880.SZ']:
        print(f'\n{"=" * 55}\n{code}')
        r = valuation_percentile(code, TEST_DATE, lookback_years=3)
        if r['error']:
            print(f'  错误: {r["error"]}')
        else:
            print(f'  信号    : {r["signal"]}')
            print(f'  PE TTM  : {r["pe_now"]}倍  分位={r["pe_pct"]}')
            print(f'  PB      : {r["pb_now"]}倍  分位={r["pb_pct"]}')
            print(f'  图表    : {"已生成" if r["chart_b64"] else "无"}')
            print(f'  叙事:\n  {r["narrative"]}')
