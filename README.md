中国种业大数据平台·主要农作物品种审定数据（自动更新）

## 覆盖范围

- **作物**: 玉米 / 大豆 / 水稻
- **年份**: 2016 年至今

## 数据文件

| 文件 | 说明 |
|---|---|
| `varieties.json` | 完整数据（前端直接 fetch 使用） |
| `varieties.csv` | 表格版（Excel/WPS 可打开） |

## 字段说明

| 字段 | 含义 |
|---|---|
| judgementNo | 审定编号（如 国审玉20176020） |
| varietyName | 品种名称 |
| crop | 作物（玉米/大豆/水稻） |
| transgenosis | 是否转基因 |
| year | 审定年份 |
| region | 审定地区（国家/省级） |
| applyCompany | 申请单位 |
| breedingCompany | 选育单位 |
| varietySource | 品种来源（亲本组合） |
| growthDays | 生育期天数（从公告文本提取） |
| gdd | ≥10℃活动积温需求（公告有则提取） |
| suitableRegion | 适宜种植区域 |
| varietyCharacter | 品种特性原文 |
| announcementNo | 公告号 |
| transformantName | 转化体名称（转基因品种） |

## 更新机制

GitHub Actions 每月 1 日自动运行增量抓取（`fetch_varieties_v2.py --incremental`），
只抓取新增审定品种，自动提交更新。也可在 Actions 页面手动触发。

## 前端使用

```js
const res = await fetch('https://raw.githubusercontent.com/wangpengcard/Crop-Certification-Data/main/varieties.json');
const varieties = await res.json();
```

## 抓取脚本

`fetch_varieties_v2.py` 本地运行：

```bash
# 全量抓取（断点续传）
python fetch_varieties_v2.py --crops 玉米,大豆,水稻 --since 2010

# 增量更新（只抓新增）
python fetch_varieties_v2.py --crops 玉米,大豆,水稻 --since 2010 --incremental
```

> 提示: 数据源为纯 HTTP 明文接口，脚本用 Python requests 直接访问，
> 浏览器前端因跨域/协议限制无法直连，故由本仓库中转。
