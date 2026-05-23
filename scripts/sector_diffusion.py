"""
sector_diffusion.py
===================
申万行业板块扩散进度分析

功能：
1. 获取申万行业成分股（使用 tushare index_member API，过滤 is_new='Y'）
2. 计算各成分股近5日涨幅（ending on trade_date）
3. 分桶统计：大幅启动 / 已启动 / 小幅跟涨 / 横盘 / 逆势下跌
4. 识别板块扩散阶段：初期 / 中期 / 后期 / 过热
5. 生成可视化图表（base64 编码）和中文叙事文字

实现要点：
- tushare daily API 有约 6000 行/次的返回上限
- 应对方式：将历史数据按 ~90 日时间段分块拉取，合并后计算（沿用 sector_breadth.py 的分块策略）
- 成分股数量超过 max_stocks 时取前 max_stocks 只（按代码排序）
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

import tushare as ts

# ── 初始化 tushare ───────────────────────────────────────────────
pro = ts.pro_api()

# ── 字体设置（中文支持）────────────────────────────────────────
rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
rcParams['axes.unicode_minus'] = False

# ── 行业名称映射 ─────────────────────────────────────────────────
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

# ── 扩散桶定义 ───────────────────────────────────────────────────
BUCKET_DEFS = [
    # (key, label, lower_exclusive, upper_inclusive, color)
    ('surge',   '已大幅启动\n(>20%)',   20.0,  float('inf'), '#d62728'),   # red
    ('started', '已启动\n(10-20%)',     10.0,  20.0,         '#ff7f0e'),   # orange
    ('mild',    '小幅跟涨\n(5-10%)',     5.0,  10.0,         '#ffd700'),   # yellow/gold
    ('flat',    '横盘\n(-5%~+5%)',      -5.0,   5.0,         '#aec7e8'),   # light gray-blue
    ('down',    '逆势下跌\n(<-5%)',  float('-inf'), -5.0,    '#add8e6'),   # light blue
]

BUCKET_LABELS = {
    'surge':   '已大幅启动(>20%)',
    'started': '已启动(10-20%)',
    'mild':    '小幅跟涨(5-10%)',
    'flat':    '横盘(-5%~+5%)',
    'down':    '逆势下跌(<-5%)',
}

BUCKET_COLORS = {
    'surge':   '#d62728',
    'started': '#ff7f0e',
    'mild':    '#ffd700',
    'flat':    '#aec7e8',
    'down':    '#add8e6',
}


# ── 数据获取辅助函数 ─────────────────────────────────────────────

def _date_chunks(start_date: str, end_date: str, chunk_days: int = 90):
    """将 [start_date, end_date] 切分为若干个不超过 chunk_days 自然日的时间段。"""
    fmt = '%Y%m%d'
    start_dt = datetime.strptime(start_date, fmt)
    end_dt = datetime.strptime(end_date, fmt)
    chunks = []
    cur = start_dt
    while cur <= end_dt:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), end_dt)
        chunks.append((cur.strftime(fmt), chunk_end.strftime(fmt)))
        cur = chunk_end + timedelta(days=1)
    return chunks


def _fetch_daily_chunked(codes: list, start_date: str, end_date: str,
                          batch_size: int = 80, chunk_days: int = 85,
                          sleep_sec: float = 0.15) -> pd.DataFrame:
    """
    分块拉取多只股票的日线数据，规避 tushare 6000 行/次限制。
    返回列：ts_code, trade_date, close
    """
    all_dfs = []
    date_chunks = _date_chunks(start_date, end_date, chunk_days=chunk_days)

    for d_start, d_end in date_chunks:
        for i in range(0, len(codes), batch_size):
            batch = codes[i: i + batch_size]
            ts_batch = ','.join(batch)
            try:
                df = pro.daily(
                    ts_code=ts_batch,
                    start_date=d_start,
                    end_date=d_end,
                    fields='ts_code,trade_date,close'
                )
                if df is not None and len(df) > 0:
                    all_dfs.append(df)
            except Exception as e:
                print(f'  [WARN] fetch error {d_start}-{d_end} batch {i // batch_size + 1}: {e}')
            time.sleep(sleep_sec)

    if not all_dfs:
        return pd.DataFrame(columns=['ts_code', 'trade_date', 'close'])

    result = pd.concat(all_dfs, ignore_index=True)
    result = result.drop_duplicates(subset=['ts_code', 'trade_date'])
    result['trade_date'] = result['trade_date'].astype(str)
    result['close'] = pd.to_numeric(result['close'], errors='coerce')
    return result


def _compute_5d_return(price_df: pd.DataFrame, codes: list,
                        trade_date: str) -> pd.Series:
    """
    计算每只股票的 5 日涨幅（截至 trade_date）。

    取 trade_date 当日及之前的最近 6 个交易日的收盘价，
    5日涨幅 = (close_today - close_5d_ago) / close_5d_ago * 100

    返回 pd.Series，index=ts_code，value=5d return (%) 或 NaN（数据不足）。
    """
    grouped = {code: grp for code, grp in price_df.groupby('ts_code')}
    returns = {}

    for code in codes:
        grp = grouped.get(code)
        if grp is None or len(grp) == 0:
            returns[code] = np.nan
            continue

        sub = grp[grp['trade_date'] <= trade_date].sort_values('trade_date')
        closes = sub['close'].dropna().values

        if len(closes) < 6:
            # 不足 6 bar，至少需要 2 bar
            if len(closes) >= 2:
                ret = (closes[-1] / closes[0] - 1) * 100
                returns[code] = float(ret)
            else:
                returns[code] = np.nan
            continue

        # 取最近 6 个收盘价（下标 -6 到 -1）
        close_today = closes[-1]
        close_5d_ago = closes[-6]

        if close_5d_ago == 0 or np.isnan(close_5d_ago) or np.isnan(close_today):
            returns[code] = np.nan
        else:
            returns[code] = float((close_today / close_5d_ago - 1) * 100)

    return pd.Series(returns)


def _assign_bucket(ret: float) -> str:
    """将涨跌幅归入扩散桶。"""
    if np.isnan(ret):
        return 'flat'  # 缺失数据归入横盘
    if ret > 20:
        return 'surge'
    elif ret > 10:
        return 'started'
    elif ret > 5:
        return 'mild'
    elif ret >= -5:
        return 'flat'
    else:
        return 'down'


def _determine_diffusion_stage(pct_active: float) -> str:
    """
    根据已启动（surge + started）占比判断扩散阶段。
    pct_active: 0-100 的百分比
    """
    if pct_active < 15:
        return '初期扩散(龙头独走)'
    elif pct_active < 35:
        return '中期扩散(跟风开始)'
    elif pct_active < 60:
        return '后期扩散(普涨)'
    else:
        return '过热(注意风险)'


# ── 图表生成 ─────────────────────────────────────────────────────

def _build_chart(buckets: dict, returns_arr: np.ndarray,
                 index_name: str, n_total: int) -> str:
    """
    生成两面板图表并返回 base64 编码字符串。

    左（60%）：横向堆叠条形图（扩散桶分布）
    右（40%）：5日涨幅分布直方图
    """
    fig, (ax_left, ax_right) = plt.subplots(
        1, 2,
        figsize=(8, 2.8),
        gridspec_kw={'width_ratios': [3, 2]},
        facecolor='white'
    )
    fig.subplots_adjust(left=0.03, right=0.97, top=0.82, bottom=0.18, wspace=0.35)

    # ── 左图：横向堆叠条形图 ──────────────────────────────────
    bucket_order = ['surge', 'started', 'mild', 'flat', 'down']
    bucket_display = {
        'surge':   '已大幅启动\n>20%',
        'started': '已启动\n10-20%',
        'mild':    '小幅跟涨\n5-10%',
        'flat':    '横盘\n-5%~+5%',
        'down':    '逆势下跌\n<-5%',
    }

    x_start = 0.0
    n_total_valid = sum(b['count'] for b in buckets.values())
    if n_total_valid == 0:
        n_total_valid = 1  # avoid divide by zero

    for key in bucket_order:
        bkt = buckets[key]
        count = bkt['count']
        pct_val = count / n_total_valid  # fraction [0,1]
        color = BUCKET_COLORS[key]

        bar = ax_left.barh(0, pct_val, left=x_start, height=0.55,
                           color=color, edgecolor='white', linewidth=0.8)

        # Label segment if wide enough
        if pct_val > 0.04:
            mid_x = x_start + pct_val / 2
            label_lines = bucket_display[key].split('\n')
            label_top = label_lines[0]
            label_bot = f'{count}只({bkt["pct"]:.0f}%)'
            ax_left.text(mid_x, 0.18, label_top,
                         ha='center', va='center', fontsize=5.5,
                         color='black', fontweight='bold')
            ax_left.text(mid_x, -0.18, label_bot,
                         ha='center', va='center', fontsize=5.5, color='black')

        x_start += pct_val

    ax_left.set_xlim(0, 1)
    ax_left.set_ylim(-0.6, 0.6)
    ax_left.set_yticks([])
    ax_left.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
    ax_left.set_xticklabels(['0%', '25%', '50%', '75%', '100%'], fontsize=7)
    ax_left.set_title(f'{index_name} 板块扩散进度\n(成分股共{n_total}只)',
                      fontsize=8, fontweight='bold', pad=4)
    ax_left.spines['top'].set_visible(False)
    ax_left.spines['right'].set_visible(False)
    ax_left.spines['left'].set_visible(False)

    # ── 右图：5日涨幅分布直方图 ───────────────────────────────
    valid_returns = returns_arr[~np.isnan(returns_arr)]

    if len(valid_returns) > 0:
        # Determine bin edges
        r_min = max(-40, valid_returns.min() - 2)
        r_max = min(60, valid_returns.max() + 2)
        bins = np.linspace(r_min, r_max, 30)

        # Color each bar by bucket
        bin_centers = (bins[:-1] + bins[1:]) / 2
        bar_colors = [BUCKET_COLORS[_assign_bucket(c)] for c in bin_centers]

        counts_hist, _ = np.histogram(valid_returns, bins=bins)
        bar_width = bins[1] - bins[0]

        for idx, (cnt_h, bc) in enumerate(zip(counts_hist, bar_colors)):
            ax_right.bar(bin_centers[idx], cnt_h, width=bar_width * 0.92,
                         color=bc, edgecolor='white', linewidth=0.4, alpha=0.85)

    # Zero line
    ax_right.axvline(x=0, color='#333333', linestyle='--', linewidth=0.9, alpha=0.7)

    ax_right.set_xlabel('5日涨幅 (%)', fontsize=6.5)
    ax_right.set_ylabel('股票数', fontsize=6.5)
    ax_right.set_title('成分股5日涨幅分布', fontsize=8, fontweight='bold', pad=4)
    ax_right.tick_params(axis='both', labelsize=6)
    ax_right.spines['top'].set_visible(False)
    ax_right.spines['right'].set_visible(False)

    # ── 输出 base64 ───────────────────────────────────────────
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


# ── 叙事生成 ─────────────────────────────────────────────────────

def _build_narrative(index_name: str, n_total: int, buckets: dict,
                     diffusion_stage: str, median_return: float) -> str:
    """根据扩散阶段生成中文叙事。"""
    surge_c = buckets['surge']['count']
    surge_p = buckets['surge']['pct']
    started_c = buckets['started']['count']
    started_p = buckets['started']['pct']
    mild_c = buckets['mild']['count']
    mild_p = buckets['mild']['pct']
    flat_c = buckets['flat']['count']
    flat_p = buckets['flat']['pct']
    down_c = buckets['down']['count']
    down_p = buckets['down']['pct']

    active_total = surge_c + started_c
    active_pct = surge_p + started_p

    stage_map = {
        '初期扩散(龙头独走)': (
            f"{index_name}{n_total}只成分股中，近5日：**大幅启动(>20%) {surge_c}只({surge_p:.0f}%)** + "
            f"**已启动(10-20%) {started_c}只({started_p:.0f}%)** = 合计{active_pct:.0f}%已起。"
            f"横盘观望的仍有 **{flat_c}只({flat_p:.0f}%)**。"
            f"当前处于『初期扩散』阶段——龙头刚开始发力，但大多数票还没有跟上。"
            f"这种阶段风险相对可控，是布局跟风票的窗口期，但需关注龙头能否持续放量。"
        ),
        '中期扩散(跟风开始)': (
            f"{index_name}{n_total}只成分股中，近5日：**大幅启动(>20%) {surge_c}只({surge_p:.0f}%)** + "
            f"**已启动(10-20%) {started_c}只({started_p:.0f}%)** = 合计{active_pct:.0f}%已起。"
            f"横盘观望的仍有 **{flat_c}只({flat_p:.0f}%)**。"
            f"当前处于『中期扩散』阶段——龙头已走、跟风已初步开始，但大多数票还没动。"
            f"这种阶段通常是主升浪的中段，板块行情持续性最强，中位涨幅{median_return:+.1f}%。"
        ),
        '后期扩散(普涨)': (
            f"{index_name}{n_total}只成分股中，近5日：**大幅启动(>20%) {surge_c}只({surge_p:.0f}%)** + "
            f"**已启动(10-20%) {started_c}只({started_p:.0f}%)** = 合计{active_pct:.0f}%已起。"
            f"横盘观望的仅剩 **{flat_c}只({flat_p:.0f}%)**，逆势下跌 {down_c}只。"
            f"当前处于『后期扩散』阶段——板块普涨已较充分，中位涨幅{median_return:+.1f}%。"
            f"新进入者需注意高位风险，注意是否有龙头开始高位换手出货。"
        ),
        '过热(注意风险)': (
            f"{index_name}{n_total}只成分股中，近5日已有{active_pct:.0f}%的个股大幅启动，"
            f"**大幅启动(>20%) {surge_c}只({surge_p:.0f}%)**，中位涨幅高达{median_return:+.1f}%。"
            f"板块已进入『过热』阶段——超过60%的股票都已大幅上涨，普涨后期往往是阶段性高点。"
            f"逆势下跌仅 {down_c}只({down_p:.0f}%)，几乎没有补涨机会。需高度警惕获利了结压力。"
        ),
    }

    return stage_map.get(diffusion_stage, f'{index_name}板块扩散阶段：{diffusion_stage}，中位5日涨幅{median_return:+.1f}%。')


# ── 主函数 ───────────────────────────────────────────────────────

def sector_diffusion(index_code: str, trade_date: str, max_stocks: int = 80) -> dict:
    """
    计算申万行业板块扩散进度。

    Parameters
    ----------
    index_code : str
        申万行业指数代码，如 '801081.SI'（半导体）、'801880.SI'（汽车）
    trade_date : str
        目标日期，格式 'YYYYMMDD'
    max_stocks : int
        成分股数量超过此值时取前 max_stocks 只（按代码排序）

    Returns
    -------
    dict with keys:
        index_code      : str
        index_name      : str
        trade_date      : str
        n_total         : int   — 成分股总数（index_member 返回）
        buckets         : dict  — {'surge': {'count', 'pct', 'label'}, ...}
        median_return_5d: float — 所有成分股5日涨幅中位数 (%)
        diffusion_stage : str   — 扩散阶段描述
        chart_b64       : str   — PNG 图表 base64 编码
        narrative       : str   — 中文叙事
        error           : str | None
    """
    index_name = INDEX_NAMES.get(index_code, index_code)
    result = {
        'index_code': index_code,
        'index_name': index_name,
        'trade_date': trade_date,
        'n_total': 0,
        'buckets': {
            'surge':   {'count': 0, 'pct': 0.0, 'label': '已大幅启动(>20%)'},
            'started': {'count': 0, 'pct': 0.0, 'label': '已启动(10-20%)'},
            'mild':    {'count': 0, 'pct': 0.0, 'label': '小幅跟涨(5-10%)'},
            'flat':    {'count': 0, 'pct': 0.0, 'label': '横盘(-5%~+5%)'},
            'down':    {'count': 0, 'pct': 0.0, 'label': '逆势下跌(<-5%)'},
        },
        'median_return_5d': 0.0,
        'diffusion_stage': '初期扩散(龙头独走)',
        'chart_b64': '',
        'narrative': '',
        'error': None,
    }

    # ── Step 1: 获取成分股 ───────────────────────────────────────
    try:
        df_members = pro.index_member(index_code=index_code)
        current_members = df_members[df_members['is_new'] == 'Y']['con_code'].tolist()
    except Exception as e:
        result['error'] = f'index_member error: {e}'
        return result

    if not current_members:
        result['error'] = 'No current members found (is_new=Y)'
        return result

    result['n_total'] = len(current_members)
    print(f'  {index_name}（{index_code}）成分股 {len(current_members)} 只')

    # 限制股票数量
    if len(current_members) > max_stocks:
        current_members = sorted(current_members)[:max_stocks]
        print(f'  成分股超过 {max_stocks}，已截取前 {max_stocks} 只（按代码排序）')

    # ── Step 2: 拉取过去10个交易日数据 ──────────────────────────
    target_dt = datetime.strptime(trade_date, '%Y%m%d')
    # 取 14 个自然日前作为数据起始（保证覆盖至少 10 个交易日）
    data_start_dt = target_dt - timedelta(days=20)
    data_start_str = data_start_dt.strftime('%Y%m%d')
    data_end_str = trade_date

    print(f'  拉取 {len(current_members)} 只成分股数据（{data_start_str} ~ {data_end_str}）...')
    t0 = time.time()
    price_df = _fetch_daily_chunked(
        codes=current_members,
        start_date=data_start_str,
        end_date=data_end_str,
        batch_size=80,
        chunk_days=85,
        sleep_sec=0.15
    )
    elapsed = time.time() - t0
    print(f'  拉取完成：{len(price_df)} 行，{price_df["ts_code"].nunique()} 只股票，'
          f'{price_df["trade_date"].nunique()} 个交易日，耗时 {elapsed:.1f}s')

    if len(price_df) == 0:
        result['error'] = 'No price data fetched'
        return result

    # ── Step 3: 计算各股5日涨幅 ─────────────────────────────────
    returns_series = _compute_5d_return(price_df, current_members, trade_date)
    valid_returns = returns_series.dropna()
    returns_arr = returns_series.values  # 含 NaN，用于直方图

    if len(valid_returns) == 0:
        result['error'] = 'Could not compute 5d returns for any constituent'
        return result

    # ── Step 4: 分桶统计 ─────────────────────────────────────────
    n_valid = len(valid_returns)
    bucket_counts = {key: 0 for key in ['surge', 'started', 'mild', 'flat', 'down']}
    for ret in valid_returns.values:
        bucket_counts[_assign_bucket(ret)] += 1

    buckets = {}
    for key in ['surge', 'started', 'mild', 'flat', 'down']:
        cnt = bucket_counts[key]
        pct = (cnt / n_valid * 100) if n_valid > 0 else 0.0
        buckets[key] = {
            'count': cnt,
            'pct': round(pct, 1),
            'label': BUCKET_LABELS[key],
        }
    result['buckets'] = buckets

    # ── Step 5: 中位数 & 扩散阶段 ───────────────────────────────
    median_ret = float(np.median(valid_returns.values))
    result['median_return_5d'] = round(median_ret, 2)

    pct_active = buckets['surge']['pct'] + buckets['started']['pct']
    diffusion_stage = _determine_diffusion_stage(pct_active)
    result['diffusion_stage'] = diffusion_stage

    print(f'  5日涨幅中位数: {median_ret:+.2f}%  |  '
          f'已启动占比: {pct_active:.1f}%  |  扩散阶段: {diffusion_stage}')
    for key in ['surge', 'started', 'mild', 'flat', 'down']:
        b = buckets[key]
        print(f'    {BUCKET_LABELS[key]}: {b["count"]}只 ({b["pct"]:.1f}%)')

    # ── Step 6: 图表 ─────────────────────────────────────────────
    try:
        chart_b64 = _build_chart(buckets, returns_arr, index_name, result['n_total'])
        result['chart_b64'] = chart_b64
    except Exception as e:
        print(f'  [WARN] chart generation failed: {e}')
        result['chart_b64'] = ''

    # ── Step 7: 叙事 ─────────────────────────────────────────────
    narrative = _build_narrative(
        index_name=index_name,
        n_total=result['n_total'],
        buckets=buckets,
        diffusion_stage=diffusion_stage,
        median_return=median_ret,
    )
    result['narrative'] = narrative

    return result


# ── 主程序（测试）────────────────────────────────────────────────
if __name__ == '__main__':
    TEST_DATE = '20260520'
    TEST_SECTORS = [
        ('801081.SI', '申万半导体'),
        ('801880.SI', '申万汽车'),
    ]

    all_results = {}
    for code, name in TEST_SECTORS:
        print(f"\n{'=' * 60}")
        print(f"计算 {name}（{code}）板块扩散进度，日期：{TEST_DATE}")
        print('=' * 60)

        r = sector_diffusion(index_code=code, trade_date=TEST_DATE, max_stocks=80)
        all_results[code] = r

        if r.get('error'):
            print(f'  错误: {r["error"]}')
        else:
            print(f'\n  扩散阶段  : {r["diffusion_stage"]}')
            print(f'  中位5日涨幅: {r["median_return_5d"]:+.2f}%')
            print(f'  图表已生成 : {"是" if r["chart_b64"] else "否"}（{len(r["chart_b64"])} 字符）')
            print(f'\n  叙事:')
            print(f'  {r["narrative"]}')

        time.sleep(0.5)

    print('\n\n' + '=' * 60)
    print('汇总')
    print('=' * 60)
    for code, r in all_results.items():
        name = r.get('index_name', code)
        if r.get('error'):
            print(f'{name}: 失败 — {r["error"]}')
        else:
            pct_active = r['buckets']['surge']['pct'] + r['buckets']['started']['pct']
            print(f'{name}: 已启动{pct_active:.1f}%, 阶段={r["diffusion_stage"]}, '
                  f'中位涨幅={r["median_return_5d"]:+.2f}%')

    print('\n完成。')
