# -*- coding: utf-8 -*-
"""
对破产业务数据库中的会议纪要人名 + 索引账号积分做脱敏。

原则：
1. 只处理用户明确指定的两处内容，法规/书籍/其他文件一律不动。
2. 人名采用"保留姓氏、名打码"（杨忠孝 -> 杨**），可读性好、可追溯。
3. 输出映射表 .json，便于审计与逆回。
4. 用占位符机制，避免人名互相包含时的误替换。

用法：
  python mask_db.py
"""
import json
import os
import re

# 数据库根目录（脚本位于 scripts/ 下，根目录是其上一级）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ============================================================
# 一、人名清单（仅这两份会议纪要中出现的"发言人/授课人"真实人名）
# ============================================================
PERSON_NAMES = [
    # 宁波市破产法学会.md
    "杨忠孝", "徐阳光", "任一民", "徐峻峰", "申林平", "俞秋玮",
    "高美丽", "徐璐", "冯坚", "严凌振", "林燕", "房伟",
    "俞文杰", "李宇", "任意", "朱黎", "江丁库", "骆忠红",
    "刘东方", "陈佳", "徐伟", "严伟坤",
    # 浙江省破协2026年度培训.md
    "冯旭峰", "庄加园",
]

# ============================================================
# 二、脱敏函数：保留姓氏，名打码
# ============================================================
def mask_name(name: str) -> str:
    if len(name) <= 1:
        return name
    return name[0] + "*" * (len(name) - 1)


# ============================================================
# 三、对单个文件文本做一致性替换
# ============================================================
def mask_text(text: str, names: list):
    """返回 (脱敏后文本, 映射表 dict)"""
    # 只替换实际出现在文本中的人名
    present = sorted({n for n in names if n in text}, key=len, reverse=True)
    mapping = {n: mask_name(n) for n in present}

    # 占位符中转，避免"徐"与"徐峻峰/徐璐/徐伟/徐阳光"等互相包含误伤
    result = text
    phs = []
    for i, n in enumerate(present):
        ph = f"\x00M{i}\x00"
        result = result.replace(n, ph)
        phs.append((ph, mapping[n]))
    for ph, rep in phs:
        result = result.replace(ph, rep)
    return result, mapping


# ============================================================
# 四、主流程
# ============================================================
def main():
    jobs = [
        ("03_其他/破产业务培训与会议纪要/宁波市破产法学会.md", None),
        ("03_其他/破产业务培训与会议纪要/浙江省破协2026年度培训.md", None),
    ]
    total_map = {}

    for rel, _ in jobs:
        path = os.path.join(ROOT, rel)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        masked, mapping = mask_text(text, PERSON_NAMES)
        total_map.update(mapping)
        with open(path, "w", encoding="utf-8") as f:
            f.write(masked)
        print(f"[已脱敏] {rel}  ->  替换 {len(mapping)} 个人名")

    # 索引账号积分脱敏
    idx_rel = "00_索引/破产法规总索引.md"
    idx_path = os.path.join(ROOT, idx_rel)
    with open(idx_path, "r", encoding="utf-8") as f:
        idx_text = f.read()
    idx_masked, n = re.subn(
        r"当前余额约 \d+ 分", "当前余额约 [已脱敏] 分", idx_text
    )
    with open(idx_path, "w", encoding="utf-8") as f:
        f.write(idx_masked)
    print(f"[已脱敏] {idx_rel}  ->  账号积分 {n} 处")

    # 写映射表
    map_path = os.path.join(ROOT, "00_索引/脱敏映射表.json")
    with open(map_path, "w", encoding="utf-8") as f:
        json.dump(total_map, f, ensure_ascii=False, indent=2)
    print(f"[已写出] 映射表 -> {map_path}")
    print("\n映射表（原文 -> 脱敏后）：")
    for k, v in total_map.items():
        print(f"  {k}  ->  {v}")


if __name__ == "__main__":
    main()
