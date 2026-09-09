#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
涉税百问重识辅助脚本 v3
=========================
策略调整（相对 v2）：
- 原 md 已被前一轮清洗删去分页标记，无法"按段覆盖"
- 改为"分卷落盘 + 全量合并"：每批识别结果写入分卷文件，全 18 批完成后一次性合并
- 跨会话续做：进度文件 .workbuddy/.ocr_progress.json 记录已识别批号
- 兜底：失败可从分卷文件重启，不丢已有成果

用法：
  # 阶段 1：渲染试点 2 页
  python reocr_book.py render --pages 8 20 --dpi 200

  # 阶段 2：渲染下一批（按进度跳过已完成）
  python reocr_book.py render-next --batch-size 17 --dpi 200

  # 阶段 2.5：把单批识别结果落到分卷文件
  python reocr_book.py save-batch --batch 01

  # 阶段 4：全量合并（18 批都完成后）
  python reocr_book.py merge

  # 进度查询
  python reocr_book.py status
"""
import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

# ---- 路径常量 ----
ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "03_其他" / "破产业务参考书籍" / "企业破产涉税百问及经典案例解析_排序版.pdf"
MD = ROOT / "03_其他" / "破产业务参考书籍" / "企业破产涉税百问及经典案例解析.md"
TMP = ROOT / "03_其他" / "破产业务参考书籍" / "_reocr_tmp"
BATCH_DIR = TMP / "batches"  # 分卷目录
PROGRESS = ROOT / ".workbuddy" / ".ocr_progress.json"

# 书的页码体系：排序版 PDF 内多套页码并存（目录/序言/正文/探讨篇各自独立计数），
# 偏移量不恒定。为避免映射复杂，直接使用 PDF 物理页 1..292。
# 物理 293..304 为封底/空白页，无识别价值。
PHYS_PAGES = list(range(1, 293))


def render_page(scan_page, dpi=200):
    """渲染指定扫描页为 PNG（scan_page 是 1-based 物理页）。"""
    import pymupdf
    TMP.mkdir(parents=True, exist_ok=True)
    out = TMP / f"scan_{scan_page:03d}.png"
    if out.exists() and out.stat().st_size > 50000:
        return out
    doc = pymupdf.open(str(PDF))
    pix = doc[scan_page - 1].get_pixmap(dpi=dpi)
    pix.save(str(out))
    doc.close()
    return out


def load_progress():
    if PROGRESS.exists():
        return json.loads(PROGRESS.read_text(encoding='utf-8'))
    return {'completed_batches': [], 'completed_pages': [], 'last_update': None}


def save_progress(prog):
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    prog['last_update'] = datetime.datetime.now().isoformat(timespec='seconds')
    tmp = PROGRESS.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(prog, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(PROGRESS)


def all_phys_pages():
    """返回全部物理页列表（1~292）。"""
    return PHYS_PAGES


def pending_pages():
    prog = load_progress()
    done = set(prog.get('completed_pages', []))
    return [p for p in PHYS_PAGES if p not in done]


def next_batch(batch_size):
    """返回下一批要处理的物理页列表。"""
    pending = pending_pages()
    return pending[:batch_size]


def cmd_render(args):
    """渲染指定物理页为 PNG。"""
    for p in args.pages:
        out = render_page(p, dpi=args.dpi)
        print(f'物理页 {p} -> {out}  ({out.stat().st_size // 1024}KB)')


def cmd_render_next(args):
    """渲染下一批未处理物理页对应的 PNG。"""
    batch = next_batch(args.batch_size)
    if not batch:
        print('全部 292 页已渲染完成')
        return
    for p in batch:
        out = render_page(p, dpi=args.dpi)
        print(f'  物理页 {p} -> {out.name}  ({out.stat().st_size // 1024}KB)')
    print(f'\n本次渲染 {len(batch)} 页：{batch[0]}–{batch[-1]}')


def cmd_save_batch(args):
    """把指定批号的"待识别"内容从分卷暂存区移到正式分卷文件。

    使用方式：
    1. 我读图后输出文本，临时存到 _reocr_tmp/batches/batch_NN.md
    2. 运行此命令把它从暂存区移到正式分卷 + 更新进度
    """
    batch_id = args.batch  # 如 '01'
    src = BATCH_DIR / f"batch_{batch_id}.md"
    if not src.exists():
        print(f'ERROR: {src} 不存在；先在 _reocr_tmp/batches/ 下创建此文件', file=sys.stderr)
        sys.exit(1)
    # 从文件名推断这批覆盖的物理页
    prog = load_progress()
    if batch_id in prog.get('completed_batches', []):
        print(f'WARN: 批 {batch_id} 已在进度中，本次仅更新 timestamp', file=sys.stderr)
    # 扫描 batch 文件内 [[PHYS_PAGE:N]] 标记提取页号
    content = src.read_text(encoding='utf-8')
    pages = [int(m.group(1)) for m in re.finditer(r'\[\[PHYS_PAGE:(\d+)\]\]', content)]
    if batch_id not in prog['completed_batches']:
        prog['completed_batches'].append(batch_id)
    for p in pages:
        if p not in prog['completed_pages']:
            prog['completed_pages'].append(p)
    save_progress(prog)
    if pages:
        print(f'✓ 批 {batch_id} 已登记；覆盖物理页 {pages[0]}–{pages[-1]}（{len(pages)} 页）；进度已更新')
    else:
        print(f'✓ 批 {batch_id} 已登记（未发现 PHYS_PAGE 标记）')


def cmd_merge(args):
    """合并所有分卷文件 + 原 md 头部，整体重写。"""
    prog = load_progress()
    batches = sorted(prog.get('completed_batches', []))
    if len(batches) < 18:
        print(f'WARN: 仅 {len(batches)} 批完成（<18），不建议合并', file=sys.stderr)
        if not args.force:
            sys.exit(1)
    # 读取原 md 头部（第一行 + 说明）
    orig = MD.read_text(encoding='utf-8')
    orig_lines = orig.split('\n')
    # 头部：只保留标题 + 编著说明 + 说明行（前 3 行），封面文字由正文分卷首段承载，
    # 避免与 batch_01 的封面内容重复。
    head = '\n'.join(orig_lines[:3])
    # 合并所有分卷
    body_parts = []
    for bid in batches:
        bf = BATCH_DIR / f"batch_{bid}.md"
        if not bf.exists():
            print(f'WARN: 分卷 {bf} 缺失', file=sys.stderr)
            continue
        body_parts.append(bf.read_text(encoding='utf-8').rstrip())
    body = '\n\n'.join(body_parts)
    # 备份
    backup = MD.with_suffix('.md.bak')
    backup.write_text(orig, encoding='utf-8')
    # 新文件：头部 + 分卷
    new_content = head.rstrip() + '\n\n' + body + '\n'
    # 原子写入
    tmp = MD.with_suffix('.md.tmp')
    tmp.write_text(new_content, encoding='utf-8')
    tmp.replace(MD)
    print(f'✓ 合并完成：新 md {len(new_content)} 字符；备份于 {backup}')


def cmd_status(args):
    prog = load_progress()
    batches = prog.get('completed_batches', [])
    pages = prog.get('completed_pages', [])
    total_pages = len(PHYS_PAGES)
    print(f'总物理页数:   {total_pages}')
    print(f'已完成批数:   {len(batches)}/18')
    print(f'已识别页数:   {len(pages)}/{total_pages}')
    print(f'最后更新:     {prog.get("last_update", "(无)")}')
    if batches:
        print(f'已完成批次:   {batches}')
    remaining = total_pages - len(pages)
    print(f'剩余:         {remaining} 页（约 {remaining // 17 + (1 if remaining % 17 else 0)} 批）')


def main():
    parser = argparse.ArgumentParser(description='涉税百问重识辅助脚本 v3')
    sub = parser.add_subparsers(dest='cmd', required=True)

    p1 = sub.add_parser('render', help='渲染指定印刷页为 PNG')
    p1.add_argument('--pages', nargs='+', type=int, required=True)
    p1.add_argument('--dpi', type=int, default=200)
    p1.set_defaults(func=cmd_render)

    p2 = sub.add_parser('render-next', help='按进度渲染下一批')
    p2.add_argument('--batch-size', type=int, default=17)
    p2.add_argument('--dpi', type=int, default=200)
    p2.set_defaults(func=cmd_render_next)

    p3 = sub.add_parser('save-batch', help='把单批识别结果落盘到分卷')
    p3.add_argument('--batch', required=True, help='如 01、02、...')
    p3.set_defaults(func=cmd_save_batch)

    p4 = sub.add_parser('merge', help='合并所有分卷到主 md')
    p4.add_argument('--force', action='store_true')
    p4.set_defaults(func=cmd_merge)

    p5 = sub.add_parser('status', help='查看进度')
    p5.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
