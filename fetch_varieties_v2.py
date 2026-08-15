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


def http_get(url, retries=RETRY):
    req = urllib.request.Request(url, headers=HEADERS)
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(1.5 * (i + 1))
    return None


def load_progress():
    """兼容 v1(list) 与 v2(dict) 两种格式"""
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


def parse_record(d):
    char = d.get("VarietyCharacter") or ""
    sugg = d.get("JudgementSuggestion") or ""
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
        "varietyCharacter": char.strip(),
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
        while True:
            data = fetch_list_page(crop, page)
            if data is None:
                time.sleep(3)
                continue
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
            # 增量: 只抓列表里还没完成的
            todo = [it for it in all_items if (it.get("JudgementNo") or "").strip() not in done_set]
            print(f"  增量模式: 新增 {len(todo)} 条待抓 (跳过 {len(all_items) - len(todo)} 条已抓)")
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
            time.sleep(DELAY)

        prog["crops"][crop] = {"done": list(done_set), "failed": list(failed_set)}
        save_progress(prog)
        print(f"  作物 {crop} 完成: 本次新增 {len(results)}, 失败 {len(new_failed)}")

    # 合并输出(按 judgementNo 去重)
    print("\n合并输出...")
    existing = []
    if os.path.exists(OUT_JSON):
        try:
            with open(OUT_JSON, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = []
    merged = {}
    for r in existing:
        merged[r["judgementNo"]] = r
    for r in global_results:
        merged[r["judgementNo"]] = r
    save_final(merged)


def save_final(merged):
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(list(merged.values()), f, ensure_ascii=False, indent=1)
    fields = ["judgementNo", "varietyName", "crop", "transgenosis", "year", "region",
              "applyCompany", "breedingCompany", "varietySource", "growthDays", "gdd",
              "suitableRegion", "varietyCharacter", "announcementNo", "transformantName"]
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(merged.values())
    print(f"输出: {len(merged)} 条 -> {OUT_JSON}")


if __name__ == "__main__":
    main()
