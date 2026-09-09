#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
破产业务数据库 .md 文件清洗脚本
================================
对 00_索引 / 01_中央法规 / 02_浙江省地方文件 / 03_其他 下的 .md 文件做确定性清洗。

规则：
  A. 通用机械清洗
     - 去除 BOM
     - 去除每行行尾空白（trailing whitespace）
     - 压缩连续空行：3 个及以上 -> 1 个
     - 删除 base64 内嵌图片行（含 data:image）
     - 删除 HTML 注释页码标记行（<!-- ===== ... -->）
  B. 元数据统一（法规类文件）
     - 将头部「> 字段：值」引用块 或「- **字段**：值」列表
       统一转换为 YAML front-matter（--- ... ---）
  C. 扫描件冗余页眉
     - 删除连续重复出现的书名/页眉行（OCR 每页重复的页眉）

用法：
  python clean_md.py --dry-run            # 只打印改动预览，不写盘
  python clean_md.py                      # 正式执行，原地覆盖
  python clean_md.py --target <dir>       # 指定根目录（默认脚本上一级目录）
"""
import os
import re
import sys
import argparse

# ---- 需要转为 front-matter 的元数据字段（按出现顺序保留）----
META_KEYS = ["效力位阶", "文号", "制定机关", "发布机关", "发布日期", "公布日期",
             "施行日期", "时效性", "效力状态", "出处"]

# ---- 识别为元数据字段的正则（引用块 / 列表两种形式）----
RE_META_QUOTE = re.compile(r'^>\s*(.+?)[：:]\s*(.*)$')
RE_META_LIST = re.compile(r'^-\s*\*\*(.+?)\*\*[：:]\s*(.*)$')

# ---- 需整行删除的模式 ----
RE_BASE64 = re.compile(r'data:image/[a-zA-Z]+;base64')
# HTML 注释页码标记：<!-- ===== ... ===== -->（两种变体：[无页码]（原扫描第N页）/ 第N页（原扫描第X页））
RE_HTML_COMMENT_PAGE = re.compile(r'^\s*<!--\s*=+\s*')
# 大模型重识物理页定位标记（独占一行）：[[PHYS_PAGE:N]]
RE_PHYS_PAGE = re.compile(r'^\s*\[\[PHYS_PAGE:\d+\]\]\s*$')
# 重识流程内部占位/说明注释（空白页标记、缺失页记录等）：<!-- ... -->
RE_PLACEHOLDER_COMMENT = re.compile(r'^\s*<!--\s*')
# 扫描件页脚页码标记（独占一行）：·111· 或 ·135.5·
RE_PAGE_NUM = re.compile(r'^\s*·\s*\d+(?:\.\d+)?\s*·\s*$')
# 扫描件冗余页眉（纯书名独立成行，不含 # 标题前缀）：企业破产涉税百问及经典案例解析
RE_BOOK_HEADER = re.compile(r'^企业破产涉税百问及经典案例解析\s*$')
# 图片标记（用于从混合行中剥离）：![任意文本](data:image...)
RE_IMG_TOKEN = re.compile(r'!\[[^\]]*\]\(data:image/[a-zA-Z]+;base64[^)]*\)')
# 行内编号前缀（如「26. 」「42. 」），当图片剥离后只剩编号时清理
RE_NUM_PREFIX = re.compile(r'^\s*\d{1,3}[.、]\s*')


def clean_text(text: str, path: str) -> str:
    """对单个文件的文本内容执行清洗，返回清洗后的文本。"""
    # 去 BOM
    if text.startswith('\ufeff'):
        text = text[1:]

    lines = text.split('\n')

    # 1. 去行尾空白
    lines = [ln.rstrip() for ln in lines]

    # 2. 处理 base64 图片行、HTML 注释页码标记行
    cleaned_lines = []
    for i, ln in enumerate(lines):
        if RE_HTML_COMMENT_PAGE.search(ln):
            continue  # 整行删除页码标记
        if RE_PHYS_PAGE.search(ln):
            continue  # 整行删除物理页定位标记 [[PHYS_PAGE:N]]
        if RE_PLACEHOLDER_COMMENT.search(ln):
            continue  # 整行删除重识占位注释 <!-- ... -->
        if RE_PAGE_NUM.search(ln):
            continue  # 整行删除页脚页码标记 ·N·
        # 扫描件冗余页眉：纯书名独立成行（不含 # 标题），且不在文件开头封面区（前 20 行）
        if RE_BOOK_HEADER.match(ln) and i >= 20:
            continue  # 整行删除冗余页眉书名
        if RE_BASE64.search(ln):
            # 剥离图片标记，保留行内其余文字
            stripped = RE_IMG_TOKEN.sub('', ln).strip()
            # 剥离后若为空或仅剩编号/强调符号，则整行删除
            if not stripped:
                continue
            # 去除残留的 ** 包裹空壳（如 "**阿里资产**" 保留，但 "** **" 之类删除）
            if stripped in ('**', '** **', '****'):
                continue
            # 若剥离后只剩编号前缀（如 "26." 或 "42."），删除
            if RE_NUM_PREFIX.match(stripped):
                after_num = RE_NUM_PREFIX.sub('', stripped).strip()
                if not after_num:
                    continue
                stripped = after_num
            # 清理图片剥离后残留的孤立 ** 或多余空格
            stripped = re.sub(r'\s{2,}', ' ', stripped)
            cleaned_lines.append(stripped)
            continue
        cleaned_lines.append(ln)
    lines = cleaned_lines

    # 3. 统一元数据为 front-matter（若存在元数据区块）
    lines = normalize_metadata(lines)

    # 4. 压缩连续空行：3+ -> 1
    lines = collapse_blank_lines(lines)

    # 5. 规整文件末尾：去掉末尾多余空行，保留单个换行
    while lines and lines[-1] == '':
        lines.pop()

    return '\n'.join(lines) + '\n'


def normalize_metadata(lines):
    """将文件头部的元数据区块（引用块或列表）统一为 YAML front-matter。

    识别范围：从第 1 行标题之后，到正文首个「##」/「#」标题或「---」之前。
    仅当连续出现 >= 2 条元数据字段时，才视为元数据区块并转换。
    """
    # 找到元数据区块：标题行（# ）之后的连续元数据行
    if not lines or not lines[0].startswith('# '):
        return lines

    meta_start = 1
    # 跳过标题后的空行
    while meta_start < len(lines) and lines[meta_start] == '':
        meta_start += 1

    # 收集连续元数据行
    meta_items = []
    idx = meta_start
    while idx < len(lines):
        ln = lines[idx]
        m = RE_META_QUOTE.match(ln)
        if m:
            key, val = m.group(1).strip(), m.group(2).strip()
            if key in META_KEYS:
                meta_items.append((key, val))
                idx += 1
                continue
        m = RE_META_LIST.match(ln)
        if m:
            key, val = m.group(1).strip(), m.group(2).strip()
            if key in META_KEYS:
                meta_items.append((key, val))
                idx += 1
                continue
        break

    # 仅当 >= 2 条元数据时才转换（避免误伤正文中的普通「- 」列表）
    if len(meta_items) < 2:
        return lines

    # 合并重复 key（如「发布日期」与「公布日期」并存时保留两个）
    # 生成 front-matter
    fm = ['---']
    for key, val in meta_items:
        fm.append(f'{key}: {val}')
    fm.append('---')

    # 元数据之后若紧跟「---」分隔线，则跳过原分隔线（避免双分隔线）
    after_meta = idx
    while after_meta < len(lines) and lines[after_meta] == '':
        after_meta += 1
    if after_meta < len(lines) and lines[after_meta].strip() == '---':
        after_meta += 1  # 跳过原 --- 分隔线

    # 保留标题行（lines[0]），在其后插入 front-matter
    title = [lines[0]]
    # 元数据后跳过连续空行，front-matter 后固定一个空行
    tail = lines[after_meta:]
    while tail and tail[0] == '':
        tail = tail[1:]
    return title + fm + [''] + tail


def collapse_blank_lines(lines):
    """压缩连续空行：3 个及以上 -> 1 个。"""
    result = []
    blank_run = 0
    for ln in lines:
        if ln == '':
            blank_run += 1
            if blank_run >= 3:
                continue  # 跳过第 3 个及以后
            result.append(ln)
        else:
            blank_run = 0
            result.append(ln)
    return result


def iter_md_files(root: str):
    """遍历目标分类目录下的 .md 文件，排除 .workbuddy 与 04_用户源文件。"""
    target_dirs = ['00_索引', '01_中央法规', '02_浙江省地方文件', '03_其他']
    for d in target_dirs:
        full = os.path.join(root, d)
        if not os.path.isdir(full):
            continue
        for dirpath, dirnames, filenames in os.walk(full):
            for fn in filenames:
                if fn.endswith('.md'):
                    yield os.path.join(dirpath, fn)


def main():
    parser = argparse.ArgumentParser(description='清洗破产业务数据库 .md 文件')
    parser.add_argument('--dry-run', action='store_true', help='只预览改动，不写盘')
    parser.add_argument('--target', default=os.path.dirname(os.path.abspath(__file__)) + os.sep + '..',
                        help='目标根目录（默认脚本上一级目录）')
    args = parser.parse_args()

    root = os.path.abspath(args.target)
    files = list(iter_md_files(root))
    print(f'目标根目录: {root}')
    print(f'发现 {len(files)} 个 .md 文件\n')

    total_changed = 0
    for path in files:
        with open(path, 'r', encoding='utf-8') as f:
            orig = f.read()
        cleaned = clean_text(orig, path)

        rel = os.path.relpath(path, root)
        if cleaned == orig:
            print(f'  [未改动] {rel}')
            continue

        total_changed += 1
        n_orig = orig.count('\n')
        n_new = cleaned.count('\n')
        # 统计删除的行类型
        removed_base64 = len(re.findall(r'data:image/[a-zA-Z]+;base64', orig))
        removed_page = len(RE_HTML_COMMENT_PAGE.findall(orig))

        print(f'  [有改动] {rel}')
        print(f'           行数 {n_orig} -> {n_new}  |  删base64图片 {removed_base64}  |  删页码标记 {removed_page}')

        if args.dry_run:
            # 打印简要 diff（仅显示被删除的行，用 - 前缀）
            import difflib
            diff = list(difflib.unified_diff(
                orig.splitlines(), cleaned.splitlines(),
                fromfile='before', tofile='after', lineterm=''))
            removed = [d for d in diff if d.startswith('-') and not d.startswith('---')]
            if removed:
                print('           删除内容预览:')
                for d in removed[:8]:
                    print(f'             {d}')
                if len(removed) > 8:
                    print(f'             ... 共删除 {len(removed)} 行')

    print(f'\n共 {total_changed} 个文件有改动 / {len(files)} 个文件')

    if args.dry_run:
        print('\n[DRY-RUN] 未写入任何文件。确认无误后去掉 --dry-run 正式执行。')
    else:
        # 正式执行：重新遍历并写盘
        written = 0
        for path in files:
            with open(path, 'r', encoding='utf-8') as f:
                orig = f.read()
            cleaned = clean_text(orig, path)
            if cleaned != orig:
                with open(path, 'w', encoding='utf-8', newline='') as f:
                    f.write(cleaned)
                written += 1
        print(f'\n[已执行] 共覆盖写入 {written} 个文件。')


if __name__ == '__main__':
    main()
