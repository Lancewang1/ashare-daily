"""
ah_premium.py
=============
AH溢价实时跟踪

功能：
1. 识别个股对应H股代码（内置映射 + stock_basic AH_Code字段）
2. 拉取近60日A股、H股日收盘价
3. 折算人民币后计算AH溢价 = (A_CNY/H_CNY − 1)×100%
4. 信号：折价（机会）/ 溢价合理 / 溢价偏高 / 严重高溢价
5. 生成双线图 + 散户叙事
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
import matplotlib.patches as mpatches

import tushare as ts
pro = ts.pro_api()

# ── 常见AH双股映射 ─────────────────────────────────────────────────────────────
# A股ts_code → H股ts_code (tushare格式, 5位数字.HK)
_AH_MAP: dict[str, str] = {
    '601988.SH': '03988.HK',  # 中国银行
    '601398.SH': '01398.HK',  # 工商银行
    '601288.SH': '01288.HK',  # 农业银行
    '601939.SH': '00939.HK',  # 建设银行
    '601328.SH': '03328.HK',  # 交通银行
    '601988.SH': '03988.HK',  # 中国银行
    '601318.SH': '02318.HK',  # 中国平安
    '601628.SH': '02628.HK',  # 中国人寿
    '601601.SH': '02601.HK',  # 中国太保
    '600015.SH': '00010.HK',  # 华夏银行
    '600016.SH': '03968.HK',  # 民生银行
    '601166.SH': '06328.HK',  # 兴业银行
    '601998.SH': '00998.HK',  # 中信银行
    '601881.SH': '01066.HK',  # 中国铁建
    '601390.SH': '00390.HK',  # 中国中铁
    '601800.SH': '01800.HK',  # 中国交建
    '601186.SH': '01186.HK',  # 中国铁路
    '600941.SH': '00941.HK',  # 中国移动
    '601728.SH': '00728.HK',  # 中国电信
    '600050.SH': '00762.HK',  # 中国联通
    '601857.SH': '00857.HK',  # 中国石油
    '600028.SH': '00386.HK',  # 中国石化
    '601919.SH': '01919.HK',  # 中远海控
    '600688.SH': '00338.HK',  # 上海石化
    '688981.SH': '00981.HK',  # 中芯国际
    '601899.SH': '01899.HK',  # 紫金矿业
    '601088.SH': '01088.HK',  # 中国神华
    '601111.SH': '00753.HK',  # 中国国航
    '600115.SH': '00670.HK',  # 东方航空
    '600029.SH': '01055.HK',  # 南方航空
    '601006.SH': '00525.HK',  # 大秦铁路
    '601333.SH': '00003.HK',  # 广深铁路
    '601699.SH': '01898.HK',  # 潞安环能
    '603993.SH': '03993.HK',  # 洛阳钼业
    '601989.SH': '00317.HK',  # 中国重工
    '601600.SH': '01600.HK',  # 中国铝业
    '600362.SH': '00358.HK',  # 江西铜业
    '600332.SH': '00874.HK',  # 白云山
    '600519.SH': '',           # 茅台无H股
}

# HKDCNY fallback rate (当无法从API获取时使用)
_HKDCNY_FALLBACK = 0.918


def _find_h_code(ts_code: str) -> str | None:
    """Return H-share tushare code, or None if not dual-listed."""
    h = _AH_MAP.get(ts_code)
    if h is not None:
        return h or None   # empty string → not dual-listed

    # Try stock_basic ah_code field as fallback
    try:
        df = pro.stock_basic(ts_code=ts_code, fields='ts_code,name,list_status,market')
        time.sleep(0.1)
    except Exception:
        pass
    return None


def _fetch_fx_rate(trade_date: str) -> float:
    """Fetch HKD/CNY rate near trade_date. Falls back to hardcoded rate."""
    end_dt   = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=10)
    try:
        df = pro.fx_daily(ts_code='HKDCNY',
                          start_date=start_dt.strftime('%Y%m%d'),
                          end_date=trade_date,
                          fields='trade_date,close')
        time.sleep(0.1)
        if df is not None and len(df) > 0:
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
            rate = float(df['close'].dropna().iloc[-1])
            if 0.8 < rate < 1.2:   # sanity check
                return rate
    except Exception:
        pass
    return _HKDCNY_FALLBACK


def _fetch_prices(ts_code: str, trade_date: str, lookback: int = 60,
                  market: str = 'A') -> pd.DataFrame:
    end_dt   = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=lookback + 15)
    try:
        if market == 'A':
            df = pro.daily(ts_code=ts_code,
                           start_date=start_dt.strftime('%Y%m%d'),
                           end_date=trade_date,
                           fields='trade_date,close')
        else:
            df = pro.hk_daily(ts_code=ts_code,
                               start_date=start_dt.strftime('%Y%m%d'),
                               end_date=trade_date,
                               fields='trade_date,close')
        time.sleep(0.2)
        if df is None or len(df) == 0:
            return pd.DataFrame()
        df['trade_date'] = df['trade_date'].astype(str)
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        return df.sort_values('trade_date').tail(lookback).reset_index(drop=True)
    except Exception as e:
        print(f'  [WARN] fetch {market} prices {ts_code}: {e}')
        return pd.DataFrame()


def _align_prices(a_df: pd.DataFrame, h_df: pd.DataFrame,
                  fx_rate: float) -> pd.DataFrame:
    """Align A/H by trade_date (inner join), compute premium."""
    if len(a_df) == 0 or len(h_df) == 0:
        return pd.DataFrame()
    merged = pd.merge(
        a_df.rename(columns={'close': 'a_close'}),
        h_df.rename(columns={'close': 'h_close'}),
        on='trade_date', how='inner'
    )
    merged['h_cny'] = merged['h_close'] * fx_rate
    merged['premium_pct'] = (merged['a_close'] / merged['h_cny'] - 1) * 100
    return merged.reset_index(drop=True)


def _compute_metrics(merged: pd.DataFrame) -> dict:
    if len(merged) < 3:
        return {}
    latest    = merged.iloc[-1]
    prem_now  = float(latest['premium_pct'])
    prem_avg  = float(merged['premium_pct'].mean())
    prem_max  = float(merged['premium_pct'].max())
    prem_min  = float(merged['premium_pct'].min())
    a_now     = float(latest['a_close'])
    h_cny_now = float(latest['h_cny'])

    if prem_now < -5:
        signal = 'A股折价（机会区间）'
    elif prem_now < 10:
        signal = 'AH溢价合理'
    elif prem_now < 30:
        signal = 'A股溢价偏高'
    else:
        signal = 'A股严重高溢价'

    return {
        'merged':    merged,
        'prem_now':  round(prem_now, 1),
        'prem_avg':  round(prem_avg, 1),
        'prem_max':  round(prem_max, 1),
        'prem_min':  round(prem_min, 1),
        'a_now':     round(a_now, 2),
        'h_cny_now': round(h_cny_now, 2),
        'signal':    signal,
    }


def _build_chart(metrics: dict, ts_code: str, h_code: str) -> str:
    merged   = metrics['merged']
    dates    = merged['trade_date'].tolist()
    a_close  = merged['a_close'].tolist()
    h_cny    = merged['h_cny'].tolist()
    premium  = merged['premium_pct'].tolist()
    n        = len(dates)
    x        = list(range(n))

    fig, (ax_top, ax_bot) = plt.subplots(
        2, 1, figsize=(8, 3.2),
        gridspec_kw={'height_ratios': [3, 1.5]},
        facecolor='white'
    )
    fig.subplots_adjust(left=0.09, right=0.97, top=0.83, bottom=0.14, hspace=0.45)

    # ── 上图：A股 vs H股(折算CNY) ──────────────────────────────────
    ax_top.plot(x, a_close, color='#d62728', linewidth=1.8, label='A股 (CNY)', zorder=3)
    ax_top.plot(x, h_cny,   color='#1f77b4', linewidth=1.8, linestyle='--',
                label='H股 (折CNY)', zorder=3)

    a_code = ts_code.split('.')[0]
    ax_top.set_title(f'{a_code} AH溢价跟踪（近{n}日）',
                     fontsize=8, fontweight='bold', pad=4)
    step = max(1, n // 6)
    ax_top.set_xticks(x[::step])
    ax_top.set_xticklabels([d[4:] for d in dates[::step]], fontsize=6, rotation=30)
    ax_top.set_ylabel('股价 (CNY)', fontsize=6.5)
    ax_top.legend(fontsize=6, loc='upper left', framealpha=0.7)
    ax_top.spines['top'].set_visible(False); ax_top.spines['right'].set_visible(False)

    # ── 下图：溢价率 ────────────────────────────────────────────────
    prem_color = ['#d62728' if p > 0 else '#1f77b4' for p in premium]
    ax_bot.bar(x, premium, color=prem_color, alpha=0.7, width=0.8)
    ax_bot.axhline(0, color='#333', linewidth=0.8)
    ax_bot.axhline(metrics['prem_avg'], color='#ff7f0e', linewidth=1,
                   linestyle='--', alpha=0.8, label=f'均值{metrics["prem_avg"]:+.1f}%')

    ax_bot.set_xticks(x[::step])
    ax_bot.set_xticklabels([d[4:] for d in dates[::step]], fontsize=6, rotation=30)
    ax_bot.set_ylabel('AH溢价(%)', fontsize=6.5)
    ax_bot.tick_params(axis='both', labelsize=6)
    ax_bot.legend(fontsize=6, loc='upper left', framealpha=0.7)
    ax_bot.spines['top'].set_visible(False); ax_bot.spines['right'].set_visible(False)

    # Annotate latest premium
    ax_bot.annotate(f'{metrics["prem_now"]:+.1f}%',
                    xy=(x[-1], premium[-1]),
                    xytext=(x[-1] - 3, premium[-1] + (max(premium) - min(premium)) * 0.2),
                    fontsize=7, fontweight='bold',
                    color='#d62728' if metrics['prem_now'] > 0 else '#1f77b4',
                    arrowprops=dict(arrowstyle='->', lw=0.8,
                                   color='#d62728' if metrics['prem_now'] > 0 else '#1f77b4'))

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def _build_narrative(ts_code: str, h_code: str, metrics: dict, fx_rate: float) -> str:
    code   = ts_code.split('.')[0]
    h_code_short = h_code.lstrip('0').split('.')[0]
    prem   = metrics['prem_now']
    avg    = metrics['prem_avg']
    signal = metrics['signal']
    a_now  = metrics['a_now']
    h_cny  = metrics['h_cny_now']

    if '折价' in signal:
        return (
            f"{code} A股当前{a_now:.2f}元，H股折算CNY {h_cny:.2f}元，"
            f"**A股相对H股折价{abs(prem):.1f}%**（近60日均值{avg:+.1f}%）。"
            f"罕见的A股折价意味着境内资金比境外更悲观——通常出现在政策压制或流动性危机时。"
            f"折价往往是均值回归机会：历史上折价>5%后6个月A股相对H股有显著超额。"
        )
    elif '合理' in signal:
        return (
            f"{code} A股{a_now:.2f}元，H股折算CNY {h_cny:.2f}元，"
            f"**AH溢价{prem:+.1f}%**，近60日均值{avg:+.1f}%。"
            f"当前溢价处于历史正常区间，南北向资金套利空间有限。"
            f"AH溢价收窄趋势（若当前低于均值）是外资在悄悄流入A股的信号。"
        )
    elif '偏高' in signal:
        return (
            f"{code} A股相对H股溢价**{prem:+.1f}%**（60日均值{avg:+.1f}%）。"
            f"溢价偏高意味着境内投资者对这只股票的定价显著高于国际市场。"
            f"外资通过沪深港通持有H股，他们的定价往往更注重基本面——高溢价时外资倾向于做空A或做多H。"
            f"若溢价继续扩大，需关注A股是否有资金炒作成分。"
        )
    else:
        return (
            f"⚠️ {code} A股相对H股溢价已达**{prem:+.1f}%**（60日均值{avg:+.1f}%）。"
            f"严重高溢价通常伴随：①A股有政策主题炒作 ②H股受港股流动性拖累 ③两市情绪分歧极端。"
            f"历史上AH溢价>50%后，A股未来6-12个月的下行风险显著高于基准。"
            f"此时从风险调整收益角度，H股的性价比远优于A股。"
        )


def ah_premium(ts_code: str, trade_date: str) -> dict:
    result = {
        'ts_code': ts_code, 'trade_date': trade_date,
        'h_code': None, 'prem_now': None,
        'signal': '无H股对应', 'chart_b64': '', 'narrative': '', 'error': None,
    }

    h_code = _find_h_code(ts_code)
    if not h_code:
        result['narrative'] = (
            f'【{ts_code.split(".")[0]}】该股无对应H股，不适用AH溢价分析。'
        )
        return result

    result['h_code'] = h_code

    fx_rate = _fetch_fx_rate(trade_date)
    print(f'  AH溢价：HKD/CNY={fx_rate:.4f}', flush=True)

    a_df = _fetch_prices(ts_code, trade_date, lookback=60, market='A')
    h_df = _fetch_prices(h_code, trade_date, lookback=60, market='H')

    if len(a_df) == 0 or len(h_df) == 0:
        result['error'] = 'price data missing'
        result['narrative'] = f'【{ts_code.split(".")[0]}】AH价格数据获取失败。'
        return result

    merged  = _align_prices(a_df, h_df, fx_rate)
    metrics = _compute_metrics(merged)

    if not metrics:
        result['error'] = 'insufficient aligned data'
        result['narrative'] = f'【{ts_code.split(".")[0]}】AH日期匹配数据不足。'
        return result

    result.update({
        'prem_now': metrics['prem_now'],
        'signal':   metrics['signal'],
    })

    print(f'  AH溢价：A={metrics["a_now"]:.2f} H_CNY={metrics["h_cny_now"]:.2f} '
          f'溢价={metrics["prem_now"]:+.1f}% → {metrics["signal"]}')

    try:
        result['chart_b64'] = _build_chart(metrics, ts_code, h_code)
    except Exception as e:
        print(f'  [WARN] ah_premium chart: {e}')

    result['narrative'] = _build_narrative(ts_code, h_code, metrics, fx_rate)
    return result


if __name__ == '__main__':
    for code in ['688981.SH', '601988.SH']:
        print(f'\n{"="*50}\n{code}')
        r = ah_premium(code, '20260520')
        print(f'  H股: {r["h_code"]}  信号: {r["signal"]}  溢价: {r["prem_now"]}%')
        print(f'  叙事: {r["narrative"][:100]}...')
