"""
model_track_record.py
=====================
模型历史选股记录分析

功能：
1. 读取 ashare-ml-strategy OOF 回测预测（oof_predictions.parquet）
2. 查询个股历史上被模型选入 Top-K 的所有交易日及次日实际涨跌幅
3. 统计当前排名 vs 历史分布，计算条件胜率、平均收益
4. 生成散点图（历史 Top-10 出现日 + 次日涨跌）
5. 输出真实数据驱动的叙事
"""

from __future__ import annotations
import io, base64
from pathlib import Path
import pandas as pd
import numpy as np

import matplotlib
matplotlib.use('Agg')
matplotlib.rcParams.update({
    'font.sans-serif': ['Microsoft YaHei', 'SimHei', 'DejaVu Sans'],
    'axes.unicode_minus': False,
})
import matplotlib.pyplot as plt

# ── 路径：ashare-ml-strategy 相对 ashare-daily/scripts ─────────────────────────
_SCRIPTS_DIR  = Path(__file__).resolve().parent
_ML_ROOT      = _SCRIPTS_DIR.parents[1] / 'ashare-ml-strategy'
_MODELS_DIR   = _ML_ROOT / 'data' / 'artifacts' / 'models'

def _find_oof_path(universe: str) -> Path:
    """Find oof_predictions.parquet for a universe, checking multiple locations."""
    candidates = [
        _MODELS_DIR / universe / 'oof_predictions.parquet',
        _MODELS_DIR / universe / 'tech_regime_v2' / 'oof_predictions.parquet',
        _MODELS_DIR / universe / 'ohlcv_v1' / 'oof_predictions.parquet',
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]  # will fail with exists() check later

_UNIVERSE_MAP = {
    'csi300':  _find_oof_path('csi300'),
    'csi1000': _find_oof_path('csi1000'),
}


def _detect_universe(ts_code: str) -> str:
    """Guess which universe parquet to load based on ts_code."""
    code = ts_code.split('.')[0]
    # Try csi300 first (contains 688xxx), fall back to csi1000
    p300 = _UNIVERSE_MAP['csi300']
    if p300.exists():
        try:
            df = pd.read_parquet(p300, columns=['ticker'])
            if code in df['ticker'].values:
                return 'csi300'
        except Exception:
            pass
    return 'csi1000'


def _load_oof(universe: str) -> pd.DataFrame | None:
    path = _UNIVERSE_MAP.get(universe)
    if path is None or not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        df['date'] = pd.to_datetime(df['date'])
        # compute rank per day
        df['rank'] = df.groupby('date')['pred'].rank(ascending=False, method='dense').astype(int)
        df['n_stocks'] = df.groupby('date')['ticker'].transform('count')
        return df
    except Exception as e:
        print(f'  [WARN] model_track_record load: {e}')
        return None


def _compute_metrics(df: pd.DataFrame, code: str, trade_date: str) -> dict:
    hits = df[df['ticker'] == code].copy()
    if len(hits) == 0:
        return {'found': False}

    ref_date = pd.Timestamp(trade_date)
    # only use OOF history up to and including trade_date
    history = hits[hits['date'] <= ref_date].copy()

    today_row = history[history['date'] == ref_date]
    current_rank = int(today_row['rank'].iloc[0]) if len(today_row) else None
    current_pred = float(today_row['pred'].iloc[0]) if len(today_row) else None
    n_stocks     = int(today_row['n_stocks'].iloc[0]) if len(today_row) else None

    # exclude today (no future y_t1 yet, likely NaN in oof)
    past = history[history['date'] < ref_date].dropna(subset=['y_t1'])

    def stats(subset):
        if len(subset) == 0:
            return None
        return {
            'n': len(subset),
            'avg_ret': float(subset['y_t1'].mean() * 100),
            'win_rate': float((subset['y_t1'] > 0).mean() * 100),
            'med_ret': float(subset['y_t1'].median() * 100),
        }

    return {
        'found': True,
        'code': code,
        'current_rank': current_rank,
        'current_pred': current_pred,
        'n_stocks': n_stocks,
        'total_days': len(past),
        'top1':  stats(past[past['rank'] == 1]),
        'top3':  stats(past[past['rank'] <= 3]),
        'top5':  stats(past[past['rank'] <= 5]),
        'top10': stats(past[past['rank'] <= 10]),
        'top20': stats(past[past['rank'] <= 20]),
        'top10_rows': past[past['rank'] <= 10].sort_values('date').copy(),
        'all_rows': past.copy(),
    }


def _build_chart(metrics: dict, ts_code: str) -> str:
    top10_rows = metrics['top10_rows']
    all_rows   = metrics['all_rows']
    code       = metrics['code']
    cur_rank   = metrics['current_rank']

    fig, (ax_left, ax_right) = plt.subplots(
        1, 2, figsize=(8, 2.8),
        gridspec_kw={'width_ratios': [4, 3]},
        facecolor='white'
    )
    fig.subplots_adjust(left=0.07, right=0.97, top=0.82, bottom=0.22, wspace=0.45)

    # ── 左图：Top-10 出现日 散点（排名 vs 次日涨跌）─────────────────────
    if len(top10_rows) > 0:
        scatter_ranks   = top10_rows['rank'].tolist()
        scatter_returns = (top10_rows['y_t1'] * 100).tolist()
        colors = ['#d62728' if r > 0 else '#1f77b4' for r in scatter_returns]
        ax_left.scatter(scatter_ranks, scatter_returns, c=colors, alpha=0.75, s=28, zorder=3)

        # highlight top-3
        for _, row in top10_rows[top10_rows['rank'] <= 3].iterrows():
            ax_left.annotate(
                row['date'].strftime('%m/%d'),
                xy=(row['rank'], row['y_t1'] * 100),
                fontsize=4.5, ha='right', va='bottom', color='#333'
            )

        # current rank marker
        if cur_rank is not None and cur_rank <= 15:
            ax_left.axvline(cur_rank, color='#ff7f0e', linewidth=1.5,
                            linestyle='--', alpha=0.8, label=f'今日排名#{cur_rank}')

        ax_left.axhline(0, color='#333', linewidth=0.8, linestyle='-', alpha=0.5)
        ax_left.set_xlabel('当日模型排名', fontsize=6.5)
        ax_left.set_ylabel('次日涨跌幅 (%)', fontsize=6.5)
        ax_left.set_title(f'{code} Top-10 出现记录（2021-2026 OOF）',
                          fontsize=7.5, fontweight='bold', pad=4)
        ax_left.tick_params(axis='both', labelsize=6)
        ax_left.legend(fontsize=5.5, loc='upper right', framealpha=0.7)
        ax_left.spines['top'].set_visible(False); ax_left.spines['right'].set_visible(False)
    else:
        ax_left.text(0.5, 0.5, '历史无Top-10记录', ha='center', va='center', fontsize=9)
        ax_left.axis('off')

    # ── 右图：条件统计表格 ───────────────────────────────────────────────
    ax_right.axis('off')
    rows_data = []
    for label, key in [('Top-1', 'top1'), ('Top-3', 'top3'), ('Top-5', 'top5'),
                       ('Top-10', 'top10'), ('Top-20', 'top20')]:
        s = metrics.get(key)
        if s and s['n'] > 0:
            rows_data.append([label, str(s['n']), f"{s['avg_ret']:+.1f}%", f"{s['win_rate']:.0f}%"])

    if rows_data:
        col_labels = ['区间', '次数', '均涨跌', '胜率']
        table = ax_right.table(
            cellText=rows_data,
            colLabels=col_labels,
            cellLoc='center',
            loc='center',
            bbox=[0.0, 0.15, 1.0, 0.75]
        )
        table.auto_set_font_size(False)
        table.set_fontsize(7)
        for (r, c), cell in table.get_celld().items():
            if r == 0:
                cell.set_facecolor('#1a6fc4')
                cell.set_text_props(color='white', fontweight='bold')
            elif r > 0 and c == 2:  # avg ret column
                try:
                    val = float(rows_data[r-1][2].replace('%',''))
                    cell.set_facecolor('#ffe0e0' if val < 0 else '#e0ffe0')
                except Exception:
                    pass
            cell.set_edgecolor('#ddd')

        # highlight current rank row
        if cur_rank is not None:
            title_row_label = (
                'Top-1' if cur_rank == 1 else
                'Top-3' if cur_rank <= 3 else
                'Top-5' if cur_rank <= 5 else
                'Top-10' if cur_rank <= 10 else None
            )

    ax_right.set_title(f'条件收益统计（历史OOF）', fontsize=7.5, fontweight='bold', pad=4)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def _build_narrative(ts_code: str, metrics: dict) -> str:
    code  = ts_code.split('.')[0]
    rank  = metrics['current_rank']
    n_stk = metrics['n_stocks'] or '?'
    total = metrics['total_days']

    def fmt_stats(s):
        if not s or s['n'] == 0:
            return '无记录'
        return f"{s['n']}次，均值{s['avg_ret']:+.1f}%，胜率{s['win_rate']:.0f}%"

    top1_txt  = fmt_stats(metrics.get('top1'))
    top10_txt = fmt_stats(metrics.get('top10'))

    rank_label = ''
    if rank == 1:
        rank_label = '**排名第1**'
        key_stats  = metrics.get('top1')
    elif rank and rank <= 3:
        rank_label = f'**排名第{rank}**'
        key_stats  = metrics.get('top3')
    elif rank and rank <= 10:
        rank_label = f'**排名第{rank}**'
        key_stats  = metrics.get('top10')
    else:
        rank_label = f'排名第{rank}（共{n_stk}只）'
        key_stats  = metrics.get('top20')

    if key_stats and key_stats['n'] > 0:
        cond_txt = (
            f"历史上同等排名区间内共触发**{key_stats['n']}次**，"
            f"次日平均涨跌**{key_stats['avg_ret']:+.1f}%**，胜率**{key_stats['win_rate']:.0f}%**。"
        )
    else:
        cond_txt = '历史同等排名区间触发次数较少，参考价值有限。'

    return (
        f"量化模型（LightGBM OOF回测，2021-2026）在{total}个交易日中追踪{code}，"
        f"今日{rank_label}（共{n_stk}只股票）。"
        f"{cond_txt}"
        f"历史Top-1记录：{top1_txt}；Top-10记录：{top10_txt}。"
        f"以上数据来自**真实走步预测（OOF）**，非模型拟合期内结果，不存在前视偏差。"
    )


def model_track_record(ts_code: str, trade_date: str,
                       universe: str | None = None) -> dict:
    result = {
        'ts_code': ts_code, 'trade_date': trade_date,
        'current_rank': None, 'n_stocks': None,
        'signal': '无数据', 'chart_b64': '', 'narrative': '', 'error': None,
    }

    code = ts_code.split('.')[0]

    # auto-detect universe
    if universe is None:
        universe = _detect_universe(ts_code)

    oof = _load_oof(universe)
    if oof is None:
        result['error'] = f'oof_predictions.parquet not found for {universe}'
        result['narrative'] = f'【{code}】模型回测数据文件未找到（{universe}）。'
        return result

    metrics = _compute_metrics(oof, code, trade_date)
    if not metrics.get('found'):
        result['signal'] = '未纳入宇宙'
        result['narrative'] = f'【{code}】未出现在{universe}回测数据中。'
        return result

    rank = metrics['current_rank']
    result['current_rank'] = rank
    result['n_stocks']     = metrics['n_stocks']

    # signal label based on current rank
    if rank == 1:
        result['signal'] = '模型第1名'
    elif rank and rank <= 3:
        result['signal'] = f'模型Top3（#{rank}）'
    elif rank and rank <= 10:
        result['signal'] = f'模型Top10（#{rank}）'
    elif rank and rank <= 20:
        result['signal'] = f'模型Top20（#{rank}）'
    else:
        result['signal'] = f'模型#{rank}'

    print(f'  模型记录：{universe} 排名#{rank}/{metrics["n_stocks"]} '
          f'pred={metrics["current_pred"]:.5f}  '
          f'top10={metrics.get("top10", {}).get("n", 0)}次 '
          f'avg={metrics.get("top10", {}).get("avg_ret", 0):+.1f}%')

    try:
        result['chart_b64'] = _build_chart(metrics, ts_code)
    except Exception as e:
        print(f'  [WARN] model_track_record chart: {e}')

    result['narrative'] = _build_narrative(ts_code, metrics)
    return result


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    for code, date in [('688981.SH', '20260520'), ('000880.SZ', '20260520')]:
        print(f'\n{"="*50}\n{code}')
        r = model_track_record(code, date)
        print(f'  信号: {r["signal"]}  排名: {r["current_rank"]}/{r["n_stocks"]}')
        print(f'  叙事: {r["narrative"][:120]}...')
