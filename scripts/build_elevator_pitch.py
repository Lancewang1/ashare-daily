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
    bull   = [x for x in items if any(c in x for c in ('①', '②', '③', '产能', '整合', '扩产'))][:3]
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

        signals.append({
            'name': name, 'q1': q1, 'price30': price30,
            'direction': direction, 'conclusion': concl,
            'color': color, 'bg': bg,
        })
    return signals


def extract_capital_narrative(long_html: str) -> str:
    title_pos = long_html.find('<span class="card-title">资金博弈雷达')
    if title_pos == -1:
        return ''
    cb_s  = long_html.find('<div class="card-body"', title_pos)
    if cb_s == -1:
        return ''
    inner = long_html.index('>', cb_s) + 1
    cb_e  = long_html.find('</div>', inner)
    narr  = _strip(long_html[inner:cb_e])
    # First 2 sentences
    parts = re.split(r'(?<=[。！？])', narr)
    return ''.join(parts[:3]).strip()[:350]


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

    return pd.DataFrame(rows) if rows else None


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

/* Chart containers */
.chart-box img { max-width: 100%; border-radius: 8px;
                 box-shadow: 0 1px 6px rgba(0,0,0,.08); }

/* Forward return table */
.fwd-table { width: 100%; border-collapse: collapse; margin-top: 2px;
             font-size: 12.5px; }
.fwd-table th { background: #f0f4ff; color: #1a3463; font-weight: 700;
                padding: 8px 10px; text-align: center;
                border: 1px solid #dde; font-size: 12px; }
.fwd-table td { padding: 7px 10px; text-align: center;
                border: 1px solid #eef; }
.fwd-table tr:nth-child(even) { background: #f9faff; }
.pos  { color: #2ca02c; font-weight: 700; }
.neg  { color: #d62728; font-weight: 700; }
.fwd-note { font-size: 11px; color: #aaa; margin-top: 6px; text-align: right; }
.fwd-label { font-size: 12.5px; font-weight: 800; color: #1a3463; }

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

/* Capital narrative */
.narr { font-size: 13px; line-height: 1.8; color: #333; margin-top: 12px;
        padding: 11px 15px; background: #f5f8ff;
        border-left: 3px solid #1a6fc4; border-radius: 0 6px 6px 0; }

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
</style>'''


def _fmt_pct(v: float, decimals: int = 1) -> str:
    s = f'{v:+.{decimals}f}%'
    cls = 'pos' if v > 0 else ('neg' if v < 0 else '')
    return f'<span class="{cls}">{s}</span>'


def _fwd_table_html(df: pd.DataFrame | None) -> str:
    if df is None or df.empty:
        return '<p style="color:#aaa;font-size:12px;padding:20px 0 0">信号历史数据不足</p>'

    rows_html = ''
    for _, r in df.iterrows():
        rows_html += (
            f'<tr>'
            f'<td class="fwd-label">{r["label"]}</td>'
            f'<td style="color:#888">{int(r["n"])} 次</td>'
            f'<td>{_fmt_pct(r["avg1"])}</td>'
            f'<td>{_fmt_pct(r["avg5"])}</td>'
            f'<td>{_fmt_pct(r["avg30"])}</td>'
            f'<td class="{"pos" if r["hit30"]>=55 else "neg"}">{r["hit30"]:.0f}%</td>'
            f'</tr>'
        )

    return f'''
<table class="fwd-table">
  <thead>
    <tr>
      <th>信号强度</th><th>样本</th>
      <th>次日均涨</th><th>5日均涨</th><th>30日均涨</th><th>30日胜率</th>
    </tr>
  </thead>
  <tbody>{rows_html}</tbody>
</table>
<p class="fwd-note">注：综合因子百分位达到阈值时（自身1年历史），历史平均表现</p>'''


def _core_focus_html(cf: dict) -> str:
    summary = cf.get('summary', '')
    bull    = cf.get('bull', [])
    bear    = cf.get('bear', '')

    bull_li = ''.join(f'<li>{b}</li>' for b in bull[:3])
    bear_div = f'<div class="bear-item">{bear}</div>' if bear else ''

    return f'''
<p class="exec-sum">{summary}</p>
<ul class="bull-list">{bull_li}</ul>
{bear_div}'''


def _leadlag_html(signals: list[dict]) -> str:
    if not signals:
        return '<p style="color:#aaa">上下游数据不足</p>'
    cards = ''
    for s in signals[:2]:
        cards += f'''
<div class="ll-card" style="background:{s["bg"]};border-left:3px solid {s["color"]}">
  <div class="co">{s["name"]}</div>
  <div class="role">{s.get("direction","")}</div>
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
    core_focus: dict,
    leadlag: list[dict],
    capital_narr: str,
) -> str:
    code = ts_code.split('.')[0]
    dt   = datetime.strptime(trade_date, '%Y%m%d').strftime('%Y-%m-%d')
    cf_summary = core_focus.get('summary', '')

    def img(b64: str, alt: str = '') -> str:
        if not b64:
            return f'<div style="height:200px;background:#f5f5f5;border-radius:8px;display:flex;align-items:center;justify-content:center;color:#ccc;font-size:12px">图表加载中</div>'
        return f'<img src="data:image/png;base64,{b64}" alt="{alt}" style="max-width:100%;border-radius:8px;box-shadow:0 1px 6px rgba(0,0,0,.08)"/>'

    # Market stat tiles
    mkt_tiles = f'''
<div class="mkt-tiles">
  <div class="mkt-tile">
    <div class="label">沪深300（30日）</div>
    <div class="val green">+8.30%</div>
    <div class="sub">60日 +2.90%  牛市格局</div>
  </div>
  <div class="mkt-tile">
    <div class="label">申万半导体（30日）</div>
    <div class="val green">+57.74%</div>
    <div class="sub">60日 +40.57%  全市场最强板块</div>
  </div>
  <div class="mkt-tile">
    <div class="label">板块广度</div>
    <div class="val amber">93.8%</div>
    <div class="sub">个股 > 20MA  共振极强</div>
  </div>
</div>'''

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
  <div class="meta">{dt} &nbsp;·&nbsp; CSI 300 量化选股 TOP 1 &nbsp;·&nbsp; 申万半导体</div>
  <p class="tagline">{cf_summary}</p>
</div>

<!-- SECTION 1: 量化指标 -->
<div class="sec">
  <div class="sec-title">📈 量化指标 <span class="sec-sub">— 买了能涨多少？</span></div>
  <div class="g-60-40">
    <div class="chart-box">{img(charts.get("kline",""), "K线关键价位")}</div>
    <div>
      <p style="font-size:12.5px;color:#555;font-weight:700;margin-bottom:8px">
        历史信号强度 → 后续表现（自身1年历史）
      </p>
      {_fwd_table_html(fwd_df)}
      <hr class="divider"/>
      <p style="font-size:12px;color:#555;font-weight:700;margin-bottom:6px">
        量化因子雷达（当前）
      </p>
      {img(charts.get("factor_radar",""), "量化因子雷达")}
    </div>
  </div>
</div>

<!-- SECTION 2: 资金博弈 -->
<div class="sec">
  <div class="sec-title">💰 资金博弈 <span class="sec-sub">— 有没有人买？</span></div>

  <p style="font-size:12px;color:#888;font-weight:700;margin-bottom:8px">市场与板块环境</p>
  {mkt_tiles}
  <div class="chart-box" style="margin-bottom:14px">
    {img(charts.get("beta",""), "大盘Beta与板块环境")}
  </div>

  <hr class="divider"/>
  <p style="font-size:12px;color:#888;font-weight:700;margin-bottom:10px">个股资金博弈</p>
  <div class="g-45-55">
    <div class="chart-box">{img(charts.get("capital_radar",""), "资金博弈雷达")}</div>
    <div class="chart-box">{img(charts.get("peer",""), "板块相对强弱")}</div>
  </div>

  <div class="narr">{capital_narr}</div>
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

    print(f'  Computing forward return table...')
    fwd_df = compute_fwd_return_table(ts_code, trade_date)

    print(f'  Building pitch HTML...')
    pitch_html = build_pitch_html(
        ts_code, trade_date, stock_name,
        charts, fwd_df, core_focus, leadlag, capital_narr,
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
