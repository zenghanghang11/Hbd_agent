# -*- coding: utf-8 -*-
"""解析 Chrome DevTools "Copy as cURL" 复制出来的命令.

为什么要这样做: searchAddress 接口带反爬令牌(URL 上的 XGiOG2f705 参数 + Cookie 里的
zA7uZWGUB1), 两者每次请求都变、且会过期. 与其在脚本里硬编码, 不如让使用者从浏览器
现抓一条 curl 存成文件, 脚本直接复用它的 URL/请求头/Cookie —— 令牌失效时重抓一条即可.
"""

import re
import shlex
import urllib.parse


class CurlRequest:
    def __init__(self, url, method, headers, cookies, body):
        self.url = url
        self.method = method
        self.headers = headers      # dict, 已去掉 cookie
        self.cookies = cookies      # dict
        self.body = body            # 原始 body 字符串(可能为 None)

    @property
    def path(self):
        return urllib.parse.urlparse(self.url).path

    def body_params(self):
        """把 form 编码的 body 解析成 dict."""
        if not self.body:
            return {}
        return dict(urllib.parse.parse_qsl(self.body, keep_blank_values=True))

    def __repr__(self):
        return "<CurlRequest %s %s headers=%d cookies=%d>" % (
            self.method, self.path, len(self.headers), len(self.cookies))


def parse_cookie_string(raw):
    """解析 -b 'a=1; b=2' 形式的 Cookie 串."""
    cookies = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        name, sep, value = part.partition("=")
        if sep:
            cookies[name.strip()] = value.strip()
    return cookies


def parse_curl(text):
    """解析单条 curl 命令, 返回 CurlRequest."""
    # 去掉续行符, 交给 shlex 按 shell 规则切词
    cleaned = re.sub(r"\\\s*\n", " ", text.strip())
    cleaned = cleaned.rstrip().rstrip(";")
    tokens = shlex.split(cleaned)
    if not tokens or tokens[0] != "curl":
        raise ValueError("不是 curl 命令: %r" % cleaned[:60])

    url, method, body = None, None, None
    headers, cookies = {}, {}
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-H", "--header"):
            i += 1
            name, sep, value = tokens[i].partition(":")
            if sep:
                name = name.strip().lower()
                if name == "cookie":
                    cookies.update(parse_cookie_string(value))
                else:
                    headers[name] = value.strip()
        elif tok in ("-b", "--cookie"):
            i += 1
            cookies.update(parse_cookie_string(tokens[i]))
        elif tok in ("-X", "--request"):
            i += 1
            method = tokens[i]
        elif tok in ("--data-raw", "--data", "-d", "--data-binary", "--data-urlencode"):
            i += 1
            body = tokens[i]
        elif tok.startswith(("http://", "https://")) and url is None:
            # URL 优先判定: 否则未知的布尔选项(如 --location-trusted)会把 URL 当成它的取值吃掉
            url = tok
        elif tok in ("--compressed", "-s", "-S", "-k", "-i", "-L", "--location", "-v",
                     "-g", "--globoff", "--http1.1", "--http2", "--insecure"):
            pass
        elif tok.startswith("-"):
            # 未知带值选项(如 --max-time 5), 跳过其取值
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("-") \
                    and not tokens[i + 1].startswith(("http://", "https://")):
                i += 1
        elif url is None:
            url = tok
        i += 1

    if not url:
        raise ValueError("curl 命令里没有找到 URL")
    if method is None:
        method = "POST" if body is not None else "GET"
    return CurlRequest(url, method.upper(), headers, cookies, body)


CURL_START_RE = re.compile(r"curl\s+(?=['\"]?https?://)")


def split_curl_commands(text):
    """把一段文本切成一条条 curl 命令.

    不能只按 ' ;' 分隔: 需求文档里常常在两条 curl 之间夹着中文说明, 说明文字会和
    后一条命令连在同一行(如 "...将接口curl 'https://...'"), 按分号切会把两条命令
    并成一块 —— 那样后一条的请求头和 Cookie 会覆盖前一条, 拼出一个张冠李戴的请求.
    所以直接以"curl + URL"出现的位置为界切分, 命令之外的说明文字自然被丢弃.
    """
    starts = [m.start() for m in CURL_START_RE.finditer(text)]
    blocks = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        chunk = text[start:end].strip()
        # 去掉命令末尾的分隔符与后面粘连的说明文字
        chunk = re.sub(r";\s*$", "", chunk.rstrip())
        blocks.append(chunk)
    return blocks


def parse_curl_file(path):
    """解析一个文件里的所有 curl 命令."""
    text = open(path, encoding="utf-8").read()
    requests_ = []
    for block in split_curl_commands(text):
        try:
            requests_.append(parse_curl(block))
        except ValueError:
            continue
    return requests_


def find_by_path(requests_, keyword):
    """从解析结果里挑出路径含 keyword 的请求(取最后一条, 通常最新)."""
    hits = [r for r in requests_ if keyword.lower() in r.path.lower()]
    return hits[-1] if hits else None
