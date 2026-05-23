"""
inject_signals.py
=================
Reusable module: inject 4 market micro-signal cards into a single-stock HTML report.

Public API
----------
    inject_signals_for_report(html_path, ts_code, trade_date, sector=None, sw_index_code=None)

Called automatically by run_top1.py --publish for every generated report.
Falls back gracefully if any signal fails (partial injection is OK).
"""

from __future__ import annotations

import sys
import os
import re
import importlib
import time
from pathlib import Path

# ── Path bootstrap ────────────────────────────────────────────────────────────
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Ensure tushare token is loaded if .env exists nearby
try:
    from dotenv import load_dotenv
    for _env in (_SCRIPTS_DIR / ".env",
                 _SCRIPTS_DIR.parent / ".env",
                 _SCRIPTS_DIR.parents[1] / ".env",
                 Path.home() / ".env"):
        if _env.exists():
            load_dotenv(_env, override=False)
            break
except ImportError:
    pass


# ── Sector → SW Level-2 index mapping ────────────────────────────────────────
# Maps sector labels (used in run_top1._SECTOR_OVERRIDES) → Shenwan L2 index codes
_SECTOR_TO_SW = {
    '半导体':   '801081.SI',
    '电子':     '801080.SI',
    '汽车':     '801880.SI',
    '机械设备': '801890.SI',
    '国防军工': '801740.SI',
    '电气设备': '801730.SI',
    '计算机':   '801750.SI',
    '通信':     '801770.SI',
    '传媒':     '801760.SI',
    '医药生物': '801150.SI',
    '食品饮料': '801120.SI',
    '家用电器': '801110.SI',
    '有色金属': '801050.SI',
    '化工':     '801030.SI',
    '钢铁':     '801040.SI',
    '采掘':     '801020.SI',
    '农林牧渔': '801010.SI',
    '银行':     '801780.SI',
    '非银金融': '801790.SI',
    '房地产':   '801180.SI',
    '公用事业': '801160.SI',
    '交通运输': '801170.SI',
    '建筑材料': '801710.SI',
    '建筑装饰': '801720.SI',
    '纺织服装': '801130.SI',
    '轻工制造': '801140.SI',
    '商业贸易': '801200.SI',
    '休闲服务': '801210.SI',
    '综合':     '801230.SI',
}

# Default fallback index (CSI 300 component)
_DEFAULT_SW_INDEX = '801080.SI'  # 申万电子


def sector_to_sw_index(sector: str | None) -> str:
    """Return the best SW index code for a given sector label."""
    if not sector:
        return _DEFAULT_SW_INDEX
    for key, code in _SECTOR_TO_SW.items():
        if key in sector:
            return code
    return _DEFAULT_SW_INDEX


# ── HTML card builders ────────────────────────────────────────────────────────

def _make_section_html(title: str, icon: str, chart_b64: str, narrative: str,
                       badge: str = '', badge_color: str = '#1a6fc4') -> str:
    badge_html = ''
    if badge:
        badge_html = (f'<span style="background:{badge_color};color:#fff;border-radius:4px;'
                      f'padding:2px 8px;font-size:12px;margin-left:8px;font-weight:600;">'
                      f'{badge}</span>')
    narrative_html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', narrative)
    return f'''
<section class="section" style="margin:24px 0;padding:20px;background:#fff;border-radius:8px;border:1px solid #e8e8e8;">
  <h3 style="margin:0 0 12px 0;font-size:16px;color:#1a1a2e;display:flex;align-items:center;">
    <span style="font-size:20px;margin-right:8px;">{icon}</span>
    {title}{badge_html}
  </h3>
  <div style="text-align:center;margin:12px 0;">
    <img src="data:image/png;base64,{chart_b64}"
         style="max-width:100%;border-radius:6px;"
         alt="{title}"/>
  </div>
  <p style="margin:12px 0 0;font-size:14px;line-height:1.7;color:#444;background:#f8f9fa;
            padding:12px 16px;border-radius:6px;border-left:3px solid {badge_color};">
    {narrative_html}
  </p>
</section>'''


def _make_wrapper_section(title: str, content: str) -> str:
    return f'''
<section class="section" style="margin:32px 0;">
  <h2 style="font-size:18px;color:#1a1a2e;border-bottom:2px solid #1a6fc4;
             padding-bottom:8px;margin-bottom:4px;">
    🔬 {title}
  </h2>
  {content}
</section>'''


# ── Signal badge helpers ──────────────────────────────────────────────────────

def _thermo_badge(pct: float) -> tuple[str, str]:
    if pct >= 80:
        return f'{pct:.0f}th百分位 · 极度亢奋', '#dc3545'
    if pct >= 60:
        return f'{pct:.0f}th百分位 · 情绪偏热', '#fd7e14'
    if pct >= 30:
        return f'{pct:.0f}th百分位 · 情绪正常', '#28a745'
    return f'{pct:.0f}th百分位 · 极度悲观', '#4472C4'


def _ca_badge(signal: str) -> tuple[str, str]:
    if '强势' in signal:
        return signal, '#dc3545'
    if '正常' in signal:
        return signal, '#fd7e14'
    return signal, '#888'


def _sd_badge(stage: str) -> tuple[str, str]:
    if '过热' in stage:
        return stage, '#dc3545'
    if '后期' in stage:
        return stage, '#fd7e14'
    return stage, '#28a745'


def _ma_badge(signal: str) -> tuple[str, str]:
    if '快速' in signal:
        return signal, '#dc3545'
    if '加速' in signal:
        return signal, '#fd7e14'
    return signal, '#28a745'


# ── HTML injection ────────────────────────────────────────────────────────────

def _do_inject(html_path: str, signals_html: str) -> bool:
    """Insert signals_html after the last </section> in html_path. Returns True on success."""
    with open(html_path, encoding='utf-8') as f:
        content = f.read()

    if '市场微观信号' in content:
        print(f'  [inject_signals] already injected in {Path(html_path).name}, skipping')
        return True

    idx = content.rfind('</section>')
    if idx == -1:
        print(f'  [inject_signals] ERROR: no </section> found in {Path(html_path).name}')
        return False

    insert_pos = idx + len('</section>')
    new_content = content[:insert_pos] + '\n' + signals_html + '\n' + content[insert_pos:]
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f'  [inject_signals] +{len(signals_html):,} chars injected into {Path(html_path).name}')
    return True


# ── Main public function ──────────────────────────────────────────────────────

def inject_signals_for_report(
    html_path: str,
    ts_code: str,
    trade_date: str,
    sector: str | None = None,
    sw_index_code: str | None = None,
) -> bool:
    """
    Run 4 market micro-signal modules and inject cards into an HTML report.

    Parameters
    ----------
    html_path     : absolute path to the target HTML file
    ts_code       : tushare code, e.g. '688981.SH'
    trade_date    : 'YYYYMMDD', the anchor date of the report
    sector        : sector label from run_top1 (e.g. '电子', '汽车')
    sw_index_code : override SW index code (e.g. '801081.SI'); if None, derived from sector

    Returns True if injection succeeded, False otherwise.
    """
    if sw_index_code is None:
        sw_index_code = sector_to_sw_index(sector)

    print(f'\n  [inject_signals] ts_code={ts_code} date={trade_date} index={sw_index_code}', flush=True)
    t0 = time.time()

    # ── 1. 情绪温度计（全市场共用）────────────────────────────────
    thermo = None
    try:
        from sentiment_thermometer import sentiment_thermometer
        thermo = sentiment_thermometer(trade_date, lookback_days=250)
        pct = thermo['percentile']
        print(f'    [thermo] 涨停={thermo["limit_up_count"]}只 百分位={pct:.1f}%', flush=True)
    except Exception as e:
        print(f'    [thermo] FAILED: {e}', flush=True)

    # ── 2. 集合竞价 ────────────────────────────────────────────────
    ca = None
    try:
        from call_auction import call_auction_signal
        ca = call_auction_signal(ts_code, trade_date)
        print(f'    [call_auction] {ca["signal"]}  溢价={ca["premium_pct"]:+.2f}%', flush=True)
    except Exception as e:
        print(f'    [call_auction] FAILED: {e}', flush=True)

    # ── 3. 板块扩散 ────────────────────────────────────────────────
    sd = None
    try:
        from sector_diffusion import sector_diffusion
        sd = sector_diffusion(sw_index_code, trade_date)
        print(f'    [sector_diffusion] {sd["diffusion_stage"]}  中位={sd["median_return_5d"]:+.2f}%', flush=True)
    except Exception as e:
        print(f'    [sector_diffusion] FAILED: {e}', flush=True)

    # ── 4. 融资加速度 ──────────────────────────────────────────────
    ma = None
    try:
        from margin_accel import margin_acceleration
        ma = margin_acceleration(ts_code, trade_date)
        print(f'    [margin_accel] {ma["signal"]}  加速={ma["accel_ratio"]:.1f}x', flush=True)
    except Exception as e:
        print(f'    [margin_accel] FAILED: {e}', flush=True)

    # ── Build HTML ─────────────────────────────────────────────────
    cards_html = ''

    if thermo and thermo.get('chart_b64'):
        badge_txt, badge_col = _thermo_badge(thermo['percentile'])
        cards_html += _make_section_html(
            '全市场情绪温度计', '🌡️',
            thermo['chart_b64'], thermo['narrative'],
            badge=badge_txt, badge_color=badge_col,
        )

    if ca and ca.get('chart_b64') and ca['signal'] != '无数据':
        badge_txt, badge_col = _ca_badge(ca['signal'])
        cards_html += _make_section_html(
            '集合竞价信号', '🕐',
            ca['chart_b64'], ca['narrative'],
            badge=badge_txt, badge_color=badge_col,
        )

    if sd and sd.get('chart_b64') and not sd.get('error'):
        badge_txt, badge_col = _sd_badge(sd['diffusion_stage'])
        index_name = sd.get('index_name', sw_index_code)
        cards_html += _make_section_html(
            f'{index_name}板块扩散进度', '📡',
            sd['chart_b64'], sd['narrative'],
            badge=badge_txt, badge_color=badge_col,
        )

    if ma and ma.get('chart_b64') and not ma.get('error'):
        badge_txt, badge_col = _ma_badge(ma['signal'])
        cards_html += _make_section_html(
            '融资余额加速度', '⚡',
            ma['chart_b64'], ma['narrative'],
            badge=badge_txt, badge_color=badge_col,
        )

    if not cards_html:
        print(f'  [inject_signals] no signal cards generated, skipping injection', flush=True)
        return False

    date_label = f'{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}'
    wrapper = _make_wrapper_section(f'市场微观信号（{date_label}）', cards_html)

    ok = _do_inject(html_path, wrapper)
    print(f'  [inject_signals] done in {time.time()-t0:.1f}s  success={ok}', flush=True)
    return ok
