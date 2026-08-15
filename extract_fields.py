#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
品种审定数据 · 通用字段提取框架 (extract_fields.py)
=================================================
从 varieties.json 的四大文本字段（原文已在数据中）按需提取任意结构化字段。

设计思路：
  - 数据是"原料"：varietyCharacter(品种特性) / outputExpression(产量表现)
    / plantRequirment(栽培要点) / judgementSuggestion(审定意见)
  - 本框架是"加工厂"：每条提取器 = 一个函数，从原料文本中抠数值
  - 想提取新字段：写一个函数 + 注册到 EXTRACTORS 即可，不用改抓取脚本

用法：
  python extract_fields.py                      # 提取全部已注册字段
  python extract_fields.py --only disease       # 只跑指定提取器
  python extract_fields.py --crop 玉米           # 只处理某作物
  python extract_fields.py --out enriched.json  # 指定输出文件

输出：
  enriched.json  = 原记录 + 新提取字段（默认写到本目录）
"""
import json
import os
import re
import sys
import argparse

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_JSON = os.path.join(OUT_DIR, "varieties.json")
OUT_JSON = os.path.join(OUT_DIR, "enriched.json")


def _n(text):
    return text or ""


def _first_num(text, pats):
    """按模式列表依次匹配，返回第一个数字(float)或 None"""
    for p in pats:
        m = re.search(p, text)
        if m:
            try:
                return float(m.group(1))
            except (ValueError, IndexError):
                continue
    return None


# ============================================================
# 提取器定义区：每个函数签名 (record: dict) -> dict
# 返回的 dict 会合并进记录（键冲突时覆盖）
# ============================================================

def extract_density(record):
    """建议密度：栽培要点里 '公顷保苗X万株' / '每亩保苗X株' -> 株/亩"""
    t = _n(record.get("plantRequirment"))
    v = _first_num(t, [r"公顷保苗\s*([\d.]+)\s*万株"])
    if v is not None:
        return {"densityMu": round(v * 10000 / 15)}
    v = _first_num(t, [r"每亩[保定]?苗\s*([\d.]+)\s*千株", r"每亩[保定]?苗\s*([\d.]+)\s*株"])
    if v is not None:
        return {"densityMu": round(v * 1000) if v < 100 else round(v)}
    return {}


def extract_yields(record):
    """产量：产量表现里的公顷产量 -> 平均/最高 kg/亩"""
    t = _n(record.get("outputExpression"))
    nums = [float(m) for m in re.findall(r"([\d.]+)\s*公斤", t)]
    nums = [n for n in nums if 3000 <= n <= 30000]
    if not nums:
        return {}
    return {
        "avgYieldMu": round(sum(nums) / len(nums) / 15),
        "maxYieldMu": round(max(nums) / 15),
    }


def extract_fert(record):
    """施肥：栽培要点里的基肥/追肥实物量(kg/亩)"""
    t = _n(record.get("plantRequirment"))
    base, top = [], []
    seen = set()
    # 常见肥料名 + 数值（完整名优先，避免"二铵"重复匹配"磷酸二铵"）
    for name in ["磷酸二铵", "硫酸钾", "尿素", "复合肥", "氯化钾", "有机肥", "农家肥"]:
        for m in re.finditer(re.escape(name) + r"\s*([\d.]+)\s*公斤", t):
            amt = round(float(m.group(1)) / 15, 1)
            item = f"{name}{amt}"
            if item not in seen:
                seen.add(item)
                base.append(item)
    return {
        "baseFertMu": "、".join(base) if base else None,
        "fertRaw": t,
    }


def extract_sowing(record):
    """播期：栽培要点里的 'X月X日左右播种' """
    t = _n(record.get("plantRequirment"))
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", t)
    if m:
        return {"sowingDate": f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"}
    return {}


def extract_quality(record):
    """品质指标：品种特性里的 容重/粗蛋白/粗脂肪/粗淀粉/赖氨酸 """
    t = _n(record.get("varietyCharacter"))
    out = {}
    # 容重可能为区间 "756～762克/升"，取第一个
    q = _first_num(t, [r"容重\s*([\d.]+)\s*克/?[\/]?升", r"容重\s*([\d.]+)\s*～?\s*[\d.]+\s*克"])
    if q: out["bulkDensity"] = q
    for key, pat in [
        ("protein", r"粗蛋白(?:含量)?\s*([\d.]+)\s*%"),
        ("fat", r"粗脂肪(?:含量)?\s*([\d.]+)\s*%"),
        ("starch", r"粗淀粉(?:含量)?\s*([\d.]+)\s*%"),
        ("lysine", r"赖氨酸(?:含量)?\s*([\d.]+)\s*%"),
    ]:
        v = _first_num(t, [pat])
        if v: out[key] = v
    return out


def extract_disease(record):
    """抗病性：品种特性里的 '抗X病/中抗X病/感X病/高感X病' 及 '抗X病和Y病' """
    t = _n(record.get("varietyCharacter"))
    found = {}
    rank_map = {"高抗": 5, "抗": 4, "中抗": 3, "感": 2, "高感": 1}
    # 病名: 1-8个非连接词汉字 + 病（非贪婪，避免吞掉"和"）
    pat = r"([^，。、；和\s]{1,8}?病)(?:和([^，。、；和\s]{1,8}?病))?"
    # 负向后顾避免"中抗/高抗"里的"抗"、"高感"里的"感"被重复匹配
    level_pats = [
        ("高抗", r"高抗" + pat),
        ("中抗", r"中抗" + pat),
        ("高感", r"高感" + pat),
        ("抗",   r"(?<![中高])抗" + pat),
        ("感",   r"(?<!高)感" + pat),
    ]
    for level, lp in level_pats:
        for m in re.finditer(lp, t):
            for g in (m.group(1), m.group(2)):
                if not g:
                    continue
                key = f"disease_{g}"
                rank = rank_map.get(level, 4)
                if key not in found or rank > found[key][1]:
                    found[key] = (level, rank)
    return {k: v[0] for k, v in found.items()}


def extract_height(record):
    """株高/穗位高/百粒重（cm / g）"""
    t = _n(record.get("varietyCharacter"))
    out = {}
    v = _first_num(t, [r"株高\s*([\d.]+)\s*厘米"])
    if v: out["plantHeight"] = v
    v = _first_num(t, [r"穗位高\s*([\d.]+)\s*厘米"])
    if v: out["earHeight"] = v
    v = _first_num(t, [r"百粒重\s*([\d.]+)\s*克"])
    if v: out["hundredGrainW"] = v
    return out


def extract_region(record):
    """适宜区域（已由抓取脚本提取 suitableRegion，这里兜底重算）"""
    t = _n(record.get("judgementSuggestion"))
    m = re.search(r"适宜[^。；\n]{2,80}?(?:种植|栽培|推广)", t)
    if m:
        return {"suitableRegion": m.group(0).strip()}
    return {}


# ============================================================
# 注册表：新增提取器只需在此加一行
# ============================================================
EXTRACTORS = {
    "density": extract_density,
    "yields": extract_yields,
    "fert": extract_fert,
    "sowing": extract_sowing,
    "quality": extract_quality,
    "disease": extract_disease,
    "height": extract_height,
    "region": extract_region,
}


def main():
    ap = argparse.ArgumentParser(description="品种审定数据字段提取")
    ap.add_argument("--only", help="只运行指定提取器(逗号分隔)")
    ap.add_argument("--crop", help="只处理指定作物(玉米/大豆/水稻)")
    ap.add_argument("--out", default=OUT_JSON, help="输出JSON路径")
    ap.add_argument("--src", default=SRC_JSON, help="输入JSON路径(默认 varieties.json)")
    args = ap.parse_args()

    if not os.path.exists(args.src):
        print(f"找不到输入文件: {args.src}")
        print("请确认抓取已完成并生成了 varieties.json")
        sys.exit(1)

    data = json.load(open(args.src, encoding="utf-8"))
    print(f"输入: {len(data)} 条记录")

    if args.only:
        names = [x.strip() for x in args.only.split(",") if x.strip()]
        run = {n: EXTRACTORS[n] for n in names if n in EXTRACTORS}
        missing = [n for n in names if n not in EXTRACTORS]
        if missing:
            print("未知提取器:", missing, "可用:", list(EXTRACTORS.keys()))
    else:
        run = EXTRACTORS
    print(f"运行提取器: {list(run.keys())}")

    stats = {k: 0 for k in run}
    for rec in data:
        if args.crop and rec.get("crop") != args.crop:
            continue
        for name, fn in run.items():
            try:
                extra = fn(rec)
                if extra:
                    rec.update(extra)
                    stats[name] += 1
            except Exception as e:
                print(f"  提取器 {name} 处理 {rec.get('judgementNo')} 出错: {e}")

    out_path = args.out
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"输出: {out_path} ({len(data)} 条)")

    print("\n提取命中统计(记录数):")
    for k, v in stats.items():
        print(f"  {k:12s} {v}")


if __name__ == "__main__":
    main()
