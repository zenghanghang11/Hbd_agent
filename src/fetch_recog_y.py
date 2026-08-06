# -*- coding: utf-8 -*-
"""对地址列表批量调 CMHK 接口, 找出 recogFlag=Y 的楼层与房间号.

按附件需求的流程:
    输入地址 -> searchAddress
             -> 取 busiResp.busiDataResp 的第一个元素(busiDataRespObj)
             -> getAddressDetail(busiDataRespObj) -> busiRespObj
             -> 遍历该 BuildingCode 的所有 Floor × 所有 Flat
             -> getInstallInfo(busiRespObj + floor + flat)
             -> 响应 recogFlag == "Y" 即记录(原始输入 + Floor + Flat), 并跳到下一个地址
    一个 Floor×Flat 组合都没命中的地址 = bad case.

三种运行模式:
    --mode live   真机调用接口(需要 curl 模板, 见下). 产出真实结果.
    --mode mock   用 tests/fixtures 下的构造样例跑通全流程, 验证提取逻辑, 不发网络请求.
    --mode plan   只产出查询计划(输入地址 -> 查询关键词 + 离线筛查结论), 不发网络请求.

live 模式准备:
    1. 浏览器打开 https://www.hk.chinamobile.com/tc/home-family/broadband;
    2. 查一个地址 -> 选中一条结果 -> 再选楼层和房间号(把三个接口都触发一遍);
    3. F12 网络面板对 searchAddress / getAddressDetail / getInstallInfo 分别
       Copy as cURL, 三条都存进一个文件, 例如 config/cmhk.curl;
    4. python3 src/fetch_recog_y.py --mode live --curl-file config/cmhk.curl

    接口带反爬令牌(URL 参数 XGiOG2f705 + Cookie zA7uZWGUB1), 会过期; 脚本遇到
    401/403 会明确提示重新抓取 curl, 不会静默产出空结果.

产出(默认 output/recog_y/):
    recog_y_result.xlsx        四个表: 结果 / 明细 / 未命中(bad case) / 查询汇总
    recog_y_result.csv         结果表: 序号 / 输入地址 / Floor / Flat
    recog_y_result_detail.csv  明细: 每个地址走到哪一步, 试了多少组合
    recog_y_result_miss.csv    未命中清单
    recog_y_result.json        完整结构化结果
    recog_y_report.md          人读版报告
    raw/                       每次调用的原始响应(live 模式), 便于核对接口结构
"""

import argparse
import csv
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cmhk_api
import curl_parser
import hk_address as hk
import screen_bad_cases as sc

Y_FLAG = "Y"


# --------------------------------------------------------------------------

def build_keyword(raw, mode):
    """决定送给接口的查询词."""
    norm, _ = hk.normalize(raw)
    if mode == "raw":
        return raw.strip()
    if mode == "normalized":
        return norm
    if mode == "canonical":
        return hk.canonical(hk.parse(norm)) or norm
    raise ValueError("未知 keyword-mode: %s" % mode)


class AddressResult:
    def __init__(self, idx, raw, keyword):
        self.idx = idx
        self.raw = raw              # 我最开始的输入
        self.keyword = keyword
        self.queried = False
        self.matched = ""           # searchAddress 第一个元素的地址
        self.building_code = ""
        self.floors = []
        self.flats = []
        self.combo_total = 0
        self.probes = 0             # 实际调用 getInstallInfo 的次数
        self.hit_floor = ""
        self.hit_flat = ""
        self.flag_counter = Counter()
        self.stage = "未查询"
        self.error = ""
        self.offline = ""           # 离线筛查命中的规则, 用于交叉印证

    @property
    def hit(self):
        return bool(self.hit_floor or self.hit_flat)

    @property
    def flag_dist(self):
        return ", ".join("%s×%d" % kv for kv in sorted(self.flag_counter.items())) or "-"

    @property
    def reason(self):
        if not self.queried:
            return "未查询(接口未调用或运行中止)"
        if self.error:
            return "接口调用失败: %s" % self.error
        return self.stage


def process(records, client, max_probes=0):
    """逐条跑完整链路; 令牌失效时立即中止, 已完成的结果照常保留.

    max_probes: 每个地址最多试多少个 Floor×Flat 组合, 0 表示不限.
    """
    aborted = ""
    for res in records:
        # --- 1. searchAddress -> 取第一个元素 ---
        try:
            resp = client.search_address(res.keyword, tag="%02d" % res.idx)
            res.queried = True
        except cmhk_api.TokenExpired as exc:
            aborted = str(exc)
            break
        except cmhk_api.ApiError as exc:
            res.error = str(exc)
            continue

        obj = cmhk_api.first_data_resp(resp)
        if not obj:
            res.stage = "searchAddress 无结果"
            continue
        res.matched = obj.get("value") or cmhk_api.plain_description(obj)
        res.building_code = cmhk_api.building_code(obj)

        # --- 2. getAddressDetail -> busiRespObj -> 所有 Floor / Flat ---
        try:
            detail = client.get_address_detail(obj, tag="%02d_%s" % (res.idx, res.building_code))
        except cmhk_api.TokenExpired as exc:
            aborted = str(exc)
            break
        except cmhk_api.ApiError as exc:
            res.error = str(exc)
            res.stage = "getAddressDetail 失败"
            continue

        busi_resp_obj = detail.get("busiResp") if isinstance(detail, dict) else None
        base = dict(obj)
        if isinstance(busi_resp_obj, dict):
            for key in ("buildingCode", "clientType", "ofcaCode", "carrierInfo"):
                if busi_resp_obj.get(key):
                    base[key] = busi_resp_obj[key]
            if base.get("buildingCode"):
                res.building_code = str(base["buildingCode"])

        floors, flats, pairs = cmhk_api.extract_floors_flats(detail)
        res.floors, res.flats = floors, flats
        if not floors and not flats:
            res.stage = "getAddressDetail 未返回楼层/房间"
            continue

        # --- 3. 遍历 Floor × Flat 调 getInstallInfo, 命中 Y 就停 ---
        combos = pairs or [(f, r) for f in (floors or [""]) for r in (flats or [""])]
        res.combo_total = len(combos)
        for floor, flat in combos:
            if max_probes and res.probes >= max_probes:
                res.stage = "已达单地址探测上限 %d, 未命中" % max_probes
                break
            res.probes += 1
            try:
                info = client.get_install_info(base, floor, flat,
                                               tag="%02d_%s_%s" % (res.idx, floor, flat))
            except cmhk_api.TokenExpired as exc:
                aborted = str(exc)
                break
            except cmhk_api.ApiError as exc:
                res.error = str(exc)
                res.stage = "getInstallInfo 失败"
                break
            flag = cmhk_api.recog_flag(info)
            res.flag_counter[flag or "(无)"] += 1
            if flag == Y_FLAG:
                res.hit_floor, res.hit_flat = floor, flat
                res.stage = "命中"
                break
        else:
            res.stage = "%d 个组合全部试完, 无 recogFlag=Y" % res.combo_total
        if aborted:
            break
    return aborted


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------

MAIN_HEADERS = ["序号", "输入地址", "Floor", "Flat"]

DETAIL_HEADERS = ["序号", "输入地址", "查询关键词", "匹配地址", "buildingCode",
                  "Floor", "Flat", "楼层数", "房间数", "组合总数", "实际探测次数",
                  "recogFlag分布", "结果", "离线筛查"]

MISS_HEADERS = ["序号", "输入地址", "查询关键词", "匹配地址", "buildingCode",
                "楼层数", "房间数", "组合总数", "实际探测次数", "recogFlag分布",
                "未命中原因", "离线筛查"]


def main_rows(records):
    """需求要的结果: 我最开始的输入 + Floor + Flat."""
    return [[r.idx, r.raw, r.hit_floor, r.hit_flat] for r in records if r.hit]


def detail_rows(records):
    return [[r.idx, r.raw, r.keyword, r.matched, r.building_code,
             r.hit_floor, r.hit_flat, len(r.floors), len(r.flats),
             r.combo_total, r.probes, r.flag_dist, r.reason, r.offline]
            for r in records]


def miss_rows(records):
    return [[r.idx, r.raw, r.keyword, r.matched, r.building_code,
             len(r.floors), len(r.flats), r.combo_total, r.probes,
             r.flag_dist, r.reason, r.offline]
            for r in records if not r.hit]


def write_csv(rows, headers, path):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


def write_xlsx(records, summary, path, miss_sheet_title="未命中(bad case)"):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        print("[warn] 未安装 openpyxl, 跳过 xlsx (pip install openpyxl)")
        return False

    wb = Workbook()
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="4472C4")

    def sheet(title, headers, rows, widths):
        ws = wb.create_sheet(title)
        ws.append(headers)
        for r in rows:
            ws.append(r)
        for cell in ws[1]:
            cell.font, cell.fill = head_font, head_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for i, h in enumerate(headers, 1):
            ws.column_dimensions[get_column_letter(i)].width = widths.get(h, 12)
        ws.freeze_panes = "A2"
        if rows:
            ws.auto_filter.ref = ws.dimensions
        return ws

    wb.remove(wb.active)
    W = {"输入地址": 32, "查询关键词": 32, "匹配地址": 34, "buildingCode": 14,
         "recogFlag分布": 16, "结果": 34, "未命中原因": 40, "离线筛查": 26,
         "Floor": 8, "Flat": 8}
    sheet("结果", MAIN_HEADERS, main_rows(records), W)
    sheet("明细", DETAIL_HEADERS, detail_rows(records), W)
    ws = sheet(miss_sheet_title, MISS_HEADERS, miss_rows(records), W)
    warn = PatternFill("solid", fgColor="FFC7CE")
    for row in ws.iter_rows(min_row=2):
        row[1].fill = warn
    sheet("查询汇总", ["项目", "值"], summary, {"项目": 26, "值": 76})
    wb.save(path)
    return True


def write_report(records, summary, path, mode):
    hit = [r for r in records if r.hit]
    miss = [r for r in records if not r.hit and r.queried]
    L = ["# recogFlag=Y 的 Floor / Flat 提取报告\n"]
    if mode != "live":
        L.append("> **本次为 `%s` 模式, 不是真实接口数据.** live 模式跑法见 USAGE.md.\n" % mode)
    for k, v in summary:
        L.append("- **%s**: %s" % (k, v))

    L.append("\n## 一、命中结果(输入地址 + Floor + Flat)\n")
    if hit:
        L.append("| 序号 | 输入地址 | Floor | Flat | 匹配地址 | 探测次数 |")
        L.append("|---|---|---|---|---|---|")
        for r in hit:
            L.append("| %d | %s | %s | %s | %s | %d |" % (
                r.idx, r.raw, r.hit_floor, r.hit_flat, r.matched, r.probes))
    else:
        L.append("无\n")

    L.append("\n## 二、未命中的地址(bad case)\n")
    L.append("走完流程没有任何 Floor×Flat 组合返回 recogFlag=Y —— 这些就是要人工复核的 bad case.\n")
    if miss:
        L.append("| 序号 | 输入地址 | 匹配地址 | 组合数 | 探测次数 | recogFlag分布 | 原因 | 离线筛查 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for r in miss:
            L.append("| %d | %s | %s | %d | %d | %s | %s | %s |" % (
                r.idx, r.raw, r.matched or "-", r.combo_total, r.probes,
                r.flag_dist, r.reason, r.offline or "-"))
    else:
        L.append("无\n")

    L.append("\n## 三、说明\n")
    L.append("- 流程: searchAddress → busiResp.busiDataResp[0] → getAddressDetail → "
             "遍历 Floor×Flat → getInstallInfo, 命中 recogFlag=Y 即记录并跳到下一个地址.")
    L.append("- `探测次数` 是该地址实际调用 getInstallInfo 的次数. 命中通常只需 1 次; "
             "次数等于组合总数说明整栋楼都试遍了仍未命中.")
    L.append("- `离线筛查` 列是不依赖接口的静态规则结论(见 bad_case_report.md), "
             "两边都判为问题的最值得优先看.\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="提取 recogFlag=Y 的 Floor / Flat")
    ap.add_argument("--input", default="data/addresses.txt")
    ap.add_argument("--outdir", default="output/recog_y")
    ap.add_argument("--mode", choices=["live", "mock", "plan"], default="mock")
    ap.add_argument("--curl-file", help="live 模式: 含 searchAddress / getAddressDetail / getInstallInfo 的 curl 文件")
    ap.add_argument("--fixture-dir", default="tests/fixtures", help="mock 模式的样例目录")
    ap.add_argument("--keyword-mode", choices=["raw", "normalized", "canonical"],
                    default="raw", help="送给接口的查询词形态, 默认用原始地址")
    ap.add_argument("--delay", type=float, default=1.0, help="两次请求间隔秒数")
    ap.add_argument("--max-probes", type=int, default=60,
                    help="单个地址最多试多少个 Floor×Flat 组合, 0 为不限(默认 60)")
    ap.add_argument("--limit", type=int, help="只处理前 N 条, 便于试跑")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        lines = [ln.rstrip("\n") for ln in f if ln.strip()]
    if args.limit:
        lines = lines[: args.limit]

    # 离线筛查(含跨条的重复类规则), 作为交叉印证列
    offline = [sc.Record(i, raw) for i, raw in enumerate(lines, 1)]
    for rec in offline:
        sc.check_row_rules(rec)
    sc.check_cross_rules(offline)

    records = []
    for i, raw in enumerate(lines, 1):
        res = AddressResult(i, raw, build_keyword(raw, args.keyword_mode))
        res.offline = ";".join(offline[i - 1].codes) or "无问题"
        records.append(res)

    os.makedirs(args.outdir, exist_ok=True)
    aborted = ""

    if args.mode == "plan":
        note = "仅生成查询计划, 未调用接口"
    else:
        if args.mode == "live":
            if not args.curl_file:
                ap.error("live 模式必须提供 --curl-file")
            reqs = curl_parser.parse_curl_file(args.curl_file)
            tpls = {k: curl_parser.find_by_path(reqs, k)
                    for k in ("searchAddress", "getAddressDetail", "getInstallInfo")}
            missing = [k for k, v in tpls.items() if not v]
            if missing:
                ap.error("%s 里缺少这些接口的 curl: %s" % (args.curl_file, ", ".join(missing)))
            client = cmhk_api.CmhkClient(
                tpls["searchAddress"], tpls["getAddressDetail"], tpls["getInstallInfo"],
                delay=args.delay, raw_dir=os.path.join(args.outdir, "raw"))
            note = "真实接口调用"
        else:
            client = cmhk_api.MockClient(args.fixture_dir)
            note = "构造样例(非真实数据), 仅用于验证提取逻辑"
        aborted = process(records, client, max_probes=args.max_probes)

    hit = [r for r in records if r.hit]
    queried = [r for r in records if r.queried]
    summary = [
        ("运行模式", "%s —— %s" % (args.mode, note)),
        ("查询词形态", args.keyword_mode),
        ("输入地址数", str(len(records))),
        ("已查询地址数", "%d / %d" % (len(queried), len(records))),
        ("命中 recogFlag=Y 的地址数", "%d / %d" % (len(hit), len(queried) or len(records))),
        ("未命中(bad case)数",
         str(len(queried) - len(hit)) if queried else "未查询, 不适用"),
        ("getInstallInfo 探测总次数", str(sum(r.probes for r in records))),
        ("调用失败数", str(sum(1 for r in records if r.error))),
    ]
    if aborted:
        summary.append(("中止原因", aborted))

    base = os.path.join(args.outdir, "recog_y_result")
    write_csv(main_rows(records), MAIN_HEADERS, base + ".csv")
    write_csv(detail_rows(records), DETAIL_HEADERS, base + "_detail.csv")
    write_csv(miss_rows(records), MISS_HEADERS, base + "_miss.csv")
    write_xlsx(records, summary, base + ".xlsx",
               "查询计划" if args.mode == "plan" else "未命中(bad case)")
    write_report(records, summary, os.path.join(args.outdir, "recog_y_report.md"), args.mode)
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({
            "summary": dict(summary),
            "results": [{
                "序号": r.idx, "输入地址": r.raw, "Floor": r.hit_floor, "Flat": r.hit_flat,
                "查询关键词": r.keyword, "匹配地址": r.matched,
                "buildingCode": r.building_code,
                "楼层": r.floors, "房间": r.flats,
                "组合总数": r.combo_total, "探测次数": r.probes,
                "recogFlag分布": r.flag_dist, "结果": r.reason,
                "离线筛查": r.offline, "错误": r.error,
            } for r in records]}, f, ensure_ascii=False, indent=2)

    for k, v in summary:
        print("%s: %s" % (k, v))
    print("已输出到 %s/" % args.outdir)
    if aborted:
        print("\n[中止] %s" % aborted)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
