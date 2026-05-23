"""
margin_accel.py
===============
融资余额加速度分析器

功能：
1. 拉取个股过去 ~30 日（交易日）的融资余额数据（margin_detail API）
2. 计算近5日累计变化% 和 近20日日均变化%
3. 计算加速度（近5日日均增速 / 近20日日均增速）
4. 给出信号强度标签：快速加速建仓 / 加速建仓 / 正常加仓 / 融资减仓
5. 生成可视化图表（base64 编码）和中文叙事文字

实现要点：
- margin_detail 返回列：ts_code, trade_date, rzye（融资余额，元）, rqye（融券余额，元）, rzrqye（合计）
- 统一将 rzye 转换为亿元单位（/ 1e8）
- 需要拉取 ~40 个自然日以确保覆盖 25 个交易日
"""

import sys
import io

import time
import base64
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

import matplotlib
matplotlib.use('Agg')
matplotlib.rcParams.update({
    'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'PingFang SC', 'DejaVu Sans'],
    'axes.unicode_minus': False,
})
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib import rcParams
from matplotlib.patches import FancyArrowPatch

import tushare as ts

# ── 初始化 tushare ───────────────────────────────────────────────
pro = ts.pro_api()

# ── 字体设置（中文支持）────────────────────────────────────────
rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
rcParams['axes.unicode_minus'] = False

# ── 行业名称映射（复用，与 sector_breadth.py 保持一致）──────────
INDEX_NAMES = {
    '801081.SI': '申万半导体',
    '801880.SI': '申万汽车',
    '801010.SI': '申万农林牧渔',
    '801020.SI': '申万采掘',
    '801030.SI': '申万化工',
    '801040.SI': '申万钢铁',
    '801050.SI': '申万有色金属',
    '801080.SI': '申万电子',
    '801110.SI': '申万家用电器',
    '801120.SI': '申万食品饮料',
    '801130.SI': '申万纺织服装',
    '801140.SI': '申万轻工制造',
    '801150.SI': '申万医药生物',
    '801160.SI': '申万公用事业',
    '801170.SI': '申万交通运输',
    '801180.SI': '申万房地产',
    '801200.SI': '申万商业贸易',
    '801210.SI': '申万休闲服务',
    '801230.SI': '申万综合',
    '801710.SI': '申万建筑材料',
    '801720.SI': '申万建筑装饰',
    '801730.SI': '申万电气设备',
    '801740.SI': '申万国防军工',
    '801750.SI': '申万计算机',
    '801760.SI': '申万传媒',
    '801770.SI': '申万通信',
    '801780.SI': '申万银行',
    '801790.SI': '申万非银金融',
    '801890.SI': '申万机械设备',
}


# ── 数据获取 ─────────────────────────────────────────────────────

def _fetch_margin_data(ts_code: str, end_date: str, lookback_calendar_days: int = 45) -> pd.DataFrame:
    """
    拉取个股融资余额数据，覆盖约 25 个交易日。

    Parameters
    ----------
    ts_code               : tushare 股票代码，如 '688981.SH'
    end_date              : 截止日期，格式 YYYYMMDD
    lookback_calendar_days: 回溯自然日数（默认 45 天 ≈ 30 个交易日）

    Returns
    -------
    DataFrame，按 trade_date 升序排列，列：trade_date, rzye_yi（亿元）
    """
    end_dt = datetime.strptime(end_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=lookback_calendar_days)
    start_str = start_dt.strftime('%Y%m%d')

    try:
        df = pro.margin_detail(
            ts_code=ts_code,
            start_date=start_str,
            end_date=end_date
        )
        time.sleep(0.2)
    except Exception as e:
        return pd.DataFrame()

    if df is None or len(df) == 0:
        return pd.DataFrame()

    df = df.copy()
    df['trade_date'] = df['trade_date'].astype(str)
    df['rzye'] = pd.to_numeric(df['rzye'], errors='coerce')
    df = df.dropna(subset=['rzye'])
    df = df.sort_values('trade_date').reset_index(drop=True)
    df['rzye_yi'] = df['rzye'] / 1e8  # 转换为亿元

    return df[['trade_date', 'rzye_yi']]


# ── 指标计算 ─────────────────────────────────────────────────────

def _compute_metrics(df: pd.DataFrame) -> dict:
    """
    从融资余额数据中计算加速度相关指标。

    期望输入：已按 trade_date 升序排列，至少 6 行数据。
    取最后 25 行（最近 25 个交易日）进行计算。

    Returns
    -------
    dict with:
        rzye_current      : float — 最新融资余额（亿元）
        rzye_5d_chg_pct   : float — 近5日变化%
        daily_chg_5d_avg  : float — 近5日日均变化%
        daily_chg_20d_avg : float — 近20日日均变化%
        accel_ratio       : float — 加速度倍数
        signal            : str   — 信号强度
        daily_changes     : list[float] — 全程每日变化%
        dates             : list[str]   — 对应日期
    """
    # 取最近 25 个交易日
    df_use = df.tail(25).reset_index(drop=True)

    rzye = df_use['rzye_yi'].values
    dates = df_use['trade_date'].tolist()

    # 每日变化% = (rzye[i] - rzye[i-1]) / |rzye[i-1]| * 100
    daily_changes = []
    for i in range(1, len(rzye)):
        prev = rzye[i - 1]
        if prev != 0 and not np.isnan(prev) and not np.isnan(rzye[i]):
            daily_changes.append(float((rzye[i] - prev) / abs(prev) * 100))
        else:
            daily_changes.append(0.0)

    # 对应日期（变化从第2行开始）
    chg_dates = dates[1:]

    n_chg = len(daily_changes)
    rzye_current = float(rzye[-1])

    # ── 近5日累计变化% ───────────────────────────────────────────
    if len(rzye) >= 6:
        rzye_5d_ago = rzye[-6]
        if rzye_5d_ago != 0 and not np.isnan(rzye_5d_ago):
            rzye_5d_chg_pct = float((rzye[-1] - rzye_5d_ago) / abs(rzye_5d_ago) * 100)
        else:
            rzye_5d_chg_pct = 0.0
    elif len(rzye) >= 2:
        rzye_5d_chg_pct = float((rzye[-1] - rzye[0]) / abs(rzye[0]) * 100) if rzye[0] != 0 else 0.0
    else:
        rzye_5d_chg_pct = 0.0

    # ── 近5日日均变化% ───────────────────────────────────────────
    chg_5d = daily_changes[-5:] if n_chg >= 5 else daily_changes
    daily_chg_5d_avg = float(np.mean(chg_5d)) if chg_5d else 0.0

    # ── 近20日日均变化% ──────────────────────────────────────────
    chg_20d = daily_changes[-20:] if n_chg >= 20 else daily_changes
    daily_chg_20d_avg = float(np.mean(chg_20d)) if chg_20d else 0.0

    # ── 加速度 ───────────────────────────────────────────────────
    # 若近20日日均变化% 绝对值极小（< 0.01%），用绝对量替代倍数
    if abs(daily_chg_20d_avg) < 0.01:
        # 以绝对日均变化（亿元）替代
        abs_20d = float(np.mean(np.abs(np.diff(rzye[-21:])))) if len(rzye) >= 21 else 0.0
        abs_5d = float(np.mean(np.abs(np.diff(rzye[-6:])))) if len(rzye) >= 6 else 0.0
        if abs_20d > 0:
            accel_ratio = float(abs_5d / abs_20d)
        else:
            accel_ratio = 1.0
    else:
        accel_ratio = float(daily_chg_5d_avg / abs(daily_chg_20d_avg))

    # ── 信号强度 ─────────────────────────────────────────────────
    if rzye_5d_chg_pct < 0:
        signal = '融资减仓'
    elif accel_ratio > 3:
        signal = '快速加速建仓'
    elif accel_ratio >= 1.5:
        signal = '加速建仓'
    else:
        signal = '正常加仓'

    return {
        'rzye_current': round(rzye_current, 4),
        'rzye_5d_chg_pct': round(rzye_5d_chg_pct, 2),
        'daily_chg_5d_avg': round(daily_chg_5d_avg, 4),
        'daily_chg_20d_avg': round(daily_chg_20d_avg, 4),
        'accel_ratio': round(accel_ratio, 2),
        'signal': signal,
        'daily_changes': [round(c, 4) for c in daily_changes],
        'dates': chg_dates,
        # Full series for charting
        '_rzye_series': rzye.tolist(),
        '_date_series': dates,
    }


# ── 图表生成 ─────────────────────────────────────────────────────

def _build_chart(metrics: dict, ts_code: str) -> str:
    """
    生成融资余额走势 + 加速度仪表图，返回 base64 编码字符串。

    左（65%）：融资余额折线图，最后5日橙色高亮
    右（35%）：加速度柱状图（仪表风格）
    """
    rzye_series = metrics['_rzye_series']
    date_series = metrics['_date_series']
    accel_ratio = metrics['accel_ratio']
    signal = metrics['signal']
    rzye_5d_chg_pct = metrics['rzye_5d_chg_pct']

    fig, (ax_left, ax_right) = plt.subplots(
        1, 2,
        figsize=(8, 2.8),
        gridspec_kw={'width_ratios': [13, 7]},
        facecolor='white'
    )
    fig.subplots_adjust(left=0.07, right=0.97, top=0.82, bottom=0.22, wspace=0.45)

    n = len(rzye_series)
    x = list(range(n))
    split_idx = max(0, n - 5)  # 最后5日起点

    # ── 左图：融资余额折线 ────────────────────────────────────
    # 历史部分（蓝色）
    if split_idx > 0:
        x_hist = x[:split_idx + 1]
        y_hist = rzye_series[:split_idx + 1]
        ax_left.plot(x_hist, y_hist, color='#2166ac', linewidth=1.2, zorder=3)
        ax_left.fill_between(x_hist, y_hist, min(rzye_series) * 0.997,
                             color='#2166ac', alpha=0.12)

    # 最后5日（橙色高亮）
    x_recent = x[split_idx:]
    y_recent = rzye_series[split_idx:]
    ax_left.plot(x_recent, y_recent, color='#d62728', linewidth=1.6, zorder=4)
    ax_left.fill_between(x_recent, y_recent, min(rzye_series) * 0.997,
                         color='#d62728', alpha=0.18)

    # 标注涨幅
    if n >= 2:
        sign_str = f'{rzye_5d_chg_pct:+.1f}%'
        ax_left.annotate(
            sign_str,
            xy=(x[-1], rzye_series[-1]),
            xytext=(x[-1] - 1.5, rzye_series[-1] + (max(rzye_series) - min(rzye_series)) * 0.12),
            fontsize=7.5, color='#d62728', fontweight='bold',
            arrowprops=dict(arrowstyle='->', color='#d62728', lw=0.8),
        )

    # X 轴：只显示部分日期标签（每5个交易日显示一个）
    tick_indices = list(range(0, n, 5))
    if (n - 1) not in tick_indices:
        tick_indices.append(n - 1)
    ax_left.set_xticks(tick_indices)
    ax_left.set_xticklabels(
        [date_series[i][4:] for i in tick_indices],  # 只显示 MMDD
        fontsize=6, rotation=30
    )
    ax_left.set_ylabel('亿元', fontsize=6.5)
    ax_left.set_title(f'融资余额走势（近{n}日）', fontsize=8, fontweight='bold', pad=4)
    ax_left.tick_params(axis='y', labelsize=6.5)
    ax_left.spines['top'].set_visible(False)
    ax_left.spines['right'].set_visible(False)

    # 图例说明
    hist_patch = mpatches.Patch(color='#2166ac', alpha=0.6, label='历史走势')
    recent_patch = mpatches.Patch(color='#d62728', alpha=0.6, label='近5日')
    ax_left.legend(handles=[hist_patch, recent_patch], fontsize=6,
                   loc='upper left', framealpha=0.7)

    # ── 右图：加速度仪表 ──────────────────────────────────────
    # 简洁柱状仪表：基准线 = 1.0
    bar_val = accel_ratio
    baseline = 1.0

    # 颜色逻辑
    if signal == '融资减仓':
        bar_color = '#4393c3'
        label_color = '#4393c3'
    elif accel_ratio > 1:
        bar_color = '#d62728'
        label_color = '#d62728'
    else:
        bar_color = '#92c5de'
        label_color = '#92c5de'

    # 截断显示（超过 6 截断）
    display_val = min(bar_val, 6.0)
    ax_right.bar([0], [display_val], color=bar_color, width=0.5, alpha=0.85,
                 edgecolor='white', linewidth=0.8)
    # 基准线
    ax_right.axhline(y=baseline, color='#555555', linestyle='--',
                     linewidth=1.0, alpha=0.8, label=f'基准 {baseline:.1f}x')

    # 大字显示倍数
    ax_right.text(0, display_val + 0.1, f'{bar_val:.1f}x',
                  ha='center', va='bottom', fontsize=13, color=label_color,
                  fontweight='bold')

    # 信号标签
    ax_right.text(0, -0.6, signal,
                  ha='center', va='top', fontsize=7.5, color=label_color,
                  fontweight='bold')

    ax_right.set_xlim(-0.6, 0.6)
    ax_right.set_ylim(-0.3, max(display_val * 1.35, 2.5))
    ax_right.set_xticks([])
    ax_right.tick_params(axis='y', labelsize=6)
    ax_right.set_ylabel('加速度（相对历史均速）', fontsize=6, labelpad=2)
    ax_right.set_title('融资加速度', fontsize=8, fontweight='bold', pad=4)
    ax_right.legend(fontsize=6, loc='upper right', framealpha=0.6)
    ax_right.spines['top'].set_visible(False)
    ax_right.spines['right'].set_visible(False)
    ax_right.spines['bottom'].set_visible(False)

    # ── 输出 base64 ───────────────────────────────────────────
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


# ── 叙事生成 ─────────────────────────────────────────────────────

def _build_narrative(ts_code: str, metrics: dict) -> str:
    """根据信号类型生成中文叙事。"""
    signal = metrics['signal']
    rzye_5d = metrics['rzye_5d_chg_pct']
    accel = metrics['accel_ratio']
    rzye_cur = metrics['rzye_current']

    sign_str = f'{rzye_5d:+.1f}%'
    accel_str = f'{accel:.1f}倍'

    if signal == '快速加速建仓':
        return (
            f"近5日融资余额增加了**{sign_str}**，日均增速是过去20日均值的**{accel_str}**"
            f"——这意味着大量杠杆资金正在快速涌入。"
            f"短期内这会助推股价上涨，但也意味着未来可能有更大的获利了结压力。"
            f"融资盘的「加速涌入」历史上往往出现在主升浪的中段。"
            f"当前融资余额 {rzye_cur:.2f}亿元，需持续跟踪是否出现减仓信号。"
        )
    elif signal == '加速建仓':
        return (
            f"近5日融资余额增加了**{sign_str}**，日均增速是过去20日均值的**{accel_str}**。"
            f"杠杆资金仍在稳步入场，增速有所加快。"
            f"这是较积极的多头信号，但需结合价格走势确认趋势有效性。"
            f"当前融资余额 {rzye_cur:.2f}亿元。"
        )
    elif signal == '正常加仓':
        return (
            f"近5日融资余额小幅变动{sign_str}，增速与历史均值相近（加速度{accel_str}）。"
            f"杠杆资金处于正常水平，市场多空情绪较为均衡。"
            f"当前融资余额 {rzye_cur:.2f}亿元，暂无明显的资金加速流入或撤出迹象。"
        )
    else:  # 融资减仓
        abs_chg = abs(rzye_5d)
        return (
            f"近5日融资余额减少了**{abs_chg:.1f}%**，杠杆资金正在撤退。"
            f"这可能是获利了结，也可能是对后续走势失去信心。"
            f"需结合价格走势判断：若价格同期上涨，说明换手后有新资金接盘；"
            f"若价格下跌，则要小心。"
            f"当前融资余额 {rzye_cur:.2f}亿元。"
        )


# ── 主函数 ───────────────────────────────────────────────────────

def margin_acceleration(ts_code: str, trade_date: str) -> dict:
    """
    计算个股融资余额加速度指标。

    Parameters
    ----------
    ts_code    : tushare 股票代码，如 '688981.SH', '000880.SZ'
    trade_date : 报告日期，格式 'YYYYMMDD'

    Returns
    -------
    dict with keys:
        ts_code         : str
        trade_date      : str
        rzye_current    : float — 最新融资余额（亿元）
        rzye_5d_chg_pct : float — 近5日变化%
        accel_ratio     : float — 加速度倍数
        signal          : str   — 信号强度
        daily_changes   : list[float] — 每日变化%
        dates           : list[str]   — 对应日期
        chart_b64       : str   — PNG 图表 base64 编码
        narrative       : str   — 中文叙事
        error           : str | None
    """
    result = {
        'ts_code': ts_code,
        'trade_date': trade_date,
        'rzye_current': 0.0,
        'rzye_5d_chg_pct': 0.0,
        'accel_ratio': 1.0,
        'signal': '正常加仓',
        'daily_changes': [],
        'dates': [],
        'chart_b64': '',
        'narrative': '',
        'error': None,
    }

    # ── Step 1: 拉取融资余额数据 ─────────────────────────────────
    print(f'  拉取 {ts_code} 融资余额数据...')
    t0 = time.time()
    df = _fetch_margin_data(ts_code=ts_code, end_date=trade_date, lookback_calendar_days=45)
    elapsed = time.time() - t0
    print(f'  拉取完成：{len(df)} 行，耗时 {elapsed:.1f}s')

    if df is None or len(df) == 0:
        result['error'] = f'No margin data returned for {ts_code}'
        return result

    if len(df) < 3:
        result['error'] = f'Insufficient margin data ({len(df)} rows, need >= 3)'
        return result

    # ── Step 2: 计算指标 ─────────────────────────────────────────
    metrics = _compute_metrics(df)

    result['rzye_current'] = metrics['rzye_current']
    result['rzye_5d_chg_pct'] = metrics['rzye_5d_chg_pct']
    result['accel_ratio'] = metrics['accel_ratio']
    result['signal'] = metrics['signal']
    result['daily_changes'] = metrics['daily_changes']
    result['dates'] = metrics['dates']

    print(f'  融资余额: {metrics["rzye_current"]:.4f}亿元  |  '
          f'近5日变化: {metrics["rzye_5d_chg_pct"]:+.2f}%  |  '
          f'加速度: {metrics["accel_ratio"]:.2f}x  |  '
          f'信号: {metrics["signal"]}')

    # ── Step 3: 图表 ─────────────────────────────────────────────
    try:
        chart_b64 = _build_chart(metrics, ts_code)
        result['chart_b64'] = chart_b64
    except Exception as e:
        print(f'  [WARN] chart generation failed: {e}')
        result['chart_b64'] = ''

    # ── Step 4: 叙事 ─────────────────────────────────────────────
    narrative = _build_narrative(ts_code, metrics)
    result['narrative'] = narrative

    return result


# ── 主程序（测试）────────────────────────────────────────────────
if __name__ == '__main__':
    TEST_DATE = '20260520'
    TEST_STOCKS = [
        ('688981.SH', '中芯国际'),
        ('000880.SZ', '潍柴重机'),
    ]

    all_results = {}
    for code, name in TEST_STOCKS:
        print(f"\n{'=' * 60}")
        print(f"计算 {name}（{code}）融资加速度，日期：{TEST_DATE}")
        print('=' * 60)

        r = margin_acceleration(ts_code=code, trade_date=TEST_DATE)
        all_results[code] = r

        if r.get('error'):
            print(f'  错误: {r["error"]}')
        else:
            print(f'\n  融资余额  : {r["rzye_current"]:.4f}亿元')
            print(f'  近5日变化 : {r["rzye_5d_chg_pct"]:+.2f}%')
            print(f'  加速度    : {r["accel_ratio"]:.2f}x')
            print(f'  信号强度  : {r["signal"]}')
            print(f'  图表已生成: {"是" if r["chart_b64"] else "否"}（{len(r["chart_b64"])} 字符）')
            print(f'\n  叙事:')
            print(f'  {r["narrative"]}')

        time.sleep(0.3)

    print('\n\n' + '=' * 60)
    print('汇总')
    print('=' * 60)
    for code, r in all_results.items():
        name_map = {'688981.SH': '中芯国际', '000880.SZ': '潍柴重机'}
        name = name_map.get(code, code)
        if r.get('error'):
            print(f'{name}: 失败 — {r["error"]}')
        else:
            print(f'{name}: 融资余额{r["rzye_current"]:.4f}亿元, '
                  f'近5日{r["rzye_5d_chg_pct"]:+.2f}%, '
                  f'加速度{r["accel_ratio"]:.2f}x, '
                  f'信号={r["signal"]}')

    print('\n完成。')
