"""
inject_signals.py
=================
Reusable module: inject market micro-signal cards into a single-stock HTML report.

Public API
----------
    inject_signals_for_report(html_path, ts_code, trade_date, sector=None, sw_index_code=None)

Called automatically by run_top1.py --publish for every generated report.
Falls back gracefully if any signal fails (partial injection is OK).

Sections injected:
  § 市场微观信号     — 4 original: sentiment_thermometer, call_auction,
                       sector_diffusion, margin_accel
  § 内部人 & 资金博弈 — 4 new: block_trade_signal, slb_vs_margin,
                       insider_trade, money_flow
  § 事件风险 & 深度   — 4 new: share_unlock, equity_incentive,
                       ah_premium, inst_survey
"""

from __future__ import annotations

import sys
import os
import re
import time
from pathlib import Path

# ── Path bootstrap ────────────────────────────────────────────────────────────
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

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
_DEFAULT_SW_INDEX = '801080.SI'


def sector_to_sw_index(sector: str | None) -> str:
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
    chart_block = ''
    if chart_b64:
        chart_block = f'''
  <div style="text-align:center;margin:12px 0;">
    <img src="data:image/png;base64,{chart_b64}"
         style="max-width:100%;border-radius:6px;"
         alt="{title}"/>
  </div>'''
    return f'''
<section class="section" style="margin:24px 0;padding:20px;background:#fff;border-radius:8px;border:1px solid #e8e8e8;">
  <h3 style="margin:0 0 12px 0;font-size:16px;color:#1a1a2e;display:flex;align-items:center;">
    <span style="font-size:20px;margin-right:8px;">{icon}</span>
    {title}{badge_html}
  </h3>{chart_block}
  <p style="margin:12px 0 0;font-size:14px;line-height:1.7;color:#444;background:#f8f9fa;
            padding:12px 16px;border-radius:6px;border-left:3px solid {badge_color};">
    {narrative_html}
  </p>
</section>'''


def _make_wrapper_section(title: str, icon: str, content: str) -> str:
    return f'''
<section class="section" style="margin:32px 0;">
  <h2 style="font-size:18px;color:#1a1a2e;border-bottom:2px solid #1a6fc4;
             padding-bottom:8px;margin-bottom:4px;">
    {icon} {title}
  </h2>
  {content}
</section>'''


# ── Badge helpers ─────────────────────────────────────────────────────────────

def _thermo_badge(pct: float) -> tuple[str, str]:
    if pct >= 80:   return f'{pct:.0f}th百分位 · 极度亢奋', '#dc3545'
    if pct >= 60:   return f'{pct:.0f}th百分位 · 情绪偏热', '#fd7e14'
    if pct >= 30:   return f'{pct:.0f}th百分位 · 情绪正常', '#28a745'
    return f'{pct:.0f}th百分位 · 极度悲观', '#4472C4'

def _ca_badge(signal: str) -> tuple[str, str]:
    if '强势' in signal: return signal, '#dc3545'
    if '正常' in signal: return signal, '#fd7e14'
    return signal, '#888'

def _sd_badge(stage: str) -> tuple[str, str]:
    if '过热' in stage: return stage, '#dc3545'
    if '后期' in stage: return stage, '#fd7e14'
    return stage, '#28a745'

def _ma_badge(signal: str) -> tuple[str, str]:
    if '快速' in signal: return signal, '#dc3545'
    if '加速' in signal: return signal, '#fd7e14'
    return signal, '#28a745'

def _block_badge(signal: str) -> tuple[str, str]:
    if '甩货' in signal: return signal, '#dc3545'
    if '战略' in signal: return signal, '#28a745'
    return signal, '#888'

def _slb_badge(signal: str) -> tuple[str, str]:
    if '纯多' in signal: return signal, '#28a745'
    if '多强' in signal: return signal, '#52b788'
    if '多占优' in signal: return signal, '#52b788'
    if '空强' in signal: return signal, '#dc3545'
    return signal, '#888'

def _insider_badge(signal: str) -> tuple[str, str]:
    if '增持' in signal: return signal, '#28a745'
    if '净减持' in signal or '高管净减持' in signal: return signal, '#dc3545'
    if '减持' in signal: return signal, '#fd7e14'
    return signal, '#888'

def _mf_badge(signal: str) -> tuple[str, str]:
    if '持续净流入' in signal: return signal, '#28a745'
    if '持续出逃' in signal: return signal, '#dc3545'
    if '净流入' in signal: return signal, '#52b788'
    return signal, '#fd7e14'

def _unlock_badge(signal: str) -> tuple[str, str]:
    if '重大' in signal: return signal, '#dc3545'
    if '中等' in signal: return signal, '#fd7e14'
    if '近期有' in signal: return signal, '#aaa'
    return signal, '#28a745'

def _eq_badge(signal: str) -> tuple[str, str]:
    if '废弃' in signal: return signal, '#dc3545'
    if '水下' in signal: return signal, '#fd7e14'
    if '强激励' in signal: return signal, '#28a745'
    if '有效' in signal: return signal, '#52b788'
    if '充分' in signal: return signal, '#888'
    return signal, '#888'

def _ah_badge(signal: str) -> tuple[str, str]:
    if '折价' in signal: return signal, '#28a745'
    if '合理' in signal: return signal, '#52b788'
    if '偏高' in signal: return signal, '#fd7e14'
    if '严重' in signal: return signal, '#dc3545'
    return signal, '#888'

def _surv_badge(signal: str) -> tuple[str, str]:
    if '密集' in signal: return signal, '#dc3545'
    if '持续' in signal: return signal, '#52b788'
    if '有机构' in signal: return signal, '#888'
    return '近期无调研', '#aaa'

def _model_badge(signal: str) -> tuple[str, str]:
    if '第1名' in signal: return signal, '#dc3545'
    if 'Top3' in signal:  return signal, '#fd7e14'
    if 'Top10' in signal: return signal, '#52b788'
    return signal, '#888'

def _dashboard_badge(signal: str) -> tuple[str, str]:
    if '偏多' in signal: return signal, '#28a745'
    if '偏空' in signal: return signal, '#dc3545'
    return signal, '#888'

def _kl_badge(signal: str) -> tuple[str, str]:
    if '支撑' in signal:  return signal, '#28a745'
    if '压力' in signal:  return signal, '#dc3545'
    if '行权' in signal:  return signal, '#fd7e14'
    if '解禁' in signal:  return signal, '#dc3545'
    return signal, '#888'


# ── HTML injection ────────────────────────────────────────────────────────────

_INJECT_GUARD = '<!-- inject_signals_v2 -->'

def _do_inject(html_path: str, signals_html: str) -> bool:
    with open(html_path, encoding='utf-8') as f:
        content = f.read()

    if _INJECT_GUARD in content:
        print(f'  [inject_signals] already injected in {Path(html_path).name}, skipping')
        return True

    idx = content.rfind('</section>')
    if idx == -1:
        print(f'  [inject_signals] ERROR: no </section> found in {Path(html_path).name}')
        return False

    insert_pos = idx + len('</section>')
    new_content = (content[:insert_pos]
                   + f'\n{_INJECT_GUARD}\n'
                   + signals_html
                   + '\n'
                   + content[insert_pos:])
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f'  [inject_signals] +{len(signals_html):,} chars → {Path(html_path).name}')
    return True


# ── Main public function ──────────────────────────────────────────────────────

def inject_signals_for_report(
    html_path: str,
    ts_code: str,
    trade_date: str,
    sector: str | None = None,
    sw_index_code: str | None = None,
) -> bool:
    if sw_index_code is None:
        sw_index_code = sector_to_sw_index(sector)

    print(f'\n  [inject_signals] ts_code={ts_code} date={trade_date} '
          f'index={sw_index_code}', flush=True)
    t0 = time.time()

    # ════════════════════════════════════════════════════════════════
    # § 0. 量化模型选股记录
    # ════════════════════════════════════════════════════════════════
    mtr = None
    try:
        from model_track_record import model_track_record
        mtr = model_track_record(ts_code, trade_date)
        print(f'    [model_track_record] {mtr["signal"]}  排名#{mtr["current_rank"]}', flush=True)
    except Exception as e:
        print(f'    [model_track_record] FAILED: {e}', flush=True)

    # ════════════════════════════════════════════════════════════════
    # § 1. 市场微观信号（4个原始模块）
    # ════════════════════════════════════════════════════════════════
    thermo = ca = sd = ma = None

    try:
        from sentiment_thermometer import sentiment_thermometer
        thermo = sentiment_thermometer(trade_date, lookback_days=250)
        print(f'    [thermo] 涨停={thermo["limit_up_count"]}只 '
              f'百分位={thermo["percentile"]:.1f}%', flush=True)
    except Exception as e:
        print(f'    [thermo] FAILED: {e}', flush=True)

    try:
        from call_auction import call_auction_signal
        ca = call_auction_signal(ts_code, trade_date)
        print(f'    [call_auction] {ca["signal"]}  溢价={ca["premium_pct"]:+.2f}%', flush=True)
    except Exception as e:
        print(f'    [call_auction] FAILED: {e}', flush=True)

    try:
        from sector_diffusion import sector_diffusion
        sd = sector_diffusion(sw_index_code, trade_date)
        print(f'    [sector_diffusion] {sd["diffusion_stage"]}  '
              f'中位={sd["median_return_5d"]:+.2f}%', flush=True)
    except Exception as e:
        print(f'    [sector_diffusion] FAILED: {e}', flush=True)

    try:
        from margin_accel import margin_acceleration
        ma = margin_acceleration(ts_code, trade_date)
        print(f'    [margin_accel] {ma["signal"]}  加速={ma["accel_ratio"]:.1f}x', flush=True)
    except Exception as e:
        print(f'    [margin_accel] FAILED: {e}', flush=True)

    sec0_html = ''
    if mtr and mtr.get('chart_b64') and mtr.get('signal') not in ('无数据', '未纳入宇宙'):
        b, c = _model_badge(mtr['signal'])
        sec0_html += _make_section_html('量化模型选股记录（OOF真实回测）', '🤖',
                                        mtr['chart_b64'], mtr['narrative'], b, c)
    elif mtr and mtr.get('narrative'):
        sec0_html += _make_section_html('量化模型选股记录', '🤖',
                                        '', mtr['narrative'], mtr.get('signal', ''), '#888')

    sec1_html = ''
    if thermo and thermo.get('chart_b64'):
        b, c = _thermo_badge(thermo['percentile'])
        sec1_html += _make_section_html('全市场情绪温度计', '🌡️',
                                        thermo['chart_b64'], thermo['narrative'], b, c)
    if ca and ca.get('chart_b64') and ca['signal'] != '无数据':
        b, c = _ca_badge(ca['signal'])
        sec1_html += _make_section_html('集合竞价信号', '🕐',
                                        ca['chart_b64'], ca['narrative'], b, c)
    if sd and sd.get('chart_b64') and not sd.get('error'):
        b, c = _sd_badge(sd['diffusion_stage'])
        idx_name = sd.get('index_name', sw_index_code)
        sec1_html += _make_section_html(f'{idx_name}板块扩散进度', '📡',
                                        sd['chart_b64'], sd['narrative'], b, c)
    if ma and ma.get('chart_b64') and not ma.get('error'):
        b, c = _ma_badge(ma['signal'])
        sec1_html += _make_section_html('融资余额加速度', '⚡',
                                        ma['chart_b64'], ma['narrative'], b, c)

    # ════════════════════════════════════════════════════════════════
    # § 2. 内部人 & 资金博弈（仪表盘 + 4个原始模块）
    # ════════════════════════════════════════════════════════════════
    cd = bt = slb = it = mf = None

    try:
        from capital_dashboard import capital_dashboard
        cd = capital_dashboard(ts_code, trade_date)
        print(f'    [capital_dashboard] {cd["signal"]}  综合{cd["composite_pct"]}%ile', flush=True)
    except Exception as e:
        print(f'    [capital_dashboard] FAILED: {e}', flush=True)

    bt = slb = it = mf = None

    try:
        from block_trade_signal import block_trade_signal
        bt = block_trade_signal(ts_code, trade_date)
        print(f'    [block_trade] {bt["signal"]}', flush=True)
    except Exception as e:
        print(f'    [block_trade] FAILED: {e}', flush=True)

    try:
        from slb_vs_margin import slb_vs_margin
        slb = slb_vs_margin(ts_code, trade_date)
        print(f'    [slb_vs_margin] {slb["signal"]}', flush=True)
    except Exception as e:
        print(f'    [slb_vs_margin] FAILED: {e}', flush=True)

    try:
        from insider_trade import insider_trade
        it = insider_trade(ts_code, trade_date)
        print(f'    [insider_trade] {it["signal"]}', flush=True)
    except Exception as e:
        print(f'    [insider_trade] FAILED: {e}', flush=True)

    try:
        from money_flow import money_flow
        mf = money_flow(ts_code, trade_date)
        print(f'    [money_flow] {mf["signal"]}', flush=True)
    except Exception as e:
        print(f'    [money_flow] FAILED: {e}', flush=True)

    sec2_html = ''
    if cd and cd.get('chart_b64') and cd['signal'] != '无数据':
        b, c = _dashboard_badge(cd['signal'])
        sec2_html += _make_section_html('资金博弈仪表盘（历史百分位）', '📊',
                                        cd['chart_b64'], cd['narrative'], b, c)
    elif cd and cd.get('narrative'):
        sec2_html += _make_section_html('资金博弈仪表盘', '📊',
                                        '', cd['narrative'], cd.get('signal', ''), '#888')

    if bt and bt.get('chart_b64') and bt['signal'] not in ('无数据', '无大宗交易'):
        b, c = _block_badge(bt['signal'])
        sec2_html += _make_section_html('大宗交易折溢价', '📦',
                                        bt['chart_b64'], bt['narrative'], b, c)
    elif bt and bt.get('narrative'):
        # No chart but has narrative (e.g. no trades)
        sec2_html += _make_section_html('大宗交易折溢价', '📦',
                                        '', bt['narrative'], bt['signal'], '#888')

    if slb and slb.get('chart_b64') and slb['signal'] != '无数据':
        b, c = _slb_badge(slb['signal'])
        sec2_html += _make_section_html('融资 vs 转融通空头', '⚖️',
                                        slb['chart_b64'], slb['narrative'], b, c)
    elif slb and slb.get('narrative'):
        sec2_html += _make_section_html('融资 vs 转融通空头', '⚖️',
                                        '', slb['narrative'], slb['signal'], '#888')

    if it and it.get('chart_b64') and it['signal'] != '无数据':
        b, c = _insider_badge(it['signal'])
        sec2_html += _make_section_html('股东增减持追踪', '👤',
                                        it['chart_b64'], it['narrative'], b, c)
    elif it and it.get('narrative'):
        sec2_html += _make_section_html('股东增减持追踪', '👤',
                                        '', it['narrative'], it['signal'], '#888')

    if mf and mf.get('chart_b64') and mf['signal'] != '无数据':
        b, c = _mf_badge(mf['signal'])
        sec2_html += _make_section_html('主力资金流向', '💰',
                                        mf['chart_b64'], mf['narrative'], b, c)
    elif mf and mf.get('narrative'):
        sec2_html += _make_section_html('主力资金流向', '💰',
                                        '', mf['narrative'], mf['signal'], '#888')

    # ════════════════════════════════════════════════════════════════
    # § 3. 事件风险 & 深度（关键价位图 + 4个原始模块）
    # ════════════════════════════════════════════════════════════════
    kl = su = ei = ah = sv = None

    try:
        from key_levels import key_levels
        kl = key_levels(ts_code, trade_date)
        print(f'    [key_levels] {kl["signal"]}  当前{kl["current_price"]}', flush=True)
    except Exception as e:
        print(f'    [key_levels] FAILED: {e}', flush=True)

    su = ei = ah = sv = None

    try:
        from share_unlock import share_unlock
        su = share_unlock(ts_code, trade_date)
        print(f'    [share_unlock] {su["signal"]}', flush=True)
    except Exception as e:
        print(f'    [share_unlock] FAILED: {e}', flush=True)

    try:
        from equity_incentive import equity_incentive
        ei = equity_incentive(ts_code, trade_date)
        print(f'    [equity_incentive] {ei["signal"]}', flush=True)
    except Exception as e:
        print(f'    [equity_incentive] FAILED: {e}', flush=True)

    try:
        from ah_premium import ah_premium
        ah = ah_premium(ts_code, trade_date)
        print(f'    [ah_premium] {ah["signal"]}', flush=True)
    except Exception as e:
        print(f'    [ah_premium] FAILED: {e}', flush=True)

    try:
        from inst_survey import inst_survey
        sv = inst_survey(ts_code, trade_date)
        print(f'    [inst_survey] {sv["signal"]}', flush=True)
    except Exception as e:
        print(f'    [inst_survey] FAILED: {e}', flush=True)

    sec3_html = ''
    if kl and kl.get('chart_b64'):
        b, c = _kl_badge(kl['signal'])
        sec3_html += _make_section_html('关键价位（支撑/压力/行权价/解禁）', '📐',
                                        kl['chart_b64'], kl['narrative'], b, c)
    elif kl and kl.get('narrative'):
        sec3_html += _make_section_html('关键价位', '📐',
                                        '', kl['narrative'], kl.get('signal', ''), '#888')

    if su and su.get('chart_b64') and su['signal'] not in ('无数据', '无近期解禁'):
        b, c = _unlock_badge(su['signal'])
        sec3_html += _make_section_html('解禁倒计时日历', '📅',
                                        su['chart_b64'], su['narrative'], b, c)
    elif su and su.get('narrative'):
        sec3_html += _make_section_html('解禁倒计时日历', '📅',
                                        '', su['narrative'], su['signal'], '#28a745')

    if ei and ei.get('chart_b64') and ei['signal'] not in ('无数据', '无有效激励'):
        b, c = _eq_badge(ei['signal'])
        sec3_html += _make_section_html('股权激励行权价锚定', '🎯',
                                        ei['chart_b64'], ei['narrative'], b, c)
    elif ei and ei.get('narrative'):
        sec3_html += _make_section_html('股权激励行权价锚定', '🎯',
                                        '', ei['narrative'], ei['signal'], '#888')

    if ah and ah.get('chart_b64') and ah['signal'] not in ('无数据', '无H股对应'):
        b, c = _ah_badge(ah['signal'])
        sec3_html += _make_section_html('AH溢价实时跟踪', '🌐',
                                        ah['chart_b64'], ah['narrative'], b, c)
    elif ah and ah.get('narrative'):
        sec3_html += _make_section_html('AH溢价实时跟踪', '🌐',
                                        '', ah['narrative'], ah['signal'], '#888')

    if sv and sv.get('chart_b64') and sv['signal'] != '无数据':
        b, c = _surv_badge(sv['signal'])
        sec3_html += _make_section_html('机构调研频次分析', '🔍',
                                        sv['chart_b64'], sv['narrative'], b, c)
    elif sv and sv.get('narrative'):
        sec3_html += _make_section_html('机构调研频次分析', '🔍',
                                        '', sv['narrative'], sv['signal'], '#888')

    # ── Combine all sections ──────────────────────────────────────
    date_label = f'{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}'

    all_html = ''
    if sec0_html:
        all_html += _make_wrapper_section(
            f'量化模型信号（{date_label}）', '🤖', sec0_html)
    if sec1_html:
        all_html += _make_wrapper_section(
            f'市场微观信号（{date_label}）', '🔬', sec1_html)
    if sec2_html:
        all_html += _make_wrapper_section(
            '内部人 & 资金博弈', '💼', sec2_html)
    if sec3_html:
        all_html += _make_wrapper_section(
            '事件风险 & 深度', '⚠️', sec3_html)

    if not all_html:
        print(f'  [inject_signals] no cards generated, skipping', flush=True)
        return False

    ok = _do_inject(html_path, all_html)
    print(f'  [inject_signals] done in {time.time()-t0:.1f}s  success={ok}', flush=True)
    return ok
