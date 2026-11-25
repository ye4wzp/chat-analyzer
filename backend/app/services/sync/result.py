"""Shared sync outcomes; completion and completeness are separate facts."""
import inspect
import re

import httpx


def new_result(platform: str) -> dict:
    return {"platform": platform, "targets": 0, "chats": 0, "imported": 0,
            "excluded": 0, "details": []}


def result_status(result: dict) -> str:
    details = result.get("details", [])
    reported = {d["chat_id"] for d in details if "chat_id" in d}
    if set(result.get("target_ids", [])) - reported:
        return "partial"
    if any(d["status"] == "pending" for d in details) or result.get("pending"):
        return "partial"
    failures = any(d["status"] in ("failed", "pending") for d in details)
    failures = failures or bool(result.get("skipped") or result.get("pending"))
    if failures:
        return "partial" if result.get("chats") or result.get("imported") else "error"
    return "done"


async def record_result(result, chat_id, name, status, imported=0, error=None, on_result=None, **checkpoint):
    item = {"chat_id": chat_id, "name": name, "status": status, "imported": imported, **checkpoint}
    if error:
        item["error"] = error
    result["details"].append(item)
    result["imported"] += imported
    if status == "done":
        result["chats"] += 1
    if on_result:
        value = on_result(result)
        if inspect.isawaitable(value):
            await value


async def record_missing(result, requested, found, on_result=None):
    result["target_ids"] = list(dict.fromkeys([*found, *(requested or [])]))
    if on_result:
        value = on_result(result)
        if inspect.isawaitable(value):
            await value
    for cid in sorted(set(requested or []) - set(found)):
        result["targets"] += 1
        await record_result(result, cid, cid, "failed", error="TargetUnavailableOrExcluded", on_result=on_result)


# Internal journal entries, distinct from real IDs that can also start with '@'.
SYSTEM_ENTRIES = {"@platform", "@cache", "@enumeration"}
WECHAT_FOLD_GROUP = "@placeholder_foldgroup"


def error_reason(exc):
    """Keep actionable diagnostics without logging request URLs or credentials."""
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        return f"HTTP {response.status_code} {response.reason_phrase}（{response.request.url.path}）"
    if isinstance(exc, httpx.ConnectError):
        return "连接失败，请检查采集服务是否已启动并可访问"
    message = " ".join(str(exc).split()) or type(exc).__name__
    message = re.sub(r'https?://\S+', '[服务地址]', message)
    message = re.sub(r'(?i)\b(Bearer\s+|(?:token|password|api[_-]?key|api_hash)\s*[:=]\s*)[^\s,;]+', r'\1***', message)
    return message[:400]


def resume_scope(platform, result, request, interrupted):
    """Return (run, selection, done IDs, live-only IDs) for one saved platform."""
    original = (request.get("selections") or {}).get(platform)
    cache_mode = platform == "wechat" and request.get("mode") in ("full", "repair")
    details = {d["chat_id"]: d for d in result.get("details", [])}
    cache_unfinished = cache_mode and ("@cache" in details or "@platform" in details or
                                       (interrupted and not result.get("finished")))
    done = {cid for cid, d in details.items() if d["status"] == "done"
            and (not cache_unfinished or d.get("cache_checked"))}
    live_only = {cid for cid, d in details.items() if cache_mode and
                 (d["status"] == "done" or d.get("live_done"))} - done
    ignored = {WECHAT_FOLD_GROUP} if platform == "wechat" else set()
    ignored.update(cid for cid, d in details.items() if d["status"] == "excluded")
    known = set(details) - SYSTEM_ENTRIES - ignored
    if original is not None:
        candidates = original
    elif cache_unfinished or not result or "@enumeration" in details or result.get("expected_min", 0) > len(known | set(result.get("target_ids", []))):
        candidates = None
    elif result.get("target_ids") is not None:
        candidates = [*result["target_ids"], *sorted(known)]
    elif "@platform" in details:
        candidates = None
    elif result.get("finished") or not interrupted or (known and len(set(details)-SYSTEM_ENTRIES) >= result.get("targets", len(details))):
        candidates = sorted(known)
    else:
        candidates = None  # Legacy partial enumeration: rediscover, skip recorded successes.
    if candidates is None:
        return True, None, done, live_only
    excluded = done | ignored | SYSTEM_ENTRIES
    remaining = list(dict.fromkeys(cid for cid in candidates if cid not in excluded))
    return bool(remaining or cache_unfinished), remaining, done, live_only


def merge_attempt(previous, current):
    """Overlay this attempt on the journal, accumulating only newly committed rows."""
    platform = current["platform"]
    old = {d["chat_id"]: d for d in previous.get("details", []) if d["chat_id"] not in SYSTEM_ENTRIES
           and not (platform == "wechat" and d["chat_id"] == WECHAT_FOLD_GROUP)}
    merged = dict(old)
    for d in current.get("details", []):
        cid = d["chat_id"]
        merged[cid] = {**d, "imported": d.get("imported", 0) + old.get(cid, {}).get("imported", 0)}
    result = {**current, "details": list(merged.values()),
              "imported": previous.get("imported", 0) + current.get("imported", 0),
              "chats": sum(d["status"] == "done" for d in merged.values())}
    if "target_ids" in current or "target_ids" in previous:
        ids = dict.fromkeys([*previous.get("target_ids", []), *old, *current.get("target_ids", [])])
        result["target_ids"] = list(ids)
        result["targets"] = len(ids)
    minimum = previous.get("expected_min", 0)
    if "target_ids" not in previous and previous.get("details") and not previous.get("finished"):
        # An interrupted legacy task saved the count, but not the full list of IDs.
        if not any(d["chat_id"] in SYSTEM_ENTRIES for d in previous["details"]):
            minimum = max(minimum, previous.get("targets", 0))
    if minimum:
        result["expected_min"] = minimum
        if "target_ids" in result and len(result["target_ids"]) < minimum:
            missing = minimum - len(result["target_ids"])
            result["details"].append({"chat_id":"@enumeration", "name":"原会话清单", "status":"pending",
                                      "imported":0, "count":missing, "error":f"旧任务未保存完整 ID，当前清单比原记录少 {missing} 个会话，无法核实。"})
            result["targets"] = minimum
    if "cache_targets" in previous or "cache_targets" in current:
        result["cache_targets"] = sorted(set(previous.get("cache_targets", [])) | set(current.get("cache_targets", [])))
    return result
