# -*- coding: utf-8 -*-
"""
sentiment_thermometer.py
========================
全市场情绪温度计 — A-share Market Sentiment Thermometer

功能：
1. 统计每个交易日全市场涨停（pct_chg >= 9.85%）和跌停（pct_chg <= -9.85%）数量
2. 维护约 250 个交易日的历史缓存（scripts/cache_sentiment.json）
3. 计算当日涨停数在历史分布中的百分位
4. 生成 matplotlib 图表（base64 编码）和中文叙事文本

主要用法：
    from sentiment_thermometer import sentiment_thermometer
    result = sentiment_thermometer('20260520')
    print(result['narrative'])

独立运行（测试）：
    python scripts/sentiment_thermometer.py
"""

import sys
import io

import os
import json
import time
import base64
import warnings
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import tushare as ts

# ── matplotlib：必须在 import pyplot 前设置 Agg 后端（无 GUI 环境）──────────
import matplotlib
matplotlib.use('Agg')
matplotlib.rcParams.update({
    'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'PingFang SC', 'DejaVu Sans'],
    'axes.unicode_minus': False,
})
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.patheffects as pe
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.gridspec import GridSpec

warnings.filterwarnings('ignore')

# ── tushare 初始化 ───────────────────────────────────────────────────────────
pro = ts.pro_api()

# ── 缓存文件路径（与本脚本同目录）───────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(_SCRIPT_DIR, 'cache_sentiment.json')

# ── 颜色常量 ─────────────────────────────────────────────────────────────────
COLOR_COLD   = '#4472C4'   # 0-30%  极度悲观
COLOR_NORMAL = '#888888'   # 30-60% 情绪正常
COLOR_WARM   = '#FF8C00'   # 60-80% 情绪偏热
COLOR_HOT    = '#DC143C'   # 80-100% 极度亢奋
COLOR_AREA   = '#BDD7EE'   # 面积图填充
COLOR_LINE   = '#2E75B6'   # 面积图线条
COLOR_MARKER = '#DC143C'   # 今日红点

# ── 区间定义 ─────────────────────────────────────────────────────────────────
ZONES = [
    (0,  30,  COLOR_COLD,   '极度悲观'),
    (30, 60,  COLOR_NORMAL, '情绪正常'),
    (60, 80,  COLOR_WARM,   '情绪偏热'),
    (80, 100, COLOR_HOT,    '极度亢奋'),
]


# ═══════════════════════════════════════════════════════════════════════════════
# 缓存读写
# ═══════════════════════════════════════════════════════════════════════════════

def _load_cache() -> dict:
    """加载本地缓存，返回 {date_str: {'up': int, 'dn': int}} 字典。"""
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f'  [WARN] 读取缓存失败（将重建）: {e}')
        return {}


def _save_cache(cache: dict) -> None:
    """将缓存写回本地 JSON 文件。"""
    try:
        with open(CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'  [WARN] 写入缓存失败: {e}')


# ═══════════════════════════════════════════════════════════════════════════════
# 交易日历
# ═══════════════════════════════════════════════════════════════════════════════

def _get_trading_dates(end_date: str, lookback_days: int) -> list:
    """
    获取 end_date 之前（含）约 lookback_days 个交易日的日期列表（升序）。

    策略：向前取 lookback_days * 1.6 个自然日，从交易日历中筛选，
    取最近的 lookback_days 个。
    """
    end_dt  = datetime.strptime(end_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=int(lookback_days * 1.6))
    start_str = start_dt.strftime('%Y%m%d')

    try:
        cal = pro.trade_cal(exchange='SSE', start_date=start_str,
                            end_date=end_date, is_open='1')
        dates = sorted(cal['cal_date'].tolist())
        # 取最近 lookback_days 个（保留 end_date 当日）
        return dates[-lookback_days:]
    except Exception as e:
        print(f'  [WARN] 获取交易日历失败，使用自然日估算: {e}')
        # 退路：粗略估计（跳过周末，忽略节假日）
        result = []
        d = end_dt
        while len(result) < lookback_days:
            if d.weekday() < 5:
                result.append(d.strftime('%Y%m%d'))
            d -= timedelta(days=1)
        return sorted(result)


# ═══════════════════════════════════════════════════════════════════════════════
# 数据获取
# ═══════════════════════════════════════════════════════════════════════════════

def _fetch_one_day(date_str: str, sleep_sec: float = 0.25) -> Optional[dict]:
    """
    拉取单日全市场 daily 数据，返回 {'up': int, 'dn': int} 或 None（失败）。

    Rate limit: ~4 calls/sec → 0.25s sleep 安全。
    """
    try:
        df = pro.daily(trade_date=date_str, fields='ts_code,pct_chg')
        time.sleep(sleep_sec)
        if df is None or len(df) == 0:
            return None
        df['pct_chg'] = pd.to_numeric(df['pct_chg'], errors='coerce')
        up = int((df['pct_chg'] >= 9.85).sum())
        dn = int((df['pct_chg'] <= -9.85).sum())
        return {'up': up, 'dn': dn}
    except Exception as e:
        print(f'  [WARN] 拉取 {date_str} 失败: {e}')
        time.sleep(sleep_sec)
        return None


def _build_history(trading_dates: list, cache: dict) -> dict:
    """
    对于 trading_dates 中缺失的日期，逐一从 tushare 拉取并更新缓存。
    打印进度（每 50 个交易日一次）。

    返回更新后的 cache 字典。
    """
    missing = [d for d in trading_dates if d not in cache]
    total = len(missing)

    if total == 0:
        return cache

    print(f'  需要拉取 {total} 个交易日数据（已缓存 {len(cache)} 天）...')

    for i, d in enumerate(missing):
        if i > 0 and i % 50 == 0:
            print(f'  [{i}/{total}] fetching {d}...')

        record = _fetch_one_day(d)
        if record is not None:
            cache[d] = record

    # 拉取完成后保存
    _save_cache(cache)
    print(f'  历史数据拉取完成，缓存共 {len(cache)} 天。')
    return cache


# ═══════════════════════════════════════════════════════════════════════════════
# 图表绘制
# ═══════════════════════════════════════════════════════════════════════════════

def _zone_for_percentile(pct: float) -> tuple:
    """返回当前百分位对应的 (color, label)。"""
    for lo, hi, color, label in ZONES:
        if pct < hi or hi == 100:
            return color, label
    return COLOR_HOT, '极度亢奋'


def _draw_thermometer(ax, percentile: float, count: int) -> None:
    """
    左子图：竖向温度计/量规。

    布局：
      - 底部到顶部为 0-100 百分位刻度
      - 按区间填充四色渐变色块
      - 当前百分位用三角形箭头标记
    """
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 100)
    ax.set_xticks([])
    ax.set_title('市场情绪', fontsize=10, fontweight='bold', pad=6)

    # ── 绘制四色区间色块 ────────────────────────────────────────────────────
    bar_x = 0.25
    bar_w = 0.50

    for lo, hi, color, label in ZONES:
        height = hi - lo
        rect = mpatches.FancyBboxPatch(
            (bar_x, lo), bar_w, height,
            boxstyle='square,pad=0',
            facecolor=color, edgecolor='none', alpha=0.85,
            zorder=2
        )
        ax.add_patch(rect)

        # 区间标签（居中）
        mid = (lo + hi) / 2
        ax.text(bar_x + bar_w + 0.05, mid, label,
                va='center', ha='left', fontsize=7.5,
                color=color, fontweight='bold')

        # 分界线（顶部）
        if hi < 100:
            ax.axhline(hi, color='white', linewidth=1.2, zorder=3,
                       xmin=bar_x, xmax=bar_x + bar_w)

    # ── 绘制温度计外框 ───────────────────────────────────────────────────────
    rect_border = mpatches.FancyBboxPatch(
        (bar_x, 0), bar_w, 100,
        boxstyle='round,pad=0.5',
        facecolor='none', edgecolor='#555555', linewidth=1.2,
        zorder=4
    )
    ax.add_patch(rect_border)

    # ── 绘制当前百分位箭头 ──────────────────────────────────────────────────
    curr_color, _ = _zone_for_percentile(percentile)
    arrow_x = bar_x - 0.04   # 箭头尖端贴在色条左侧

    # 三角箭头：用 annotate
    ax.annotate(
        '',
        xy=(bar_x, percentile),
        xytext=(arrow_x - 0.08, percentile),
        arrowprops=dict(
            arrowstyle='-|>',
            color=curr_color,
            lw=2.0,
            mutation_scale=14,
        ),
        zorder=5
    )

    # ── 数值标注（箭头左侧）────────────────────────────────────────────────
    label_text = f'{count}只\n{percentile:.0f}%'
    ax.text(0.02, percentile, label_text,
            va='center', ha='left', fontsize=8.5,
            color=curr_color, fontweight='bold',
            bbox=dict(boxstyle='round,pad=0.25', facecolor='white',
                      edgecolor=curr_color, linewidth=1.0, alpha=0.9),
            zorder=6)

    # ── Y 轴刻度（百分位标尺） ───────────────────────────────────────────────
    for tick_val in [0, 30, 60, 80, 100]:
        ax.axhline(tick_val, color='#cccccc', linewidth=0.5, zorder=1,
                   linestyle='--', alpha=0.6)
        ax.text(bar_x + bar_w + 0.52, tick_val, f'{tick_val}%',
                va='center', ha='left', fontsize=7, color='#666666')

    ax.set_yticks([])
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_visible(False)
    ax.spines['bottom'].set_visible(False)


def _draw_area_chart(ax, hist_dates: list, hist_counts: list,
                     today_date: str, today_count: int,
                     percentiles_thresholds: dict) -> None:
    """
    右子图：近60日涨停数面积图。

    percentiles_thresholds: {'p30': int, 'p60': int, 'p80': int}
    """
    # ── 取最近 60 个交易日 ───────────────────────────────────────────────────
    combined = list(zip(hist_dates, hist_counts))
    # 包含今日
    combined_with_today = combined + [(today_date, today_count)]
    combined_with_today = sorted(combined_with_today, key=lambda x: x[0])
    last60 = combined_with_today[-60:]

    dates_60  = [x[0] for x in last60]
    counts_60 = [x[1] for x in last60]

    x_idx = list(range(len(dates_60)))

    # ── 面积图 ──────────────────────────────────────────────────────────────
    ax.fill_between(x_idx, counts_60, alpha=0.35, color=COLOR_AREA)
    ax.plot(x_idx, counts_60, color=COLOR_LINE, linewidth=1.5, zorder=3)

    # ── 分位阈值虚线 ────────────────────────────────────────────────────────
    dashed_styles = [
        (percentiles_thresholds.get('p30', 0), COLOR_COLD,   '30%分位'),
        (percentiles_thresholds.get('p60', 0), COLOR_NORMAL, '60%分位'),
        (percentiles_thresholds.get('p80', 0), COLOR_WARM,   '80%分位'),
    ]
    for val, color, lbl in dashed_styles:
        if val > 0:
            ax.axhline(val, color=color, linewidth=1.0, linestyle='--',
                       alpha=0.75, zorder=2)
            ax.text(x_idx[-1] + 0.3, val, lbl,
                    va='center', ha='left', fontsize=6.5, color=color, alpha=0.85)

    # ── 今日红点 ────────────────────────────────────────────────────────────
    today_x_candidates = [i for i, d in enumerate(dates_60) if d == today_date]
    if today_x_candidates:
        today_x = today_x_candidates[0]
        ax.scatter([today_x], [today_count],
                   color=COLOR_MARKER, s=55, zorder=5)
        # 标注偏移（避免压线）
        offset_y = max(counts_60) * 0.08
        ax.annotate(
            f'{today_count}只',
            xy=(today_x, today_count),
            xytext=(today_x - 2, today_count + offset_y),
            fontsize=8, color=COLOR_MARKER, fontweight='bold',
            arrowprops=dict(arrowstyle='->', color=COLOR_MARKER, lw=1.0),
            bbox=dict(boxstyle='round,pad=0.2', facecolor='white',
                      edgecolor=COLOR_MARKER, linewidth=0.8, alpha=0.9),
            zorder=6
        )

    # ── X 轴：每 10 个 tick 显示一次日期 ────────────────────────────────────
    tick_positions = list(range(0, len(dates_60), 10))
    tick_labels = [dates_60[i][4:] for i in tick_positions]   # MM-DD
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, fontsize=7, rotation=30)

    ax.set_xlim(-0.5, len(dates_60) - 0.5 + 3)  # 留右侧标签空间
    ax.set_ylabel('涨停数量（只）', fontsize=8)
    ax.set_title('近60日涨停数走势', fontsize=10, fontweight='bold', pad=6)

    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.tick_params(axis='y', labelsize=7)
    ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True, nbins=5))
    ax.grid(axis='y', linestyle='--', linewidth=0.4, alpha=0.5)


def _make_chart(trade_date: str,
                today_count: int,
                percentile: float,
                hist_dates: list,
                hist_counts: list,
                thresholds: dict) -> str:
    """
    生成双子图图表，返回 base64 编码的 PNG 字符串。

    图表尺寸约 560x220px（dpi=110）。
    """
    fig = plt.figure(figsize=(5.6, 2.2), dpi=110)
    fig.patch.set_facecolor('white')

    # ── 双列布局：左40% + 右60% ─────────────────────────────────────────────
    gs = GridSpec(1, 2, figure=fig,
                  width_ratios=[0.4, 0.6],
                  left=0.04, right=0.90,
                  top=0.88, bottom=0.18,
                  wspace=0.35)

    ax_therm = fig.add_subplot(gs[0])
    ax_area  = fig.add_subplot(gs[1])

    # ── 左子图 ──────────────────────────────────────────────────────────────
    _draw_thermometer(ax_therm, percentile, today_count)

    # ── 右子图 ──────────────────────────────────────────────────────────────
    _draw_area_chart(ax_area, hist_dates, hist_counts,
                     trade_date, today_count, thresholds)

    # ── 总标题 ──────────────────────────────────────────────────────────────
    fmt_date = f'{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}'
    fig.suptitle(f'全市场情绪温度计  {fmt_date}',
                 fontsize=9.5, fontweight='bold', y=0.99)

    # ── 导出 base64 ─────────────────────────────────────────────────────────
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=110,
                bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
    return b64


# ═══════════════════════════════════════════════════════════════════════════════
# 中文叙事生成
# ═══════════════════════════════════════════════════════════════════════════════

def _build_narrative(count: int, percentile: float,
                     dn_count: int, hist_counts: list) -> str:
    """
    根据当日数据生成 2-3 句中文叙事，适合零售投资者阅读。

    分区间调整措辞：
      0-30%  : 极度悲观  — 市场极度低迷，注意抄底陷阱，但也可能出现反转机会
      30-60% : 情绪正常  — 市场情绪中性，是精选个股的较好环境
      60-80% : 情绪偏热  — 赚钱效应扩散，需提高选股标准，控制追高冲动
      80-100%: 极度亢奋  — 亢奋期短期调整风险上升，强势股仍可能冲高
    """
    # 计算历史均值（参考值）
    hist_mean = int(np.mean(hist_counts)) if hist_counts else 0

    # 区间判断
    if percentile < 30:
        zone_label = '极度悲观'
        pct_above  = 100 - percentile
        s1 = (f'今日全市场共有 **{count}只** 股票涨停、**{dn_count}只** 股票跌停，'
              f'处于近一年历史的 **第{percentile:.0f}百分位**（{zone_label}区间）。')
        s2 = (f'简单说：这一年里有约 {pct_above:.0f}% 的交易日比今天更热闹——'
              f'市场正处于极度低迷状态（历史日均约 {hist_mean} 只涨停）。')
        s3 = ('极度悲观区间往往蕴含反转机会，但下跌惯性也可能延续，'
              '建议等待成交量和涨停数同步回升再考虑加仓。')
    elif percentile < 60:
        zone_label = '情绪正常'
        s1 = (f'今日全市场共有 **{count}只** 股票涨停、**{dn_count}只** 股票跌停，'
              f'处于近一年历史的 **第{percentile:.0f}百分位**（{zone_label}区间）。')
        s2 = (f'简单说：这一年里约有一半的交易日与今天热度相当——'
              f'市场情绪处于正常中性状态（历史日均约 {hist_mean} 只涨停）。')
        s3 = ('中性情绪环境下，赚钱效应分散，个股分化加大，是精选优质标的的较好时机，'
              '追涨热点需控制仓位。')
    elif percentile < 80:
        zone_label = '情绪偏热'
        s1 = (f'今日全市场共有 **{count}只** 股票涨停、**{dn_count}只** 股票跌停，'
              f'处于近一年历史的 **第{percentile:.0f}百分位**（{zone_label}区间）。')
        pct_below = percentile
        s2 = (f'简单说：这一年里只有约 {100 - pct_below:.0f}% 的交易日比今天更热闹——'
              f'市场赚钱效应正在扩散（历史日均约 {hist_mean} 只涨停）。')
        s3 = ('情绪偏热阶段主升浪往往尚未结束，但追高风险也在上升，'
              '建议提高选股标准，重点关注量价配合良好的强势品种。')
    else:
        zone_label = '极度亢奋'
        pct_below = 100 - percentile
        s1 = (f'今日全市场共有 **{count}只** 股票涨停、**{dn_count}只** 股票跌停，'
              f'处于近一年历史的 **第{percentile:.0f}百分位**（{zone_label}区间）。')
        s2 = (f'简单说：这一年里，只有约 {pct_below:.0f}% 的交易日比今天更热闹——'
              f'市场正处于高亢奋状态（历史日均约 {hist_mean} 只涨停）。')
        s3 = ('市场情绪越亢奋，短期调整的概率越高，但强势股在亢奋期也往往会继续冲高——'
              '这是把双刃剑；建议已持仓者注意移动止损，新进资金控制追高冲动。')

    return ' '.join([s1, s2, s3])


# ═══════════════════════════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════════════════════════

def sentiment_thermometer(trade_date: str, lookback_days: int = 250) -> dict:
    """
    计算全市场情绪温度计。

    Parameters
    ----------
    trade_date   : str  — 目标日期，格式 'YYYYMMDD'
    lookback_days: int  — 历史回溯交易日数，默认 250（约1年）

    Returns
    -------
    dict with keys:
        trade_date       : str   — 目标日期
        limit_up_count   : int   — 当日涨停数量
        limit_down_count : int   — 当日跌停数量
        percentile       : float — 0-100，当日涨停数在历史中的百分位
        hist_counts      : list[int] — 历史各日涨停数（由早到晚，不含今日）
        hist_dates       : list[str] — 对应日期
        chart_b64        : str   — base64 PNG 图表
        narrative        : str   — 2-3 句中文叙事
    """
    print(f'[sentiment_thermometer] 日期: {trade_date}，回溯: {lookback_days} 个交易日')

    # ── Step 1: 获取交易日历 ────────────────────────────────────────────────
    print('  获取交易日历...')
    all_dates = _get_trading_dates(trade_date, lookback_days + 1)
    # 将 trade_date 本身也包含进来（确保当日在列表里）
    if trade_date not in all_dates:
        all_dates = sorted(all_dates + [trade_date])
    # 历史日期 = 不含今日的最近 lookback_days 个
    hist_candidates = [d for d in all_dates if d < trade_date]
    hist_dates_need = hist_candidates[-lookback_days:]
    fetch_dates = hist_dates_need + [trade_date]

    # ── Step 2: 加载缓存，补拉缺失数据 ─────────────────────────────────────
    cache = _load_cache()
    cache = _build_history(fetch_dates, cache)

    # ── Step 3: 提取今日数据 ────────────────────────────────────────────────
    today_record = cache.get(trade_date)
    if today_record is None:
        print(f'  [WARN] 无法获取 {trade_date} 的数据，尝试直接拉取...')
        today_record = _fetch_one_day(trade_date)
        if today_record is not None:
            cache[trade_date] = today_record
            _save_cache(cache)

    if today_record is None:
        # 返回空结果，避免崩溃
        return {
            'trade_date':        trade_date,
            'limit_up_count':    0,
            'limit_down_count':  0,
            'percentile':        50.0,
            'hist_counts':       [],
            'hist_dates':        [],
            'chart_b64':         '',
            'narrative':         f'无法获取 {trade_date} 的市场数据，请稍后重试。',
        }

    today_up = today_record['up']
    today_dn = today_record['dn']

    # ── Step 4: 构建历史序列 ────────────────────────────────────────────────
    hist_data = []
    for d in hist_dates_need:
        rec = cache.get(d)
        if rec is not None:
            hist_data.append((d, rec['up']))

    hist_data.sort(key=lambda x: x[0])
    hist_dates  = [x[0] for x in hist_data]
    hist_counts = [x[1] for x in hist_data]

    # ── Step 5: 计算百分位 ──────────────────────────────────────────────────
    if hist_counts:
        arr = np.array(hist_counts)
        # 百分位：今日严格超过历史中多少 %（0=最低，100=最高）
        percentile = float(np.mean(arr < today_up) * 100)
    else:
        percentile = 50.0

    print(f'  涨停: {today_up}只  跌停: {today_dn}只  '
          f'百分位: {percentile:.1f}%（历史 {len(hist_counts)} 天）')

    # ── Step 6: 计算分位阈值（用于图表虚线）──────────────────────────────
    if len(hist_counts) >= 10:
        arr = np.array(hist_counts)
        thresholds = {
            'p30': int(np.percentile(arr, 30)),
            'p60': int(np.percentile(arr, 60)),
            'p80': int(np.percentile(arr, 80)),
        }
    else:
        thresholds = {'p30': 0, 'p60': 0, 'p80': 0}

    # ── Step 7: 绘制图表 ────────────────────────────────────────────────────
    print('  生成图表...')
    try:
        chart_b64 = _make_chart(
            trade_date=trade_date,
            today_count=today_up,
            percentile=percentile,
            hist_dates=hist_dates,
            hist_counts=hist_counts,
            thresholds=thresholds,
        )
    except Exception as e:
        print(f'  [WARN] 图表生成失败: {e}')
        chart_b64 = ''

    # ── Step 8: 生成叙事 ────────────────────────────────────────────────────
    narrative = _build_narrative(today_up, percentile, today_dn, hist_counts)

    return {
        'trade_date':        trade_date,
        'limit_up_count':    today_up,
        'limit_down_count':  today_dn,
        'percentile':        round(percentile, 1),
        'hist_counts':       hist_counts,
        'hist_dates':        hist_dates,
        'chart_b64':         chart_b64,
        'narrative':         narrative,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CLI 入口（测试）
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='全市场情绪温度计测试')
    parser.add_argument('--date', type=str, default='20260520',
                        help='报告日期 YYYYMMDD（默认 20260520）')
    parser.add_argument('--lookback', type=int, default=250,
                        help='历史回溯交易日数（默认 250）')
    parser.add_argument('--save-chart', type=str, default='',
                        help='将图表 PNG 保存到指定路径（可选）')
    args = parser.parse_args()

    print('=' * 60)
    print(f'全市场情绪温度计  日期: {args.date}')
    print('=' * 60)

    result = sentiment_thermometer(args.date, lookback_days=args.lookback)

    print()
    print(f'涨停数量   : {result["limit_up_count"]} 只')
    print(f'跌停数量   : {result["limit_down_count"]} 只')
    print(f'历史百分位 : {result["percentile"]:.1f}%')
    print(f'历史序列   : {len(result["hist_counts"])} 个交易日')

    if result['hist_counts']:
        arr = np.array(result['hist_counts'])
        print(f'历史涨停   : 最低 {arr.min()} / 均值 {arr.mean():.0f} / 最高 {arr.max()} 只')

    print()
    print('--- 叙事 ---')
    # 去掉 markdown 加粗符号，方便终端显示
    narrative_plain = result['narrative'].replace('**', '')
    print(narrative_plain)

    # 保存图表（可选）
    if args.save_chart:
        img_path = args.save_chart
    else:
        img_path = os.path.join(_SCRIPT_DIR, f'sentiment_{args.date}.png')

    if result['chart_b64']:
        img_bytes = base64.b64decode(result['chart_b64'])
        with open(img_path, 'wb') as f:
            f.write(img_bytes)
        print(f'\n图表已保存: {img_path}')
    else:
        print('\n[WARN] 图表未生成')

    print()
    print('完成。')
