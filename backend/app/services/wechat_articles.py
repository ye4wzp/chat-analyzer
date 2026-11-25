"""Identify articles in a WeChat push and archive readable article text."""
import asyncio
import html
import json
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import httpx

MAX_HTML = 6 * 1024 * 1024
HOST = "mp.weixin.qq.com"
LINK = re.compile(r"""https?://mp\.weixin\.qq\.com/s(?:/[-\w]+|\?[^\s<>"'\]]+)""", re.ASCII)


def article_url(value: str) -> str | None:
    """Keep only public WeChat article identifiers, dropping tracking parameters."""
    try:
        u = urlsplit(html.unescape(value.strip()))
    except ValueError:
        return None
    if u.scheme not in ("https", "http") or u.netloc.lower() != HOST:
        return None
    base = f"https://{HOST}"
    if re.fullmatch(r"/s/[A-Za-z0-9_-]+", u.path):
        return base + u.path
    q = parse_qs(u.query)
    if u.path != "/s" or not all(q.get(k) for k in ("__biz", "mid", "idx")):
        return None
    return base + "/s?" + urlencode({k: q[k][0] for k in ("__biz", "mid", "idx", "sn") if k in q})


def article_links(content: str | None, raw: str | None = None) -> list[dict]:
    found = {}

    def add(value, title):
        url = article_url(value or "")
        if url and url not in found:
            found[url] = {"url": url, "title": (title or "").strip() or "公众号文章"}

    def read(value):
        if not isinstance(value, str) or HOST not in value:
            return
        if value.lstrip().startswith("<") and "<!DOCTYPE" not in value.upper():
            try:
                root = ET.fromstring(value)
                for item in root.findall(".//mmreader/category/item"):
                    add(item.findtext("url") or item.findtext("shorturl"), item.findtext("title"))
                app = root if root.tag == "appmsg" else root.find(".//appmsg")
                if app is not None:
                    add(app.findtext("url"), app.findtext("title"))
                    if found:
                        return
            except ET.ParseError:
                pass
        for match in LINK.finditer(html.unescape(value)):
            add(match.group().rstrip(".,)，。"), "")

    read(content)
    if raw and not found:
        try:
            obj = json.loads(raw)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            for key in ("content", "text", "url"):
                read(obj.get(key))
    return list(found.values())


class ArticleBody(HTMLParser):
    # Collect text only inside WeChat's article container. Scripts, unrelated
    # recommendations and the verification page must never become saved text.
    void = set("area base br col embed hr img input link meta param source track wbr".split())
    blocks = set("p section div h1 h2 h3 h4 h5 h6 li blockquote tr pre".split())

    def __init__(self):
        super().__init__()
        self.stack = []
        self.parts = []
        self.restricted = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        element_id = attrs.get("id") or ""
        if element_id in {"js_pay_content", "js_pay_area"}:
            self.restricted = True
        if not self.stack and element_id != "js_content":
            return
        if tag in self.void:
            if tag in ("br", "hr"):
                self.parts.append("\n")
            return
        self.stack.append(tag)
        if tag in self.blocks:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack.pop() != tag:
                pass
            if tag in self.blocks:
                self.parts.append("\n")

    def handle_data(self, data):
        if self.stack and not any(t in self.stack for t in ("script", "style", "noscript")):
            self.parts.append(data)

    def body(self):
        return "\n".join(line.strip() for line in "".join(self.parts).splitlines() if line.strip())


class ReadError(ValueError):
    pass


class WechatBusy(ReadError):
    """WeChat explicitly challenged or rate-limited the automated reader."""
    def __init__(self, message: str, delay: int = 600):
        super().__init__(message)
        self.delay = delay


async def fetch_body(url: str) -> str:
    url = article_url(url)
    if not url:
        raise ReadError("这不是可读取的微信公众号文章链接")
    try:
        async with asyncio.timeout(24), httpx.AsyncClient(timeout=10) as client:
            for _ in range(3):
                async with client.stream("GET", url) as resp:
                    if resp.is_redirect:
                        location = urljoin(url, resp.headers.get("location", ""))
                        if "captcha" in location:
                            raise WechatBusy("微信要求安全验证，请打开原文查看")
                        url = article_url(location)
                        if not url:
                            raise ReadError("微信将文章转到了其他页面，请打开原文检查")
                        continue
                    if resp.status_code == 429:
                        wait = resp.headers.get("Retry-After", "")
                        delay = int(wait) if wait.isascii() and wait.isdigit() and len(wait) <= 8 else 600
                        raise WechatBusy("微信限制了读取频率，请稍后再试", max(60, delay))
                    if resp.status_code != 200:
                        raise ReadError(f"微信返回 HTTP {resp.status_code}，请打开原文检查")
                    data = bytearray()
                    async for chunk in resp.aiter_bytes(65536):
                        data.extend(chunk)
                        if len(data) > MAX_HTML:
                            raise ReadError("文章页面过大，请手动保存正文")
                    page = data.decode("utf-8", "replace")
                    parser = ArticleBody()
                    parser.feed(page)
                    parser.close()
                    if parser.restricted:
                        raise ReadError("文章有付费阅读区域，无法确认公开页面包含全文")
                    body = parser.body()
                    if body:
                        return body
                    raise ReadError("页面没有可读取的文字正文，文章可能已删除、受限或仅有图片")
            raise ReadError("原文多次跳转，未能读取正文")
    except (httpx.HTTPError, TimeoutError) as exc:
        raise ReadError("连接微信文章页面失败或超时，请稍后重试") from exc
