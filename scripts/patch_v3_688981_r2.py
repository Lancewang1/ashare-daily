"""
patch_v3_688981_r2.py
=====================
Round-2 edits to the 688981 中芯国际 report HTML.

Changes:
  1. Delete 后续追踪 — T+1 / 5 日实际收益 section
  2. Fix ch5 broken img tags (remove truncated base64 fragments)
  3. Expand + reposition 供应链上下游 Lead-Lag to after company intro
  4. Update 集合竞价 narrative (explain low-open + intraday rally = bullish pattern)
  5. CSS overhaul — more elegant, readable layout
  6. Regenerate capital_dashboard chart (融券/融资比 fix + 券商评级 slot)
  7. Regenerate factor_percentile chart (radar version)

Usage:
    python patch_v3_688981_r2.py
"""

from __future__ import annotations
import re, sys, time
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

HTML_PATH  = _SCRIPTS.parent / 'stocks' / '20260520_csi300_688981_中芯国际.html'
TS_CODE    = '688981.SH'
TRADE_DATE = '20260520'


def load_html() -> str:
    with open(HTML_PATH, encoding='utf-8') as f:
        return f.read()

def save_html(html: str) -> None:
    with open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'  [save] {len(html):,} chars → {HTML_PATH.name}')


# ── Helper: find matching </div> ──────────────────────────────────────────────

def _find_tb_end(html: str, tb_start: int) -> int:
    depth = 0
    i = tb_start
    while i < len(html):
        if html[i:i+5] == '<div ':
            depth += 1
            i += 5
        elif html[i:i+6] == '</div>':
            depth -= 1
            if depth == 0:
                return i + 6
            i += 6
        else:
            i += 1
    return len(html)


# ── 1. Delete 后续追踪 block ───────────────────────────────────────────────────

def delete_followup_tracking(html: str) -> str:
    marker = '<div class="tb-head">后续追踪'
    if marker not in html:
        print('  [INFO] 后续追踪 not found (already removed)')
        return html
    pos     = html.index(marker)
    tb_start = html.rfind('<div class="tb">', 0, pos)
    if tb_start == -1:
        # try without space after div
        tb_start = html.rfind('<div class="tb">', 0, pos)
    # The 后续追踪 might share a tb block with the 5日观察清单; only delete
    # from the 后续追踪 tb-head onwards to end of its enclosing block.
    # Check if there's another tb-head before it in the same tb block.
    prev_tbhead = html.rfind('<div class="tb-head">', tb_start, pos)
    if prev_tbhead != -1 and prev_tbhead > tb_start:
        # There's another section in this tb block before 后续追踪
        # Just remove from the 后续追踪 heading to the end of the tb block
        tb_end = _find_tb_end(html, tb_start)
        new_block = html[tb_start:pos] + '</div>'
        result = html[:tb_start] + new_block + html[tb_end:]
        print(f'  [R2.1] 后续追踪 removed from shared tb block')
        return result
    else:
        # Entire tb block is the 后续追踪 block
        tb_end = _find_tb_end(html, tb_start)
        removed = tb_end - tb_start
        result = html[:tb_start] + html[tb_end:]
        print(f'  [R2.1] 后续追踪 block deleted ({removed:,} chars)')
        return result


# ── 2. Fix broken img tags in ch5 ────────────────────────────────────────────

def fix_ch5_broken_imgs(html: str) -> str:
    """Remove malformed <img> tags with truncated base64 (< 1000 chars)."""
    ch5_start = html.find('<section class="chapter" id="ch5"')
    if ch5_start == -1:
        print('  [WARN] ch5 not found')
        return html

    removed = 0
    # Work backwards so positions stay valid
    img_positions = []
    pos = ch5_start
    while True:
        p = html.find('data:image/png;base64,', pos)
        if p == -1:
            break
        b64_start = p + 22
        b64_end = html.find('"', b64_start)
        b64_len = b64_end - b64_start if b64_end != -1 else -1
        img_open = html.rfind('<img', 0, p)
        img_positions.append((img_open, p, b64_len))
        pos = p + 1

    # Identify broken ones (b64 < 2000 chars = obviously truncated fragment)
    broken = [(io, dp, bl) for io, dp, bl in img_positions if 0 < bl < 2000]
    if not broken:
        print('  [INFO] No broken imgs found in ch5')
        return html

    # Remove in reverse order to preserve positions
    for img_open, data_pos, b64_len in reversed(broken):
        # The broken img ends just before the b64_end quote OR before next <img
        b64_end_quote = html.find('"', data_pos + 22)
        # But if the broken img has no closing quote, find where it actually ends
        # (the next `<img` start)
        next_img = html.find('<img', img_open + 1)
        if next_img != -1 and next_img < b64_end_quote:
            # Remove from <img to start of next <img
            removed_block = html[img_open:next_img]
            html = html[:img_open] + html[next_img:]
            removed += 1
            print(f'  [R2.2] Removed broken img at {img_open} ({len(removed_block)} chars, b64_len={b64_len})')
        else:
            # Has a closing quote — just a short but properly formed img; keep it
            print(f'  [INFO] Short img at {img_open} has closing quote, keeping')

    return html


# ── 3. Expand + reposition LeadLag ───────────────────────────────────────────

LEADLAG_NEW_CONTENT = '''<div class="tb-head">供应链上下游 Lead-Lag 分析</div>
<p>
Lead-Lag（领先-滞后）分析通过研究供应链伙伴的营收周期，提前判断中芯国际的业务趋势。
晶圆代工处于芯片产业链<strong>中游</strong>：上游是设备（北方华创等）、下游是芯片设计/成品（立讯精密、圣邦股份等）。
设备采购放量 → 扩产进行 → 代工收入上升 → 下游芯片放量验证，通常需要 2-4 个季度完成传导。
</p>

<p><strong>▶ 上游领先指标（北方华创 002371）</strong></p>
<p>
北方华创是中芯最核心的国产设备供应商，历史数据表明其营收增速<strong>领先中芯约 2 个季度</strong>，相关系数 <strong>+0.63</strong>。
当北方华创接到大批新订单，意味着中芯正在下单扩产——设备安装调试需要约 2 个季度，之后才体现在中芯的产能与营收数据中。
</p>
<p>
<strong>当前信号（2026Q1）</strong>：北方华创 Q1 营收同比 <strong>+25.8%</strong>，近 30 日股价 +49.8%，为过去两年罕见高增速。
代入历史领先关系，预示中芯 <strong>2026Q3–Q4</strong> 可能进入新一轮产能扩张，对后续两季度盈利形成先行支撑。
</p>

<p><strong>▶ 下游滞后指标（圣邦股份 / 立讯精密）</strong></p>
<p>
<strong>圣邦股份</strong>（模拟芯片设计，代表芯片成品端）营收增速<strong>滞后中芯约 2 个季度</strong>——这是典型的下游验证信号：
圣邦放量意味着下游终端市场正在消化中芯的产出，景气传导完整。
</p>
<p>
<strong>立讯精密</strong>营收增速与中芯呈<strong>负相关（-0.88）</strong>，可能反映两者在 PCB/封装环节存在供应链替代或客户结构差异，
信号意义较复杂，<strong>不宜直接作为正向领先指标</strong>，建议结合季报趋势独立判断。
</p>

<p><strong>▶ 当前综合读数</strong></p>
<ul>
  <li>上游北方华创已发出 <strong>强烈扩产信号</strong>（Q1 +25.8% + 股价领涨板块）</li>
  <li>中芯北方整合获批（2026-05-21）直接强化扩产预期，是上游信号的制度性确认</li>
  <li>AI 算力需求侧持续扩张，下游消化压力较小</li>
  <li>盈利转正节点预期在 2026Q3（下游验证阶段）</li>
</ul>
<p>
<strong>总结</strong>：当前处于"上游信号已响 · 中游扩产进行时 · 下游验证待观察"结构，
历史上此类组合多次出现于中芯股价上涨中段，而非起点——说明市场已在 price-in 扩产逻辑，
<strong>中短期驱动力仍在，但安全边际依赖 Q2 财报净利润环比改善的验证。</strong>
</p>'''


def expand_and_reposition_leadlag(html: str) -> str:
    """
    1. Remove current LeadLag tb-head + its content from current position
    2. Insert expanded LeadLag right after 营收业务结构 section (before 财务景气度)
    """
    ch5_start = html.find('<section class="chapter" id="ch5"')
    if ch5_start == -1:
        print('  [WARN] ch5 not found for LeadLag')
        return html

    # ── Find and remove current LeadLag section (tb-head + content until next tb-head) ──
    ll_marker = '<div class="tb-head">供应链上下游 Lead-Lag'
    if ll_marker not in html:
        print('  [INFO] LeadLag not found, will insert fresh')
        ll_block_html = ''
        ll_block_start = ll_block_end = -1
    else:
        ll_pos   = html.index(ll_marker)
        # Find next tb-head (to delimit this block's content)
        next_head = html.find('<div class="tb-head">', ll_pos + len(ll_marker))
        # Remove from ll_marker through start of next_head (exclusive)
        ll_block_start = ll_pos
        ll_block_end   = next_head if next_head != -1 else html.find('</div>', ll_pos + 500)
        removed_len = ll_block_end - ll_block_start
        html = html[:ll_block_start] + html[ll_block_end:]
        print(f'  [R2.3] Removed old LeadLag ({removed_len} chars) from position {ll_block_start}')

    # ── Find insertion point: right after 財務景氣度 tb-head (before it) ──
    # We want LeadLag to appear BEFORE 財務景氣度 in the new order
    # Locate the 财务景气度 tb-head in current html (positions may have shifted)
    fin_marker = '<div class="tb-head">财务景气度</div>'
    if fin_marker not in html:
        print('  [WARN] 财务景气度 not found; inserting at end of ch5')
        end_sec = html.find('</section>', ch5_start)
        insert_pos = end_sec if end_sec != -1 else len(html)
    else:
        insert_pos = html.index(fin_marker)

    html = html[:insert_pos] + LEADLAG_NEW_CONTENT + '\n' + html[insert_pos:]
    print(f'  [R2.3] Expanded LeadLag inserted before 财务景气度 ({len(LEADLAG_NEW_CONTENT)} chars)')
    return html


# ── 4. Update 集合竞价 narrative ──────────────────────────────────────────────

AUCTION_NARRATIVE_NEW = (
    '集合竞价显示688981以<strong>-0.69%低开</strong>（118.00元 vs 前收118.82元），竞价量仅日均的0.1倍，短期买盘尚未集结。'
    '然而当日全天大涨+12.62%（收133.81元）——<strong>低开高走是明确的强势形态</strong>：空头无力砸盘，多头在开盘后迅速主导全天。'
    '这种"集合竞价弱 → 连续竞价强"的背离，往往出现在消息催化初期（本例为中芯北方整合获批），机构未在竞价期抢货而在盘中低位吸筹。'
    '结论：数据本身完全准确，弱开盘并非看空信号，反而是全天大阳线的前奏。'
)


def update_auction_narrative(html: str) -> str:
    """Replace the 集合竞价 card-body narrative text."""
    # Find the auction card by title
    title_marker = '<span class="card-title">集合竞价'
    if title_marker not in html:
        print('  [WARN] 集合竞价 card not found')
        return html

    title_pos = html.index(title_marker)
    # Find card-body after title
    cb_start = html.find('<div class="card-body"', title_pos)
    if cb_start == -1:
        print('  [WARN] 集合竞价 card-body not found')
        return html

    cb_inner_start = html.index('>', cb_start) + 1
    cb_end = html.find('</div>', cb_inner_start)
    if cb_end == -1:
        print('  [WARN] 集合竞价 card-body closing tag not found')
        return html

    old_text = html[cb_inner_start:cb_end]
    html = html[:cb_inner_start] + AUCTION_NARRATIVE_NEW + html[cb_end:]
    print(f'  [R2.4] 集合竞价 narrative updated ({len(old_text)} → {len(AUCTION_NARRATIVE_NEW)} chars)')
    return html


# ── 5. CSS overhaul ───────────────────────────────────────────────────────────

CSS_ENHANCEMENT = '''
/* ══ R2 Layout Enhancement ══════════════════════════════════════════════════ */

/* Smoother page background gradient */
body {
    background: linear-gradient(160deg, #eef1f6 0%, #e8ecf2 100%);
    min-height: 100vh;
}

/* Elevated card aesthetics */
.card {
    border-radius: 12px;
    box-shadow: 0 2px 12px rgba(26,63,138,.08), 0 1px 3px rgba(0,0,0,.04);
    transition: box-shadow .18s ease;
    border: 1px solid rgba(230,234,245,.8);
    margin-bottom: 14px;
}
.card:hover {
    box-shadow: 0 4px 20px rgba(26,63,138,.13), 0 2px 6px rgba(0,0,0,.06);
}

/* tb blocks — softer look distinct from cards */
.tb {
    background: #fff;
    border-radius: 12px;
    box-shadow: 0 1px 8px rgba(0,0,0,.05);
    border: 1px solid rgba(230,234,245,.7);
    padding: 18px 20px;
    margin-bottom: 14px;
    line-height: 1.8;
}
.tb-head {
    font-size: 13.5px;
    color: #1a3463;
    font-weight: 700;
    margin: 16px 0 8px;
    padding: 4px 0 4px 12px;
    border-left: 4px solid #1a6fc4;
    background: linear-gradient(90deg, rgba(26,111,196,.06) 0%, transparent 100%);
    border-radius: 0 4px 4px 0;
}
.tb-head:first-child { margin-top: 0; }

/* Chapter section spacing */
.chapter { margin: 22px 16px 0; }

/* Chapter heading bar */
.ch-head {
    font-size: 15.5px;
    background: linear-gradient(90deg, #1a1a2e 0%, #2b3a6e 100%);
    color: white;
    padding: 11px 18px;
    border-radius: 10px;
    margin-bottom: 16px;
    letter-spacing: .02em;
    box-shadow: 0 3px 10px rgba(26,26,46,.18);
}

/* Nav bar refinement */
.ch-nav {
    background: white;
    box-shadow: 0 2px 12px rgba(0,0,0,.08);
    border-bottom: none;
}
.ch-nav a {
    font-size: 12.5px;
    padding: 11px 15px;
    color: #444;
    border-bottom: 3px solid transparent;
    font-weight: 500;
}
.ch-nav a:hover { color: #1a6fc4; border-bottom-color: #1a6fc4; background: #f3f6ff; }

/* card-body — warmer look */
.card-body {
    font-size: 13px;
    background: linear-gradient(135deg, #f8f9fc 0%, #f4f6fb 100%);
    padding: 12px 16px;
    border-radius: 7px;
    border-left: 4px solid #1a6fc4;
    line-height: 1.78;
    color: #3a3a4a;
}

/* card-title refinement */
.card-title {
    font-size: 14px;
    font-weight: 700;
    color: #1a2d63;
    letter-spacing: .01em;
}

/* Badge polish */
.badge {
    padding: 3px 9px;
    border-radius: 5px;
    font-size: 11.5px;
    font-weight: 700;
    letter-spacing: .03em;
    box-shadow: 0 1px 3px rgba(0,0,0,.15);
}

/* sig-box enhancements */
.sig-box {
    border-radius: 10px;
    border: 1px solid rgba(230,234,245,.9);
    background: white;
    box-shadow: 0 1px 5px rgba(0,0,0,.05);
}
.sig-box .lbl { color: #7a8aaa; font-size: 11px; font-weight: 500; }
.sig-box .val { font-size: 13.5px; font-weight: 800; }

/* grid2 gap */
.grid2 { gap: 14px; }

/* Paragraph spacing in tb */
.tb p { margin: 7px 0; }
.tb ul { margin: 6px 0 6px 20px; }
.tb li { margin: 4px 0; font-size: 13.5px; }

/* card-chart image border */
.card-chart img {
    max-width: 100%;
    border-radius: 8px;
    box-shadow: 0 1px 6px rgba(0,0,0,.08);
}

/* Hero polish */
.hero {
    background: linear-gradient(135deg, #12172e 0%, #1e3060 60%, #2a4a8c 100%);
    padding: 36px 28px 28px;
    border-radius: 0 0 16px 16px;
    box-shadow: 0 4px 20px rgba(12,17,46,.3);
}
.hero h1 { font-size: 28px; letter-spacing: -.01em; }
'''


def apply_css_overhaul(html: str) -> str:
    """Inject CSS enhancement block before </head>."""
    head_end = html.find('</head>')
    if head_end == -1:
        print('  [WARN] </head> not found; appending CSS at document start')
        return f'<style>{CSS_ENHANCEMENT}</style>' + html

    insert = f'<style>{CSS_ENHANCEMENT}</style>\n'
    html = html[:head_end] + insert + html[head_end:]
    print(f'  [R2.5] CSS enhancement injected ({len(insert)} chars)')
    return html


# ── 6 & 7. Regenerate capital_dashboard + factor_percentile charts ─────────────

def _replace_b64_in_card(html: str, card_title: str, new_b64: str) -> str:
    """Replace base64 image in a card identified by card-title text."""
    pos = html.find(card_title)
    if pos == -1:
        return html
    img_start = html.find('data:image/png;base64,', pos)
    if img_start == -1:
        return html
    b64_start = img_start + 22
    b64_end   = html.index('"', b64_start)
    return html[:b64_start] + new_b64 + html[b64_end:]


def regenerate_capital_dashboard(html: str) -> str:
    print('\n  [capital_dashboard] regenerating...')
    try:
        from capital_dashboard import capital_dashboard
        r = capital_dashboard(TS_CODE, TRADE_DATE)
        print(f'    → {r["n_metrics"]} metrics  {r["composite_pct"]}%ile  signal={r["signal"]}')

        if not r.get('chart_b64'):
            print('    [WARN] no chart generated')
            return html

        # Replace chart
        title_marker = '<span class="card-title">资金博弈雷达'
        if title_marker not in html:
            print('    [WARN] 资金博弈雷达 card not found in HTML')
            return html

        title_pos = html.index(title_marker)
        img_start = html.find('data:image/png;base64,', title_pos)
        if img_start == -1:
            print('    [WARN] no img in 资金博弈雷达 card')
            return html

        b64_start = img_start + 22
        b64_end   = html.index('"', b64_start)
        html = html[:b64_start] + r['chart_b64'] + html[b64_end:]

        # Update badge
        badge_col = '#28a745' if '多' in r['signal'] else ('#dc3545' if '空' in r['signal'] else '#888')
        badge_start = html.find('<span class="badge"', title_pos)
        if badge_start != -1:
            badge_close = html.index('</span>', badge_start) + 7
            new_badge = (f'<span class="badge" style="background:{badge_col}">'
                         f'{r["signal"]}（{r["n_metrics"]}项）</span>')
            html = html[:badge_start] + new_badge + html[badge_close:]

        # Update narrative
        if r.get('narrative'):
            cb_start = html.find('<div class="card-body"', title_pos)
            if cb_start != -1:
                cb_end = html.find('</div>', cb_start)
                narr_html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', r['narrative'])
                cb_inner  = html.index('>', cb_start) + 1
                html = html[:cb_inner] + narr_html + html[cb_end:]

        print(f'    → capital_dashboard chart replaced  ({r["composite_pct"]}%ile  {r["signal"]})')
    except Exception as e:
        print(f'    FAILED: {e}')
        import traceback; traceback.print_exc()
    return html


def regenerate_factor_percentile(html: str) -> str:
    print('\n  [factor_percentile] regenerating...')
    try:
        from factor_percentile import factor_percentile
        r = factor_percentile(TS_CODE, TRADE_DATE)
        factors = r.get('factors', [])
        print(f'    → {len(factors)} factors  chart={bool(r.get("chart_b64"))}')

        if not r.get('chart_b64'):
            print('    [WARN] no chart generated')
            return html

        card_title = '量化因子百分位（自身1年历史）'
        if card_title not in html:
            print(f'    [WARN] factor card "{card_title}" not found in HTML')
            return html

        title_pos = html.index(card_title)
        img_start = html.find('data:image/png;base64,', title_pos)
        if img_start == -1:
            print('    [WARN] no img in factor card')
            return html

        b64_start = img_start + 22
        b64_end   = html.index('"', b64_start)
        html = html[:b64_start] + r['chart_b64'] + html[b64_end:]

        # Update badge
        avg_pct = sum(f['pct'] for f in factors) / len(factors) if factors else 50
        badge_col = '#2ca02c' if avg_pct >= 65 else ('#d62728' if avg_pct <= 35 else '#888')
        badge_start = html.find('<span class="badge"', title_pos)
        if badge_start != -1:
            badge_close = html.index('</span>', badge_start) + 7
            new_badge = (f'<span class="badge" style="background:{badge_col}">'
                         f'{avg_pct:.0f}%ile 综合</span>')
            html = html[:badge_start] + new_badge + html[badge_close:]

        # Update narrative
        if r.get('narrative'):
            cb_start = html.find('<div class="card-body"', title_pos)
            if cb_start != -1:
                cb_end = html.find('</div>', cb_start)
                narr_html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', r['narrative'])
                cb_inner  = html.index('>', cb_start) + 1
                html = html[:cb_inner] + narr_html + html[cb_end:]

        print(f'    → factor_percentile chart replaced  ({avg_pct:.0f}%ile)')
    except Exception as e:
        print(f'    FAILED: {e}')
        import traceback; traceback.print_exc()
    return html


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f'\n{"="*60}')
    print(f'patch_v3_688981_r2  ts_code={TS_CODE}  date={TRADE_DATE}')
    print('='*60)

    html = load_html()
    print(f'  Loaded: {len(html):,} chars')

    html = delete_followup_tracking(html)
    html = fix_ch5_broken_imgs(html)
    html = expand_and_reposition_leadlag(html)
    html = update_auction_narrative(html)
    html = apply_css_overhaul(html)
    html = regenerate_capital_dashboard(html)
    html = regenerate_factor_percentile(html)

    save_html(html)
    print('\n  Done.')


if __name__ == '__main__':
    main()
