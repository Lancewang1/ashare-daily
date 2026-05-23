"""
peer_comparison.py
==================
板块同类股强弱对比

功能：
1. 按行业板块内置同类股票池（半导体/汽车/医药/计算机/电子等）
2. 拉取目标股 + 同类股近30日收盘价，计算各自30日涨跌幅
3. 排名：板块领涨 / 前列 / 随涨 / 相对弱势 / 垫底
4. 生成水平柱状图（目标股高亮）+ 散户叙事
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


# ── 板块同类股票池 ─────────────────────────────────────────────────
_SECTOR_PEERS: dict[str, list[str]] = {
    '半导体': ['688981.SH', '688347.SH', '002371.SZ', '688012.SH', '688008.SH', '688037.SH'],
    '汽车':   ['000880.SZ', '002594.SZ', '601238.SH', '000625.SZ', '600418.SH', '601633.SH'],
    '医药生物': ['600276.SH', '300760.SZ', '688006.SH', '002252.SZ', '300122.SZ', '600196.SH'],
    '计算机': ['688041.SH', '300496.SZ', '688111.SH', '000977.SZ', '300033.SZ', '002415.SZ'],
    '电子':   ['002475.SZ', '000725.SZ', '600745.SH', '002056.SZ', '601127.SH', '688012.SH'],
}

# ── 默认股票名称缓存（减少API调用）─────────────────────────────
_NAME_FALLBACK: dict[str, str] = {
    '688981.SH': '中芯国际', '688347.SH': '华虹半导体A',
    '002371.SZ': '北方华创',  '688012.SH': '中微公司',
    '688008.SH': '澜起科技',  '688037.SH': '芯源微',
    '000880.SZ': '潍柴重机',  '002594.SZ': '比亚迪',
    '601238.SH': '广汽集团',  '000625.SZ': '长安汽车',
    '600418.SH': '江淮汽车',  '601633.SH': '长城汽车',
    '600276.SH': '恒瑞医药',  '300760.SZ': '迈瑞医疗',
    '688006.SH': '百济神州',  '002252.SZ': '上海莱士',
    '300122.SZ': '智飞生物',  '600196.SH': '复星医药',
    '688041.SH': '海光信息',  '300496.SZ': '中科创达',
    '688111.SH': '金山办公',  '000977.SZ': '浪潮信息',
    '300033.SZ': '同花顺',    '002415.SZ': '海康威视',
    '002475.SZ': '立讯精密',  '000725.SZ': '京东方A',
    '600745.SH': '闻泰科技',  '002056.SZ': '横店东磁',
    '601127.SH': '赛力斯',    '688012.SH': '中微公司',
}


def _get_peers(ts_code: str, sector: str) -> list[str]:
    """返回该板块所有同类股（含目标股本身，去重）。"""
    pool = _SECTOR_PEERS.get(sector, [])
    if ts_code not in pool:
        pool = [ts_code] + pool
    # Deduplicate, preserve order
    seen = set()
    result = []
    for c in pool:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


def _fetch_names(peer_list: list[str]) -> dict[str, str]:
    """拉取股票名称，失败时回退到内置映射。"""
    names = dict(_NAME_FALLBACK)  # start with known names
    unknown = [c for c in peer_list if c not in names]
    if not unknown:
        return names
    try:
        df = pro.stock_basic(
            ts_code=','.join(unknown),
            fields='ts_code,name',
        )
        time.sleep(0.2)
        if df is not None and len(df) > 0:
            for _, row in df.iterrows():
                names[row['ts_code']] = row['name']
    except Exception as e:
        print(f'  [WARN] peer_comparison stock_basic: {e}')
    # Fill any still-missing with last 6 chars of code
    for c in peer_list:
        if c not in names:
            names[c] = c.split('.')[0]
    return names


def _fetch_returns(peer_list: list[str], trade_date: str) -> dict[str, float]:
    """
    拉取所有同类股近30日收盘价，计算30日涨跌幅。
    分批6个一组请求（tushare单次多股限制）。
    """
    end_dt   = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=50)   # 多取以确保30个交易日
    start_str = start_dt.strftime('%Y%m%d')

    returns: dict[str, float] = {}
    batch_size = 6
    batches = [peer_list[i:i + batch_size]
               for i in range(0, len(peer_list), batch_size)]

    for batch in batches:
        try:
            df = pro.daily(
                ts_code=','.join(batch),
                start_date=start_str,
                end_date=trade_date,
                fields='trade_date,ts_code,close',
            )
            time.sleep(0.2)
        except Exception as e:
            print(f'  [WARN] peer_comparison daily batch: {e}')
            continue

        if df is None or len(df) == 0:
            continue

        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.dropna(subset=['close'])
        df = df.sort_values('trade_date')

        for code in batch:
            sub = df[df['ts_code'] == code].tail(30).reset_index(drop=True)
            if len(sub) < 5:
                continue
            close_first = float(sub['close'].iloc[0])
            close_last  = float(sub['close'].iloc[-1])
            if close_first > 0:
                returns[code] = round((close_last / close_first - 1) * 100, 2)

    return returns


def _compute_metrics(ts_code: str, peer_list: list[str],
                     returns: dict[str, float],
                     names: dict[str, str], sector: str) -> dict:
    if ts_code not in returns:
        return {}

    # Build sorted list (descending by return)
    peers_data = [
        {'ts_code': c, 'name': names.get(c, c.split('.')[0]),
         'return_30d': returns[c]}
        for c in peer_list if c in returns
    ]
    peers_sorted = sorted(peers_data, key=lambda x: x['return_30d'], reverse=True)

    n = len(peers_sorted)
    rank = next((i + 1 for i, p in enumerate(peers_sorted)
                 if p['ts_code'] == ts_code), n)

    target_return = returns[ts_code]

    # Signal based on rank
    if rank == 1:
        signal = '板块领涨 (Alpha)'
    elif rank <= 3:
        signal = '板块前列'
    elif rank >= n:
        signal = '板块垫底'
    elif rank >= n - 1:
        signal = '相对弱势'
    else:
        signal = '随板块跟涨 (Beta)'

    return {
        'peers_sorted': peers_sorted,
        'rank': rank,
        'n_peers': n,
        'return_30d': target_return,
        'signal': signal,
        'sector': sector,
    }


def _build_chart(metrics: dict, ts_code: str) -> str:
    peers  = metrics['peers_sorted']
    sector = metrics['sector']
    n      = len(peers)

    names   = [p['name'] for p in peers]
    returns = [p['return_30d'] for p in peers]
    codes   = [p['ts_code'] for p in peers]

    # Colors: target stock = red (positive) or deep blue (negative); peers = gray shades
    bar_colors = []
    for c, r in zip(codes, returns):
        if c == ts_code:
            bar_colors.append('#d62728' if r >= 0 else '#1f77b4')
        else:
            bar_colors.append('#aaaaaa' if r >= 0 else '#c9c9c9')

    fig_h = max(2.8, 0.38 * n + 0.8)
    fig, ax = plt.subplots(figsize=(8, fig_h), facecolor='white')
    fig.subplots_adjust(left=0.22, right=0.97, top=0.88, bottom=0.12)

    y_pos = list(range(n - 1, -1, -1))   # top = highest return

    bars = ax.barh(y_pos, returns, color=bar_colors, alpha=0.85,
                   height=0.65, edgecolor='white', linewidth=0.5)

    # Labels: name + return%
    for i, (yp, ret, name, code) in enumerate(zip(y_pos, returns, names, codes)):
        label = f'{name}  {ret:+.1f}%'
        x_offset = 0.3 if ret >= 0 else -0.3
        ha = 'left' if ret >= 0 else 'right'
        ax.text(ret + x_offset, yp, label,
                va='center', ha=ha, fontsize=6.5,
                fontweight='bold' if code == ts_code else 'normal',
                color='#d62728' if code == ts_code else '#444444')

    ax.axvline(0, color='#333333', linewidth=0.8, zorder=5)
    ax.set_yticks([])
    ax.set_xlabel('近30日涨跌幅 (%)', fontsize=7)
    ax.tick_params(axis='x', labelsize=6.5)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_visible(False)

    code_disp = ts_code.split('.')[0]
    ax.set_title(f'{sector}板块近30日强弱对比（{code_disp} 红色高亮）',
                 fontsize=8.5, fontweight='bold', pad=5)

    # Rank annotation
    ax.text(0.99, 0.01,
            f'排名 {metrics["rank"]}/{metrics["n_peers"]}',
            transform=ax.transAxes, ha='right', va='bottom',
            fontsize=7, color='#666666')

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def _build_narrative(ts_code: str, metrics: dict, names: dict) -> str:
    code    = ts_code.split('.')[0]
    name    = names.get(ts_code, code)
    rank    = metrics['rank']
    n       = metrics['n_peers']
    ret30   = metrics['return_30d']
    signal  = metrics['signal']
    sector  = metrics['sector']
    peers   = metrics['peers_sorted']

    ret_str = f'{ret30:+.1f}%'

    # Best and worst in sector for context
    best  = peers[0]
    worst = peers[-1]

    if signal == '板块领涨 (Alpha)':
        return (
            f"**{name}**近30日涨幅{ret_str}，在{sector}板块{n}只个股中**排名第1（领涨全板块）**。"
            f"跑赢板块的收益被称为「Alpha」——说明{name}有超越行业的自身驱动力，"
            f"可能来自业绩超预期、订单催化或资金定向流入。"
            f"这是最强的相对强势信号，但领涨后需警惕获利了结压力。"
        )
    elif signal == '板块前列':
        return (
            f"**{name}**近30日涨幅{ret_str}，在{sector}板块{n}只个股中**排名第{rank}（板块前列）**。"
            f"明显强于板块整体，具备一定Alpha特征，但并非绝对领涨。"
            f"板块最强：{best['name']}（{best['return_30d']:+.1f}%）。"
            f"相对强势通常是机构重点关注的标志，值得深入研究。"
        )
    elif signal == '板块垫底':
        return (
            f"**{name}**近30日涨幅{ret_str}，在{sector}板块{n}只个股中**排名最末（垫底）**。"
            f"**显著弱于同板块个股**，需要重点排查原因："
            f"是业绩预期下调、公司特有利空，还是资金出逃？"
            f"板块最强：{best['name']}（{best['return_30d']:+.1f}%）。"
            f"相对弱势不等于马上反弹，低估值陷阱（Value Trap）值得警惕。"
        )
    elif signal == '相对弱势':
        return (
            f"**{name}**近30日涨幅{ret_str}，在{sector}板块{n}只个股中**排名第{rank}（相对弱势）**。"
            f"跑输大多数同板块个股，说明有个股特有的阻力因素。"
            f"板块最强：{best['name']}（{best['return_30d']:+.1f}%），"
            f"最弱：{worst['name']}（{worst['return_30d']:+.1f}%）。"
            f"建议对比同类股基本面，找出相对弱势的原因后再决策。"
        )
    else:  # Beta
        return (
            f"**{name}**近30日涨幅{ret_str}，在{sector}板块{n}只个股中**排名第{rank}（随板块）**。"
            f"涨跌幅与板块整体接近，主要是「Beta」驱动——板块涨它跟涨，板块跌它跟跌。"
            f"板块最强：{best['name']}（{best['return_30d']:+.1f}%），"
            f"最弱：{worst['name']}（{worst['return_30d']:+.1f}%）。"
            f"若看好{sector}板块方向，优选Alpha股（板块领涨者）会更有效率。"
        )


def peer_comparison(ts_code: str, trade_date: str, sector: str = '半导体') -> dict:
    """
    板块同类股强弱对比。

    Parameters
    ----------
    ts_code    : tushare 股票代码，如 '688981.SH'
    trade_date : 报告日期，格式 'YYYYMMDD'
    sector     : 所属板块名称（中文），默认 '半导体'

    Returns
    -------
    dict with keys:
        signal        : str
        chart_b64     : str (base64 PNG)
        narrative     : str
        rank          : int
        n_peers       : int
        return_30d    : float
        peers_sorted  : list[dict]  — [{name, return_30d}, ...]
        error         : str | None
    """
    result = {
        'ts_code': ts_code,
        'trade_date': trade_date,
        'signal': '无数据',
        'chart_b64': '',
        'narrative': '',
        'rank': 0,
        'n_peers': 0,
        'return_30d': 0.0,
        'peers_sorted': [],
        'error': None,
    }

    peer_list = _get_peers(ts_code, sector)

    # Check if sector is unknown (fallback to target stock only)
    if sector not in _SECTOR_PEERS:
        note = f'板块「{sector}」无内置数据，仅分析目标股自身。'
        print(f'  [INFO] {note}')
        result['narrative'] = f'【{ts_code.split(".")[0]}】{note}'
        # Still compute the return for the target stock alone
        peer_list = [ts_code]

    names   = _fetch_names(peer_list)
    returns = _fetch_returns(peer_list, trade_date)

    if ts_code not in returns:
        result['error'] = f'No price data for target {ts_code}'
        result['narrative'] = f'【{ts_code.split(".")[0]}】无法获取行情数据。'
        return result

    if len(returns) < 2 and sector in _SECTOR_PEERS:
        result['error'] = f'Insufficient peer data (only {len(returns)} stocks returned)'

    metrics = _compute_metrics(ts_code, peer_list, returns, names, sector)
    if not metrics:
        result['error'] = 'Metric computation failed'
        return result

    result.update({
        'signal': metrics['signal'],
        'rank': metrics['rank'],
        'n_peers': metrics['n_peers'],
        'return_30d': metrics['return_30d'],
        'peers_sorted': [{'name': p['name'], 'return_30d': p['return_30d']}
                         for p in metrics['peers_sorted']],
    })

    print(f'  同类对比：{sector}板块 排名{metrics["rank"]}/{metrics["n_peers"]} '
          f'30日{metrics["return_30d"]:+.1f}% → {metrics["signal"]}')

    try:
        result['chart_b64'] = _build_chart(metrics, ts_code)
    except Exception as e:
        print(f'  [WARN] peer_comparison chart: {e}')

    result['narrative'] = _build_narrative(ts_code, metrics, names)
    return result


# ── 板块推断辅助（供 inject_signals 或外部调用）──────────────────
_CODE_TO_SECTOR: dict[str, str] = {
    # 半导体
    '688981': '半导体', '688347': '半导体', '002371': '半导体',
    '688012': '半导体', '688008': '半导体', '688037': '半导体',
    # 汽车
    '000880': '汽车', '002594': '汽车', '601238': '汽车',
    '000625': '汽车', '600418': '汽车', '601633': '汽车',
    # 医药
    '600276': '医药生物', '300760': '医药生物', '688006': '医药生物',
    '002252': '医药生物', '300122': '医药生物', '600196': '医药生物',
    # 计算机
    '688041': '计算机', '300496': '计算机', '688111': '计算机',
    '000977': '计算机', '300033': '计算机', '002415': '计算机',
    # 电子
    '002475': '电子', '000725': '电子', '600745': '电子',
    '002056': '电子', '601127': '电子',
}


def infer_sector(ts_code: str) -> str:
    """根据股票代码猜测所属板块，未知时返回 '综合'。"""
    code6 = ts_code.split('.')[0]
    return _CODE_TO_SECTOR.get(code6, '综合')


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    TEST_DATE = '20260520'
    TEST_CASES = [
        ('688981.SH', '半导体'),
        ('000880.SZ', '汽车'),
    ]

    for code, sec in TEST_CASES:
        print(f'\n{"=" * 55}\n{code} | 板块：{sec}')
        r = peer_comparison(code, TEST_DATE, sector=sec)
        if r['error']:
            print(f'  错误: {r["error"]}')
        else:
            print(f'  信号      : {r["signal"]}')
            print(f'  排名      : {r["rank"]}/{r["n_peers"]}')
            print(f'  30日涨跌  : {r["return_30d"]:+.2f}%')
            print(f'  图表      : {"已生成" if r["chart_b64"] else "无"}')
            print(f'  板块成员  :')
            for p in r['peers_sorted']:
                marker = '◀' if p['name'] in (code.split('.')[0],) else ' '
                print(f'    {marker} {p["name"]:10s}  {p["return_30d"]:+.1f}%')
            print(f'  叙事:\n  {r["narrative"]}')
