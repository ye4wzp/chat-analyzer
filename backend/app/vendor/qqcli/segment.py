"""[40800] protobuf 正文 -> OneBot 风格分段。

设计参考 qqcli-rs 的 normalize/segment：不把正文压成一个字符串，而是
保序产出分段（text / image / record / file / face / at / reply / unknown），
再提供 content_inline 视图（媒体以占位符呈现）。

QQ NT 的正文是 protobuf，元素字段号随平台/版本而变，且部分正文内嵌 XML
富文本（<gtip> / <qq uin=.. jp=../> / <nor txt=../>）。因此解析策略是
「protobuf 保序扫描 + 富文本标记识别 + 噪声过滤」，不硬编码版本相关的字段号。
"""

import html
import re
from dataclasses import dataclass, field, asdict

# ── 噪声判定 ──────────────────────────────────────────
_RE_HEX_FILE = re.compile(r"^[0-9A-Fa-f]{20,}\.[A-Za-z0-9]{2,5}$")
_RE_HEX = re.compile(r"^[0-9A-Fa-f]{32,}$")
_RE_FID = re.compile(r"^E[A-Za-z0-9_\-]{40,}$")
_RE_UID = re.compile(r"^u_[A-Za-z0-9_\-]{8,}$")
_RE_LONG_DIGITS = re.compile(r"^\d{15,}$")           # 消息 ID / QQ 号
_RE_CDN = ("multimedia.nt.qq.com.cn", "download?appid=", "rkey=")
_RE_URL = re.compile(r"https?://[^\s\"'<>]+")
_RE_IMG_EXT = re.compile(r"(?i)\.(jpg|jpeg|png|gif|webp|bmp)$")
_RE_AUDIO_EXT = re.compile(r"(?i)\.(amr|silk|mp3|wav|m4a|ogg)$")


def _is_droppable(s):
    return bool(
        _RE_UID.match(s)
        or _RE_HEX.match(s)
        or _RE_FID.match(s)
        or _RE_LONG_DIGITS.match(s)
    )


def _is_media_url(s):
    if any(tok in s for tok in _RE_CDN):
        return True
    m = _RE_URL.fullmatch(s)
    return bool(m and (_RE_IMG_EXT.search(s) or _RE_AUDIO_EXT.search(s)))


def _media_kind(s):
    """按扩展名/内容判定媒体类型：image / record / file。

    QQ 图片 CDN 形如 .../download?appid=..&fileid=..&spec=..（无扩展名），
    统一按图片处理。
    """
    if _RE_AUDIO_EXT.search(s) or s.endswith(".silk"):
        return "record"
    if any(tok in s for tok in _RE_CDN) or _RE_IMG_EXT.search(s):
        return "image"
    return "file"


def _is_media_name(s):
    return bool(_RE_HEX_FILE.match(s))


# 正文里夹带的资源路径（群头像/图片 CDN/文件 UUID 名），需从文本中剔除
_RE_ASSET = re.compile(
    r"\{[0-9a-fA-F-]{20,}\}\.[A-Za-z0-9]{2,5}[^\s\"'<>]*"          # {uuid}.jpg + 后续路径
    r"|/gchatpic_new/[^\s\"'<>]*"                                   # 群头像路径
    r"|https?://[^\s\"'<>]*(?:download\?appid=|multimedia\.nt\.qq\.com\.cn)[^\s\"'<>]*"
    r"|[0-9A-Fa-f]{20,}\.(?:jpg|jpeg|png|gif|webp|bmp|amr|silk|mp4)"
)


def strip_assets(s):
    """剔除文本中的资源路径/文件名，保留可读正文。"""
    if not s:
        return s
    return _RE_ASSET.sub("", s)


# ── varint / protobuf 保序扫描 ─────────────────────────
def _read_varint(buf, i):
    v = 0
    shift = 0
    while i < len(buf):
        b = buf[i]
        i += 1
        v |= (b & 0x7F) << shift
        shift += 7
        if not (b & 0x80):
            break
    return v, i


def _meaningful_text(seg):
    try:
        s = seg.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not s:
        return None
    ok = sum(1 for c in s if c.isprintable() or c in "\n\t")
    if ok < len(s) * 0.9:
        return None
    return s


def _scan(buf, depth, out):
    """保序遍历 blob，产出 (kind, value)：kind 属于 text/media/drop。"""
    if depth > 8 or not buf:
        return
    i, n = 0, len(buf)
    while i < n:
        try:
            tag, i = _read_varint(buf, i)
        except Exception:
            break
        wire = tag & 7
        if wire == 0:
            try:
                _, i = _read_varint(buf, i)
            except Exception:
                break
        elif wire == 1:
            i += 8
        elif wire == 5:
            i += 4
        elif wire == 2:
            try:
                ln, i = _read_varint(buf, i)
            except Exception:
                break
            if i + ln > n:
                break
            seg = buf[i:i + ln]
            i += ln
            s = _meaningful_text(seg)
            if s is None:
                if depth < 8:
                    _scan(seg, depth + 1, out)
            elif _is_media_url(s) or _is_media_name(s):
                out.append(("media", s))
            elif _is_droppable(s):
                continue
            else:
                out.append(("text", s))
        else:
            break


# ── 富文本标记（<gtip>/<qq>/<nor>/<img>）───────────────
_RE_TAG = re.compile(r"<(/?)([a-zA-Z_][\w:-]*)((?:\s+[\w:.\-]+\s*=\s*\"[^\"]*\")*)\s*/?\s*>")
_RE_ATTR = re.compile(r"([\w:.\-]+)\s*=\s*\"([^\"]*)\"")


def _has_markup(s):
    return "<gtip" in s or "<nor " in s or "<qq " in s or "<img " in s or "</" in s


def _parse_markup(s, out):
    pos = 0
    for m in _RE_TAG.finditer(s):
        if m.start() > pos:
            chunk = html.unescape(s[pos:m.start()])
            if chunk.strip():
                out.append(Segment("text", text=chunk))
        name = m.group(2).lower()
        attrs = dict(_RE_ATTR.findall(m.group(3) or ""))
        if name == "qq":
            uid = attrs.get("uin", "")
            jp = attrs.get("jp", "") or attrs.get("uin", "")
            out.append(Segment("at", uid=uid, qq=jp, name=attrs.get("nm", "")))
        elif name == "nor":
            txt = html.unescape(attrs.get("txt", ""))
            if txt:
                out.append(Segment("text", text=txt))
        elif name == "img":
            out.append(Segment("image", url=attrs.get("src", ""), name=attrs.get("alt", "")))
        pos = m.end()
    if pos < len(s):
        chunk = html.unescape(s[pos:])
        if chunk.strip():
            out.append(Segment("text", text=chunk))


# ── 分段模型 ──────────────────────────────────────────
@dataclass
class Segment:
    kind: str                     # text/image/record/file/face/at/reply/forward/unknown
    text: str = ""
    url: str = ""
    name: str = ""
    uid: str = ""
    qq: str = ""

    def to_dict(self):
        d = {"type": self.kind}
        d.update({k: v for k, v in asdict(self).items() if k != "kind" and v})
        d.pop("type", None)
        d["type"] = self.kind
        return d

    def inline(self, resolve=None):
        if self.kind == "text":
            return self.text
        if self.kind == "at":
            nm = self.name
            if not nm and resolve:
                nm = resolve(self.uid) or resolve(self.qq)
            return "@" + (nm or self.qq or "某人")
        if self.kind == "image":
            return "[图片]"
        if self.kind == "record":
            return "[语音]"
        if self.kind == "file":
            return "[文件:%s]" % self.name if self.name else "[文件]"
        if self.kind == "face":
            return "[表情]"
        if self.kind == "reply":
            return "[回复]"
        if self.kind == "forward":
            return "[聊天记录]"
        return ""


def _merge_text(segs):
    """合并相邻 text；折叠连续同类媒体；去掉重复片段。

    protobuf 正文常把内容存两份、占位符重复，故：
      * 相邻 text 合并；连续出现的重复片段丢弃
      * 连续的 image / record / face 折叠为一段
    """
    out = []
    for s in segs:
        if s.kind == "text" and not s.text:
            continue
        if out and out[-1].kind == "text" and s.kind == "text":
            if s.text and s.text in out[-1].text:
                continue
            out[-1].text += s.text
            continue
        if out and s.kind in ("image", "record", "face") and out[-1].kind == s.kind:
            continue  # 连续同类媒体折叠
        if out and s.kind == out[-1].kind and s.kind != "text" \
                and s.name == out[-1].name and s.url == out[-1].url:
            continue
        out.append(s)
    return out


def parse_blob(blob):
    """blob -> list[Segment]。空 blob 返回 []。"""
    if not blob:
        return []
    items = []
    _scan(blob, 0, items)

    segs = []
    prev_text = None
    for kind, val in items:
        if kind == "text":
            if val == prev_text:
                continue  # 完全相同且相邻的重复片段
            prev_text = val
            if _has_markup(val):
                chunk = []
                _parse_markup(val, chunk)
                for s in chunk:
                    if s.kind == "text":
                        s.text = strip_assets(s.text)
                        if not s.text.strip():
                            continue
                    segs.append(s)
            else:
                cleaned = strip_assets(val)
                if cleaned.strip():
                    segs.append(Segment("text", text=cleaned))
        elif kind == "media":
            prev_text = None
            mk = _media_kind(val)
            if val.startswith("http"):
                segs.append(Segment(mk, url=val))
            elif mk == "file":
                segs.append(Segment("file", name=val))
            elif _is_media_name(val):
                segs.append(Segment(mk, name=val))
            else:
                # 裸 CDN 主机名之类的无意义片段，只保留类型
                segs.append(Segment(mk))

    return _merge_text(segs)


def render_inline(blob_or_segments, resolve=None):
    """分段或 blob -> 单行可读文本（媒体以占位符呈现）。"""
    if isinstance(blob_or_segments, (bytes, bytearray)):
        segs = parse_blob(bytes(blob_or_segments))
    else:
        segs = blob_or_segments
    return "".join(s.inline(resolve) for s in segs).strip()


def media_of(segments):
    """提取媒体分段（url/name），供 bundle 或 JSON 使用。"""
    out = []
    for s in segments:
        if s.kind in ("image", "record", "file") and (s.url or s.name):
            out.append(s)
    return out
