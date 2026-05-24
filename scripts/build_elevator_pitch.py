"""
build_elevator_pitch.py
=======================
Generate a short "elevator pitch" HTML from an existing long report.

Usage:
    python build_elevator_pitch.py [ts_code] [trade_date] [stock_name]
    python build_elevator_pitch.py 688981.SH 20260520 中芯国际

Sections:
  1. 量化指标  — K-line chart + forward return table (self-history backtest)
  2. 资金博弈  — Market stats + capital radar + peer comparison + narrative
  3. 基本面催化剂 — Core focus bullets + upstream/downstream signals
"""

from __future__ import annotations
import io, base64, time, sys, re
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
from scipy.stats import percentileofscore

import tushare as ts
pro = ts.pro_api()

sys.stdout.reconfigure(encoding='utf-8')

_SCRIPTS = Path(__file__).resolve().parent
_STOCKS  = _SCRIPTS.parent / 'stocks'


# ── Chart extraction from long HTML ──────────────────────────────────────────

def _extract_b64(html: str, marker: str, max_scan: int = 200_000) -> str:
    pos = html.find(marker)
    if pos == -1:
        return ''
    img_pos = html.find('data:image/png;base64,', pos, pos + max_scan)
    if img_pos == -1:
        return ''
    b64_start = img_pos + 22
    b64_end   = html.find('"', b64_start)
    if b64_end == -1:
        return ''
    b64 = html[b64_start:b64_end]
    return b64 if len(b64) > 5000 else ''


def extract_all_charts(long_html: str) -> dict:
    return {
        'kline':        _extract_b64(long_html, '<span class="card-title">关键价位'),
        'factor_radar': _extract_b64(long_html, '<span class="card-title">量化因子百分位'),
        'capital_radar':_extract_b64(long_html, '<span class="card-title">资金博弈雷达'),
        'peer':         _extract_b64(long_html, '半导体板块强弱对比'),
        'beta':         _extract_b64(long_html, '<div class="tb-head">大盘 BETA 与板块环境'),
    }


# ── Text extraction from long HTML ───────────────────────────────────────────

def _strip(html: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', html)).strip()


def extract_core_focus(long_html: str) -> dict:
    """Extract 市场核心关注 as structured bullets."""
    ch1_s = long_html.find('<section class="chapter" id="ch1"')
    ch2_s = long_html.find('<section class="chapter" id="ch2"')
    if ch1_s == -1:
        return {}
    ch1 = long_html[ch1_s:ch2_s]

    # Executive summary (first <p>)
    pm = re.search(r'<p>(.*?)</p>', ch1, re.DOTALL)
    summary = _strip(pm.group(1))[:280] if pm else ''

    # Bull / bear <li> items
    li_raw = re.findall(r'<li>(.*?)</li>', ch1, re.DOTALL)
    items  = [_strip(x) for x in li_raw]
    bull   = [x for x in items if any(c in x for c in ('①', '②', '③'))][:3]
    bear   = next((x for x in items if any(c in x for c in ('盈利', '估值', '风险', '压力'))), '')[:220]

    return {'summary': summary, 'bull': bull, 'bear': bear}


def extract_leadlag(long_html: str) -> list[dict]:
    """Extract upstream/downstream signals from ch5 财务景气传导."""
    ch5_s = long_html.find('<section class="chapter" id="ch5"')
    pos   = long_html.find('<div class="tb-head">财务景气传导</div>', ch5_s)
    if pos == -1:
        return []
    next_h = long_html.find('<div class="tb-head">', pos + 50)
    section = long_html[pos: next_h if next_h != -1 else pos + 3000]

    signals = []

    # Parse each company block (split on 📙 📗)
    blocks = re.split(r'📙|📗', section)
    for block in blocks[1:]:
        name_m   = re.search(r'([一-鿿]+（\d{6}）)', block)
        # Try both <strong>...</strong> and **...** formats
        q1_m     = re.search(r'营收同比<strong>(.*?)</strong>', block) or \
                   re.search(r'营收同比\*\*(.*?)\*\*', block)
        price_m  = re.search(r'股价近30天<strong>(.*?)</strong>', block) or \
                   re.search(r'股价近30天\*\*(.*?)\*\*', block)
        concl_m  = re.search(r'结论：(.*?)(?:</strong>|</p>|$)', block, re.DOTALL)
        dir_m    = re.search(r'读数：([一-鿿A-Za-z\s]+)', block)

        name     = name_m.group(1) if name_m else '?'
        q1       = q1_m.group(1) if q1_m else ''
        price30  = price_m.group(1) if price_m else ''
        concl    = _strip(concl_m.group(1))[:150] if concl_m else ''
        direction= dir_m.group(1).strip() if dir_m else ''

        green    = '正面' in direction or '正向' in direction or '扩产' in direction
        color    = '#2ca02c' if green else '#e8a500'
        bg       = '#f0faf0' if green else '#fffbf0'

        # Infer supply-chain role from block text
        if any(x in block for x in ('上游', '设备', '刻蚀', '光刻', 'CVD', '北方')):
            role = '上游供应商'
        elif any(x in block for x in ('下游', '消费电子', '终端', '客户', '立讯')):
            role = '下游客户'
        else:
            role = ''

        signals.append({
            'name': name, 'q1': q1, 'price30': price30,
            'direction': direction, 'conclusion': concl,
            'role': role, 'color': color, 'bg': bg,
        })
    return signals


def extract_capital_narrative(long_html: str) -> str:
    """Overall capital summary: pull from 量能节奏 section (volume/flow narrative)."""
    ch3_s = long_html.find('<section class="chapter" id="ch3"')
    ch4_s = long_html.find('<section class="chapter" id="ch4"')
    if ch3_s == -1:
        return ''
    pos = long_html.find('>量能节奏<', ch3_s, ch4_s)
    if pos == -1:
        return ''
    pm = re.search(r'<p[^>]*>(.*?)</p>', long_html[pos:pos+3000], re.DOTALL)
    if not pm:
        return ''
    text = _strip(pm.group(1))
    parts = re.split(r'(?<=[。！？])', text)
    return ''.join(parts[:3]).strip()[:350]


def extract_quant_narratives(long_html: str) -> dict:
    """Extract ch2 narrative texts: tagline, model rationale, stage assessment."""
    ch2_s = long_html.find('<section class="chapter" id="ch2"')
    ch3_s = long_html.find('<section class="chapter" id="ch3"')
    if ch2_s == -1:
        return {}
    ch2 = long_html[ch2_s:ch3_s]

    def _section_para(head_text: str, max_sents: int = 2) -> str:
        pos = ch2.find(f'>{head_text}<')
        if pos == -1:
            return ''
        pm = re.search(r'<p[^>]*>(.*?)</p>', ch2[pos:pos+3000], re.DOTALL)
        if not pm:
            return ''
        text = _strip(pm.group(1))
        parts = re.split(r'(?<=[。！？])', text)
        return ''.join(parts[:max_sents]).strip()

    tagline  = _section_para('一句话定调', 1)
    rationale = _section_para('量化模型为何选中', 3)
    stage    = _section_para('当前阶段定性', 2)
    return {'tagline': tagline, 'rationale': rationale, 'stage': stage}


def extract_market_narrative(long_html: str) -> str:
    """Extract beta/market environment paragraph from ch4."""
    pos = long_html.find('<div class="tb-head">大盘 BETA 与板块环境</div>')
    if pos == -1:
        return ''
    pm = re.search(r'<p[^>]*>(.*?)</p>', long_html[pos:pos+4000], re.DOTALL)
    if not pm:
        return ''
    text = _strip(pm.group(1))
    # First 3 sentences
    parts = re.split(r'(?<=[。！？])', text)
    return ''.join(parts[:3]).strip()[:400]


def extract_radar_narrative(long_html: str) -> str:
    """Extract 资金博弈雷达 card-body narrative."""
    title_pos = long_html.find('<span class="card-title">资金博弈雷达')
    if title_pos == -1:
        return ''
    cb_s = long_html.find('<div class="card-body"', title_pos)
    if cb_s == -1:
        return ''
    inner = long_html.index('>', cb_s) + 1
    cb_e  = long_html.find('</div>', inner)
    text = _strip(long_html[inner:cb_e])
    # Clean up "688981 7维" → "7维" (strip leading stock code if present)
    text = re.sub(r'^\d{6}\s*', '', text)
    return text[:400]


# ── Forward return table (self-history) ──────────────────────────────────────

def _rsi(close: pd.Series, p: int = 14) -> pd.Series:
    d   = close.diff()
    g   = d.clip(lower=0).ewm(alpha=1/p, min_periods=p).mean()
    l   = (-d).clip(lower=0).ewm(alpha=1/p, min_periods=p).mean()
    return 100 - (100 / (1 + g / l.replace(0, np.nan)))


def _roll_pct(s: pd.Series, win: int = 252) -> pd.Series:
    """Rolling historical percentile rank within a window."""
    return s.rolling(win, min_periods=80).apply(
        lambda x: float(percentileofscore(x[~np.isnan(x)], x[-1], kind='rank'))
        if int((~np.isnan(x)).sum()) >= 30 else np.nan,
        raw=True
    )


def compute_fwd_return_table(ts_code: str, trade_date: str) -> pd.DataFrame | None:
    """
    Self-history signal backtest:
      composite = avg(5d-momentum-pct, 20d-momentum-pct, RSI-pct, turnover-pct)
    Returns forward return stats when composite is in top 20/5/1%.
    """
    end_dt   = datetime.strptime(trade_date, '%Y%m%d')
    start_dt = end_dt - timedelta(days=900)  # ~3yr = enough for rolling 252d

    try:
        daily = pro.daily(
            ts_code=ts_code,
            start_date=start_dt.strftime('%Y%m%d'),
            end_date=trade_date,
            fields='trade_date,close,pct_chg,vol',
        )
        time.sleep(0.3)
        basic = pro.daily_basic(
            ts_code=ts_code,
            start_date=start_dt.strftime('%Y%m%d'),
            end_date=trade_date,
            fields='trade_date,turnover_rate_f',
        )
        time.sleep(0.3)
    except Exception as e:
        print(f'  [WARN] fwd_table API error: {e}')
        return None

    if daily is None or len(daily) < 300:
        return None

    df = (daily.merge(basic, on='trade_date', how='left')
          .sort_values('trade_date').reset_index(drop=True))
    for col in ('close', 'pct_chg', 'vol', 'turnover_rate_f'):
        df[col] = pd.to_numeric(df[col], errors='coerce')

    close = df['close']
    ret5  = close.pct_change(5) * 100
    ret20 = close.pct_change(20) * 100
    rsi14 = _rsi(close, 14)
    tr    = df['turnover_rate_f']

    print('  [fwd_table] computing rolling percentiles...')
    p_r5  = _roll_pct(ret5)
    p_r20 = _roll_pct(ret20)
    p_rsi = _roll_pct(rsi14)
    p_tr  = _roll_pct(tr)

    composite = (p_r5 + p_r20 + p_rsi + p_tr) / 4

    # Forward returns (shift=-N: today's signal → N-day future return)
    fwd1  = close.pct_change(1).shift(-1)  * 100
    fwd5  = close.pct_change(5).shift(-5)  * 100
    fwd30 = close.pct_change(30).shift(-30)* 100

    # Today's composite (last non-NaN)
    current_pct = float(composite.dropna().iloc[-1]) if composite.dropna().size else 50.0
    print(f'    current composite: {current_pct:.1f}%ile')

    rows = []
    for thr, label in [(80, 'Top 20%'), (95, 'Top 5%'), (99, 'Top 1%')]:
        mask = composite.ge(thr) & fwd1.notna() & fwd5.notna() & fwd30.notna()
        n = int(mask.sum())
        if n < 4:
            continue
        rows.append({
            'label': label, 'n': n,
            'avg1':  float(fwd1[mask].mean()),
            'avg5':  float(fwd5[mask].mean()),
            'avg30': float(fwd30[mask].mean()),
            'hit30': float((fwd30[mask] > 0).mean() * 100),
        })
        print(f'    {label}: n={n}  avg1={rows[-1]["avg1"]:+.2f}%  '
              f'avg30={rows[-1]["avg30"]:+.2f}%  hit30={rows[-1]["hit30"]:.0f}%')

    df = pd.DataFrame(rows) if rows else None
    return df, current_pct


def _quant_summary_text(fwd_df: pd.DataFrame | None, current_pct: float) -> str:
    """
    Build a 2-sentence narrative describing today's signal strength
    and what history says about subsequent returns.
    """
    if fwd_df is None or fwd_df.empty:
        return ''

    # Determine which tier today falls in
    if current_pct >= 99:
        tier_label, tier_desc = 'Top 1%', '历史最强信号区间'
    elif current_pct >= 95:
        tier_label, tier_desc = 'Top 5%', '强信号区间'
    elif current_pct >= 80:
        tier_label, tier_desc = 'Top 20%', '较强信号区间'
    else:
        tier_label, tier_desc = '', ''

    rows = {r['label']: r for _, r in fwd_df.iterrows()}

    parts = []

    # Sentence 1: today's signal position (bilingual: plain explanation of %ile)
    pct_int = int(round(current_pct))
    plain_note = f'过去1年中，只有约{100-pct_int}%的时间比今天更强'
    parts.append(
        f'综合因子评分当前 <strong>{pct_int}分</strong>（满分100，{plain_note}），'
        f'处于自身历史 <strong>{tier_label}</strong>（{tier_desc}）。'
    )

    # Sentence 2: historical outcome stats — plain language first
    best_tiers = [t for t in ('Top 1%', 'Top 5%', 'Top 20%') if t in rows]
    if best_tiers:
        r  = rows[best_tiers[0]]
        s2 = (
            f'过去3年，每当出现类似强度的信号（共 <strong>{int(r["n"])} 次</strong>），'
            f'后续30天平均上涨 <strong style="color:#2ca02c">{r["avg30"]:+.1f}%</strong>，'
            f'其中 <strong style="color:#2ca02c">{r["hit30"]:.0f}%</strong> 的时候是上涨的。'
        )
        if len(best_tiers) > 1:
            r2 = rows[best_tiers[1]]
            s2 += (
                f'&nbsp;信号更弱一档（{r2["label"]}，{int(r2["n"])}次）'
                f'后续30日均涨 {r2["avg30"]:+.1f}%，胜率{r2["hit30"]:.0f}%。'
            )
        parts.append(s2)

    return ''.join(parts)


# ── HTML builders ─────────────────────────────────────────────────────────────

_CSS = '''
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
       background: #eef1f7; color: #222; font-size: 13.5px; }
.page { max-width: 1080px; margin: 0 auto; padding-bottom: 24px; }

/* Hero */
.hero { background: linear-gradient(135deg, #0d1322 0%, #1a2d63 100%);
        color: #fff; padding: 22px 28px 18px; }
.hero h1 { font-size: 22px; font-weight: 900; letter-spacing: -.01em; }
.hero .meta { font-size: 11.5px; color: #8ea8d8; margin-top: 4px; }
.hero .tagline { font-size: 13px; color: #c8d8f0; margin-top: 10px;
                 line-height: 1.6; max-width: 820px; }
.verdict { display: inline-block; padding: 3px 12px; border-radius: 4px;
           font-size: 12px; font-weight: 800; margin-left: 10px;
           vertical-align: middle; }
.v-bull  { background: #2ca02c; color: #fff; }
.v-neut  { background: #e8a500; color: #fff; }
.v-bear  { background: #d62728; color: #fff; }

/* Sections */
.sec { background: #fff; margin: 12px 12px 0; border-radius: 12px;
       padding: 18px 20px;
       box-shadow: 0 2px 10px rgba(0,0,0,.06); }
.sec-title { font-size: 14px; font-weight: 800; color: #1a1a2e;
             border-left: 4px solid #1a6fc4; padding-left: 10px;
             margin-bottom: 14px; }
.sec-sub { font-size: 11.5px; color: #888; font-weight: 400;
           margin-left: 6px; }

/* Grids */
.g2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.g3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }
.g-60-40 { display: grid; grid-template-columns: 60% 40%; gap: 14px; }
.g-45-55 { display: grid; grid-template-columns: 45% 55%; gap: 14px; }
/* Prevent grid children from overflowing their track */
.g2 > *, .g3 > *, .g-60-40 > *, .g-45-55 > * { min-width: 0; overflow: hidden; }

/* Global image rule */
img { max-width: 100%; height: auto; display: block; }

/* Chart containers */
.chart-box img { border-radius: 8px;
                 box-shadow: 0 1px 6px rgba(0,0,0,.08); }

/* War-record emoji cards */
.fwd-cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px;
             margin-top: 4px; }
.fwd-card { border-radius: 10px; padding: 12px 14px; }
.fc-header { display: flex; align-items: center; gap: 6px; margin-bottom: 10px; }
.fc-emoji  { font-size: 20px; line-height: 1; }
.fc-name   { font-size: 14px; font-weight: 900; }
.fc-tier   { font-size: 10.5px; font-weight: 700; margin-left: auto; }
.fc-stats  { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px; }
.fc-stat   { text-align: center; }
.fc-stat-val { font-size: 16px; font-weight: 900; line-height: 1.2; }
.fc-stat-lbl { font-size: 10px; color: #888; margin-top: 2px; }
.pos  { color: #2ca02c; font-weight: 700; }
.neg  { color: #d62728; font-weight: 700; }
.fwd-note { font-size: 11px; color: #aaa; margin-top: 6px; text-align: right; }

/* Market stat tiles */
.mkt-tiles { display: grid; grid-template-columns: repeat(3, 1fr);
             gap: 8px; margin-bottom: 14px; }
.mkt-tile { background: #f5f8ff; border: 1px solid #dde; border-radius: 8px;
            padding: 10px 12px; text-align: center; }
.mkt-tile .label { font-size: 10.5px; color: #888; }
.mkt-tile .val   { font-size: 20px; font-weight: 900; margin: 3px 0;
                   line-height: 1.1; }
.mkt-tile .sub   { font-size: 10.5px; color: #aaa; }
.green { color: #2ca02c; }
.red   { color: #d62728; }
.amber { color: #e8a500; }

/* Narrative blocks */
.narr { font-size: 13px; line-height: 1.8; color: #333; margin-top: 12px;
        padding: 11px 15px; background: #f5f8ff;
        border-left: 3px solid #1a6fc4; border-radius: 0 6px 6px 0; }
.narr-sm { font-size: 12px; line-height: 1.7; color: #555; margin-top: 8px;
           padding: 8px 12px; background: #f9fafc;
           border-left: 3px solid #bcd; border-radius: 0 5px 5px 0; }
.quant-view { background: #f0f4ff; border-radius: 8px; padding: 12px 16px;
              margin-bottom: 14px; }
.quant-view .tagline { font-size: 14px; font-weight: 800; color: #1a2d63;
                       margin-bottom: 6px; }
.quant-view .detail  { font-size: 12.5px; color: #444; line-height: 1.75; }

/* Core focus */
.exec-sum { font-size: 13px; font-weight: 700; color: #1a2d63;
            line-height: 1.6; margin-bottom: 10px; }
.bull-list, .bear-list { list-style: none; padding: 0; }
.bull-list li { padding: 4px 0 4px 18px; border-bottom: 1px solid #f0f3f8;
                font-size: 12.5px; line-height: 1.6; position: relative;
                color: #333; }
.bull-list li::before { content: "▲"; color: #2ca02c; position: absolute;
                        left: 0; font-size: 10px; top: 6px; }
.bear-item { padding: 8px 12px; background: #fff5f5; border-radius: 6px;
             border-left: 3px solid #d62728; font-size: 12.5px;
             line-height: 1.6; margin-top: 10px; color: #4a1818; }
.bear-item::before { content: "▼ 风险  "; color: #d62728; font-weight: 700; }

/* Lead-lag signal cards */
.ll-cards { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.ll-card { border-radius: 8px; padding: 12px 14px; border: 1px solid #e8e8e8; }
.ll-card .co { font-size: 13px; font-weight: 800; color: #1a1a2e; }
.ll-card .role { font-size: 11px; color: #888; margin-bottom: 6px; }
.ll-card .q1  { font-size: 20px; font-weight: 900; margin: 4px 0; }
.ll-card .dir-badge { display: inline-block; padding: 2px 10px;
                      border-radius: 20px; font-size: 11px; font-weight: 700;
                      color: #fff; margin-bottom: 6px; }
.ll-card .concl { font-size: 11.5px; color: #555; line-height: 1.5; }
.divider { border: none; border-top: 1px solid #eef; margin: 14px 0; }

/* Verdict card (首屏结论) */
.verdict-card { background: #fff; margin: 10px 12px 0; border-radius: 12px;
                padding: 14px 18px; box-shadow: 0 2px 10px rgba(0,0,0,.06);
                border-top: 3px solid #1a6fc4; }
.vc-header { display: flex; align-items: center; gap: 10px; margin-bottom: 10px;
             flex-wrap: wrap; }
.vc-title  { font-size: 13px; font-weight: 800; color: #1a1a2e; }
.vc-date   { font-size: 11px; color: #bbb; margin-left: auto; }
.vc-rows   { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.vc-row    { font-size: 12.5px; line-height: 1.55; padding: 7px 10px;
             border-radius: 6px; display: flex; gap: 8px; align-items: flex-start; }
.vc-bull   { background: #f0faf0; color: #1a3a1a; }
.vc-bear   { background: #fff5f5; color: #4a1818; }
.vc-icon   { flex-shrink: 0; font-size: 10px; margin-top: 3px; font-weight: 900; }
.vc-bull .vc-icon { color: #2ca02c; }
.vc-bear .vc-icon { color: #d62728; }

/* Bilingual gloss notes */
.gloss-note { font-size: 11px; color: #999; display: block;
              margin-top: 5px; line-height: 1.5; }

/* Responsive — single column on mobile */
@media (max-width: 640px) {
  .g2, .g3, .g-60-40, .g-45-55, .ll-cards, .vc-rows {
    grid-template-columns: 1fr !important;
  }
  .fwd-cards { grid-template-columns: 1fr !important; }
  .fc-stats  { grid-template-columns: repeat(3, 1fr); }
  .hero { padding: 16px 16px 14px; }
  .hero h1 { font-size: 18px; }
  .sec, .verdict-card { margin: 8px 8px 0; padding: 13px 13px; }
  .mkt-tile .val { font-size: 17px; }
  img { max-height: 260px; object-fit: contain; }
  .ll-card .q1 { font-size: 17px; }
  .vc-date { display: none; }
}
</style>'''


def _verdict_card_html(core_focus: dict, current_pct: float, dt: str,
                       fwd_df=None, stock_name: str = '') -> str:
    """首屏 AI 结论卡：看多理由 + 主要风险，2列排布 + 白话总结。"""
    bull = core_focus.get('bull', [])
    bear = core_focus.get('bear', '')

    if current_pct >= 95:
        badge_cls, badge_txt = 'v-bull', '买入信号'
    elif current_pct >= 70:
        badge_cls, badge_txt = 'v-neut', '关注观察'
    else:
        badge_cls, badge_txt = 'v-bear', '谨慎观望'

    bull_rows = ''.join(
        f'<div class="vc-row vc-bull">'
        f'<span class="vc-icon">▲</span><span>{b}</span></div>'
        for b in bull[:3]
    )
    bear_row = (
        f'<div class="vc-row vc-bear">'
        f'<span class="vc-icon">▼</span><span>{bear}</span></div>'
    ) if bear else ''

    simple = _simple_say(current_pct, stock_name, fwd_df)
    simple_block = (
        f'<p style="font-size:12.5px;color:#1a3463;background:#eef3fb;'
        f'border-radius:6px;padding:9px 13px;margin-top:10px;line-height:1.7">'
        f'{simple}</p>'
    )

    return (
        f'<div class="verdict-card">'
        f'<div class="vc-header">'
        f'<span class="vc-title">🤖 AI 综合判断</span>'
        f'<span class="verdict {badge_cls}">{badge_txt}</span>'
        f'<span class="vc-date">数据截至 {dt}</span>'
        f'</div>'
        f'<div class="vc-rows">{bull_rows}{bear_row}</div>'
        f'{simple_block}'
        f'</div>'
    )


def _war_record_html(df: pd.DataFrame | None) -> str:
    """Gaming-style war-record cards: 🔥顶级 / 🚀很强 / ✅较强 with full data."""
    if df is None or df.empty:
        return '<p style="color:#aaa;font-size:12px;padding:12px 0">信号历史数据不足</p>'

    tier_cfg = {
        'Top 1%':  ('🔥', '顶级', '#fff8f0', '#c45200', '#e65100'),
        'Top 5%':  ('🚀', '很强', '#f0faf0', '#1b5e20', '#2ca02c'),
        'Top 20%': ('✅', '较强', '#f0f4ff', '#0d47a1', '#1a6fc4'),
    }

    cards = ''
    for _, r in df.iterrows():
        emoji, name, bg, dark, mid = tier_cfg.get(
            r['label'], ('·', r['label'], '#f5f5f5', '#333', '#666'))
        avg30_col = '#2ca02c' if r['avg30'] > 0 else '#d62728'
        hit30_col = '#2ca02c' if r['hit30'] >= 55 else '#d62728'
        cards += (
            f'<div class="fwd-card" style="background:{bg};border:1.5px solid {mid}40">'
            f'<div class="fc-header">'
            f'<span class="fc-emoji">{emoji}</span>'
            f'<span class="fc-name" style="color:{dark}">{name}</span>'
            f'<span class="fc-tier" style="color:{mid}">{r["label"]}</span>'
            f'</div>'
            f'<div class="fc-stats">'
            f'<div class="fc-stat"><div class="fc-stat-val" style="color:{dark}">{int(r["n"])}次</div>'
            f'<div class="fc-stat-lbl">历史出现</div></div>'
            f'<div class="fc-stat"><div class="fc-stat-val" style="color:{avg30_col}">{r["avg30"]:+.1f}%</div>'
            f'<div class="fc-stat-lbl">30日均涨</div></div>'
            f'<div class="fc-stat"><div class="fc-stat-val" style="color:{hit30_col}">{r["hit30"]:.0f}%</div>'
            f'<div class="fc-stat-lbl">30日胜率</div></div>'
            f'</div>'
            f'</div>'
        )

    return (
        f'<div class="fwd-cards">{cards}</div>'
        f'<p class="fwd-note">过去3年历史回测：信号进入该区间后，实际股价平均表现</p>'
    )


def _simple_say(current_pct: float, stock_name: str, fwd_df: pd.DataFrame | None) -> str:
    """One plain-language sentence for the verdict card."""
    if current_pct >= 99:
        strength = '历史罕见的最强信号'
    elif current_pct >= 95:
        strength = '强信号'
    elif current_pct >= 80:
        strength = '较强信号'
    else:
        strength = '信号中等'

    perf = ''
    if fwd_df is not None and not fwd_df.empty:
        r = fwd_df.iloc[0]
        perf = f'历史上类似情形后30天平均涨 <strong style="color:#2ca02c">{r["avg30"]:+.1f}%</strong>，胜率 <strong>{r["hit30"]:.0f}%</strong>。'

    return f'<strong>简单说：</strong>{stock_name}当前处于{strength}，{perf}值得关注。'


def _ai_voice_html(core_focus: dict, current_pct: float,
                   fwd_df: pd.DataFrame | None, stock_name: str) -> str:
    """Dark-gradient 'Freeride AI 对你说' footer card."""
    bull = core_focus.get('bull', [])

    if current_pct >= 95:
        strength = '量化信号极强'
    elif current_pct >= 80:
        strength = '量化信号偏强'
    else:
        strength = '量化信号中等'

    perf_line = ''
    if fwd_df is not None and not fwd_df.empty:
        r = fwd_df.iloc[0]
        perf_line = (f'历史上类似信号出现后30天，'
                     f'平均上涨 {r["avg30"]:+.1f}%，{r["hit30"]:.0f}% 的时候收益为正。')

    tip = (bull[0][:80] if bull else '').rstrip('。；')
    main = f'{strength}，{perf_line}' + (f'最值得关注：{tip}。' if tip else '')

    return (
        '<div style="background:linear-gradient(135deg,#0d1322 0%,#1a2d63 100%);'
        'margin:12px 12px 0;border-radius:12px;padding:16px 20px;color:#fff">'
        '<div style="font-size:12px;color:#8ea8d8;margin-bottom:8px;font-weight:700">'
        '🤖 Freeride AI 对你说</div>'
        f'<p style="font-size:13.5px;line-height:1.8;color:#e8f0ff">{main}</p>'
        '<p style="font-size:11px;color:#4a6a90;margin-top:10px;'
        'border-top:1px solid #1e3060;padding-top:8px">'
        '⚠️ 仅供学习参考，不构成投资建议。历史回测不代表未来收益，投资有风险，入市需谨慎。'
        '</p>'
        '</div>'
    )


def _core_focus_html(cf: dict) -> str:
    summary = cf.get('summary', '')
    bull    = cf.get('bull', [])
    bear    = cf.get('bear', '')

    connectors = [
        ('首先', '#1a6fc4', '#eef3fb'),
        ('其次', '#2ca02c', '#f0faf0'),
        ('此外', '#e8a500', '#fffbf0'),
    ]

    story = ''
    for i, b in enumerate(bull[:3]):
        label, text_col, bg = connectors[i] if i < len(connectors) else ('另', '#666', '#f5f5f5')
        story += (
            f'<div style="display:flex;gap:10px;margin-bottom:8px;align-items:flex-start">'
            f'<span style="flex-shrink:0;font-size:11px;font-weight:900;color:{text_col};'
            f'background:{bg};border-radius:4px;padding:2px 8px;margin-top:2px">{label}</span>'
            f'<span style="font-size:12.5px;color:#333;line-height:1.65">{b}</span>'
            f'</div>'
        )

    bear_div = ''
    if bear:
        bear_div = (
            f'<div style="display:flex;gap:10px;margin-top:4px;align-items:flex-start">'
            f'<span style="flex-shrink:0;font-size:11px;font-weight:900;color:#d62728;'
            f'background:#fff5f5;border-radius:4px;padding:2px 8px;margin-top:2px">注意</span>'
            f'<span style="font-size:12.5px;color:#4a1818;line-height:1.65">{bear}</span>'
            f'</div>'
        )

    return f'<p class="exec-sum">{summary}</p>{story}{bear_div}'


def _leadlag_html(signals: list[dict]) -> str:
    if not signals:
        return '<p style="color:#aaa">上下游数据不足</p>'
    cards = ''
    for s in signals[:2]:
        role_txt  = s.get('role', '')
        price_txt = s.get('price30', '')
        sub_line  = f'{role_txt}  ·  近30日股价 {price_txt}' if price_txt else role_txt
        cards += f'''
<div class="ll-card" style="background:{s["bg"]};border-left:3px solid {s["color"]}">
  <div class="co">{s["name"]}</div>
  <div class="role">{sub_line}</div>
  <div class="q1" style="color:{s["color"]}">{s.get("q1","")}</div>
  <span class="dir-badge" style="background:{s["color"]}">{s.get("direction","")}</span>
  <p class="concl">{s.get("conclusion","")}</p>
</div>'''
    return f'<div class="ll-cards">{cards}</div>'


def build_pitch_html(
    ts_code: str,
    trade_date: str,
    stock_name: str,
    charts: dict,
    fwd_df: pd.DataFrame | None,
    current_pct: float,
    core_focus: dict,
    leadlag: list[dict],
    capital_narr: str,
    quant_narr: dict | None = None,
    market_narr: str = '',
    radar_narr: str = '',
) -> str:
    code = ts_code.split('.')[0]
    dt   = datetime.strptime(trade_date, '%Y%m%d').strftime('%Y-%m-%d')
    cf_summary_full = core_focus.get('summary', '')
    # Shorten to first sentence for hero tagline
    _sent = re.split(r'[。！？]', cf_summary_full)
    cf_summary = (_sent[0] + '。') if _sent and _sent[0] else cf_summary_full

    qn = quant_narr or {}
    q_tagline   = qn.get('tagline', '')
    q_stage     = qn.get('stage', '')
    q_signal_text = _quant_summary_text(fwd_df, current_pct)

    def img(b64: str, alt: str = '') -> str:
        if not b64:
            return f'<div style="height:200px;background:#f5f5f5;border-radius:8px;display:flex;align-items:center;justify-content:center;color:#ccc;font-size:12px">图表加载中</div>'
        return f'<img src="data:image/png;base64,{b64}" alt="{alt}" style="max-width:100%;border-radius:8px;box-shadow:0 1px 6px rgba(0,0,0,.08)"/>'

    # Market stat tiles — vertical stack (used inside 45% column)
    mkt_tiles = (
        '<div style="display:flex;flex-direction:column;gap:8px;height:100%">'
        '<div class="mkt-tile">'
        '<div class="label">沪深300（30日）</div>'
        '<div class="val green">+8.30%</div>'
        '<div class="sub">60日 +2.90% &nbsp;·&nbsp; 牛市格局</div>'
        '</div>'
        '<div class="mkt-tile">'
        '<div class="label">申万半导体（30日）</div>'
        '<div class="val green">+57.74%</div>'
        '<div class="sub">60日 +40.57% &nbsp;·&nbsp; 全市场最强</div>'
        '</div>'
        '<div class="mkt-tile">'
        '<div class="label">板块广度</div>'
        '<div class="val amber">93.8%</div>'
        '<div class="sub">个股 &gt; 20MA &nbsp;·&nbsp; 共振极强</div>'
        '</div>'
        '</div>'
    )

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{code} {stock_name} — 研究摘要</title>
{_CSS}
</head>
<body>
<div class="page">

<!-- HERO -->
<div class="hero">
  <h1>{code} {stock_name}
    <span class="verdict v-bull">买入信号</span>
  </h1>
  <div class="meta">
    {dt} &nbsp;·&nbsp; CSI 300 量化选股 TOP 1 &nbsp;·&nbsp; 申万半导体
    &nbsp;·&nbsp; <span style="color:#7090b8">数据截至收盘 {dt}</span>
  </div>
  <p class="tagline">{cf_summary}</p>
</div>

<!-- VERDICT CARD -->
{_verdict_card_html(core_focus, current_pct, dt, fwd_df=fwd_df, stock_name=stock_name)}

<!-- SECTION 1: 量化指标 -->
<div class="sec">
  <div class="sec-title">📈 量化指标 <span class="sec-sub">— 历史上类似信号之后涨了多少？</span></div>
  <div class="quant-view">
    <div class="tagline">{q_tagline}</div>
    <div class="detail">{q_signal_text}</div>
  </div>

  <p style="font-size:12.5px;color:#555;font-weight:700;margin:0 0 4px">
    🏆 战绩回放 <span style="font-size:11px;font-weight:400;color:#aaa">— 过去3年，信号达到该强度后股价怎么走</span>
  </p>
  <span class="gloss-note" style="margin-bottom:10px;display:block">
    「信号强度」= 综合评分在历史中的排位，越高代表当前动量越罕见强势
  </span>
  {_war_record_html(fwd_df)}

  <div class="g-60-40" style="margin-top:14px">
    <div class="chart-box">{img(charts.get("kline",""), "K线关键价位")}</div>
    <div>
      <p style="font-size:12px;color:#555;font-weight:700;margin-bottom:4px">
        量化因子雷达（当前状态）
      </p>
      <span class="gloss-note">7个技术指标打分，满分100分，分数越高代表该维度越强势</span>
      {img(charts.get("factor_radar",""), "量化因子雷达")}
    </div>
  </div>
  <div class="narr-sm" style="margin-top:12px">{q_stage}</div>
</div>

<!-- SECTION 2: 资金博弈 -->
<div class="sec">
  <div class="sec-title">💰 资金博弈 <span class="sec-sub">— 有没有大资金在买？</span></div>

  <p style="font-size:12px;color:#888;font-weight:700;margin-bottom:8px">① 大盘与板块：买的环境好不好</p>
  <div class="g-45-55" style="margin-bottom:8px">
    <div>{mkt_tiles}</div>
    <div class="chart-box">{img(charts.get("beta",""), "大盘Beta与板块环境")}</div>
  </div>
  <div class="narr-sm">{market_narr}
    <span class="gloss-note">Beta=1.24：大盘每涨1%，中芯理论上涨约1.24%；板块超额49pp = 半导体远强于大盘。</span>
  </div>

  <hr class="divider"/>
  <p style="font-size:12px;color:#888;font-weight:700;margin-bottom:6px">② 个股资金：这只股票有没有人买</p>
  <span class="gloss-note" style="margin-bottom:8px;display:block">
    左图：7项资金指标综合打分（满分100），分越高代表当前资金越活跃；右图：在申万半导体板块内的相对强弱排名。
  </span>
  <div class="g-45-55" style="margin-bottom:8px">
    <div class="chart-box">{img(charts.get("capital_radar",""), "资金博弈雷达")}</div>
    <div class="chart-box">{img(charts.get("peer",""), "板块相对强弱")}</div>
  </div>
  <div class="narr-sm">{radar_narr}</div>

  <hr class="divider"/>
  <p style="font-size:12px;color:#888;font-weight:700;margin-bottom:6px">③ 成交量节奏：放量还是缩量</p>
  <div class="narr">{capital_narr}
    <span class="gloss-note">成交量Z分数：当日成交量比近20日均量高出几倍标准差；+2.42 ≈ 比平时放量约2.4倍，属极高水平。</span>
  </div>
</div>

<!-- SECTION 3: 基本面催化剂 -->
<div class="sec">
  <div class="sec-title">🔍 基本面催化剂 <span class="sec-sub">— 为什么会涨？</span></div>
  <div class="g2">
    <div>
      <p style="font-size:12px;color:#888;font-weight:700;margin-bottom:8px">市场核心关注点</p>
      {_core_focus_html(core_focus)}
    </div>
    <div>
      <p style="font-size:12px;color:#888;font-weight:700;margin-bottom:8px">上下游领先指标（Q1 2026 实际数据）</p>
      {_leadlag_html(leadlag)}
    </div>
  </div>
</div>

<!-- AI VOICE FOOTER -->
{_ai_voice_html(core_focus, current_pct, fwd_df, stock_name)}
<div style="height:20px"></div>

</div><!-- /page -->
</body>
</html>'''


# ── Main ──────────────────────────────────────────────────────────────────────

def build_elevator_pitch(ts_code: str, trade_date: str, stock_name: str) -> Path:
    code = ts_code.split('.')[0]
    long_html_path = _STOCKS / f'{trade_date}_csi300_{code}_{stock_name}.html'

    if not long_html_path.exists():
        # Try glob
        candidates = list(_STOCKS.glob(f'*{code}*.html'))
        candidates = [p for p in candidates if 'pitch' not in p.name]
        if not candidates:
            raise FileNotFoundError(f'Long report not found for {ts_code}')
        long_html_path = candidates[0]
        print(f'  Using: {long_html_path.name}')

    print(f'  Loading long report: {long_html_path.name}')
    with open(long_html_path, encoding='utf-8') as f:
        long_html = f.read()

    print(f'  Extracting charts...')
    charts = extract_all_charts(long_html)
    for k, v in charts.items():
        print(f'    {k}: {len(v):,} chars' if v else f'    {k}: MISSING')

    print(f'  Extracting text...')
    core_focus    = extract_core_focus(long_html)
    leadlag       = extract_leadlag(long_html)
    capital_narr  = extract_capital_narrative(long_html)
    quant_narr    = extract_quant_narratives(long_html)
    market_narr   = extract_market_narrative(long_html)
    radar_narr    = extract_radar_narrative(long_html)

    print(f'  Computing forward return table...')
    fwd_result = compute_fwd_return_table(ts_code, trade_date)
    if isinstance(fwd_result, tuple):
        fwd_df, current_pct = fwd_result
    else:
        fwd_df, current_pct = None, 50.0

    print(f'  Building pitch HTML...')
    pitch_html = build_pitch_html(
        ts_code, trade_date, stock_name,
        charts, fwd_df, current_pct, core_focus, leadlag, capital_narr,
        quant_narr=quant_narr,
        market_narr=market_narr,
        radar_narr=radar_narr,
    )

    out_path = _STOCKS / f'{trade_date}_pitch_{code}_{stock_name}.html'
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(pitch_html)
    print(f'  Saved: {out_path.name} ({len(pitch_html):,} chars)')
    return out_path


if __name__ == '__main__':
    import sys
    argv = sys.argv[1:]
    ts_code    = argv[0] if len(argv) > 0 else '688981.SH'
    trade_date = argv[1] if len(argv) > 1 else '20260520'
    stock_name = argv[2] if len(argv) > 2 else '中芯国际'

    print(f'\n{"="*60}')
    print(f'build_elevator_pitch  {ts_code}  {trade_date}  {stock_name}')
    print('='*60)
    out = build_elevator_pitch(ts_code, trade_date, stock_name)
    print(f'\n  Done -> {out}')
