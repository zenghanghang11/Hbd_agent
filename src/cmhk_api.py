# -*- coding: utf-8 -*-
"""CMHK 寬頻地址查询接口客户端.

接口链路(按附件里的需求说明):
    1. searchAddress    busInfo={"keyword": "<我输入的地址>"}
       -> 取 busiResp.busiDataResp 列表的【第一个元素】, 记为 busiDataRespObj
    2. getAddressDetail orderStr=busiDataRespObj
       -> 返回值 busiResp 记为 busiRespObj, 从中取当前 BuildingCode 的所有 Floor 与所有 Flat
    3. getInstallInfo   busInfo={busiRespObj 的字段..., floor, flat}
       -> 遍历 Floor×Flat, 响应里 recogFlag == "Y" 即记录(原始输入 + Floor + Flat),
          找到一个就跳到下一个地址

反爬令牌(URL 参数 XGiOG2f705 与 Cookie zA7uZWGUB1)每次请求都变且会过期, 所以
请求模板一律从浏览器现抓的 curl 里读, 不在代码里硬编码.
"""

import json
import os
import time
import urllib.parse

try:
    import requests
except ImportError:                                     # pragma: no cover
    requests = None


class TokenExpired(RuntimeError):
    """反爬令牌失效 / 被拒, 需要重新抓一条 curl."""


class ApiError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# 响应解析: 接口响应结构未在抓包里出现, 因此按"在任意层级找字段"的方式容错解析
# --------------------------------------------------------------------------

def walk(obj):
    """按文档顺序深度遍历 JSON, 产出每个 dict 节点.

    顺序很重要: 楼层与房间号要保持接口返回的原始次序, 否则展开出来的
    测试用例顺序会和页面下拉框对不上.
    """
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            for node in walk(value):
                yield node
    elif isinstance(obj, list):
        for value in obj:
            for node in walk(value):
                yield node


def json_prefix(text):
    """解析字符串开头的那段 JSON, 忽略后面粘连的内容.

    需求文档常把说明文字直接写在 curl 的 --data-raw 引号里(如
    `busInfo={...}的"recogFlag"返回值为"Y"...`), 直接 json.loads 会因
    "Extra data" 报错. 这里只取前缀的合法 JSON.
    """
    if not text:
        return {}
    try:
        obj, _ = json.JSONDecoder().raw_decode(text.strip())
        return obj if isinstance(obj, dict) else {}
    except ValueError:
        return {}


def find_dicts_with(obj, key):
    """找出所有含指定字段的 dict(不区分嵌套层级)."""
    return [d for d in walk(obj) if key in d]


def scalar(value):
    """把候选值转成展示用字符串; 非标量返回 None."""
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        s = str(value).strip()
        return s or None
    return None


FLOOR_KEYS = ("floor", "floorname", "floorno", "floorcode", "floordesc")
FLAT_KEYS = ("flat", "flatname", "flatno", "flatcode", "room", "roomno",
             "roomname", "unit", "unitno")


def _labels(container, keys):
    """从 dict / list 里抽出楼层或房间标签."""
    out = []
    for node in walk(container):
        if not isinstance(node, dict):
            continue
        for k, v in node.items():
            if k.lower() in keys:
                s = scalar(v)
                if s:
                    out.append(s)
    if not out and isinstance(container, list):
        out = [s for s in (scalar(x) for x in container) if s]
    return out


def extract_floors_flats(resp):
    """从 getAddressDetail 响应里抽出 (楼层列表, 房间列表, 楼层-房间对).

    兼容两种常见结构:
      A. 楼层与房间各是一个平铺列表      -> pairs 为空, 由调用方按笛卡尔积展开
      B. 每个楼层对象内嵌该层的房间列表  -> 直接给出 (楼层, 房间) 对
    """
    floors, flats, pairs = [], [], []

    for node in walk(resp):
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            kl = key.lower()
            if any(f in kl for f in ("floor",)) and isinstance(value, list):
                for item in value:
                    fl = scalar(item) or _first(_labels(item, FLOOR_KEYS))
                    if not fl:
                        continue
                    floors.append(fl)
                    # 该楼层对象里内嵌的房间
                    if isinstance(item, dict):
                        sub = _labels(item, FLAT_KEYS)
                        for fa in sub:
                            pairs.append((fl, fa))
                            flats.append(fa)
            elif any(f in kl for f in ("flat", "room", "unit")) and isinstance(value, list):
                for item in value:
                    fa = scalar(item) or _first(_labels(item, FLAT_KEYS))
                    if fa:
                        flats.append(fa)

    # 列表形式没抓到时, 退回逐字段扫描
    if not floors:
        floors = _labels(resp, FLOOR_KEYS)
    if not flats:
        flats = _labels(resp, FLAT_KEYS)

    return _uniq(floors), _uniq(flats), _uniq(pairs)


def _first(seq):
    return seq[0] if seq else None


def _uniq(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def first_data_resp(resp):
    """取 busiResp.busiDataResp 的第一个元素(需求明确要求取第一个, 不做筛选).

    找不到标准路径时, 退回"第一个含 value+carrierInfo 的对象", 以防字段名有出入.
    """
    if not isinstance(resp, dict):
        return None
    busi = resp.get("busiResp")
    if isinstance(busi, dict):
        lst = busi.get("busiDataResp")
        if isinstance(lst, list) and lst:
            return lst[0] if isinstance(lst[0], dict) else None
        if isinstance(lst, dict):
            return lst
    for node in walk(resp):
        if isinstance(node, dict) and "value" in node and "carrierInfo" in node:
            return node
    return None


def recog_flag(resp):
    """从 getInstallInfo 响应里取 recogFlag(任意层级)."""
    for node in walk(resp):
        if isinstance(node, dict) and "recogFlag" in node:
            s = scalar(node["recogFlag"])
            if s:
                return s.strip().upper()
    return ""


def extract_candidates(resp):
    """从 searchAddress 响应里抽出候选地址列表(含 recogFlag 的那些 dict)."""
    cands = find_dicts_with(resp, "recogFlag")
    if cands:
        return cands
    # 没有 recogFlag 字段时, 退回"含 value + carrierInfo"的地址对象
    return [d for d in walk(resp)
            if isinstance(d, dict) and "value" in d and "carrierInfo" in d]


def building_code(cand):
    """取该地址的 buildingCode: 优先 CMHK 的 addressCode(与 getInstallInfo 一致)."""
    for key in ("buildingCode", "addressCode"):
        s = scalar(cand.get(key))
        if s:
            return s
    for info in cand.get("carrierInfo") or []:
        if isinstance(info, dict) and info.get("carrier") == "CMHK":
            s = scalar(info.get("addressCode"))
            if s:
                return s
    for info in cand.get("carrierInfo") or []:
        if isinstance(info, dict):
            s = scalar(info.get("addressCode"))
            if s:
                return s
    return ""


TAG_RE = __import__("re").compile(r"<[^>]+>")


def plain_description(cand):
    """description 里带 <span class='keyWord'> 高亮标签, 去掉标签取纯文本."""
    desc = scalar(cand.get("description")) or ""
    return TAG_RE.sub("", desc).strip()


# --------------------------------------------------------------------------
# 客户端
# --------------------------------------------------------------------------

class CmhkClient:
    """按抓包模板发请求. search_tpl / detail_tpl 均为 CurlRequest."""

    def __init__(self, search_tpl, detail_tpl=None, install_tpl=None, delay=1.0,
                 timeout=30, retries=2, raw_dir=None, session=None):
        if requests is None:
            raise RuntimeError("需要 requests 库: pip install requests")
        self.search_tpl = search_tpl
        self.detail_tpl = detail_tpl
        self.install_tpl = install_tpl
        # getInstallInfo 模板自带的固定入参(broadBandType 等), 作为构造入参的底板
        self.install_base = (json_prefix(install_tpl.body_params().get("busInfo", ""))
                             if install_tpl else {})
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.raw_dir = raw_dir
        self.session = session or requests.Session()
        # 反爬 Cookie(zA7uZWGUB1 等)服务端每次响应都会轮换. 放进 session 里,
        # 后续请求才会带上最新值; 每次都发模板里的旧值会被判定为异常流量.
        for tpl in (search_tpl, detail_tpl, install_tpl):
            if tpl:
                self.session.cookies.update(tpl.cookies)
        self._last_call = 0.0
        self.stats = {"requests": 0, "errors": 0}
        if raw_dir:
            os.makedirs(raw_dir, exist_ok=True)

    # -- 内部 ---------------------------------------------------------------

    def _throttle(self):
        wait = self.delay - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def _post(self, tpl, body_params, tag):
        headers = dict(tpl.headers)
        headers.pop("content-length", None)
        # 让 requests 自己协商压缩, 避免模板里的 br/zstd 本地解不开
        headers.pop("accept-encoding", None)
        last_err = None

        for attempt in range(self.retries + 1):
            self._throttle()
            self.stats["requests"] += 1
            try:
                resp = self.session.post(
                    tpl.url, headers=headers, data=body_params, timeout=self.timeout)
            except Exception as exc:                     # 网络抖动才重试
                last_err = exc
                self.stats["errors"] += 1
                if attempt < self.retries:
                    time.sleep(2 ** attempt)
                    continue
                raise ApiError("请求失败(%s): %s" % (tag, exc))

            if resp.status_code in (401, 403, 407):
                raise TokenExpired(
                    "接口返回 %d —— 反爬令牌/会话已失效. 请在浏览器重新执行一次地址查询, "
                    "对 %s 请求 Copy as cURL, 覆盖 curl 模板文件后重跑."
                    % (resp.status_code, tag))
            if resp.status_code >= 500:
                last_err = ApiError("HTTP %d" % resp.status_code)
                self.stats["errors"] += 1
                if attempt < self.retries:
                    time.sleep(2 ** attempt)
                    continue
                raise last_err
            if resp.status_code != 200:
                raise ApiError("%s 返回 HTTP %d: %s" % (tag, resp.status_code, resp.text[:200]))

            self._dump(tag, resp.text)
            try:
                return resp.json()
            except ValueError:
                raise ApiError("%s 响应不是 JSON: %s" % (tag, resp.text[:200]))
        raise ApiError(str(last_err))

    def _dump(self, tag, text):
        if not self.raw_dir:
            return
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in tag)[:80]
        path = os.path.join(self.raw_dir, "%s.json" % safe)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    # -- 对外 ---------------------------------------------------------------

    def search_address(self, keyword, tag=""):
        body = {"busInfo": json.dumps({"keyword": keyword}, ensure_ascii=False)}
        return self._post(self.search_tpl, body, "searchAddress_%s" % (tag or keyword))

    def get_address_detail(self, candidate, tag=""):
        """candidate 直接回传搜索结果里的那条地址对象(前端就是这么做的)."""
        if not self.detail_tpl:
            raise ApiError("未提供 getAddressDetail 的 curl 模板")
        order = {k: candidate[k] for k in
                 ("carrierInfo", "clientType", "ofcaCode", "description", "custCode", "value")
                 if k in candidate}
        order.setdefault("custCode", "")
        body = {"orderStr": json.dumps(order, ensure_ascii=False)}
        return self._post(self.detail_tpl, body, "getAddressDetail_%s" % (tag or order.get("value", "")))


    def get_install_info(self, base, floor, flat, tag=""):
        """构造 getInstallInfo 入参: 模板底板 + busiRespObj 的字段 + floor/flat."""
        if not self.install_tpl:
            raise ApiError("未提供 getInstallInfo 的 curl 模板")
        info = dict(self.install_base)
        for key in ("buildingCode", "clientType", "ofcaCode", "carrierInfo"):
            if base.get(key):
                info[key] = base[key]
        if not info.get("buildingCode"):
            info["buildingCode"] = building_code(base)
        info.setdefault("broadBandType", "CMHK")
        info["floor"] = floor
        info["flat"] = flat
        body = {"busInfo": json.dumps(info, ensure_ascii=False)}
        return self._post(self.install_tpl, body,
                          "getInstallInfo_%s" % (tag or "%s_%s" % (floor, flat)))


class MockClient:
    """离线自测用: 从 fixture 目录读预置响应, 不发网络请求."""

    def __init__(self, fixture_dir):
        self.fixture_dir = fixture_dir
        self.stats = {"requests": 0, "errors": 0}

    def _load(self, name):
        path = os.path.join(self.fixture_dir, name)
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def search_address(self, keyword, tag=""):
        self.stats["requests"] += 1
        data = self._load("searchAddress.sample.json")
        return data.get(keyword, data.get("__default__", {}))

    def get_address_detail(self, candidate, tag=""):
        self.stats["requests"] += 1
        data = self._load("getAddressDetail.sample.json")
        return data.get(candidate.get("value", ""), data.get("__default__", {}))

    def get_install_info(self, base, floor, flat, tag=""):
        self.stats["requests"] += 1
        data = self._load("getInstallInfo.sample.json")
        key = "%s|%s|%s" % (base.get("buildingCode") or building_code(base), floor, flat)
        return data.get(key, data.get("__default__", {}))
