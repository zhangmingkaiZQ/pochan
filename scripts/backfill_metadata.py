#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
破产业务数据库 .md 元数据回填脚本
==================================
将存量 .md 文件统一补齐到 mdconvert 新元数据规范：

  1. 每个 .md 头部写入「--- 置顶」的 YAML Front Matter；
  2. 同目录生成同名 metadata.json。

处理规则：
  - 法规类（01_中央法规/*、02_浙江省地方文件/*）：
      提取现有 front matter 字段（保留原值），重建为「--- 置顶」结构，
      并补充 title / source / source_format 基础字段；
      title 取自正文第一个 # 标题。
  - 索引类（00_索引/*）：
      补最小 front matter（title + 类型），来源说明保留在正文不动。
  - 参考书籍 / 会议纪要（03_其他/*）：
      补最小 front matter（title + 类型）。

用法：
  python backfill_metadata.py --dry-run    # 只预览，不写盘
  python backfill_metadata.py              # 正式执行，原地覆盖 + 生成 metadata.json
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 法规字段顺序（与 mdconvert 白名单一致）
LEGAL_KEYS = [
    "效力位阶", "文号", "制定机关", "发布机关",
    "公布日期", "发布日期", "施行日期", "时效性", "效力状态", "出处",
]

# 文件分类 -> (类型标签, 是否法规类)
def classify(path: Path):
    rel = path.relative_to(ROOT).as_posix()
    if rel.startswith("01_中央法规/") or rel.startswith("02_浙江省地方文件/"):
        return "法规", True
    if rel.startswith("00_索引/"):
        return "索引", False
    if "参考书籍" in rel:
        return "参考书籍", False
    if "培训" in rel or "会议" in rel or "纪要" in rel:
        return "会议纪要", False
    return "其他", False


def _quote_yaml(value: str) -> str:
    if value == "":
        return '""'
    if any(ch in value for ch in (":", "#")) or value != value.strip():
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def parse_front_matter(text: str):
    """解析现有 front matter（两种结构都兼容）。

    结构 A：--- 置顶（首行 ---）
    结构 B：标题在前（# 标题 / 说明 / --- / 字段 / ---）

    返回 (title, fields_dict, body_text)
    """
    lines = text.split("\n")

    # 结构 A：首行即 ---
    if lines and lines[0].strip() == "---":
        # 收集字段
        fields = {}
        idx = 1
        while idx < len(lines) and lines[idx].strip() != "---":
            m = re.match(r'^([^:：]+)[:：]\s*(.*)$', lines[idx])
            if m:
                fields[m.group(1).strip()] = m.group(2).strip()
            idx += 1
        if idx < len(lines) and lines[idx].strip() == "---":
            idx += 1  # 跳过结尾 ---
        # 跳过空行
        while idx < len(lines) and lines[idx] == "":
            idx += 1
        # 标题：front matter 后的第一个 # 标题
        title = ""
        for j in range(idx, len(lines)):
            m = re.match(r'^#\s+(.*)$', lines[j])
            if m:
                title = m.group(1).strip()
                break
        body = "\n".join(lines[idx:])
        return title, fields, body

    # 结构 B：标题在前，front matter 在中间
    title = ""
    fields = {}
    fm_start = -1
    for i, ln in enumerate(lines):
        if ln.strip() == "---":
            fm_start = i
            break
        m = re.match(r'^#\s+(.*)$', ln)
        if m and not title:
            title = m.group(1).strip()
    if fm_start == -1:
        # 无 front matter：整个文件视为 body
        return title, fields, text

    # 收集 fm_start 之后的字段
    idx = fm_start + 1
    while idx < len(lines) and lines[idx].strip() != "---":
        m = re.match(r'^([^:：]+)[:：]\s*(.*)$', lines[idx])
        if m:
            fields[m.group(1).strip()] = m.group(2).strip()
        idx += 1
    if idx < len(lines) and lines[idx].strip() == "---":
        idx += 1  # 跳过结尾 ---
    while idx < len(lines) and lines[idx] == "":
        idx += 1

    # body：保留 front matter 之前的标题行 + front matter 之后的内容
    head = lines[:fm_start]  # 含标题行
    tail = lines[idx:]
    # 去掉 head 末尾空行
    while head and head[-1] == "":
        head.pop()
    body = "\n".join(head + tail)
    return title, fields, body


def build_new_meta(title: str, fields: dict, src_path: Path, doc_type: str, is_legal: bool) -> dict:
    """构建新规范元数据字典。"""
    meta = {
        "title": title or src_path.stem,
        "source": src_path.name,
        "source_format": ".md",
        "converted_at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "tool": "mdconvert",
    }
    if is_legal:
        for key in LEGAL_KEYS:
            if key in fields and fields[key]:
                meta[key] = fields[key]
    else:
        meta["类型"] = doc_type
    return meta


def to_front_matter(meta: dict) -> str:
    lines = ["---"]
    for k, v in meta.items():
        lines.append(f"{k}: {_quote_yaml(str(v))}")
    lines.append("---")
    return "\n".join(lines)


def process_file(path: Path, dry_run: bool) -> bool:
    """处理单个 .md，返回是否有改动。"""
    text = path.read_text(encoding="utf-8")
    doc_type, is_legal = classify(path)
    title, fields, body = parse_front_matter(text)

    meta = build_new_meta(title, fields, path, doc_type, is_legal)
    new_fm = to_front_matter(meta)

    # 规整 body：去首尾空行，保证 front matter 后空一行
    body = body.strip("\n")
    new_text = new_fm + "\n\n" + body.rstrip() + "\n"

    json_path = path.with_suffix(".json")
    new_json = json.dumps(meta, ensure_ascii=False, indent=2) + "\n"

    changed = (new_text != text) or (not json_path.exists())

    if dry_run:
        status = "将改动" if changed else "无需改动"
        extra = ""
        if is_legal and fields:
            extra = f"（法规字段 {len(fields)} 项）"
        print(f"  [{status}] {path.relative_to(ROOT)}{extra}")
        return changed

    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
    if not json_path.exists() or json_path.read_text(encoding="utf-8") != new_json:
        json_path.write_text(new_json, encoding="utf-8")
    return changed


def iter_md_files():
    target_dirs = ["00_索引", "01_中央法规", "02_浙江省地方文件", "03_其他"]
    for d in target_dirs:
        full = ROOT / d
        if not full.is_dir():
            continue
        for dirpath, _, filenames in os.walk(full):
            for fn in sorted(filenames):
                if fn.endswith(".md"):
                    yield Path(dirpath) / fn


def main():
    parser = argparse.ArgumentParser(description="回填破产业务数据库 .md 元数据")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写盘")
    args = parser.parse_args()

    files = list(iter_md_files())
    print(f"共 {len(files)} 个 .md 文件\n")

    changed = 0
    for path in files:
        if process_file(path, dry_run=args.dry_run):
            changed += 1

    print(f"\n共 {changed} 个文件{'需处理' if args.dry_run else '已处理'} / {len(files)} 个文件")
    if args.dry_run:
        print("[DRY-RUN] 未写入。确认后去掉 --dry-run 正式执行。")
    else:
        print("[已执行] 元数据回填完成。")


if __name__ == "__main__":
    main()
