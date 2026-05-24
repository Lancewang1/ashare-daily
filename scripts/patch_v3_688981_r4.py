"""
patch_v3_688981_r4.py
=====================
Round-4 edits for 688981 中芯国际 report.

Changes:
  1. Move 盈利景气度 + 财务景气传导 from ch4 to ch5 (基本面分析)
  2. Build and inject 大盘 BETA 与板块环境 visualization chart into ch4
"""

from __future__ import annotations
import re, sys, io, base64
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
    print(f'  [save] {len(html):,} chars -> {HTML_PATH.name}')


# ── 1. Move 盈利景气度 + 财务景气传导 from ch4 to ch5 ──────────────────────────

def move_sections_to_ch5(html: str) -> str:
    """
    Extract 盈利景气度 and 财务景气传导 tb-head sections from ch4,
    insert them in ch5 after 财务景气度 (before 一句话基本面定性).
    """
    ch4_start = html.find('<section class="chapter" id="ch4"')
    ch5_start = html.find('<section class="chapter" id="ch5"')
    if ch4_start == -1 or ch5_start == -1:
        print('  [WARN] ch4 or ch5 not found')
        return html

    yingli_head = '<div class="tb-head">盈利景气度</div>'
    caiwu_head  = '<div class="tb-head">财务景气传导</div>'
    dapan_head  = '<div class="tb-head">大盘 BETA 与板块环境</div>'

    yingli_pos = html.find(yingli_head, ch4_start, ch5_start)
    caiwu_pos  = html.find(caiwu_head,  ch4_start, ch5_start)
    dapan_pos  = html.find(dapan_head,  ch4_start, ch5_start)

    if yingli_pos == -1 or caiwu_pos == -1 or dapan_pos == -1:
        print(f'  [WARN] section markers not found in ch4: '
              f'yingli={yingli_pos} caiwu={caiwu_pos} dapan={dapan_pos}')
        return html

    # Extract content of each section (up to the next tb-head)
    yingli_content = html[yingli_pos:caiwu_pos]   # 161 chars
    caiwu_content  = html[caiwu_pos:dapan_pos]    # 825 chars
    print(f'  [R4.1] Extracted 盈利景气度 ({len(yingli_content)} chars) '
          f'and 财务景气传导 ({len(caiwu_content)} chars) from ch4')

    # Remove from ch4 in reverse order (preserve forward positions)
    html = html[:caiwu_pos] + html[dapan_pos:]
    html = html[:yingli_pos] + html[yingli_pos + len(yingli_content):]

    # Re-find insertion point in ch5 (positions shifted after removal)
    yijuhua_head = '<div class="tb-head">一句话基本面定性</div>'
    yijuhua_pos  = html.find(yijuhua_head)
    if yijuhua_pos == -1:
        print('  [WARN] 一句话基本面定性 not found in ch5; inserting at end of ch5')
        end_sec = html.find('</section>', html.find('<section class="chapter" id="ch5"'))
        yijuhua_pos = end_sec if end_sec != -1 else len(html)

    insert_block = yingli_content + caiwu_content
    html = html[:yijuhua_pos] + insert_block + html[yijuhua_pos:]
    print(f'  [R4.1] Sections inserted before 一句话基本面定性 in ch5 '
          f'({len(insert_block)} chars)')
    return html


# ── 2. Build 大盘 BETA 与板块环境 chart ─────────────────────────────────────────

def _build_beta_chart() -> str:
    """
    Build a horizontal bar comparison chart showing:
    - 30d/60d returns: 中芯国际 vs 申万半导体 vs 沪深300
    - Beta annotation, alpha, correlations, breadth
    """
    import matplotlib
    matplotlib.use('Agg')
    matplotlib.rcParams.update({
        'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'DejaVu Sans'],
        'axes.unicode_minus': False,
    })
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np

    fig = plt.figure(figsize=(10, 4.5), facecolor='white')
    gs  = fig.add_gridspec(1, 2, width_ratios=[3, 2], wspace=0.35)
    ax_bar  = fig.add_subplot(gs[0])
    ax_info = fig.add_subplot(gs[1])
    ax_bar.set_facecolor('white')
    ax_info.set_facecolor('white')
    ax_info.axis('off')

    # ── Bar chart data ──
    labels   = ['沪深300', 'Beta理论弹性\n(1.24×基准)', '中芯国际', '申万半导体']
    ret_30d  = [8.30, 10.30, 45.80, 57.74]   # theoretical = 1.24 × 8.30
    colors   = ['#aaaaaa', '#7eb4e0', '#1a6fc4', '#e8a500']
    alphas   = [0.85,      0.55,      0.90,     0.90]
    patterns = ['',        '///',     '',       '']

    y_pos = np.arange(len(labels))
    bars = ax_bar.barh(y_pos, ret_30d, color=colors, alpha=0.9,
                       height=0.55, zorder=3)

    # Apply hatch to theoretical bar
    bars[1].set_hatch('///')
    bars[1].set_edgecolor('#1a6fc4')
    bars[1].set_facecolor('#d0e8f8')
    bars[1].set_alpha(0.8)

    # Value labels
    for i, (bar, val) in enumerate(zip(bars, ret_30d)):
        color = '#222'
        weight = 'bold' if i in (2, 3) else 'normal'
        ax_bar.text(val + 0.8, bar.get_y() + bar.get_height() / 2,
                    f'+{val:.1f}%', va='center', ha='left',
                    fontsize=9.5, color=color, fontweight=weight)

    # Annotations
    ax_bar.text(46.5, 2, 'Alpha超额\n+35.5pp', fontsize=7.5, color='#1a6fc4',
                ha='left', va='center',
                bbox=dict(boxstyle='round,pad=0.3', fc='#eef3fb', ec='#1a6fc4', lw=0.8))
    ax_bar.annotate('', xy=(46.0, 2), xytext=(10.5, 2),
                    arrowprops=dict(arrowstyle='<->', color='#1a6fc4', lw=1.2))

    ax_bar.set_yticks(y_pos)
    ax_bar.set_yticklabels(labels, fontsize=10)
    ax_bar.set_xlabel('近30日涨跌幅（%）', fontsize=9, color='#555')
    ax_bar.set_xlim(0, 75)
    ax_bar.axvline(8.30, color='#aaa', lw=0.8, ls='--', zorder=2)
    ax_bar.set_title('688981 Beta与板块环境（近30日）',
                     fontsize=11, fontweight='bold', color='#1a1a2e', pad=10)
    ax_bar.spines['top'].set_visible(False)
    ax_bar.spines['right'].set_visible(False)
    ax_bar.tick_params(axis='x', labelsize=8.5, colors='#666')
    ax_bar.tick_params(axis='y', labelsize=9.5)
    ax_bar.grid(axis='x', color='#eee', lw=0.7, zorder=1)

    # ── Info panel ──
    info_lines = [
        ('Beta (vs 沪深300)',    '1.24',       '#1a1a2e'),
        ('理论弹性 (30d)',        '+10.3%',     '#1a6fc4'),
        ('实际超额 (vs CSI300)', '+37.5pp',    '#2ca02c'),
        ('相对板块 (vs 申万)',    '-11.9pp',    '#e8a500'),
        ('',                     '',           '#fff'),
        ('全球相关性',           '',           '#555'),
        ('  SOX (费城半导体)',    'r = 0.32',  '#555'),
        ('  NASDAQ (纳指)',       'r = 0.25',  '#555'),
        ('',                     '',           '#fff'),
        ('板块广度',             '',           '#555'),
        ('  个股>20日均线',       '93.8%',     '#2ca02c'),
        ('  申万半导超额(30d)',   '+49.4pp',   '#e8a500'),
    ]

    y0 = 0.97
    dy = 0.077
    for label, val, color in info_lines:
        if not label and not val:
            y0 -= dy * 0.4
            continue
        is_header = not val
        ax_info.text(0.02, y0, label, transform=ax_info.transAxes,
                     fontsize=9 if not is_header else 9.5,
                     color='#777' if is_header else '#333',
                     fontweight='bold' if is_header else 'normal',
                     va='top')
        if val:
            ax_info.text(0.98, y0, val, transform=ax_info.transAxes,
                         fontsize=9.5, color=color, fontweight='bold',
                         ha='right', va='top')
        y0 -= dy

    # Divider line (use plot in axes coords instead of axhline)
    ax_info.plot([0, 1], [0.0, 0.0], color='#ddd', lw=0.5,
                 transform=ax_info.transAxes, clip_on=False)
    ax_info.text(0.5, -0.04,
                 '数据截至 2026-05-20',
                 transform=ax_info.transAxes, fontsize=7.5,
                 color='#aaa', ha='center')

    fig.tight_layout(pad=1.0)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=140, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def inject_beta_chart(html: str) -> str:
    """Build Beta chart and inject into 大盘 BETA 与板块环境 section in ch4."""
    print('\n  [beta_chart] generating...')
    beta_head = '<div class="tb-head">大盘 BETA 与板块环境</div>'
    if beta_head not in html:
        print('  [WARN] 大盘 BETA 与板块环境 section not found')
        return html

    # Idempotent: skip if chart already there
    h_pos    = html.index(beta_head)
    next_pos = html.find('<div class="tb-head">', h_pos + len(beta_head))
    if next_pos == -1:
        next_pos = h_pos + 5000
    section_slice = html[h_pos:next_pos]
    if 'data:image/png;base64,' in section_slice:
        print('  [INFO] Beta chart already present (idempotent)')
        return html

    try:
        b64 = _build_beta_chart()
        chart_html = (
            f'\n<div style="text-align:center;margin:10px 0">'
            f'<img src="data:image/png;base64,{b64}"'
            f' alt="大盘BETA与板块环境"'
            f' style="max-width:100%;border-radius:8px;'
            f'box-shadow:0 2px 8px rgba(0,0,0,.1)"/></div>\n'
        )
        # Insert after the first </p> in this section
        p_end = html.find('</p>', h_pos)
        if p_end == -1:
            print('  [WARN] no </p> in BETA section; inserting right after tb-head')
            insert_pos = h_pos + len(beta_head)
        else:
            insert_pos = p_end + 4

        html = html[:insert_pos] + chart_html + html[insert_pos:]
        print(f'  [R4.2] Beta chart injected ({len(b64)} chars b64)')
    except Exception as e:
        print(f'  FAILED: {e}')
        import traceback; traceback.print_exc()
    return html


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f'\n{"="*60}')
    print(f'patch_v3_688981_r4  ts_code={TS_CODE}  date={TRADE_DATE}')
    print('='*60)

    html = load_html()
    print(f'  Loaded: {len(html):,} chars')

    print('\n--- Move 盈利景气度 + 财务景气传导 to ch5 ---')
    html = move_sections_to_ch5(html)

    print('\n--- Inject 大盘 BETA chart ---')
    html = inject_beta_chart(html)

    save_html(html)
    print('\n  Done.')


if __name__ == '__main__':
    main()
