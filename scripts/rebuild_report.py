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


# ── CSS template ──────────────────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #f0f2f5; color: #222; line-height: 1.7;
}
.container { max-width: 960px; margin: 0 auto; padding: 0 0 80px; }

.hero {
    background: linear-gradient(135deg, #1a1f36 0%, #2b3a5e 100%);
    color: white; padding: 32px 28px 24px;
    border-radius: 0 0 12px 12px;
}
.hero h1 { font-size: 28px; margin-bottom: 4px; line-height: 1.2; }
.hero .ticker-tag { opacity: .65; font-size: 18px; font-weight: 400; }
.hero .meta { opacity: .75; font-size: 13px; margin-top: 4px; }
.hero .pills { margin-top: 16px; display: flex; flex-wrap: wrap; gap: 8px; }
.pill { background: rgba(255,255,255,.13); padding: 5px 11px;
        border-radius: 12px; font-size: 12px; backdrop-filter: blur(4px); }
.pill .v { font-weight: 600; }

.ch-nav {
    position: sticky; top: 0; z-index: 100; background: white;
    box-shadow: 0 2px 8px rgba(0,0,0,.08);
    display: flex; overflow-x: auto; border-bottom: 1px solid #eee;
}
.ch-nav a {
    padding: 11px 16px; color: #555; text-decoration: none; font-size: 13px;
    white-space: nowrap; border-bottom: 3px solid transparent;
    font-weight: 500; transition: all .15s; flex-shrink: 0;
}
.ch-nav a:hover { color: #1a6fc4; border-bottom-color: #1a6fc4; }

.chapter { margin: 20px 16px 0; }
.ch-head {
    font-size: 16px; font-weight: 700; color: #1a1a2e;
    padding: 0 0 10px; border-bottom: 2px solid #1a6fc4;
    margin-bottom: 14px; display: flex; align-items: center; gap: 8px;
}

.card {
    background: white; border-radius: 10px; padding: 16px 18px;
    box-shadow: 0 1px 5px rgba(0,0,0,.05); margin-bottom: 12px;
}
.card-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.card-icon { font-size: 16px; }
.card-title { font-size: 14px; font-weight: 600; color: #1a3463; flex: 1; }
.badge {
    padding: 2px 7px; border-radius: 4px; font-size: 11px;
    font-weight: 600; color: white; white-space: nowrap;
}
.card-chart { text-align: center; margin: 6px 0 8px; }
.card-chart img { max-width: 100%; border-radius: 5px; }
.card-text {
    font-size: 13px; color: #444; background: #f8f9fa;
    padding: 9px 13px; border-radius: 5px; border-left: 3px solid #ddd;
    line-height: 1.65;
}
.card-text strong { color: #c44; font-weight: 600; }

.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 600px) { .grid2 { grid-template-columns: 1fr; } }

.sig-row { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; }
.sig-box {
    background: white; border-radius: 8px; padding: 10px 14px;
    box-shadow: 0 1px 4px rgba(0,0,0,.05); min-width: 120px; flex: 1;
}
.sig-box .lbl { font-size: 11px; color: #888; margin-bottom: 2px; }
.sig-box .val { font-size: 13px; font-weight: 600; }

.text-block {
    background: white; border-radius: 10px; padding: 16px 18px;
    box-shadow: 0 1px 5px rgba(0,0,0,.05); margin-bottom: 12px;
    font-size: 13.5px; color: #333; line-height: 1.75;
}
.text-block h4 {
    font-size: 13px; color: #1a3463; margin: 12px 0 5px;
    padding-left: 9px; border-left: 3px solid #1a6fc4; font-weight: 600;
}
.text-block h4:first-child { margin-top: 0; }
.text-block p  { margin: 5px 0; }
.text-block ul { margin: 5px 0 5px 18px; }
.text-block li { margin: 3px 0; font-size: 13px; }
.text-block strong { color: #c44; font-weight: 600; }
.text-block img { max-width: 100%; border-radius: 5px; margin: 8px 0; display: block; }

.divider { height: 1px; background: #eee; margin: 16px 0; }

/* Tracking table */
.track-table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; }
.track-table th { background: #1a6fc4; color: white; padding: 6px 10px; text-align: left; }
.track-table td { padding: 6px 10px; border-bottom: 1px solid #f0f0f0; }
.track-table tr:nth-child(even) td { background: #f8f9fa; }
"""


# ── HTML utilities ────────────────────────────────────────────────────────────

def make_card(icon: str, title: str, badge_text: str, badge_color: str,
              chart_b64: str, narrative_html: str) -> str:
    badge = (f'<span class="badge" style="background:{badge_color}">{badge_text}</span>'
             if badge_text else '')
    chart = (f'<div class="card-chart"><img src="data:image/png;base64,{chart_b64}" alt="{title}"/></div>'
             if chart_b64 else '')
    border = badge_color if badge_color else '#ddd'
    narr   = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', narrative_html)
    return f'''<div class="card">
  <div class="card-head">
    <span class="card-icon">{icon}</span>
    <span class="card-title">{title}</span>{('  ' + badge) if badge else ''}
  </div>
  {chart}
  <div class="card-text" style="border-left-color:{border}">{narr}</div>
</div>'''


def make_chapter(ch_id: str, icon: str, title: str, content: str) -> str:
    return f'''<section class="chapter" id="{ch_id}">
  <div class="ch-head"><span>{icon}</span> {title}</div>
  {content}
</section>'''


# ── Content extraction ────────────────────────────────────────────────────────

def extract_hero(html: str) -> str:
    m = re.search(r'<div class="hero">(.*?)</div>\s*</div>', html, re.DOTALL)
    if m:
        return f'<div class="hero"><div class="container">{m.group(0)}</div></div>'
    # Fallback: grab everything up to first </section>
    m2 = re.search(r'(<div class="hero">.*?</div>)', html, re.DOTALL)
    return m2.group(1) if m2 else ''


def extract_base_section(base_html: str, title_keyword: str) -> str:
    idx = base_html.find(title_keyword)
    if idx < 0:
        return ''
    sec_start = base_html.rfind('<section', 0, idx)
    if sec_start < 0:
        return ''
    next_sec = base_html.find('<section class="section"', sec_start + 50)
    return base_html[sec_start: next_sec if next_sec > 0 else len(base_html)]


def extract_h3_block(section_html: str, h3_keyword: str, max_chars: int = 3000) -> str:
    idx = section_html.find(h3_keyword)
    if idx < 0:
        return ''
    h3_open = section_html.rfind('<h3', 0, idx)
    h3_close = section_html.find('</h3>', h3_open) + 5
    next_h3  = section_html.find('<h3', h3_close)
    content  = section_html[h3_close: next_h3 if next_h3 > 0 else h3_close + max_chars]
    return content.strip()[:max_chars]


def condense(html_frag: str, max_p: int = 4) -> str:
    """Keep first max_p <p> blocks and all <ul>/<img> from an HTML fragment."""
    # Remove style/class attrs to keep output lean
    frag = re.sub(r'\s+(?:style|class)="[^"]*"', '', html_frag)
    # Keep only useful tags
    frag = re.sub(r'<(?!/?(?:p|ul|li|strong|em|br|img|h[3-6])[>\s])[^>]+>', '', frag)
    frag = re.sub(r'\n{3,}', '\n\n', frag)
    # Limit to max_p paragraphs
    parts = re.split(r'(?=<p[>\s])', frag)
    if len(parts) > max_p + 1:
        parts = parts[:max_p + 1]
        frag  = ''.join(parts)
    return frag.strip()


def parse_injected_cards(inject_html: str) -> list[dict]:
    """Parse inner cards from injected chapters."""
    cards = []
    raw = re.findall(r'<section[^>]*margin:24px[^>]*>(.*?)</section>', inject_html, re.DOTALL)
    for c in raw:
        h3 = re.search(r'<h3[^>]*>(.*?)</h3>', c, re.DOTALL)
        if not h3:
            continue
        h3_inner  = h3.group(1)
        icon_m    = re.search(r'<span[^>]*font-size[^>]*>([^<]+)</span>', h3_inner)
        icon      = icon_m.group(1).strip() if icon_m else ''
        title_raw = re.sub(r'<span[^>]*>[^<]*</span>', '', h3_inner)
        title     = re.sub(r'<[^>]+>', '', title_raw).strip()
        badge_m   = re.search(r'background:([^;,"]+)[^>]*>([^<]+)</span>', h3_inner)
        badge_col = badge_m.group(1).strip() if badge_m else '#888'
        badge_txt = badge_m.group(2).strip() if badge_m else ''
        chart_m   = re.search(r'data:image/png;base64,([^"\']+)', c)
        chart_b64 = chart_m.group(1) if chart_m else ''
        narr_m    = re.search(r'<p[^>]*>(.*?)</p>', c, re.DOTALL)
        narrative = narr_m.group(1) if narr_m else ''
        cards.append({
            'icon': icon, 'title': title,
            'badge_col': badge_col, 'badge_txt': badge_txt,
            'chart_b64': chart_b64, 'narrative': narrative,
            'has_chart': bool(chart_b64),
        })
    return cards


def find_card(cards: list[dict], title_kw: str) -> dict | None:
    for c in cards:
        if title_kw in c['title']:
            return c
    return None


# ── Chapter builders ──────────────────────────────────────────────────────────

def build_ch1(base_html: str, cards: list[dict]) -> str:
    html = ''

    # Signal summary row
    model_card  = find_card(cards, '量化模型选股记录')
    cd_card     = find_card(cards, '资金博弈仪表盘')
    sd_card     = find_card(cards, '板块扩散')
    thermo_card = find_card(cards, '情绪温度计')
    unlock_card = find_card(cards, '解禁')

    def sig_box(label, value, color='#333'):
        return (f'<div class="sig-box"><div class="lbl">{label}</div>'
                f'<div class="val" style="color:{color}">{value}</div></div>')

    sig_row = '<div class="sig-row">'
    if model_card:
        sig_row += sig_box('量化排名', model_card['badge_txt'],
                           model_card['badge_col'])
    if cd_card:
        sig_row += sig_box('资金信号', cd_card['badge_txt'],
                           cd_card['badge_col'])
    if sd_card:
        sig_row += sig_box('板块状态', sd_card['badge_txt'],
                           sd_card['badge_col'])
    if thermo_card:
        sig_row += sig_box('市场情绪', thermo_card['badge_txt'],
                           thermo_card['badge_col'])
    sig_row += '</div>'
    html += sig_row

    # 综合观点 from 核心摘要 section
    summary_sec = extract_base_section(base_html, '核心摘要')
    guanidian   = extract_h3_block(summary_sec, '综合观点', max_chars=1500)
    checklist   = extract_h3_block(summary_sec, '5 日观察', max_chars=1000)

    if guanidian:
        html += f'<div class="text-block"><h4>综合观点</h4>{condense(guanidian, 3)}</div>'

    # 核心分歧 from 基本面 section
    fund_sec    = extract_base_section(base_html, '基本面')
    core_fenge  = extract_h3_block(fund_sec, '核心分歧', max_chars=2000)
    if core_fenge:
        html += f'<div class="text-block"><h4>市场核心分歧</h4>{condense(core_fenge, 6)}</div>'

    if checklist:
        html += f'<div class="text-block"><h4>5 日观察清单</h4>{condense(checklist, 5)}</div>'

    return make_chapter('ch1', '🎯', '第一章 · 观点总结', html)


def build_ch2(base_html: str, cards: list[dict]) -> str:
    html = ''

    # OOF model record
    mtr = find_card(cards, '量化模型选股记录')
    if mtr and mtr['has_chart']:
        html += make_card(mtr['icon'], mtr['title'],
                          mtr['badge_txt'], mtr['badge_col'],
                          mtr['chart_b64'], mtr['narrative'])

    # Call auction + Key levels side by side
    ca  = find_card(cards, '集合竞价')
    kl  = find_card(cards, '关键价位')
    grid = ''
    if ca:
        grid += make_card(ca['icon'], ca['title'],
                          ca['badge_txt'], ca['badge_col'],
                          ca['chart_b64'], ca['narrative'])
    if kl and kl['has_chart']:
        grid += make_card(kl['icon'], kl['title'],
                          kl['badge_txt'], kl['badge_col'],
                          kl['chart_b64'], kl['narrative'])
    if grid:
        html += f'<div class="grid2">{grid}</div>'
    elif kl:
        html += make_card(kl['icon'], kl['title'],
                          kl['badge_txt'], kl['badge_col'],
                          kl['chart_b64'], kl['narrative'])

    # Quant model reasoning from LLM tech section
    tech_sec    = extract_base_section(base_html, '技术面')
    model_why   = extract_h3_block(tech_sec, '量化模型为什么', max_chars=1200)
    one_liner   = extract_h3_block(tech_sec, '一句话定调', max_chars=400)

    if one_liner or model_why:
        block = ''
        if one_liner:
            block += f'<h4>一句话定调</h4>{condense(one_liner, 1)}'
        if model_why:
            block += f'<h4>量化模型为何选中</h4>{condense(model_why, 3)}'
        html += f'<div class="text-block">{block}</div>'

    # K-line charts from LLM tech section (extract embedded images)
    kline_imgs = re.findall(
        r'data:image/png;base64,([^"\']{20,})', tech_sec)
    if kline_imgs:
        imgs_html = ''.join(
            f'<img src="data:image/png;base64,{b64}" style="max-width:100%;border-radius:5px;margin:6px 0;"/>'
            for b64 in kline_imgs[:2]
        )
        html += (f'<div class="text-block"><h4>K 线技术形态</h4>'
                 f'{imgs_html}</div>')

    return make_chapter('ch2', '📈', '第二章 · 量化分析', html)


def build_ch3(base_html: str, cards: list[dict], new_radar_b64: str = '') -> str:
    html = ''

    # Capital dashboard radar
    cd = find_card(cards, '资金博弈仪表盘')
    if cd:
        chart = new_radar_b64 or cd['chart_b64']
        html += make_card('📊', '资金博弈雷达（历史百分位）',
                          cd['badge_txt'], cd['badge_col'],
                          chart, cd['narrative'])

    # Money flow + Margin accel side by side
    mf = find_card(cards, '主力资金流向')
    ma = find_card(cards, '融资余额加速度')
    grid = ''
    for c in [mf, ma]:
        if c and c['has_chart']:
            grid += make_card(c['icon'], c['title'],
                              c['badge_txt'], c['badge_col'],
                              c['chart_b64'], c['narrative'])
    if grid:
        html += f'<div class="grid2">{grid}</div>'

    # Block trade
    bt = find_card(cards, '大宗交易折溢价')
    if bt and bt['has_chart']:
        html += make_card(bt['icon'], bt['title'],
                          bt['badge_txt'], bt['badge_col'],
                          bt['chart_b64'], bt['narrative'])
    elif bt and bt['narrative']:
        html += make_card(bt['icon'], bt['title'],
                          bt['badge_txt'], bt['badge_col'],
                          '', bt['narrative'])

    # SLB / insider if they have chart
    for kw in ['融资 vs 转融通', '股东增减持']:
        c = find_card(cards, kw)
        if c and c['has_chart']:
            html += make_card(c['icon'], c['title'],
                              c['badge_txt'], c['badge_col'],
                              c['chart_b64'], c['narrative'])

    # LLM capital narrative: 一句话资金面 + 量能节奏
    cap_sec    = extract_base_section(base_html, '资金流')
    cap_liner  = extract_h3_block(cap_sec, '一句话资金面', max_chars=400)
    vol_rhythm = extract_h3_block(cap_sec, '量能节奏', max_chars=800)
    if cap_liner or vol_rhythm:
        block = ''
        if cap_liner:
            block += f'<h4>一句话资金面定性</h4>{condense(cap_liner, 1)}'
        if vol_rhythm:
            block += f'<h4>量能节奏与活跃度</h4>{condense(vol_rhythm, 2)}'
        html += f'<div class="text-block">{block}</div>'

    # Market thermometer + sector diffusion side by side
    thermo = find_card(cards, '情绪温度计')
    sd     = find_card(cards, '板块扩散')
    grid2  = ''
    for c in [thermo, sd]:
        if c and c['has_chart']:
            grid2 += make_card(c['icon'], c['title'],
                               c['badge_txt'], c['badge_col'],
                               c['chart_b64'], c['narrative'])
    if grid2:
        html += f'<div class="grid2">{grid2}</div>'

    return make_chapter('ch3', '💰', '第三章 · 资金博弈', html)


def build_ch4(base_html: str, cards: list[dict]) -> str:
    html = ''

    # AH premium if available
    ah = find_card(cards, 'AH溢价')
    if ah and ah['has_chart']:
        html += make_card(ah['icon'], ah['title'],
                          ah['badge_txt'], ah['badge_col'],
                          ah['chart_b64'], ah['narrative'])

    # Valuation text from LLM fundamental section
    fund_sec   = extract_base_section(base_html, '基本面')
    val_text   = extract_h3_block(fund_sec, '估值分位', max_chars=1000)
    earn_text  = extract_h3_block(fund_sec, '盈利景气度', max_chars=800)
    macro_text = extract_h3_block(
        extract_base_section(base_html, '宏观环境'), '大盘 BETA', max_chars=1000)

    block = ''
    if val_text:
        block += f'<h4>估值分位</h4>{condense(val_text, 3)}'
    if earn_text:
        block += f'<h4>盈利景气度</h4>{condense(earn_text, 2)}'
    if macro_text:
        block += f'<h4>大盘 BETA 与宏观环境</h4>{condense(macro_text, 3)}'
    if block:
        html += f'<div class="text-block">{block}</div>'

    # Inst survey + equity incentive
    for kw in ['机构调研', '股权激励']:
        c = find_card(cards, kw)
        if c:
            if c['has_chart']:
                html += make_card(c['icon'], c['title'],
                                  c['badge_txt'], c['badge_col'],
                                  c['chart_b64'], c['narrative'])
            elif c['narrative'] and '无' not in c['narrative'][:20]:
                html += make_card(c['icon'], c['title'],
                                  c['badge_txt'], c['badge_col'],
                                  '', c['narrative'])

    return make_chapter('ch4', '📊', '第四章 · 估值参考', html)


def build_ch5(base_html: str, cards: list[dict]) -> str:
    html = ''

    # Company intro from LLM
    co_sec    = extract_base_section(base_html, '公司简介')
    co_what   = extract_h3_block(co_sec, '公司做什么', max_chars=600)
    co_rev    = extract_h3_block(co_sec, '营收业务结构', max_chars=500)
    co_fin    = extract_h3_block(co_sec, '财务景气度', max_chars=600)
    # extract charts in company section
    co_imgs   = re.findall(r'data:image/png;base64,([^"\']{20,})', co_sec)

    block = ''
    if co_what:
        block += f'<h4>公司做什么</h4>{condense(co_what, 2)}'
    if co_rev:
        block += f'<h4>营收业务结构</h4>{condense(co_rev, 2)}'
        if co_imgs:
            block += f'<img src="data:image/png;base64,{co_imgs[0]}" />'
    if co_fin:
        block += f'<h4>财务景气度</h4>{condense(co_fin, 2)}'
    if block:
        html += f'<div class="text-block">{block}</div>'

    # Supply chain from fundamental section
    fund_sec   = extract_base_section(base_html, '基本面')
    supply     = extract_h3_block(fund_sec, '供应链', max_chars=1000)
    if supply:
        html += (f'<div class="text-block"><h4>供应链上下游</h4>'
                 f'{condense(supply, 3)}</div>')

    # Macro environment brief
    macro_sec  = extract_base_section(base_html, '宏观环境')
    macro_sum  = extract_h3_block(macro_sec, '大类资产', max_chars=800)
    if macro_sum:
        html += (f'<div class="text-block"><h4>大类资产传导</h4>'
                 f'{condense(macro_sum, 2)}</div>')

    # Tracking table (keep as-is from LLM)
    track_sec = extract_base_section(base_html, '后续追踪')
    if track_sec:
        track_inner = re.sub(r'<section[^>]*>|</section>', '', track_sec, count=2)
        track_inner = re.sub(r'<[^>]*class="section-head"[^>]*>.*?</[a-z]+>', '',
                             track_inner, flags=re.DOTALL)
        html += f'<div class="text-block">{track_inner.strip()}</div>'

    return make_chapter('ch5', '🏭', '第五章 · 公司基本面', html)


# ── Main rebuild ──────────────────────────────────────────────────────────────

def rebuild(html_path: str, ts_code: str, trade_date: str) -> None:
    t0 = time.time()

    with open(html_path, encoding='utf-8') as f:
        html = f.read()

    guard     = '<!-- inject_signals_v2 -->'
    base_html = html[:html.find(guard)] if guard in html else html
    inject_html = html[html.find(guard) + len(guard):] if guard in html else ''

    print(f'  base={len(base_html):,}  inject={len(inject_html):,}')

    # Parse injected signal cards
    cards = parse_injected_cards(inject_html)
    print(f'  injected cards: {len(cards)}')
    for c in cards:
        print(f'    {c["title"][:35]:35s}  chart={c["has_chart"]}  badge={c["badge_txt"]}')

    # Re-run capital_dashboard for fresh radar chart
    radar_b64 = ''
    try:
        sys.path.insert(0, str(Path(html_path).resolve().parent.parent / 'scripts'))
        from capital_dashboard import capital_dashboard
        cd_result = capital_dashboard(ts_code, trade_date)
        radar_b64 = cd_result.get('chart_b64', '')
        print(f'  radar chart: {len(radar_b64):,} chars')
    except Exception as e:
        print(f'  [WARN] capital_dashboard: {e}')

    # Extract hero
    hero_m = re.search(r'<div class="hero".*?</div>\s*</div>', base_html, re.DOTALL)
    hero   = hero_m.group(0) if hero_m else ''

    # Extract title from hero for <title> tag
    title_m = re.search(r'<h1[^>]*>(.*?)</h1>', hero, re.DOTALL)
    page_title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip() if title_m else '深度研究'

    # Build chapters
    ch1 = build_ch1(base_html, cards)
    ch2 = build_ch2(base_html, cards)
    ch3 = build_ch3(base_html, cards, new_radar_b64=radar_b64)
    ch4 = build_ch4(base_html, cards)
    ch5 = build_ch5(base_html, cards)

    # Chapter nav
    nav = '''<nav class="ch-nav">
  <a href="#ch1">🎯 观点总结</a>
  <a href="#ch2">📈 量化分析</a>
  <a href="#ch3">💰 资金博弈</a>
  <a href="#ch4">📊 估值参考</a>
  <a href="#ch5">🏭 公司基本面</a>
</nav>'''

    # Assemble
    date_str = f'{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}'
    new_html = f'''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{page_title} - 深度研究 ({date_str})</title>
<style>{_CSS}</style>
</head>
<body>
<div class="container">
{hero}
{nav}
{ch1}
{ch2}
{ch3}
{ch4}
{ch5}
</div>
</body>
</html>'''

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(new_html)

    print(f'  rebuilt {len(new_html):,} chars  ({time.time()-t0:.1f}s) → {Path(html_path).name}')


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python rebuild_report.py <html_path> [ts_code] [trade_date]')
        sys.exit(1)
    html_path  = sys.argv[1]
    ts_code    = sys.argv[2] if len(sys.argv) > 2 else '688981.SH'
    trade_date = sys.argv[3] if len(sys.argv) > 3 else '20260520'
    rebuild(html_path, ts_code, trade_date)
