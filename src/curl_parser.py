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
        elif tok in ("--compressed", "-s", "-S", "-k", "-i", "-L", "--location", "-v"):
            pass
        elif tok.startswith("-"):
            # 未知带值选项(如 --max-time 5), 跳过其取值
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                i += 1
        elif url is None:
            url = tok
        i += 1

    if not url:
        raise ValueError("curl 命令里没有找到 URL")
    if method is None:
        method = "POST" if body is not None else "GET"
    return CurlRequest(url, method.upper(), headers, cookies, body)


def parse_curl_file(path):
    """解析一个文件里的所有 curl 命令(浏览器 "Copy all as cURL" 用 ' ;' 分隔)."""
    text = open(path, encoding="utf-8").read()
    requests_ = []
    for block in re.split(r";\s*\n(?=\s*curl )", text):
        block = block.strip()
        if not block.startswith("curl"):
            # 文件开头可能有说明文字, 从第一个 curl 开始截
            idx = block.find("curl '")
            if idx < 0:
                idx = block.find("curl \"")
            if idx < 0:
                continue
            block = block[idx:]
        try:
            requests_.append(parse_curl(block))
        except ValueError:
            continue
    return requests_


def find_by_path(requests_, keyword):
    """从解析结果里挑出路径含 keyword 的请求(取最后一条, 通常最新)."""
    hits = [r for r in requests_ if keyword.lower() in r.path.lower()]
    return hits[-1] if hits else None
