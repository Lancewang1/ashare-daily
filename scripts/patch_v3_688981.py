"""
patch_v3_688981.py
==================
Apply v3 framework changes to the 688981 中芯国际 report HTML.

Changes applied:
  Ch1: Rename '市场核心分歧' → '市场核心关注'; restructure to 3 bull + 1 bear
  Ch2: Delete '历史同形态回顾' block
  Ch2: Add factor percentile chart (new card before grid2)
  Ch2: Replace key_levels chart with candlestick version
  Ch3: Replace capital dashboard chart with 7-signal version

Usage:
    python patch_v3_688981.py
"""

from __future__ import annotations
import re, sys, time
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

HTML_PATH = _SCRIPTS.parent / 'stocks' / '20260520_csi300_688981_中芯国际.html'
TS_CODE   = '688981.SH'
TRADE_DATE = '20260520'

# ── Read HTML ────────────────────────────────────────────────────────────────

def load_html() -> str:
    with open(HTML_PATH, encoding='utf-8') as f:
        return f.read()

def save_html(html: str) -> None:
    with open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'  [save] {len(html):,} chars → {HTML_PATH.name}')


# ── Ch1: Rename + restructure 市场核心关注 ───────────────────────────────────

CORE_FOCUS_NEW_HTML = '''<div class="tb-head">市场核心关注</div>
<p><strong>【交易主线】AI算力+国产替代双轮驱动，中芯国际是本轮行情的核心标的之一。</strong>
半导体板块近30天涨+57.74%（超沪深300整整49ppt），为全市场最强板块；中芯作为国内最大晶圆代工龙头，处于板块核心位置。</p>

<p><strong>多头逻辑三条：</strong></p>
<ul>
  <li><strong>①产能供不应求</strong>：中芯投资者关系活动披露，AI配套电源管理芯片产能供不应求，未来订单能见度高（2026-05-19）。全球AI算力投资持续扩张，晶圆代工需求处于上行周期。</li>
  <li><strong>②中芯北方整合获批</strong>：公司发行股份购买中芯北方49%股权已获证监会批复，预期整合后将进一步降低成本、提升先进制程产能利用率，是近期最重要的催化剂（2026-05-21）。</li>
  <li><strong>③板块扩产链验证</strong>：北方华创Q1营收同比+25.8%、股价近30天+49.8%——设备出货数据领先确认中芯及行业整体扩产节奏，板块共振广度达93.8%（173只成分股中）。</li>
</ul>

<p><strong>空头风险一条：</strong></p>
<ul>
  <li><strong>盈利压力+估值高位</strong>：Q1净利润同比-31.3%（营收虽+8.1%，但利润大幅下滑），PE处于5年82%分位、PB处于89%分位——盈利尚未出现拐点，当前价格已反映了较多乐观预期。高估值在情绪降温时回撤风险显著。</li>
</ul>

<p><strong>操作参考：</strong>若133元附近（近支撑区间）能守住且板块共振继续，短线动量策略可参与；
中芯北方并购正式实施或Q2财报净利润由负转正为关键催化剂；
若申万半导体指数回撤超10%或中芯日换手率骤降至1%以下，应及时降低暴露。</p>'''


def patch_ch1_core_focus(html: str) -> str:
    """Rename '市场核心分歧' → '市场核心关注' and replace content."""

    # Find the tb-head div
    old_head = '<div class="tb-head">市场核心分歧</div>'
    if old_head not in html:
        print('  [WARN] Ch1: 市场核心分歧 heading not found, skipping Ch1 patch')
        return html

    # Find the start of this tb block (the enclosing <div class="tb">)
    head_pos = html.index(old_head)
    # Walk backwards to find <div class="tb">
    tb_start = html.rfind('<div class="tb">', 0, head_pos)
    if tb_start == -1:
        print('  [WARN] Ch1: could not find enclosing tb div, skipping')
        return html

    # Find the end of this tb block (next </div> after closing </div> of tb)
    # The tb block ends at the matching </div>
    # Count div depth to find the matching close
    search_from = head_pos
    depth = 0
    i = tb_start
    while i < len(html):
        if html[i:i+5] == '<div ':
            depth += 1
            i += 5
        elif html[i:i+6] == '</div>':
            depth -= 1
            if depth == 0:
                tb_end = i + 6
                break
            i += 6
        else:
            i += 1
    else:
        tb_end = len(html)

    old_block = html[tb_start:tb_end]
    new_block  = f'<div class="tb">{CORE_FOCUS_NEW_HTML}</div>'
    result = html[:tb_start] + new_block + html[tb_end:]
    print('  [Ch1] 市场核心分歧 → 市场核心关注  (content rewritten)')
    return result


# ── Ch2: Delete 历史同形态回顾 ─────────────────────────────────────────────────

def delete_hist_pattern(html: str) -> str:
    """Remove the '历史同形态回顾' tb block entirely."""
    marker = '<div class="tb-head">历史同形态回顾</div>'
    if marker not in html:
        print('  [INFO] Ch2: 历史同形态回顾 not found (already removed)')
        return html

    pos = html.index(marker)
    tb_start = html.rfind('<div class="tb">', 0, pos)
    if tb_start == -1:
        print('  [WARN] Ch2: cannot find tb wrapper for 历史同形态回顾')
        return html

    # Find matching close
    depth = 0
    i = tb_start
    while i < len(html):
        if html[i:i+5] == '<div ':
            depth += 1
            i += 5
        elif html[i:i+6] == '</div>':
            depth -= 1
            if depth == 0:
                tb_end = i + 6
                break
            i += 6
        else:
            i += 1
    else:
        tb_end = len(html)

    removed_len = tb_end - tb_start
    result = html[:tb_start] + html[tb_end:]
    print(f'  [Ch2] 历史同形态回顾 deleted ({removed_len:,} chars)')
    return result


# ── Ch2: Inject factor percentile chart ──────────────────────────────────────

def make_card_html(icon: str, title: str, badge_txt: str, badge_col: str,
                   chart_b64: str, narrative: str) -> str:
    badge = (f'<span class="badge" style="background:{badge_col}">{badge_txt}</span>'
             if badge_txt else '')
    chart = (f'<div class="card-chart"><img src="data:image/png;base64,{chart_b64}"'
             f' alt="{title}"/></div>' if chart_b64 else '')
    border = badge_col if badge_col not in ('#888', '') else '#ddd'
    narrative_html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', narrative)
    return (
        f'<div class="card">'
        f'<div class="card-head">'
        f'<span class="card-icon">{icon}</span>'
        f'<span class="card-title">{title}</span>'
        f'{"  " + badge if badge else ""}'
        f'</div>'
        f'{chart}'
        f'<div class="card-body" style="border-left-color:{border}">'
        f'{narrative_html}'
        f'</div>'
        f'</div>'
    )


def inject_factor_percentile(html: str, fp_result: dict) -> str:
    """Insert factor percentile card before the grid2 containing 集合竞价+关键价位."""
    if not fp_result.get('chart_b64'):
        print('  [WARN] factor_percentile: no chart, skipping inject')
        return html

    # Find the grid2 div that contains 集合竞价 and 关键价位
    grid2_marker = '<div class="grid2">'
    # Find the first grid2 in ch2 (ch2 starts around ch2Start)
    ch2_start = html.index('<section class="chapter" id="ch2"')
    ch3_start = html.index('<section class="chapter" id="ch3"')

    grid2_pos = html.find(grid2_marker, ch2_start, ch3_start)
    if grid2_pos == -1:
        print('  [WARN] factor_percentile: grid2 not found in ch2')
        return html

    # Badge color based on average pct
    factors = fp_result.get('factors', [])
    avg_pct = sum(f['pct'] for f in factors) / len(factors) if factors else 50
    badge_col = '#2ca02c' if avg_pct >= 65 else ('#d62728' if avg_pct <= 35 else '#888')
    badge_txt = f'{avg_pct:.0f}%ile 综合'

    card_html = make_card_html(
        '📊', '量化因子百分位（自身1年历史）',
        badge_txt, badge_col,
        fp_result['chart_b64'],
        fp_result['narrative'],
    )

    result = html[:grid2_pos] + card_html + html[grid2_pos:]
    print(f'  [Ch2] factor_percentile card injected ({len(card_html):,} chars)')
    return result


# ── Ch2: Replace key_levels chart ────────────────────────────────────────────

def replace_key_levels_chart(html: str, kl_result: dict) -> str:
    """Replace the existing key_levels chart base64 with the new candlestick version."""
    if not kl_result.get('chart_b64'):
        print('  [WARN] key_levels: no new chart, skipping')
        return html

    # Find the 关键价位 card: look for its title span
    title_marker = '<span class="card-title">关键价位'
    if title_marker not in html:
        print('  [WARN] key_levels: card title not found')
        return html

    title_pos = html.index(title_marker)

    # Find the next img tag (the chart) after the card head
    img_start = html.find('data:image/png;base64,', title_pos)
    if img_start == -1:
        # No chart yet — insert chart div before card-body
        body_marker = '<div class="card-body"'
        body_pos = html.find(body_marker, title_pos)
        if body_pos == -1:
            print('  [WARN] key_levels: cannot find card-body for chart inject')
            return html
        new_chart = (f'<div class="card-chart"><img src="data:image/png;base64,'
                     f'{kl_result["chart_b64"]}" alt="关键价位"/></div>')
        result = html[:body_pos] + new_chart + html[body_pos:]
        print('  [Ch2] key_levels chart injected (new)')
        return result

    # Find the end of the base64 string (next quote)
    b64_start = img_start + len('data:image/png;base64,')
    b64_end   = html.index('"', b64_start)
    old_b64   = html[b64_start:b64_end]

    new_html = html[:b64_start] + kl_result['chart_b64'] + html[b64_end:]

    # Also update the narrative in the card-body
    if kl_result.get('narrative'):
        # Find card-body after the img
        cb_start = new_html.find('<div class="card-body"', title_pos)
        if cb_start != -1:
            cb_end = new_html.find('</div>', cb_start)
            if cb_end != -1:
                narr_html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>',
                                   kl_result['narrative'])
                cb_inner_start = new_html.index('>', cb_start) + 1
                new_html = (new_html[:cb_inner_start]
                            + narr_html
                            + new_html[cb_end:])

    print(f'  [Ch2] key_levels chart replaced ({len(old_b64):,} → {len(kl_result["chart_b64"]):,} chars)')
    return new_html


# ── Ch3: Replace capital_dashboard chart ─────────────────────────────────────

def replace_capital_dashboard_chart(html: str, cd_result: dict) -> str:
    """Replace the capital radar chart with the new 7-signal version."""
    if not cd_result.get('chart_b64'):
        print('  [WARN] capital_dashboard: no new chart')
        return html

    # Find the capital dashboard card title
    title_marker = '<span class="card-title">资金博弈雷达'
    if title_marker not in html:
        print('  [WARN] capital_dashboard: radar card title not found')
        return html

    title_pos = html.index(title_marker)
    img_start = html.find('data:image/png;base64,', title_pos)
    if img_start == -1:
        print('  [WARN] capital_dashboard: no chart img found in radar card')
        return html

    b64_start = img_start + len('data:image/png;base64,')
    b64_end   = html.index('"', b64_start)
    old_b64   = html[b64_start:b64_end]

    new_html = html[:b64_start] + cd_result['chart_b64'] + html[b64_end:]

    # Update badge
    badge_txt = cd_result.get('signal', '')
    n_metrics = cd_result.get('n_metrics', 0)
    badge_col = '#28a745' if '多' in badge_txt else ('#dc3545' if '空' in badge_txt else '#888')
    if badge_txt:
        # Find the badge span after title_pos and update it
        badge_start = new_html.find('<span class="badge"', title_pos)
        if badge_start != -1:
            badge_close = new_html.index('</span>', badge_start) + 7
            new_badge = (f'<span class="badge" style="background:{badge_col}">'
                         f'{badge_txt}（{n_metrics}项）</span>')
            new_html = new_html[:badge_start] + new_badge + new_html[badge_close:]

    # Update narrative in card-body
    if cd_result.get('narrative'):
        cb_start = new_html.find('<div class="card-body"', title_pos)
        if cb_start != -1:
            cb_end = new_html.find('</div>', cb_start)
            if cb_end != -1:
                narr_html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>',
                                   cd_result['narrative'])
                cb_inner_start = new_html.index('>', cb_start) + 1
                new_html = (new_html[:cb_inner_start]
                            + narr_html
                            + new_html[cb_end:])

    print(f'  [Ch3] capital_dashboard chart replaced  '
          f'({cd_result["n_metrics"]}项指标  {cd_result.get("composite_pct")}%ile)')
    return new_html


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f'\n{"="*60}')
    print(f'patch_v3_688981  ts_code={TS_CODE}  date={TRADE_DATE}')
    print('='*60)

    html = load_html()
    print(f'  Loaded: {len(html):,} chars')

    # ── 1. Ch1: 市场核心关注 ─────────────────────────────────────────────────
    html = patch_ch1_core_focus(html)

    # ── 2. Ch2: 删除历史同形态 ───────────────────────────────────────────────
    html = delete_hist_pattern(html)

    # ── 3. Regenerate factor_percentile chart ────────────────────────────────
    print('\n  [factor_percentile] generating...')
    try:
        from factor_percentile import factor_percentile
        fp_result = factor_percentile(TS_CODE, TRADE_DATE)
        print(f'    → {len(fp_result.get("factors", []))} factors  '
              f'chart={bool(fp_result.get("chart_b64"))}')
        html = inject_factor_percentile(html, fp_result)
    except Exception as e:
        print(f'    FAILED: {e}')
        import traceback; traceback.print_exc()

    # ── 4. Regenerate key_levels with candlestick ────────────────────────────
    print('\n  [key_levels] generating candlestick chart...')
    try:
        from key_levels import key_levels
        kl_result = key_levels(TS_CODE, TRADE_DATE)
        print(f'    → signal={kl_result["signal"]}  chart={bool(kl_result.get("chart_b64"))}')
        html = replace_key_levels_chart(html, kl_result)
    except Exception as e:
        print(f'    FAILED: {e}')
        import traceback; traceback.print_exc()

    # ── 5. Regenerate capital_dashboard (7 signals) ──────────────────────────
    print('\n  [capital_dashboard] generating 7-signal radar...')
    try:
        from capital_dashboard import capital_dashboard
        cd_result = capital_dashboard(TS_CODE, TRADE_DATE)
        print(f'    → {cd_result["n_metrics"]} metrics  signal={cd_result["signal"]}  '
              f'chart={bool(cd_result.get("chart_b64"))}')
        html = replace_capital_dashboard_chart(html, cd_result)
    except Exception as e:
        print(f'    FAILED: {e}')
        import traceback; traceback.print_exc()

    # ── Save ─────────────────────────────────────────────────────────────────
    save_html(html)
    print('\n  Done.')


if __name__ == '__main__':
    main()
