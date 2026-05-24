"""
patch_v3_688981_r3.py
=====================
Round-3 fixes for 688981 中芯国际 report.

Issues addressed:
  1. ch2: Remove 2 broken imgs (量化模型为何选中 + 当前阶段定性, no closing quote)
  2. ch2: Remove 1 broken img (量能节奏, no closing quote)  [was Issue 3 in ch3]
  3. ch3: Move 资金面一句话 text to before the radar card
  4. ch4: Remove 3 broken imgs (盈利景气度/大盘BETA/大类资产传导)
  5. ch4: Regenerate 估值分位 chart with valuation_percentile.py
  6. ch5: Add Lead-Lag correlation chart to the Lead-Lag section
  7. Regenerate factor_percentile (now 7 factors: +量能节奏)
"""

from __future__ import annotations
import re, sys, io, base64, time
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


# ── Helper ────────────────────────────────────────────────────────────────────

def _remove_broken_img(html: str, img_open_pos: int, label: str) -> str:
    """Remove a broken <img> tag that has no closing quote (next tag starts before closing quote)."""
    p = html.find('data:image/png;base64,', img_open_pos)
    if p == -1:
        print(f'  [WARN] {label}: data URI not found from {img_open_pos}')
        return html
    b64_end_quote = html.find('"', p + 22)
    next_tag      = html.find('<', p + 22)
    if next_tag != -1 and (b64_end_quote == -1 or next_tag < b64_end_quote):
        # No valid closing quote — remove from img_open to next_tag
        removed = html[img_open_pos:next_tag]
        html = html[:img_open_pos] + html[next_tag:]
        print(f'  [R3] Removed broken img "{label}" ({len(removed)} chars)')
    else:
        # Has a closing quote but still too short (< 2000)
        img_close = html.find('>', b64_end_quote)
        img_close += 1
        removed = html[img_open_pos:img_close]
        html = html[:img_open_pos] + html[img_close:]
        print(f'  [R3] Removed short img "{label}" ({len(removed)} chars)')
    return html


def _locate_broken_imgs(html: str, ch_start: int, ch_end: int, max_b64: int = 2000):
    """Return list of (img_open_pos, b64_len, last_head) for broken imgs in range."""
    results = []
    pos = ch_start
    while pos < ch_end:
        p = html.find('data:image/png;base64,', pos)
        if p == -1 or p >= ch_end:
            break
        b64_start = p + 22
        b64_end   = html.find('"', b64_start)
        b64_len   = (b64_end - b64_start) if b64_end != -1 else -1
        img_open  = html.rfind('<img', ch_start, p)
        if 0 < b64_len < max_b64:
            # Find last tb-head before this img
            h = html.rfind('<div class="tb-head">', ch_start, img_open)
            if h != -1:
                h_end = html.find('</div>', h)
                label = html[h + 21:h_end]
            else:
                label = '?'
            results.append((img_open, b64_len, label))
        pos = p + 1
    return results


# ── 1. Fix ch2 broken imgs ────────────────────────────────────────────────────

def fix_ch2_broken_imgs(html: str) -> str:
    ch2_start = html.find('<section class="chapter" id="ch2"')
    ch3_start = html.find('<section class="chapter" id="ch3"')
    if ch2_start == -1:
        print('  [WARN] ch2 not found')
        return html

    broken = _locate_broken_imgs(html, ch2_start, ch3_start)
    if not broken:
        print('  [INFO] No broken imgs in ch2')
        return html

    # Remove in reverse order to preserve positions
    for img_open, b64_len, label in reversed(broken):
        html = _remove_broken_img(html, img_open, label)
    return html


# ── 2. Fix ch3 broken img (量能节奏) ─────────────────────────────────────────

def fix_ch3_broken_img(html: str) -> str:
    ch3_start = html.find('<section class="chapter" id="ch3"')
    ch4_start = html.find('<section class="chapter" id="ch4"')
    if ch3_start == -1:
        return html

    broken = _locate_broken_imgs(html, ch3_start, ch4_start)
    if not broken:
        print('  [INFO] No broken imgs in ch3')
        return html

    for img_open, b64_len, label in reversed(broken):
        html = _remove_broken_img(html, img_open, label)
    return html


# ── 3. Move 资金面一句话 to before radar ──────────────────────────────────────

def move_zijin_summary(html: str) -> str:
    """Extract '资金面一句话' text and place it right before the 资金博弈雷达 card."""
    head_marker = '<div class="tb-head">资金面一句话</div>'
    if head_marker not in html:
        print('  [WARN] 资金面一句话 not found')
        return html

    # Find the paragraph after this heading
    h_pos = html.index(head_marker)
    p_start = html.find('<p>', h_pos)
    p_end   = html.find('</p>', p_start) + 4 if p_start != -1 else -1
    if p_start == -1 or p_end <= p_start:
        print('  [WARN] 资金面一句话 paragraph not found')
        return html

    summary_text = html[p_start:p_end]  # e.g. <p>大幅放量突破...</p>

    # Remove the heading + paragraph from their current location
    remove_start = h_pos
    remove_end   = p_end
    html = html[:remove_start] + html[remove_end:]

    # Find the radar card to insert before it
    radar_marker = '<div class="card"><div class="card-head"><span class="card-icon">'
    # More specific: find the card containing 资金博弈雷达
    radar_title = '<span class="card-title">资金博弈雷达'
    if radar_title not in html:
        print('  [WARN] 资金博弈雷达 card not found')
        return html

    rt_pos   = html.index(radar_title)
    card_start = html.rfind('<div class="card">', 0, rt_pos)

    # Build the summary block
    clean_text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', summary_text)
    summary_block = (
        '<div style="background:#eef3fb;border-left:4px solid #1a6fc4;'
        'padding:10px 16px;border-radius:6px;margin-bottom:12px;'
        'font-size:13.5px;color:#333;line-height:1.7;">'
        '<span style="font-weight:700;color:#1a3463;margin-right:6px;">资金面一句话</span>'
        + clean_text.replace('<p>', '').replace('</p>', '') +
        '</div>\n'
    )

    html = html[:card_start] + summary_block + html[card_start:]
    print(f'  [R3.3] 资金面一句话 moved before 资金博弈雷达 ({len(summary_block)} chars)')
    return html


# ── 4. Fix ch4 broken imgs ────────────────────────────────────────────────────

def fix_ch4_broken_imgs(html: str) -> str:
    ch4_start = html.find('<section class="chapter" id="ch4"')
    ch5_start = html.find('<section class="chapter" id="ch5"')
    if ch4_start == -1:
        return html

    broken = _locate_broken_imgs(html, ch4_start, ch5_start)
    if not broken:
        print('  [INFO] No broken imgs in ch4')
        return html

    # Keep 估值分位 for regeneration (remove others)
    val_img_pos = None
    for img_open, b64_len, label in broken:
        if '估值分位' in label:
            val_img_pos = img_open
        else:
            pass  # will remove below

    # Remove all broken except 估值分位 (which we'll regenerate)
    for img_open, b64_len, label in reversed(broken):
        if '估值分位' not in label:
            html = _remove_broken_img(html, img_open, label)
    return html


# ── 5. Regenerate 估值分位 chart ──────────────────────────────────────────────

def regenerate_valuation_chart(html: str) -> str:
    print('\n  [valuation_percentile] regenerating...')
    try:
        from valuation_percentile import valuation_percentile
        r = valuation_percentile(TS_CODE, TRADE_DATE)
        print(f'    → signal={r.get("signal")}  pe_pct={r.get("pe_pct")}  pb_pct={r.get("pb_pct")}')

        if not r.get('chart_b64'):
            print('    [WARN] no chart')
            return html

        # Find the broken 估值分位 img and replace its b64
        ch4_start = html.find('<section class="chapter" id="ch4"')
        ch5_start = html.find('<section class="chapter" id="ch5"')

        # Re-locate broken img (positions may have shifted after removals)
        broken_in_ch4 = _locate_broken_imgs(html, ch4_start, ch5_start)
        val_img_pos = None
        for img_open, b64_len, label in broken_in_ch4:
            if '估值分位' in label:
                val_img_pos = img_open
                break

        if val_img_pos is None:
            print('    [WARN] 估值分位 broken img not found; inserting new')
            # Insert new chart after 估值分位 text
            val_head = html.find('<div class="tb-head">估值分位</div>', ch4_start, ch5_start)
            if val_head != -1:
                # Find end of first paragraph after it
                p_end = html.find('</p>', val_head)
                if p_end != -1:
                    chart_img = (f'\n<img src="data:image/png;base64,{r["chart_b64"]}"'
                                 f' alt="估值分位" style="max-width:100%;border-radius:6px;margin:8px 0"/>')
                    html = html[:p_end + 4] + chart_img + html[p_end + 4:]
                    print(f'    → 估值分位 chart inserted')
            return html

        # Replace broken b64 with valid b64
        p = html.find('data:image/png;base64,', val_img_pos)
        b64_start = p + 22
        next_tag  = html.find('<', b64_start)
        # Close the img tag properly
        new_img = (f'data:image/png;base64,{r["chart_b64"]}"'
                   f' alt="估值分位" style="max-width:100%;border-radius:6px;margin:8px 0"/>')
        html = html[:b64_start] + new_img + html[next_tag:]
        print(f'    → 估值分位 chart replaced')
    except Exception as e:
        print(f'    FAILED: {e}')
        import traceback; traceback.print_exc()
    return html


# ── 6. Lead-Lag chart ─────────────────────────────────────────────────────────

def _build_leadlag_chart() -> str:
    """Build a Lead-Lag timeline diagram as base64 PNG."""
    import matplotlib
    matplotlib.use('Agg')
    matplotlib.rcParams.update({
        'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'DejaVu Sans'],
        'axes.unicode_minus': False,
    })
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.patches import FancyArrowPatch

    fig, ax = plt.subplots(figsize=(9, 3.8), facecolor='white')
    ax.set_facecolor('white')
    ax.set_xlim(-3.5, 3.5)
    ax.set_ylim(-0.5, 3.2)
    ax.axis('off')

    # Title
    ax.set_title('供应链 Lead-Lag 结构（中芯国际为中心）',
                 fontsize=11, fontweight='bold', color='#1a1a2e', pad=10)

    # ── 时间轴 ──
    ax.annotate('', xy=(3.2, 1.5), xytext=(-3.2, 1.5),
                arrowprops=dict(arrowstyle='->', color='#999', lw=1.5))
    for x, lbl in [(-2, 'Q-2'), (0, 'Q0\n（今天）'), (2, 'Q+2')]:
        ax.axvline(x, color='#ddd', lw=1, ymin=0.1, ymax=0.9, zorder=0)
        ax.text(x, 0.05, lbl, ha='center', va='bottom', fontsize=8.5, color='#888')

    # ── 北方华创 → 中芯 (上游领先，正相关 +0.63) ──
    box_kw = dict(boxstyle='round,pad=0.4', fc='#e8f4e8', ec='#2ca02c', lw=1.5)
    ax.text(-2, 2.75, '北方华创\n(上游设备)', ha='center', va='center',
            fontsize=9, fontweight='bold', color='#2ca02c', bbox=box_kw)
    box_kw2 = dict(boxstyle='round,pad=0.5', fc='#e8f0fb', ec='#1a6fc4', lw=2)
    ax.text(0, 2.75, '中芯国际\n(中游代工)', ha='center', va='center',
            fontsize=10, fontweight='bold', color='#1a3463', bbox=box_kw2)
    box_kw3 = dict(boxstyle='round,pad=0.4', fc='#fff8e8', ec='#e8a500', lw=1.5)
    ax.text(2, 2.75, '圣邦股份\n(下游芯片)', ha='center', va='center',
            fontsize=9, fontweight='bold', color='#a07000', bbox=box_kw3)

    # Arrow: 北华创 → 中芯
    ax.annotate('', xy=(-0.38, 2.75), xytext=(-1.62, 2.75),
                arrowprops=dict(arrowstyle='->', color='#2ca02c', lw=2))
    ax.text(-1.0, 2.92, 'r = +0.63  领先2Q', ha='center', fontsize=8,
            color='#2ca02c', fontweight='bold')

    # Arrow: 中芯 → 圣邦
    ax.annotate('', xy=(1.62, 2.75), xytext=(0.38, 2.75),
                arrowprops=dict(arrowstyle='->', color='#e8a500', lw=2))
    ax.text(1.0, 2.92, '滞后2Q  传导验证', ha='center', fontsize=8,
            color='#a07000', fontweight='bold')

    # ── 立讯精密 ↔ 中芯 (负相关 -0.88) ──
    box_kw4 = dict(boxstyle='round,pad=0.4', fc='#fde8e8', ec='#d62728', lw=1.5)
    ax.text(-2, 1.55, '立讯精密\n(消费电子)', ha='center', va='center',
            fontsize=9, fontweight='bold', color='#d62728', bbox=box_kw4)
    # Dashed double-headed arrow
    ax.annotate('', xy=(-0.38, 1.65), xytext=(-1.62, 1.65),
                arrowprops=dict(arrowstyle='<->', color='#d62728', lw=1.8,
                                linestyle='dashed'))
    ax.text(-1.0, 1.82, 'r = −0.88  领先2Q（反向）', ha='center', fontsize=8,
            color='#d62728', fontweight='bold')

    # ── 当前信号框 ──
    signal_text = (
        '当前信号（2026Q1）\n'
        '北华创 营收 +25.8%  →  中芯 Q3-Q4 扩产预期  [正向]\n'
        '立讯精密 营收 +35.8%  →  中芯 Q3-Q4 消费电子压力  [复杂]\n'
        '申万半导体 +57.7%  →  板块全面共振  [顺风]'
    )
    ax.text(0, 0.65, signal_text, ha='center', va='center',
            fontsize=8, color='#333',
            bbox=dict(boxstyle='round,pad=0.5', fc='#f8f9fc', ec='#1a6fc4',
                      alpha=0.9, lw=1.2))

    fig.tight_layout(pad=0.8)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def inject_leadlag_chart(html: str) -> str:
    """Insert Lead-Lag chart into the Lead-Lag section in ch5."""
    print('\n  [leadlag_chart] generating...')
    ll_marker = '<div class="tb-head">供应链上下游 Lead-Lag 分析</div>'
    if ll_marker not in html:
        print('    [WARN] Lead-Lag section not found')
        return html

    try:
        b64 = _build_leadlag_chart()
        chart_html = (
            f'\n<div style="text-align:center;margin:12px 0">'
            f'<img src="data:image/png;base64,{b64}"'
            f' alt="Lead-Lag 结构图"'
            f' style="max-width:100%;border-radius:8px;'
            f'box-shadow:0 2px 8px rgba(0,0,0,.1)"/></div>\n'
        )
        # Find a good insertion point: after the first paragraph in the LL section
        h_pos = html.index(ll_marker)
        p_end = html.find('</p>', h_pos)
        if p_end == -1:
            print('    [WARN] paragraph end not found in Lead-Lag section')
            return html

        # Check if chart already exists
        if 'Lead-Lag 结构图' in html[h_pos:h_pos + 5000]:
            print('    [INFO] Lead-Lag chart already present (idempotent)')
            return html

        insert_pos = p_end + 4
        html = html[:insert_pos] + chart_html + html[insert_pos:]
        print(f'    → Lead-Lag chart injected ({len(b64)} chars b64)')
    except Exception as e:
        print(f'    FAILED: {e}')
        import traceback; traceback.print_exc()
    return html


# ── 7. Regenerate factor_percentile (now 7 factors) ──────────────────────────

def regenerate_factor_percentile(html: str) -> str:
    print('\n  [factor_percentile] regenerating (7 factors)...')
    try:
        from factor_percentile import factor_percentile
        r = factor_percentile(TS_CODE, TRADE_DATE)
        factors = r.get('factors', [])
        print(f'    → {len(factors)} factors  chart={bool(r.get("chart_b64"))}')
        for f in factors:
            print(f'      {f["name"]:10s} {f["value_str"]:>10s}  {f["pct"]:.0f}%ile')

        if not r.get('chart_b64'):
            print('    [WARN] no chart')
            return html

        card_title = '量化因子百分位（自身1年历史）'
        if card_title not in html:
            print(f'    [WARN] factor card not found')
            return html

        title_pos = html.index(card_title)
        img_start = html.find('data:image/png;base64,', title_pos)
        if img_start == -1:
            print('    [WARN] no img in factor card')
            return html

        b64_start = img_start + 22
        b64_end   = html.index('"', b64_start)
        html = html[:b64_start] + r['chart_b64'] + html[b64_end:]

        avg_pct = sum(f['pct'] for f in factors) / len(factors) if factors else 50
        badge_col = '#2ca02c' if avg_pct >= 65 else ('#d62728' if avg_pct <= 35 else '#888')
        badge_start = html.find('<span class="badge"', title_pos)
        if badge_start != -1:
            badge_close = html.index('</span>', badge_start) + 7
            new_badge = (f'<span class="badge" style="background:{badge_col}">'
                         f'{avg_pct:.0f}%ile 综合（7项）</span>')
            html = html[:badge_start] + new_badge + html[badge_close:]

        if r.get('narrative'):
            cb_start = html.find('<div class="card-body"', title_pos)
            if cb_start != -1:
                cb_end = html.find('</div>', cb_start)
                narr_html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', r['narrative'])
                cb_inner  = html.index('>', cb_start) + 1
                html = html[:cb_inner] + narr_html + html[cb_end:]

        print(f'    → factor_percentile chart replaced ({avg_pct:.0f}%ile, {len(factors)} factors)')
    except Exception as e:
        print(f'    FAILED: {e}')
        import traceback; traceback.print_exc()
    return html


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f'\n{"="*60}')
    print(f'patch_v3_688981_r3  ts_code={TS_CODE}  date={TRADE_DATE}')
    print('='*60)

    html = load_html()
    print(f'  Loaded: {len(html):,} chars')

    print('\n--- ch2 broken imgs ---')
    html = fix_ch2_broken_imgs(html)

    print('\n--- ch3 broken img ---')
    html = fix_ch3_broken_img(html)

    print('\n--- move 资金面一句话 ---')
    html = move_zijin_summary(html)

    print('\n--- ch4 broken imgs ---')
    html = fix_ch4_broken_imgs(html)

    html = regenerate_valuation_chart(html)
    html = inject_leadlag_chart(html)
    html = regenerate_factor_percentile(html)

    save_html(html)
    print('\n  Done.')


if __name__ == '__main__':
    main()
