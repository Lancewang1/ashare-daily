"""
analyst_rating.py
=================
券商评级与目标价分析

功能：
1. 拉取近90日券商研报评级（report_rc API）
2. 统计看多/中性/看空分布、净上调数、平均目标价
3. 计算目标价相对当前股价的上涨空间（upside_pct）
4. 信号：机构强烈看多 / 机构看多 / 中性偏多 / 机构看空 / 机构分歧
5. 生成双面板图表（评级分布 + 目标价 vs 现价标注）+ 散户叙事
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

# Rating bucket mapping (covers both Chinese and numeric rating codes)
_BULLISH_KEYWORDS  = ['买入', '强烈推荐', '强推', '推荐', '增持', '优于大市', '跑赢',
                       'BUY', 'OUTPERFORM', 'OVERWEIGHT', '1', '2']
_NEUTRAL_KEYWORDS  = ['中性', '持有', '观望', '谨慎推荐', '审慎推荐', 'HOLD', 'NEUTRAL', '3']
_BEARISH_KEYWORDS  = ['减持', '低于大市', '跑输', '回避', '卖出', 'SELL',
                       'UNDERPERFORM', 'UNDERWEIGHT', '4', '5']


def _bucket_rating(raw: str) -> str:
    """Return '看多', '中性', or '看空' for a raw rating string."""
    if pd.isna(raw):
        return '中性'
    s = str(raw).strip()
    for kw in _BULLISH_KEYWORDS:
        if kw in s:
            return '看多'
    for kw in _BEARISH_KEYWORDS:
        if kw in s:
            return '看空'
    for kw in _NEUTRAL_KEYWORDS:
        if kw in s:
            return '中性'
    return '中性'  # default unknown → neutral


def _fetch_ratings(ts_code: str, start_dt: str, trade_date: str) -> pd.DataFrame:
    try:
        df = pro.report_rc(ts_code=ts_code, start_date=start_dt, end_date=trade_date)
        time.sleep(0.2)
    except Exception as e:
        print(f'  [WARN] report_rc: {e}')
        return pd.DataFrame()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    return df.copy()


def _fetch_current_price(ts_code: str, trade_date: str) -> float | None:
    """Get the most recent close price within 5 trading days of trade_date."""
    end_dt   = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = (end_dt - timedelta(days=10)).strftime('%Y%m%d')
    try:
        df = pro.daily(ts_code=ts_code, start_date=start_dt, end_date=trade_date,
                       fields='trade_date,close')
        time.sleep(0.2)
    except Exception as e:
        print(f'  [WARN] daily price fetch: {e}')
        return None
    if df is None or len(df) == 0:
        return None
    df = df.sort_values('trade_date', ascending=False).reset_index(drop=True)
    try:
        return float(df.iloc[0]['close'])
    except Exception:
        return None


def _compute_metrics(df: pd.DataFrame, current_price: float | None) -> dict:
    df = df.copy()

    # Normalise column names (API may return different casing)
    df.columns = [c.lower().strip() for c in df.columns]

    # Identify report_date column
    date_col = next((c for c in ['report_date', 'ann_date', 'date'] if c in df.columns), None)
    if date_col:
        df['report_date'] = df[date_col].astype(str)
        df = df.sort_values('report_date', ascending=False).reset_index(drop=True)

    n_reports = len(df)

    # Target price: tp column (or peg_3month, pe_ttm etc.)
    tp_col = next((c for c in ['tp', 'target_price', 'tp_year'] if c in df.columns), None)
    avg_tp = None
    if tp_col:
        tp_vals = pd.to_numeric(df[tp_col], errors='coerce').dropna()
        if len(tp_vals) > 0:
            avg_tp = float(tp_vals.mean())

    upside_pct = None
    if avg_tp is not None and current_price and current_price > 0:
        upside_pct = (avg_tp / current_price - 1) * 100

    # Rating distribution
    rating_col = next((c for c in ['ratingcd', 'rating', 'rating_type'] if c in df.columns), None)
    if rating_col:
        df['bucket'] = df[rating_col].apply(_bucket_rating)
    else:
        df['bucket'] = '中性'

    n_bullish = int((df['bucket'] == '看多').sum())
    n_neutral = int((df['bucket'] == '中性').sum())
    n_bearish = int((df['bucket'] == '看空').sum())

    # Net revision: rating_change column direction
    upgrade_count   = 0
    downgrade_count = 0
    rc_col = next((c for c in ['rating_change', 'change', 'upgrade'] if c in df.columns), None)
    if rc_col:
        for v in df[rc_col].dropna():
            sv = str(v).strip()
            if any(k in sv for k in ['上调', '升级', '调高', 'up', 'upgrade', '1']):
                upgrade_count += 1
            elif any(k in sv for k in ['下调', '降级', '调低', 'down', 'downgrade', '-1']):
                downgrade_count += 1
    net_revision = upgrade_count - downgrade_count

    # Rating granular breakdown for chart (canonical 5 buckets)
    fine_map = {
        '买入': 0, '增持': 0,
        '强烈推荐': 0, '推荐': 0, '强推': 0,
        '中性': 0, '持有': 0, '观望': 0,
        '减持': 0, '卖出': 0,
    }
    if rating_col:
        for v in df[rating_col].dropna():
            sv = str(v).strip()
            matched = False
            for key in fine_map:
                if key in sv:
                    fine_map[key] += 1
                    matched = True
                    break
            if not matched:
                fine_map['中性'] += 1

    # Build 5-bucket summary for chart
    chart_buckets = {
        '买入': fine_map.get('买入', 0) + fine_map.get('强烈推荐', 0) + fine_map.get('强推', 0),
        '增持': fine_map.get('增持', 0) + fine_map.get('推荐', 0),
        '中性': fine_map.get('中性', 0) + fine_map.get('持有', 0) + fine_map.get('观望', 0),
        '减持': fine_map.get('减持', 0),
        '卖出': fine_map.get('卖出', 0),
    }
    # Absorb unaccounted into 中性
    accounted = sum(chart_buckets.values())
    if accounted < n_reports:
        chart_buckets['中性'] += n_reports - accounted

    # Signal
    if upside_pct is not None:
        if upside_pct > 20 and net_revision >= 0:
            signal = '机构强烈看多（大幅上调空间）'
        elif upside_pct > 10:
            signal = '机构看多（有上调空间）'
        elif upside_pct > 0:
            signal = '机构中性偏多'
        elif upside_pct < -10:
            signal = '目标价已在股价下方（机构看空）'
        else:
            signal = '机构分歧'
    else:
        if n_bullish > n_bearish + n_neutral:
            signal = '机构看多（无目标价）'
        elif n_bearish > n_bullish:
            signal = '机构偏空（无目标价）'
        else:
            signal = '机构分歧'

    return {
        'df': df,
        'n_reports':   n_reports,
        'avg_tp':      round(avg_tp, 2) if avg_tp is not None else None,
        'current_price': round(current_price, 2) if current_price else None,
        'upside_pct':  round(upside_pct, 2) if upside_pct is not None else None,
        'n_bullish':   n_bullish,
        'n_neutral':   n_neutral,
        'n_bearish':   n_bearish,
        'net_revision':    net_revision,
        'upgrade_count':   upgrade_count,
        'downgrade_count': downgrade_count,
        'chart_buckets':   chart_buckets,
        'signal': signal,
    }


def _build_chart(metrics: dict, ts_code: str) -> str:
    chart_buckets = metrics['chart_buckets']
    current_price = metrics['current_price']
    avg_tp        = metrics['avg_tp']
    upside_pct    = metrics['upside_pct']
    code_short    = ts_code.split('.')[0]

    bucket_labels  = ['买入', '增持', '中性', '减持', '卖出']
    bucket_colors  = ['#2ca02c', '#98df8a', '#aec7e8', '#ff7f0e', '#d62728']
    bucket_counts  = [chart_buckets.get(b, 0) for b in bucket_labels]

    fig, (ax_left, ax_right) = plt.subplots(
        1, 2, figsize=(8, 2.8),
        gridspec_kw={'width_ratios': [3, 2]},
        facecolor='white',
    )
    fig.subplots_adjust(left=0.07, right=0.97, top=0.82, bottom=0.14, wspace=0.45)

    # ── Left panel: horizontal bar — rating distribution ─────────
    y_pos = list(range(len(bucket_labels)))
    ax_left.barh(y_pos, bucket_counts, color=bucket_colors, alpha=0.85, height=0.55)
    ax_left.set_yticks(y_pos)
    ax_left.set_yticklabels(bucket_labels, fontsize=7.5)
    ax_left.set_xlabel('研报数量', fontsize=7)
    ax_left.set_title(f'{code_short} 券商评级分布（近90日）',
                      fontsize=8, fontweight='bold', pad=5)
    ax_left.spines['top'].set_visible(False)
    ax_left.spines['right'].set_visible(False)
    ax_left.tick_params(axis='x', labelsize=6.5)

    for i, v in enumerate(bucket_counts):
        if v > 0:
            ax_left.text(v + 0.05, i, str(v), va='center', fontsize=7, color='#333')

    # ── Right panel: current price vs avg target price ────────────
    ax_right.set_xlim(0, 1)
    ax_right.set_ylim(0, 1)
    ax_right.axis('off')
    ax_right.set_title('现价 vs 目标价', fontsize=8, fontweight='bold', pad=5)

    if current_price and avg_tp:
        lo = min(current_price, avg_tp) * 0.96
        hi = max(current_price, avg_tp) * 1.04
        rng = hi - lo if hi > lo else 1.0

        # Scale to [0.1, 0.9] vertical range
        def _scale(v):
            return 0.10 + 0.80 * (v - lo) / rng

        y_cur = _scale(current_price)
        y_tp  = _scale(avg_tp)

        # Draw price axis line
        ax_right.plot([0.45, 0.45], [0.08, 0.92], color='#ccc', linewidth=1.5, zorder=1)

        # Current price marker
        ax_right.scatter([0.45], [y_cur], s=80, color='#1f77b4', zorder=5)
        ax_right.annotate(f'现价\n¥{current_price:.2f}',
                          xy=(0.45, y_cur), xytext=(0.60, y_cur),
                          fontsize=7, va='center', color='#1f77b4',
                          arrowprops=dict(arrowstyle='->', color='#1f77b4', lw=0.8))

        # Target price marker
        tp_color = '#d62728' if avg_tp > current_price else '#ff7f0e'
        ax_right.scatter([0.45], [y_tp], s=80, color=tp_color, marker='^', zorder=5)
        upside_str = f'{upside_pct:+.1f}%' if upside_pct is not None else ''
        ax_right.annotate(f'均目标\n¥{avg_tp:.2f} ({upside_str})',
                          xy=(0.45, y_tp), xytext=(0.60, y_tp),
                          fontsize=7, va='center', color=tp_color,
                          arrowprops=dict(arrowstyle='->', color=tp_color, lw=0.8))

        # Arrow between the two
        if abs(y_tp - y_cur) > 0.04:
            ax_right.annotate('', xy=(0.35, y_tp), xytext=(0.35, y_cur),
                              arrowprops=dict(arrowstyle='->', color='#666', lw=1.2))
    else:
        msg = '暂无目标价数据' if not avg_tp else f'当前价格未获取'
        ax_right.text(0.5, 0.5, msg, ha='center', va='center',
                      fontsize=8, color='#888')

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def _build_narrative(ts_code: str, metrics: dict) -> str:
    code      = ts_code.split('.')[0]
    n         = metrics['n_reports']
    avg_tp    = metrics['avg_tp']
    cur_price = metrics['current_price']
    upside    = metrics['upside_pct']
    nb        = metrics['n_bullish']
    nn        = metrics['n_neutral']
    nz        = metrics['n_bearish']
    net_rev   = metrics['net_revision']
    up_cnt    = metrics['upgrade_count']
    dn_cnt    = metrics['downgrade_count']
    signal    = metrics['signal']

    if n == 0:
        return f'近90日内暂无{code}的券商研报评级数据，机构覆盖度可能较低。'

    tp_str = (f'平均目标价**¥{avg_tp:.2f}元**（较当前股价有**{upside:+.1f}%**空间）'
              if avg_tp and upside is not None
              else '（目标价数据暂缺）')

    revision_str = ''
    if up_cnt > 0 or dn_cnt > 0:
        revision_str = (f'近期评级变动：上调{up_cnt}次，下调{dn_cnt}次'
                        f'，净{"上调" if net_rev >= 0 else "下调"}{abs(net_rev)}次。')

    base = (
        f'过去90日共有**{n}家**券商发布{code}研报，'
        f'{tp_str}。'
        f'其中**{nb}家看多**，{nn}家中性，{nz}家看空。'
        f'{revision_str}'
    )

    if '强烈看多' in signal:
        insight = (
            '大幅正向空间叠加机构多数看多，说明卖方分析师对公司未来12个月表现普遍乐观。'
            '目标价通常基于未来12个月DCF或PE估值，实现概率取决于业绩兑现节奏。'
            '投资者应关注下季度业绩是否验证分析师预期。'
        )
    elif '机构看多' in signal:
        insight = (
            '机构目标价高于当前股价，意味着市场共识认为股票仍有上行空间。'
            '但需注意：目标价代表12个月观点，短期股价可能先跌后涨；'
            '若多家券商近期上调目标价，趋势更可信。'
        )
    elif '中性偏多' in signal:
        insight = (
            '机构目标价小幅高于现价，属于轻度看多。'
            '这类情形下股票往往缺乏强催化剂，跑赢指数的概率适中。'
            '可重点关注是否有机构上调推动预期差修复。'
        )
    elif '看空' in signal:
        insight = (
            '目标价低于现价意味着机构认为股票**已经高估**。'
            '这是需要认真对待的风险信号——专业机构认为持有该股在未来12个月难以获得正收益。'
            '建议等待股价回调至目标价区间或有新正面催化剂后再考虑介入。'
        )
    else:
        insight = (
            '机构内部存在分歧，看多与看空力量相当或缺少明确目标价共识。'
            '分歧本身也是信息——通常意味着公司前景存在真实的不确定性，个人投资者需自行判断风险。'
        )

    return base + insight


def analyst_rating(ts_code: str, trade_date: str, lookback_days: int = 90) -> dict:
    """
    Analyse analyst ratings and consensus target price for a single A-share.

    Returns
    -------
    dict with keys: signal, chart_b64, narrative, n_reports, avg_tp, current_price,
                    upside_pct, n_bullish, n_neutral, n_bearish, net_revision, error
    """
    result: dict = {
        'signal':        '无数据',
        'chart_b64':     '',
        'narrative':     '',
        'n_reports':     0,
        'avg_tp':        None,
        'current_price': None,
        'upside_pct':    None,
        'n_bullish':     0,
        'n_neutral':     0,
        'n_bearish':     0,
        'net_revision':  0,
        'error':         None,
    }

    end_dt   = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = (end_dt - timedelta(days=lookback_days)).strftime('%Y%m%d')

    df = _fetch_ratings(ts_code, start_dt, trade_date)

    if df is None or len(df) == 0:
        result['signal']    = '无评级数据'
        result['narrative'] = (
            f'【{ts_code.split(".")[0]}】近{lookback_days}日内暂无券商评级研报数据，'
            f'该股机构覆盖度可能较低，或数据接口暂时无法访问。'
        )
        return result

    current_price = _fetch_current_price(ts_code, trade_date)
    metrics       = _compute_metrics(df, current_price)

    result.update({
        'signal':        metrics['signal'],
        'n_reports':     metrics['n_reports'],
        'avg_tp':        metrics['avg_tp'],
        'current_price': metrics['current_price'],
        'upside_pct':    metrics['upside_pct'],
        'n_bullish':     metrics['n_bullish'],
        'n_neutral':     metrics['n_neutral'],
        'n_bearish':     metrics['n_bearish'],
        'net_revision':  metrics['net_revision'],
    })

    print(f'  券商评级：{metrics["n_reports"]}份研报 '
          f'看多{metrics["n_bullish"]} 中性{metrics["n_neutral"]} 看空{metrics["n_bearish"]} '
          f'均目标¥{metrics["avg_tp"]} 现价¥{metrics["current_price"]} '
          f'空间{metrics["upside_pct"]}% → {metrics["signal"]}')

    try:
        result['chart_b64'] = _build_chart(metrics, ts_code)
    except Exception as e:
        print(f'  [WARN] analyst chart: {e}')
        result['error'] = str(e)

    result['narrative'] = _build_narrative(ts_code, metrics)
    return result


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    for code in ['688981.SH']:
        print(f'\n{"=" * 55}\n{code}')
        r = analyst_rating(code, '20260520')
        print(f'  信号:       {r["signal"]}')
        print(f'  研报数量:   {r["n_reports"]}')
        print(f'  均目标价:   {r["avg_tp"]}')
        print(f'  当前股价:   {r["current_price"]}')
        print(f'  上涨空间:   {r["upside_pct"]}%')
        print(f'  看多/中性/看空: {r["n_bullish"]}/{r["n_neutral"]}/{r["n_bearish"]}')
        print(f'  净上调:     {r["net_revision"]}')
        print(f'  叙事: {(r["narrative"] or "")[:120]}...')
        print(f'  错误: {r["error"]}')
