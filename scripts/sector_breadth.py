"""
sector_breadth.py
=================
申万行业板块广度指标计算器

功能：
1. 获取申万行业成分股（使用 tushare index_member API，过滤 is_new='Y'）
2. 计算当日广度：板块内有多少 % 的成分股收盘价高于 20 日均线
3. 计算历史分位：该广度值在过去 lookback_days 个交易日处于第几百分位

实现要点：
- tushare daily API 有约 6000 行/次的返回上限
- 应对方式：将历史数据按 ~90 日时间段分块拉取，合并后计算
- 成分股数量超过 max_stocks 时取前 max_stocks 只（按代码排序）
- 历史分位：从历史价格数据中提取实际交易日，每 history_sample_every 个交易日采样
"""

import sys
import io
import time
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import tushare as ts

# ── 初始化 tushare ───────────────────────────────────────────────
pro = ts.pro_api()

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


def _date_chunks(start_date: str, end_date: str, chunk_days: int = 90):
    """
    将 [start_date, end_date] 切分为若干个不超过 chunk_days 自然日的时间段。
    返回 [(start1, end1), (start2, end2), ...] 列表。
    """
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

    策略：将日期范围切成 chunk_days 天的段（每段 ~60 个交易日），
    80 只股票 × 60 交易日 = 4800 行，低于 6000 行上限。
    如果股票数量 > batch_size，则在日期块内再按股票分批。

    返回列：ts_code, trade_date, close
    """
    all_dfs = []
    date_chunks = _date_chunks(start_date, end_date, chunk_days=chunk_days)

    for d_start, d_end in date_chunks:
        # 按股票批次
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
                print(f'  [WARN] fetch error {d_start}-{d_end} batch {i//batch_size+1}: {e}')
            time.sleep(sleep_sec)

    if not all_dfs:
        return pd.DataFrame(columns=['ts_code', 'trade_date', 'close'])

    result = pd.concat(all_dfs, ignore_index=True)
    result = result.drop_duplicates(subset=['ts_code', 'trade_date'])
    result['trade_date'] = result['trade_date'].astype(str)
    result['close'] = pd.to_numeric(result['close'], errors='coerce')
    return result


def _compute_breadth_for_date(price_df: pd.DataFrame,
                               codes: list,
                               target_date: str,
                               ma_period: int = 20,
                               min_bars: int = 15) -> tuple:
    """
    在给定的 price_df 中计算 target_date 当日的板块广度。

    price_df 需含 ts_code, trade_date, close 列，且包含 target_date 之前的足够历史。

    Returns
    -------
    (breadth_ratio, n_above, n_valid) :
        breadth_ratio: 0~1 之间的广度值（收盘>20dMA 占比），若无法计算则 None
        n_above: 收盘>20dMA 的股票数
        n_valid: 有效参与计算的股票数
    """
    above_count = 0
    valid_count = 0

    # 预先构建 dict，提速
    grouped = {code: grp for code, grp in price_df.groupby('ts_code')}

    for code in codes:
        grp = grouped.get(code)
        if grp is None or len(grp) == 0:
            continue

        # 截取到 target_date（含）
        sub = grp[grp['trade_date'] <= target_date].sort_values('trade_date')
        if len(sub) < min_bars:
            continue

        close_arr = sub['close'].values
        # 必须有非 NaN 数据
        close_arr = close_arr[~np.isnan(close_arr)]
        if len(close_arr) < min_bars:
            continue

        latest_date = sub['trade_date'].values[-1]
        # 最新数据不能距 target_date 超过 7 自然日（节假日容忍）
        if abs((datetime.strptime(latest_date, '%Y%m%d') -
                datetime.strptime(target_date, '%Y%m%d')).days) > 7:
            continue

        latest_close = close_arr[-1]
        ma = close_arr[-ma_period:].mean() if len(close_arr) >= ma_period else close_arr.mean()

        if ma == 0 or np.isnan(ma) or np.isnan(latest_close):
            continue

        valid_count += 1
        if latest_close > ma:
            above_count += 1

    if valid_count == 0:
        return None, 0, 0

    return above_count / valid_count, above_count, valid_count


def sector_breadth(index_code: str,
                   trade_date: str,
                   lookback_days: int = 252,
                   max_stocks: int = 80,
                   history_sample_every: int = 5) -> dict:
    """
    计算申万行业板块广度指标。

    Parameters
    ----------
    index_code : str
        申万行业指数代码，如 '801081.SI'（半导体）、'801880.SI'（汽车）
    trade_date : str
        目标日期，格式 'YYYYMMDD'
    lookback_days : int
        历史分位的回溯交易日数（默认 252 ≈ 12 个月）
    max_stocks : int
        成分股数量超过此值时取前 max_stocks 只（按代码排序）
    history_sample_every : int
        历史分位采样间隔（每 N 个交易日采样一次），默认 5

    Returns
    -------
    dict with keys:
        index_code           : str   — 行业代码
        index_name           : str   — 行业名称
        date                 : str   — 计算日期
        n_stocks_total       : int   — 成分股总数（index_member 返回）
        n_stocks             : int   — 有效参与计算的成分股数
        n_above_ma           : int   — 收盘>20dMA 的股票数
        pct_above_20ma       : float — 0~1，当日广度
        historical_pct       : list  — 历史各采样日广度值
        historical_percentile: float — 当日广度在历史中的百分位（0~100）
        error                : str | None
    """
    result = {
        'index_code': index_code,
        'index_name': INDEX_NAMES.get(index_code, index_code),
        'date': trade_date,
        'n_stocks_total': 0,
        'n_stocks': 0,
        'n_above_ma': 0,
        'pct_above_20ma': None,
        'historical_pct': [],
        'historical_percentile': None,
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

    result['n_stocks_total'] = len(current_members)

    # 限制股票数量（排序后取前 max_stocks 只）
    if len(current_members) > max_stocks:
        current_members = sorted(current_members)[:max_stocks]
        print(f'  成分股超过 {max_stocks}，已截取前 {max_stocks} 只（按代码排序）')

    # ── Step 2: 拉取价格数据 ─────────────────────────────────────
    target_dt = datetime.strptime(trade_date, '%Y%m%d')

    # 历史起点：往前 lookback_days 个交易日（约 lookback_days * 1.45 个自然日）
    # 再额外往前 30 个自然日，为第一个历史采样日提供足够的 MA 计算数据
    cal_days_back = int(lookback_days * 1.5) + 30
    data_start_dt = target_dt - timedelta(days=cal_days_back + 40)  # +40 for MA warmup
    data_start_str = data_start_dt.strftime('%Y%m%d')
    data_end_str = trade_date  # 含当日

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

    # ── Step 3: 计算当日广度 ─────────────────────────────────────
    current_breadth, above_n, valid_n = _compute_breadth_for_date(
        price_df=price_df,
        codes=current_members,
        target_date=trade_date,
        ma_period=20,
        min_bars=15
    )

    if current_breadth is None:
        result['error'] = 'Cannot compute breadth for target date (insufficient data)'
        return result

    result['n_stocks'] = valid_n
    result['n_above_ma'] = above_n
    result['pct_above_20ma'] = current_breadth

    # ── Step 4: 计算历史分位 ─────────────────────────────────────
    # 从价格数据中提取所有历史交易日
    hist_start_dt = target_dt - timedelta(days=int(lookback_days * 1.5))
    hist_start_str = hist_start_dt.strftime('%Y%m%d')
    # 历史日期：hist_start 到 trade_date 前一天
    hist_end_str = (target_dt - timedelta(days=1)).strftime('%Y%m%d')

    all_trade_dates = sorted(price_df['trade_date'].unique())
    hist_dates = [d for d in all_trade_dates if hist_start_str <= d <= hist_end_str]

    # 每 history_sample_every 个交易日采样一次
    sampled_dates = hist_dates[::history_sample_every]
    print(f'  历史采样点：{len(sampled_dates)} 个（{hist_start_str} ~ {hist_end_str}，每 {history_sample_every} 个交易日）')

    if len(sampled_dates) < 5:
        print('  [WARN] 历史采样点太少，跳过分位计算')
        return result

    # 批量计算历史广度（向量化：按日分组 groupby）
    hist_breadths = []
    for hist_date in sampled_dates:
        b, _, _ = _compute_breadth_for_date(
            price_df=price_df,
            codes=current_members,
            target_date=hist_date,
            ma_period=20,
            min_bars=15
        )
        if b is not None:
            hist_breadths.append(b)

    result['historical_pct'] = hist_breadths

    if hist_breadths:
        arr = np.array(hist_breadths)
        # 百分位：当日广度严格超过历史中多少 %
        percentile = float(np.mean(arr < current_breadth) * 100)
        result['historical_percentile'] = percentile
        print(f'  当日广度: {current_breadth:.1%}  |  '
              f'历史均值: {arr.mean():.1%}  |  '
              f'历史范围: {arr.min():.1%}~{arr.max():.1%}  |  '
              f'历史百分位: {percentile:.1f}%')

    return result


def print_report(result: dict, verbose: bool = True):
    """格式化打印板块广度报告"""
    name = result.get('index_name', result.get('index_code', ''))
    print(f"\n=== {name} ({result.get('index_code','')}) ===")
    if result.get('error'):
        print(f"错误: {result['error']}")
        return

    total = result.get('n_stocks_total', 0)
    n = result.get('n_stocks', 0)
    above = result.get('n_above_ma', 0)
    pct = result.get('pct_above_20ma')
    percentile = result.get('historical_percentile')
    hist_pct = result.get('historical_pct', [])

    print(f"成分股数量: {total} 只（有效计算: {n} 只）")
    if pct is not None:
        print(f"收盘>20dMA 股票数: {above} ({pct:.1%})")
    if percentile is not None:
        print(f"近12月历史分位: {percentile:.1f}%（当前广度超过近12月 {percentile:.0f}% 的交易日）")
        if verbose and hist_pct:
            arr = np.array(hist_pct)
            print(f"  历史广度：最低 {arr.min():.1%} / 均值 {arr.mean():.1%} / 最高 {arr.max():.1%}"
                  f"（采样 {len(hist_pct)} 个交易日）")


# ── 主程序（测试）────────────────────────────────────────────────
if __name__ == '__main__':
    # 确保 UTF-8 输出（Windows PowerShell 兼容）
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    TEST_DATE = '20260520'
    TEST_SECTORS = [
        ('801081.SI', '申万半导体'),
        ('801880.SI', '申万汽车'),
    ]

    all_results = {}
    for code, name in TEST_SECTORS:
        print(f"\n{'='*55}")
        print(f"计算 {name}（{code}）板块广度，日期：{TEST_DATE}")
        print('='*55)

        r = sector_breadth(
            index_code=code,
            trade_date=TEST_DATE,
            lookback_days=252,
            max_stocks=80,
            history_sample_every=5
        )
        all_results[code] = r
        print_report(r)
        time.sleep(0.3)

    print("\n\n" + "="*55)
    print("汇总")
    print("="*55)
    for code, r in all_results.items():
        pct = r.get('pct_above_20ma')
        perc = r.get('historical_percentile')
        name = r.get('index_name', code)
        if pct is not None and perc is not None:
            print(f"{name}: 广度 {pct:.1%}, 近12月百分位 {perc:.0f}%")
        else:
            print(f"{name}: 计算失败 - {r.get('error')}")

    print("\n完成。")
