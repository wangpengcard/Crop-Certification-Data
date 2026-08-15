#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中国种业大数据平台 - 主要农作物品种审定数据抓取 v2 (多作物)
数据源: http://202.127.42.145 (纯HTTP明文)
  列表: /VarietyAuthorize/GetVarietyAuthorizeList   (分页JSON)
  详情: /bigdataNew/BA/GetAnnouncementInfo?judgementNo=xxx (审定公告全文)

用法:
  # 全量抓取(玉米/大豆/水稻, 2010年后), 断点续传
  python fetch_varieties_v2.py --crops 玉米,大豆,水稻 --since 2010

  # 每月增量更新: 只抓列表里新增的审定编号, 快
  python fetch_varieties_v2.py --crops 玉米,大豆,水稻 --since 2010 --incremental

  # 小样本验证
  python fetch_varieties_v2.py --crops 玉米 --since 2010 --limit 20
"""
import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

BASE = "http://202.127.42.145"
LIST_URL = BASE + "/VarietyAuthorize/GetVarietyAuthorizeList"
DETAIL_URL = BASE + "/bigdataNew/BA/GetAnnouncementInfo"
ROWS_PER_PAGE = 100
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0"}
OUT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.path.join(OUT_DIR, "varieties.json")
OUT_CSV = os.path.join(OUT_DIR, "varieties.csv")
PROGRESS = os.path.join(OUT_DIR, "progress.json")
DELAY = 0.15
RETRY = 3

# 每作物独立文件：varieties_玉米.json / varieties_大豆.json / varieties_水稻.json
def crop_json_path(crop):
    return os.path.join(OUT_DIR, f"varieties_{crop}.json")

def crop_csv_path(crop):
    return os.path.join(OUT_DIR, f"varieties_{crop}.csv")

FIELDS = ["judgementNo", "varietyName", "crop", "transgenosis", "year", "region",
          "applyCompany", "breedingCompany", "varietySource", "growthDays", "gdd",
          "suitableRegion", "densityMu", "avgYieldMu", "maxYieldMu", "baseFertMu",
          "varietyCharacter", "outputExpression", "plantRequirment",
          "judgementSuggestion", "announcementNo", "transformantName"]


def http_get(url, retries=8, base_wait=2.0):
    """带退避重试的 GET；ConnectionRefused/超时等瞬时故障多试几次（8次×递增等待≈最长2分钟）
       全部失败返回 None（由调用方决定跳过或继续），不再 raise 导致整个任务崩溃"""
    req = urllib.request.Request(url, headers=HEADERS)
    last_err = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as e:
            last_err = e
            if i == retries - 1:
                break
            time.sleep(base_wait * (i + 1))
    print(f"  [http_get失败] {url[:80]} 最后错误: {last_err}")
    return None


def load_progress():
    """兼容 v1(list) 与 v2(dict) 两种格式"""
    raw = {}
    if os.path.exists(PROGRESS):
        with open(PROGRESS, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict) and "crops" in raw:
            return raw
    # v1 格式迁移: {"done": [no...], "failed": [no...]} -> 归到玉米
    v1 = raw if isinstance(raw, dict) else {}
    return {
        "crops": {
            "玉米": {
                "done": list(v1.get("done", [])),
                "failed": list(v1.get("failed", [])),
            }
        }
    }


def save_progress(prog):
    with open(PROGRESS, "w", encoding="utf-8") as f:
        json.dump(prog, f, ensure_ascii=False)


def fetch_list_page(crop, page):
    params = {
        "VarietyName": "", "CropID": crop, "JudgementRegionID": "",
        "_search": "false", "nd": str(int(time.time() * 1000)),
        "rows": ROWS_PER_PAGE, "page": page, "sidx": "", "sord": "asc",
    }
    url = LIST_URL + "?" + urllib.parse.urlencode(params)
    raw = http_get(url)
    return json.loads(raw) if raw else None


def fetch_detail(judgement_no):
    url = DETAIL_URL + "?judgementNo=" + urllib.parse.quote(judgement_no)
    raw = http_get(url)
    if not raw:
        return None
    try:
        s = json.loads(raw)
        arr = json.loads(s) if isinstance(s, str) else s
        return arr[0] if arr else {}
    except Exception:
        try:
            s = raw.replace("\\", "")[2:-2]
            arr = json.loads(s)
            return arr[0] if arr else {}
        except Exception:
            return None


def extract_days(text):
    if not text:
        return None
    range_pats = [
        r"出苗至成熟[^，。]{0,12}?(\d+(?:\.\d+)?)\s*[～~—\-至]\s*\d+\s*天",
        r"出苗至成熟期[^，。]{0,12}?(\d+(?:\.\d+)?)\s*[～~—\-至]\s*\d+\s*天",
        r"生育期[^，。]{0,14}?(\d+(?:\.\d+)?)\s*[～~—\-至]\s*\d+\s*天",
        r"全生育期[^，。]{0,10}?(\d+(?:\.\d+)?)\s*[～~—\-至]\s*\d+\s*天",
    ]
    for p in range_pats:
        m = re.search(p, text)
        if m:
            return float(m.group(1))
    pats = [
        r"出苗至成熟[^，。]{0,12}?(\d+(?:\.\d+)?)\s*天",
        r"出苗至成熟期[^，。]{0,12}?(\d+(?:\.\d+)?)\s*天",
        r"生育期[^，。]{0,14}?(\d+(?:\.\d+)?)\s*天",
        r"全生育期[^，。]{0,10}?(\d+(?:\.\d+)?)\s*天",
        r"出苗至鲜穗采收期[^，。]{0,8}?(\d+(?:\.\d+)?)\s*天",
        r"[播种至|春播|春季]?播种至?采收鲜穗[约]?(\d+(?:\.\d+)?)\s*天",
    ]
    for p in pats:
        m = re.search(p, text)
        if m:
            return float(m.group(1))
    return None


def extract_gdd(char_text, sugg_text):
    for text in (char_text, sugg_text):
        if not text:
            continue
        pats = [
            r"≥?\s*10\s*℃[^，。]{0,6}?活动积温\s*(\d+)\s*℃",
            r"活动积温\s*(\d+)\s*℃",
            r"需有效积温\s*(\d+)\s*℃",
        ]
        for p in pats:
            m = re.search(p, text)
            if m:
                return float(m.group(1))
    return None


def extract_region(suggestion):
    if not suggestion:
        return None
    m = re.search(r"适宜[^。；\n]{2,80}?(?:种植|栽培|推广)", suggestion)
    if m:
        return m.group(0).strip()
    m = re.search(r"[^。；\n]{2,60}?(?:区|地区)?(?:种植|栽培)", suggestion)
    if m:
        return m.group(0).strip()
    return None


def extract_density_mu(plant_req):
    """从栽培要点提取公顷保苗数并折算株/亩"""
    if not plant_req:
        return None
    m = re.search(r"公顷保苗\s*([\d.]+)\s*万株", plant_req)
    if m:
        return round(float(m.group(1)) * 10000 / 15)
    m = re.search(r"每亩[保定]?苗\s*([\d.]+)\s*千株", plant_req)
    if m:
        return round(float(m.group(1)) * 1000)
    m = re.search(r"每亩[保定]?苗\s*([\d.]+)\s*株", plant_req)
    if m:
        return round(float(m.group(1)))
    return None


def extract_yields(output_expr):
    """从产量表现提取所有产量数值(kg/公顷)，返回 (平均kg/亩, 最高kg/亩) 折算14%水前"""
    if not output_expr:
        return (None, None)
    nums = []
    for m in re.finditer(r"([\d.]+)\s*公斤", output_expr):
        v = float(m.group(1))
        if 3000 <= v <= 30000:  # 公顷产量合理范围
            nums.append(v)
    if not nums:
        return (None, None)
    avg_ha = sum(nums) / len(nums)
    max_ha = max(nums)
    return (round(avg_ha / 15), round(max_ha / 15))  # kg/亩


def extract_fert_mu(plant_req):
    """从栽培要点提取公顷施肥实物量并折算kg/亩，返回 {base:str, top:str} 展示用"""
    if not plant_req:
        return None
    base_parts, top_parts = [], []
    for m in re.finditer(r"(磷酸二铵|硫酸钾|尿素|复合肥|氯化钾|二铵|钾肥)\s*([\d.]+)\s*公斤", plant_req):
        name, amt = m.group(1), round(float(m.group(2)) / 15, 1)
        base_parts.append(f"{name}{amt}")
    return {
        "base": "、".join(base_parts) if base_parts else None,
        "raw": plant_req.strip()
    }


def parse_record(d):
    char = d.get("VarietyCharacter") or ""
    sugg = d.get("JudgementSuggestion") or ""
    plant = d.get("PlantRequirment") or ""
    out = d.get("OutputExpression") or ""
    avg_y, max_y = extract_yields(out)
    fert = extract_fert_mu(plant)
    return {
        "judgementNo": (d.get("JudgementNo") or "").strip(),
        "varietyName": (d.get("VarietyName") or "").strip(),
        "crop": d.get("CropID"),
        "transgenosis": d.get("IsTransgenosis"),
        "year": d.get("JudgementYear"),
        "region": d.get("JudgementRegionID"),
        "applyCompany": (d.get("ApplyCompany") or "").strip(),
        "breedingCompany": (d.get("BreedingCompany") or "").strip(),
        "varietySource": (d.get("VarietySource") or "").strip(),
        "growthDays": extract_days(char),
        "gdd": extract_gdd(char, sugg),
        "suitableRegion": extract_region(sugg),
        "densityMu": extract_density_mu(plant),
        "avgYieldMu": avg_y,
        "maxYieldMu": max_y,
        "baseFertMu": fert["base"] if fert else None,
        "varietyCharacter": char.strip(),
        "outputExpression": out.strip(),
        "plantRequirment": plant.strip(),
        "judgementSuggestion": sugg.strip(),
        "announcementNo": (d.get("AnnouncementID") or "").strip(),
        "transformantName": (d.get("TransformantName") or "").strip() if d.get("TransformantName") else None,
    }


def main():
    crops = ["玉米"]
    since = None
    limit = None
    incremental = False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--crops":
            crops = [c.strip() for c in args[i + 1].split(",") if c.strip()]
            i += 2
        elif args[i] == "--since":
            since = int(args[i + 1]); i += 2
        elif args[i] == "--limit":
            limit = int(args[i + 1]); i += 2
        elif args[i] == "--incremental":
            incremental = True; i += 1
        else:
            i += 1

    prog = load_progress()
    for crop in crops:
        if crop not in prog["crops"]:
            prog["crops"][crop] = {"done": [], "failed": []}
    save_progress(prog)

    global_results = []
    for crop in crops:
        state = prog["crops"][crop]
        done_set = set(state["done"])
        failed_set = set(state["failed"])
        print(f"\n===== 作物: {crop} | 已抓 {len(done_set)} | 失败 {len(failed_set)}"
              f"{' | 增量模式' if incremental else ''} =====")

        # 列表全量
        all_items = []
        page = 1
        total_pages = None
        page_fail = 0
        while True:
            data = fetch_list_page(crop, page)
            if data is None:
                page_fail += 1
                if page_fail >= 5:   # 连续5次失败则放弃该页，跳到下一页（防死循环）
                    print(f"  列表第{page}页连续失败{page_fail}次，跳过")
                    page += 1
                    page_fail = 0
                    continue
                time.sleep(3)
                continue
            page_fail = 0
            if total_pages is None:
                total_pages = data.get("total", 0)
                print(f"  总记录: {data.get('records', 0)}, 总页数: {total_pages}")
                if limit:
                    total_pages = min(total_pages, (limit + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE)
            rows = data.get("rows", [])
            all_items.extend(rows)
            if page >= total_pages or not rows:
                break
            page += 1
            time.sleep(DELAY)
        print(f"  列表获取完成: {len(all_items)} 条")

        # 年份过滤
        if since:
            before = len(all_items)
            all_items = [it for it in all_items if (it.get("JudgementYear") or 0) >= since]
            print(f"  年份过滤(>={since}): {before} -> {len(all_items)}")

        if incremental:
            # 增量: 只抓对应作物独立文件里还没有详情的（而非 progress 的 done，防止中断后数据丢失）
            existing_map = load_existing(crop)
            todo = [it for it in all_items if (it.get("JudgementNo") or "").strip() not in existing_map]
            print(f"  增量模式: 新增 {len(todo)} 条待抓 (跳过 {len(all_items) - len(todo)} 条已有详情)")
            all_items = todo

        # 详情
        results = []
        new_failed = []
        for idx, item in enumerate(all_items):
            no = (item.get("JudgementNo") or "").strip()
            if not no or no in done_set:
                continue
            if limit and len(results) >= limit:
                break
            d = fetch_detail(no)
            if d:
                rec = parse_record(d)
                rec["crop"] = crop
                results.append(rec)
                global_results.append(rec)
                done_set.add(no)
            else:
                new_failed.append(no)
                failed_set.add(no)
            if (idx + 1) % 50 == 0:
                prog["crops"][crop] = {"done": list(done_set), "failed": list(failed_set)}
                save_progress(prog)
                save_incremental(global_results, crops)   # 实时落盘：中断最多丢50条
            time.sleep(DELAY)

        prog["crops"][crop] = {"done": list(done_set), "failed": list(failed_set)}
        save_progress(prog)
        print(f"  作物 {crop} 完成: 本次新增 {len(results)}, 失败 {len(new_failed)}")

    # 合并输出(基于实时落盘的各作物独立文件)
    print("\n合并输出...")
    merged = {}
    for crop in crops:
        merged.update(load_existing(crop))
    for r in global_results:
        merged[r["judgementNo"]] = r
    save_final(merged)


def save_final(merged):
    # 按作物分组输出独立文件
    by_crop = {}
    for r in merged.values():
        by_crop.setdefault(r.get("crop") or "未知", []).append(r)
    for crop, recs in by_crop.items():
        jp = crop_json_path(crop)
        cp = crop_csv_path(crop)
        with open(jp, "w", encoding="utf-8") as f:
            json.dump(recs, f, ensure_ascii=False, indent=1)
        with open(cp, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(recs)
        print(f"输出: {crop} {len(recs)} 条 -> {os.path.basename(jp)}")
    # 兼容旧路径：合并版 varieties.json
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(list(merged.values()), f, ensure_ascii=False, indent=1)
    print(f"输出: 合并版 {len(merged)} 条 -> varieties.json")


def load_existing(crop=None):
    """读取对应作物独立文件已有记录，返回 {judgementNo: rec}"""
    path = crop_json_path(crop) if crop else OUT_JSON
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return {r["judgementNo"]: r for r in json.load(f)}
        except Exception:
            pass
    return {}


def save_incremental(global_results, crops):
    """实时落盘：按作物独立文件合并保存，中断最多丢最后一批"""
    # 按作物分组本次结果
    by_crop = {}
    for r in global_results:
        by_crop.setdefault(r.get("crop") or "未知", []).append(r)
    for crop in crops:
        recs = by_crop.get(crop, [])
        if not recs:
            continue
        merged = load_existing(crop)
        for r in recs:
            merged[r["judgementNo"]] = r
        jp = crop_json_path(crop)
        with open(jp, "w", encoding="utf-8") as f:
            json.dump(list(merged.values()), f, ensure_ascii=False, indent=1)
        print(f"  [增量保存] {os.path.basename(jp)} 现有 {len(merged)} 条")


if __name__ == "__main__":
    main()
