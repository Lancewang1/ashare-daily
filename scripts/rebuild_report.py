"""
rebuild_report.py
=================
Rewrite an existing LLM+injected HTML report into a clean 5-chapter document.

Usage:
    python rebuild_report.py <html_path> [ts_code] [trade_date]

Example:
    python rebuild_report.py ../stocks/20260520_csi300_688981_中芯国际.html 688981.SH 20260520
"""

from __future__ import annotations
import sys, re, io, time
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ── CSS ───────────────────────────────────────────────────────────────────────

_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #f0f2f5; color: #222; line-height: 1.7; font-size: 14px;
}
.container { max-width: 960px; margin: 0 auto; padding: 0 0 80px; }

/* ── Hero ── */
.hero {
    background: linear-gradient(135deg, #1a1f36 0%, #2b3a5e 100%);
    color: white; padding: 32px 28px 24px;
    border-radius: 0 0 12px 12px;
}
.hero h1 { font-size: 26px; margin-bottom: 4px; line-height: 1.2; }
.hero .ticker-tag { opacity: .65; font-size: 17px; font-weight: 400; }
.hero .meta { opacity: .72; font-size: 13px; margin-top: 4px; }
.hero .pillrow, .hero .pills {
    margin-top: 14px; display: flex; flex-wrap: wrap; gap: 8px;
}
.pill {
    background: rgba(255,255,255,.14); padding: 5px 11px;
    border-radius: 12px; font-size: 12px; backdrop-filter: blur(4px);
}
.pill .v { font-weight: 600; }

/* ── Chapter nav ── */
.ch-nav {
    position: sticky; top: 0; z-index: 100; background: white;
    box-shadow: 0 2px 8px rgba(0,0,0,.08);
    display: flex; overflow-x: auto; border-bottom: 2px solid #e8eaf0;
}
.ch-nav a {
    padding: 12px 16px; color: #555; text-decoration: none; font-size: 13px;
    white-space: nowrap; border-bottom: 3px solid transparent; margin-bottom: -2px;
    font-weight: 500; transition: all .15s; flex-shrink: 0;
}
.ch-nav a:hover { color: #1a6fc4; border-bottom-color: #1a6fc4; background: #f8f9ff; }

/* ── Chapter layout ── */
.chapter { margin: 20px 14px 0; }
.ch-head {
    font-size: 16px; font-weight: 700; color: #1a1a2e;
    padding: 0 0 10px; border-bottom: 2px solid #1a6fc4;
    margin-bottom: 14px; display: flex; align-items: center; gap: 8px;
}

/* ── Cards ── */
.card {
    background: white; border-radius: 10px; padding: 16px 18px;
    box-shadow: 0 1px 6px rgba(0,0,0,.06); margin-bottom: 12px;
}
.card-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
.card-icon { font-size: 16px; line-height: 1; }
.card-title { font-size: 14px; font-weight: 600; color: #1a3463; flex: 1; }
.badge {
    padding: 2px 8px; border-radius: 4px; font-size: 11px;
    font-weight: 600; color: white; white-space: nowrap; flex-shrink: 0;
}
.card-chart { text-align: center; margin: 4px 0 10px; }
.card-chart img { max-width: 100%; border-radius: 5px; }
.card-body {
    font-size: 13px; color: #444; background: #f8f9fa;
    padding: 10px 14px; border-radius: 5px; border-left: 3px solid #ddd;
    line-height: 1.7;
}
.card-body strong { color: #c33; }
.card-body ul { margin: 4px 0 4px 16px; }
.card-body li { margin: 2px 0; }

/* ── Text blocks (LLM content) ── */
.tb {
    background: white; border-radius: 10px; padding: 16px 18px;
    box-shadow: 0 1px 6px rgba(0,0,0,.06); margin-bottom: 12px;
    font-size: 13.5px; color: #333; line-height: 1.78;
}
.tb-head {
    font-size: 13px; color: #1a3463; margin: 14px 0 6px;
    padding: 0 0 0 10px; border-left: 3px solid #1a6fc4; font-weight: 600;
}
.tb-head:first-child { margin-top: 0; }
.tb p  { margin: 5px 0; }
.tb ul { margin: 4px 0 4px 18px; }
.tb li { margin: 3px 0; font-size: 13px; }
.tb strong { color: #c33; font-weight: 600; }
.tb img { max-width: 100%; border-radius: 5px; margin: 8px 0; display: block; }

/* ── Signal summary row ── */
.sig-row { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; }
.sig-box {
    background: white; border-radius: 8px; padding: 10px 14px;
    box-shadow: 0 1px 4px rgba(0,0,0,.06); flex: 1; min-width: 110px;
}
.sig-box .lbl { font-size: 11px; color: #888; margin-bottom: 3px; }
.sig-box .val { font-size: 13px; font-weight: 700; }

/* ── Two-column grid ── */
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 600px) { .grid2 { grid-template-columns: 1fr; } }

/* ── Tracking table ── */
.track-table { width: 100%; border-collapse: collapse; font-size: 13px; margin: 6px 0; }
.track-table th { background: #1a6fc4; color: white; padding: 7px 10px; text-align: left; }
.track-table td { padding: 7px 10px; border-bottom: 1px solid #f0f0f0; vertical-align: top; }
.track-table tr:nth-child(even) td { background: #f8f9fa; }
"""


# ── Content parsing utilities ─────────────────────────────────────────────────

def get_expert_section(base: str, cls: str) -> str:
    """Return full HTML of <section class='...expert-{cls}...'> block."""
    m = re.search(f'<section[^>]*{re.escape(cls)}[^>]*>', base)
    if not m:
        return ''
    start = m.start()
    next_m = re.search(r'<section[^>]*expert-[a-z]+[^>]*>', base[start + 10:])
    end = start + 10 + next_m.start() if next_m else len(base)
    return base[start:end]


def get_h3_block(sec: str, keyword: str, max_chars: int = 2500) -> str:
    """Return content between the <h3> containing keyword and the next <h3>."""
    for m in re.finditer(r'<h3[^>]*>(.*?)</h3>', sec, re.DOTALL):
        if keyword in m.group(1):
            content_start = m.end()
            next_h3 = sec.find('<h3', content_start)
            content = sec[content_start: next_h3 if next_h3 > 0 else content_start + max_chars]
            return content.strip()[:max_chars]
    return ''


def to_plain(html_frag: str) -> str:
    """Strip HTML tags, collapse whitespace → plain text."""
    t = re.sub(r'<[^>]+>', ' ', html_frag)
    return re.sub(r'\s+', ' ', t).strip()


def condense_html(frag: str, max_p: int = 4, max_li: int = 8) -> str:
    """
    Keep first max_p <p> blocks + first max_li <li> elements.
    Remove inline style/class attrs to keep output lean.
    """
    # Strip style/class attrs
    frag = re.sub(r'\s+(?:style|class)="[^"]*"', '', frag)
    # Remove everything that's not a useful structural tag
    frag = re.sub(r'<(?!\/?(?:p|ul|ol|li|strong|em|b|br|img|h[3-6])[>\s])[^>]+>',
                  '', frag)
    frag = re.sub(r'\n{3,}', '\n\n', frag).strip()
    # Limit paragraphs
    parts = re.split(r'(?=<p[>\s])', frag)
    if len(parts) > max_p + 1:
        frag = ''.join(parts[:max_p + 1])
    # Limit list items
    lis = re.findall(r'<li[^>]*>.*?</li>', frag, re.DOTALL)
    if len(lis) > max_li:
        for li in lis[max_li:]:
            frag = frag.replace(li, '', 1)
    return frag.strip()


def bold_md(html: str) -> str:
    """Convert **text** markdown bold to <strong>text</strong>."""
    return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)


def get_hero(base: str) -> str:
    """Extract hero header block."""
    m = re.search(r'<header[^>]*hero[^>]*>.*?</header>', base, re.DOTALL)
    if m:
        return m.group(0)
    # Fallback: grab everything before first <section
    first_sec = base.find('<section')
    if first_sec > 0:
        return base[:first_sec].strip()
    return ''


# ── Injected card parser ──────────────────────────────────────────────────────

def parse_injected_cards(inject_html: str) -> list[dict]:
    """
    Parse signal cards from the V2 inject block.
    Each card is a <section ... style="margin:24px ..."> element.
    """
    cards = []
    for m in re.finditer(
            r'<section[^>]*margin:\s*24px[^>]*>(.*?)</section>',
            inject_html, re.DOTALL):
        c = m.group(1)

        h3 = re.search(r'<h3[^>]*>(.*?)</h3>', c, re.DOTALL)
        if not h3:
            continue
        h3_inner = h3.group(1)

        # Icon: first emoji-ish span
        icon_m = re.search(r'<span[^>]*font-size[^>]*>([^<]+)</span>', h3_inner)
        icon = icon_m.group(1).strip() if icon_m else ''

        # Title: h3 text minus all <span> elements (icon + badge)
        title_raw = re.sub(r'<span[^>]*>[^<]*</span>', '', h3_inner)
        title = re.sub(r'<[^>]+>', '', title_raw).strip()

        # Badge (last colored span in h3)
        badge_m = re.findall(
            r'background:([^;"]+)[^>]*>([^<]+)</span>', h3_inner)
        if badge_m:
            badge_col = badge_m[-1][0].strip()
            badge_txt = badge_m[-1][1].strip()
        else:
            badge_col, badge_txt = '#888', ''

        # Embedded chart
        chart_m = re.search(r'data:image/png;base64,([A-Za-z0-9+/=]{20,})', c)
        chart_b64 = chart_m.group(1) if chart_m else ''

        # Narrative text (first <p>)
        narr_m = re.search(r'<p[^>]*>(.*?)</p>', c, re.DOTALL)
        narrative = narr_m.group(1) if narr_m else ''

        cards.append({
            'icon': icon, 'title': title,
            'badge_col': badge_col, 'badge_txt': badge_txt,
            'chart_b64': chart_b64, 'narrative': narrative,
            'has_chart': bool(chart_b64),
        })
    return cards


def find_card(cards: list[dict], kw: str) -> dict | None:
    for c in cards:
        if kw in c['title']:
            return c
    return None


# ── HTML component builders ───────────────────────────────────────────────────

def make_card(icon: str, title: str, badge_txt: str, badge_col: str,
              chart_b64: str, narrative: str) -> str:
    badge = (f'<span class="badge" style="background:{badge_col}">{badge_txt}</span>'
             if badge_txt else '')
    chart = (f'<div class="card-chart"><img src="data:image/png;base64,{chart_b64}"'
             f' alt="{title}"/></div>'
             if chart_b64 else '')
    border = badge_col if badge_col and badge_col != '#888' else '#ddd'
    return (
        f'<div class="card">'
        f'<div class="card-head">'
        f'<span class="card-icon">{icon}</span>'
        f'<span class="card-title">{title}</span>'
        f'{("  " + badge) if badge else ""}'
        f'</div>'
        f'{chart}'
        f'<div class="card-body" style="border-left-color:{border}">'
        f'{bold_md(narrative)}'
        f'</div>'
        f'</div>'
    )


def make_tb(sections: list[tuple[str, str]]) -> str:
    """
    Text block with named sub-sections.
    sections = [(heading, html_content), ...]
    Heading='' means no sub-heading.
    """
    parts = []
    for heading, content in sections:
        if not content or not content.strip():
            continue
        if heading:
            parts.append(f'<div class="tb-head">{heading}</div>')
        parts.append(content.strip())
    if not parts:
        return ''
    return f'<div class="tb">{"".join(parts)}</div>'


def make_chapter(ch_id: str, icon: str, title: str, content: str) -> str:
    return (
        f'<section class="chapter" id="{ch_id}">'
        f'<div class="ch-head"><span>{icon}</span> {title}</div>'
        f'{content}'
        f'</section>'
    )


def sig_box(label: str, value: str, color: str = '#333') -> str:
    return (f'<div class="sig-box">'
            f'<div class="lbl">{label}</div>'
            f'<div class="val" style="color:{color}">{value}</div>'
            f'</div>')


# ── Chapter builders ──────────────────────────────────────────────────────────

def build_ch1(base: str, cards: list[dict]) -> str:
    """第一章 · 观点总结: verdict, core divergence, checklist."""
    html = ''

    # Signal summary row
    model_c  = find_card(cards, '量化模型选股记录')
    cd_c     = find_card(cards, '资金博弈仪表盘')
    sd_c     = find_card(cards, '板块扩散')
    thermo_c = find_card(cards, '温度计')

    row = ''
    if model_c:
        row += sig_box('量化排名', model_c['badge_txt'], model_c['badge_col'])
    if cd_c:
        row += sig_box('资金信号', cd_c['badge_txt'], cd_c['badge_col'])
    if sd_c:
        row += sig_box('板块状态', sd_c['badge_txt'], sd_c['badge_col'])
    if thermo_c:
        row += sig_box('市场情绪', thermo_c['badge_txt'], thermo_c['badge_col'])
    if row:
        html += f'<div class="sig-row">{row}</div>'

    # 综合观点 from expert-conclusion
    conc_sec  = get_expert_section(base, 'expert-conclusion')
    guanidian = get_h3_block(conc_sec, '综合观点', max_chars=1800)
    if guanidian:
        html += make_tb([('综合观点', condense_html(guanidian, max_p=5))])

    # 市场核心分歧 from expert-fund
    fund_sec   = get_expert_section(base, 'expert-fund')
    core_fenge = get_h3_block(fund_sec, '市场核心分歧', max_chars=2500)
    if core_fenge:
        html += make_tb([('市场核心分歧', condense_html(core_fenge, max_p=8))])

    # 5日观察清单
    checklist = get_h3_block(conc_sec, '5 日观察清单', max_chars=1200)
    if checklist:
        html += make_tb([('5 日观察清单', condense_html(checklist, max_p=5, max_li=6))])

    return make_chapter('ch1', '🎯', '第一章 · 观点总结', html)


def build_ch2(base: str, cards: list[dict]) -> str:
    """第二章 · 量化分析: model record, tech signals, K-line."""
    html = ''
    tech_sec = get_expert_section(base, 'expert-tech')

    # OOF model track record
    mtr = find_card(cards, '量化模型选股记录')
    if mtr and mtr['has_chart']:
        html += make_card(mtr['icon'], mtr['title'],
                          mtr['badge_txt'], mtr['badge_col'],
                          mtr['chart_b64'], mtr['narrative'])

    # 一句话定调 + 量化为何选中 (text block)
    one_liner = get_h3_block(tech_sec, '一句话定调', max_chars=500)
    model_why = get_h3_block(tech_sec, '量化模型为什么', max_chars=1500)
    cur_stage = get_h3_block(tech_sec, '当前阶段定性', max_chars=600)

    sections = []
    if one_liner:
        sections.append(('一句话定调', condense_html(one_liner, max_p=1)))
    if model_why:
        sections.append(('量化模型为何选中', condense_html(model_why, max_p=4)))
    if cur_stage:
        sections.append(('当前阶段定性', condense_html(cur_stage, max_p=2)))
    if sections:
        html += make_tb(sections)

    # 集合竞价 + 关键价位 side by side
    ca = find_card(cards, '集合竞价')
    kl = find_card(cards, '关键价位')
    if ca and kl and kl['has_chart']:
        ca_card = make_card(ca['icon'], ca['title'],
                            ca['badge_txt'], ca['badge_col'],
                            ca['chart_b64'], ca['narrative'])
        kl_card = make_card(kl['icon'], kl['title'],
                            kl['badge_txt'], kl['badge_col'],
                            kl['chart_b64'], kl['narrative'])
        html += f'<div class="grid2">{ca_card}{kl_card}</div>'
    else:
        if ca:
            html += make_card(ca['icon'], ca['title'],
                              ca['badge_txt'], ca['badge_col'],
                              ca['chart_b64'], ca['narrative'])
        if kl and kl['has_chart']:
            html += make_card(kl['icon'], kl['title'],
                              kl['badge_txt'], kl['badge_col'],
                              kl['chart_b64'], kl['narrative'])

    # K-line charts from LLM
    kline_content = get_h3_block(tech_sec, 'K线技术形态', max_chars=5000)
    kline_imgs = re.findall(r'data:image/png;base64,([A-Za-z0-9+/=]{20,})',
                            kline_content)
    kline_text = condense_html(
        re.sub(r'<img[^>]*/>', '', kline_content), max_p=3)
    if kline_imgs or kline_text:
        imgs_html = ''.join(
            f'<img src="data:image/png;base64,{b64}" alt="K线"/>'
            for b64 in kline_imgs[:2]
        )
        html += make_tb([('K 线技术形态', imgs_html + kline_text)])

    # Historical pattern if useful
    hist = get_h3_block(tech_sec, '历史同形态', max_chars=800)
    if hist:
        html += make_tb([('历史同形态回顾', condense_html(hist, max_p=3))])

    return make_chapter('ch2', '📈', '第二章 · 量化分析', html)


def build_ch3(base: str, cards: list[dict], new_radar_b64: str = '') -> str:
    """第三章 · 资金博弈: radar, flows, market sentiment."""
    html = ''
    flow_sec = get_expert_section(base, 'expert-flow')

    # Capital radar
    cd = find_card(cards, '资金博弈仪表盘')
    if cd:
        radar = new_radar_b64 or cd['chart_b64']
        html += make_card('📊', '资金博弈雷达（历史百分位）',
                          cd['badge_txt'], cd['badge_col'],
                          radar, cd['narrative'])

    # 一句话资金面定性 + 量能节奏 (text block)
    cap_liner  = get_h3_block(flow_sec, '一句话资金面定性', max_chars=500)
    vol_rhythm = get_h3_block(flow_sec, '量能节奏', max_chars=1000)
    bk_sync    = get_h3_block(flow_sec, '板块共振度', max_chars=800)
    sections = []
    if cap_liner:
        sections.append(('资金面一句话', condense_html(cap_liner, max_p=1)))
    if vol_rhythm:
        sections.append(('量能节奏', condense_html(vol_rhythm, max_p=3)))
    if bk_sync:
        sections.append(('板块共振度', condense_html(bk_sync, max_p=2)))
    if sections:
        html += make_tb(sections)

    # 主力资金流向 + 融资余额加速度 side by side
    mf = find_card(cards, '主力资金流向')
    ma = find_card(cards, '融资余额加速度')
    pair = ''
    for c in [mf, ma]:
        if c and c['has_chart']:
            pair += make_card(c['icon'], c['title'],
                              c['badge_txt'], c['badge_col'],
                              c['chart_b64'], c['narrative'])
    if pair:
        html += f'<div class="grid2">{pair}</div>'
    else:
        for c in [mf, ma]:
            if c and c['narrative']:
                html += make_card(c['icon'], c['title'],
                                  c['badge_txt'], c['badge_col'],
                                  '', c['narrative'])

    # 大宗交易
    bt = find_card(cards, '大宗交易')
    if bt:
        html += make_card(bt['icon'], bt['title'],
                          bt['badge_txt'], bt['badge_col'],
                          bt.get('chart_b64', ''), bt['narrative'])

    # 融资 vs 转融通 + 股东增减持
    for kw in ['融资 vs 转融通', '股东增减持']:
        c = find_card(cards, kw)
        if c and (c['has_chart'] or c['narrative']):
            html += make_card(c['icon'], c['title'],
                              c['badge_txt'], c['badge_col'],
                              c.get('chart_b64', ''), c['narrative'])

    # 情绪温度计 + 板块扩散 side by side
    thermo = find_card(cards, '温度计')
    sd     = find_card(cards, '板块扩散')
    pair2  = ''
    for c in [thermo, sd]:
        if c and c['has_chart']:
            pair2 += make_card(c['icon'], c['title'],
                               c['badge_txt'], c['badge_col'],
                               c['chart_b64'], c['narrative'])
    if pair2:
        html += f'<div class="grid2">{pair2}</div>'

    return make_chapter('ch3', '💰', '第三章 · 资金博弈', html)


def build_ch4(base: str, cards: list[dict]) -> str:
    """第四章 · 估值比较: valuation, earnings, macro context."""
    html = ''
    fund_sec  = get_expert_section(base, 'expert-fund')
    macro_sec = get_expert_section(base, 'expert-macro')

    # Valuation + earnings from expert-fund
    val_text  = get_h3_block(fund_sec, '估值分位', max_chars=1200)
    earn_text = get_h3_block(fund_sec, '盈利景气度', max_chars=1000)
    fc_text   = get_h3_block(fund_sec, '财务景气传导', max_chars=800)

    sections = []
    if val_text:
        sections.append(('估值分位', condense_html(val_text, max_p=4)))
    if earn_text:
        sections.append(('盈利景气度', condense_html(earn_text, max_p=3)))
    if fc_text:
        sections.append(('财务景气传导', condense_html(fc_text, max_p=2)))
    if sections:
        html += make_tb(sections)

    # 大盘 BETA + 大类资产传导 from expert-macro
    beta_text  = get_h3_block(macro_sec, '大盘 BETA 分析', max_chars=1500)
    asset_text = get_h3_block(macro_sec, '大类资产传导', max_chars=1000)

    macro_sections = []
    if beta_text:
        macro_sections.append(('大盘 BETA 与板块环境', condense_html(beta_text, max_p=4)))
    if asset_text:
        macro_sections.append(('大类资产传导', condense_html(asset_text, max_p=3)))
    if macro_sections:
        html += make_tb(macro_sections)

    # AH premium if available
    ah = find_card(cards, 'AH溢价')
    if ah and ah['has_chart']:
        html += make_card(ah['icon'], ah['title'],
                          ah['badge_txt'], ah['badge_col'],
                          ah['chart_b64'], ah['narrative'])

    # Equity incentive
    ei = find_card(cards, '股权激励')
    if ei and ei.get('narrative'):
        skip_txt = '无有效激励' in ei.get('badge_txt', '') or '无' in ei.get('badge_txt', '')
        if not skip_txt or ei['has_chart']:
            html += make_card(ei['icon'], ei['title'],
                              ei['badge_txt'], ei['badge_col'],
                              ei.get('chart_b64', ''), ei['narrative'])

    # Inst survey
    sv = find_card(cards, '机构调研')
    if sv and sv.get('narrative') and '近期无' not in sv.get('badge_txt', ''):
        html += make_card(sv['icon'], sv['title'],
                          sv['badge_txt'], sv['badge_col'],
                          sv.get('chart_b64', ''), sv['narrative'])

    return make_chapter('ch4', '📊', '第四章 · 估值比较', html)


def build_ch5(base: str, cards: list[dict]) -> str:
    """第五章 · 基本面/行业分析: business, supply chain, tracking."""
    html = ''
    brief_sec = get_expert_section(base, 'expert-brief')
    fund_sec  = get_expert_section(base, 'expert-fund')

    # Company overview
    # h3s in expert-brief vary by stock — grab company description and revenue structure
    # The first descriptive h3 is typically the company tagline
    all_h3s_in_brief = re.findall(r'<h3[^>]*>(.*?)</h3>', brief_sec, re.DOTALL)
    tagline_h3 = re.sub(r'<[^>]+>', '', all_h3s_in_brief[0]).strip() if all_h3s_in_brief else ''

    co_desc  = get_h3_block(brief_sec, tagline_h3[:10] if tagline_h3 else '公司', max_chars=800) \
               if tagline_h3 else ''
    if not co_desc:
        # fallback: try generic keywords
        for kw in ['公司做什么', '公司简介', tagline_h3]:
            co_desc = get_h3_block(brief_sec, kw, max_chars=800)
            if co_desc:
                break

    rev_struct = get_h3_block(brief_sec, '营收业务结构', max_chars=600)
    fin_health = get_h3_block(brief_sec, '财务景气度', max_chars=600)

    # Charts in brief section
    co_imgs = re.findall(r'data:image/png;base64,([A-Za-z0-9+/=]{20,})', brief_sec)

    sections = []
    if tagline_h3 and co_desc:
        sections.append((tagline_h3, condense_html(co_desc, max_p=3)))
    if rev_struct:
        rev_html = condense_html(rev_struct, max_p=2)
        if co_imgs:
            rev_html += f'<img src="data:image/png;base64,{co_imgs[0]}" alt="营收结构"/>'
        sections.append(('营收业务结构', rev_html))
    if fin_health:
        sections.append(('财务景气度', condense_html(fin_health, max_p=2)))
    if sections:
        html += make_tb(sections)

    # Supply chain + fundamental qualitative
    supply     = get_h3_block(fund_sec, '供应链上下游', max_chars=1500)
    fund_liner = get_h3_block(fund_sec, '一句话基本面定性', max_chars=400)
    fund_obs   = get_h3_block(fund_sec, '5 日基本面观察', max_chars=800)

    fund_sections = []
    if fund_liner:
        fund_sections.append(('一句话基本面定性', condense_html(fund_liner, max_p=1)))
    if supply:
        fund_sections.append(('供应链上下游 Lead-Lag', condense_html(supply, max_p=5)))
    if fund_obs:
        fund_sections.append(('5 日基本面观察清单', condense_html(fund_obs, max_p=5, max_li=6)))
    if fund_sections:
        html += make_tb(fund_sections)

    # Tracking table from expert-outcome
    outcome_sec = get_expert_section(base, 'expert-outcome')
    if outcome_sec:
        # Keep just the table / result rows; strip signal cards appended inside
        # (old inject format sometimes puts signals here)
        table_m = re.search(r'<table[^>]*>.*?</table>', outcome_sec, re.DOTALL)
        if table_m:
            # Restyle the table cleanly
            table_html = table_m.group(0)
            table_html = re.sub(r'\s*style="[^"]*"', '', table_html)
            table_html = re.sub(r'\s*class="[^"]*"', '', table_html)
            table_html = table_html.replace('<table', '<table class="track-table"')
            # Also get the predicted return text near the table
            pred_m = re.search(r'量化模型预测[^<]{0,200}', outcome_sec)
            pred_text = pred_m.group(0).strip() if pred_m else ''
            track_content = table_html
            if pred_text:
                track_content += f'<p style="margin-top:8px;font-size:13px;color:#555">{pred_text}</p>'
            html += make_tb([('后续追踪 — T+1 / 5 日实际收益', track_content)])

    return make_chapter('ch5', '🏭', '第五章 · 基本面 / 行业分析', html)


# ── Main rebuild ──────────────────────────────────────────────────────────────

def rebuild(html_path: str, ts_code: str, trade_date: str) -> None:
    t0 = time.time()

    with open(html_path, encoding='utf-8') as f:
        html = f.read()

    # Guard: refuse to rebuild an already-rebuilt file (no expert sections)
    if 'expert-conclusion' not in html and 'expert-tech' not in html:
        print(f'  [ABORT] {Path(html_path).name} has no expert sections — '
              f'looks like already rebuilt. Restore original first.')
        return

    guard     = '<!-- inject_signals_v2 -->'
    guard_pos = html.find(guard)
    base_html    = html[:guard_pos] if guard_pos >= 0 else html
    inject_html  = html[guard_pos + len(guard):] if guard_pos >= 0 else ''

    print(f'  base={len(base_html):,}  inject={len(inject_html):,}')

    cards = parse_injected_cards(inject_html)
    print(f'  injected cards: {len(cards)}')
    for c in cards:
        print(f'    {c["title"][:40]:40s}  chart={c["has_chart"]}  badge={c["badge_txt"]}')

    # Fresh capital dashboard radar
    radar_b64 = ''
    try:
        from capital_dashboard import capital_dashboard
        cd_res    = capital_dashboard(ts_code, trade_date)
        radar_b64 = cd_res.get('chart_b64', '')
        print(f'  capital_dashboard: {cd_res["signal"]}  {cd_res["composite_pct"]}%ile'
              f'  radar={len(radar_b64):,} chars')
    except Exception as e:
        print(f'  [WARN] capital_dashboard: {e}')

    # Extract hero
    hero = get_hero(base_html)

    # Page title from hero
    title_m = re.search(r'<h1[^>]*>(.*?)</h1>', hero, re.DOTALL)
    page_title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip() if title_m else '深度研究'

    # Build 5 chapters
    print('  building chapters ...')
    ch1 = build_ch1(base_html, cards)
    ch2 = build_ch2(base_html, cards)
    ch3 = build_ch3(base_html, cards, new_radar_b64=radar_b64)
    ch4 = build_ch4(base_html, cards)
    ch5 = build_ch5(base_html, cards)

    nav = (
        '<nav class="ch-nav">'
        '<a href="#ch1">🎯 观点总结</a>'
        '<a href="#ch2">📈 量化分析</a>'
        '<a href="#ch3">💰 资金博弈</a>'
        '<a href="#ch4">📊 估值比较</a>'
        '<a href="#ch5">🏭 基本面</a>'
        '</nav>'
    )

    date_str = f'{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}'
    new_html = (
        f'<!DOCTYPE html>\n<html lang="zh">\n<head>\n'
        f'<meta charset="UTF-8"/>\n'
        f'<meta name="viewport" content="width=device-width,initial-scale=1"/>\n'
        f'<title>{page_title} 深度研究 ({date_str})</title>\n'
        f'<style>{_CSS}</style>\n'
        f'</head>\n<body>\n'
        f'<div class="container">\n'
        f'{hero}\n'
        f'{nav}\n'
        f'{ch1}\n{ch2}\n{ch3}\n{ch4}\n{ch5}\n'
        f'</div>\n</body>\n</html>'
    )

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(new_html)

    elapsed = time.time() - t0
    print(f'  rebuilt {len(new_html):,} chars ({elapsed:.1f}s) → {Path(html_path).name}')


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python rebuild_report.py <html_path> [ts_code] [trade_date]')
        sys.exit(1)
    html_path  = sys.argv[1]
    ts_code    = sys.argv[2] if len(sys.argv) > 2 else '688981.SH'
    trade_date = sys.argv[3] if len(sys.argv) > 3 else '20260520'
    rebuild(html_path, ts_code, trade_date)
