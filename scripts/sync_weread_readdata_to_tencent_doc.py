#!/usr/bin/env python3

import argparse
import base64
import concurrent.futures
import datetime as dt
import json
import os
import re
import ssl
import subprocess
import sys
import time
from urllib import parse, request


# 微信读书数据统一通过 weread-skill 暴露的 Agent API Gateway 读取。
# 这里的 /shelf/sync、/mine/readbook、/book/info 等是 gateway 的 api_name，
# 不直接请求微信读书原生/raw API。
WEREAD_SKILL_GATEWAY_URL = "https://i.weread.qq.com/api/agent/gateway"
WEREAD_SKILL_VERSION = "1.0.3"
SSL_CTX = ssl._create_unverified_context()
SHEET_ID_PATTERN = re.compile(r"^(sheet_|tab_|grid_|[a-zA-Z0-9_-]{6,})")
TEMPLATE_SMARTSHEET_URL = "https://docs.qq.com/smartsheet/DYXpmanNXaURNWVB4?nlc=1&no_promotion=1&is_blank_or_template=template&tab=sc_tNPtzz"
TEMPLATE_FILE_ID = "DYXpmanNXaURNWVB4"
TARGET_TABLE_TITLE = "书籍列表"

REQUIRED_FIELDS = [
    "bookId",
    "书名",
    "书架分类",
    "价格",
    "作者",
    "分类",
    "一级分类",
    "是否可读",
    "评分",
    "阅读时长（秒）",
    "阅读时长（时）",
    "阅读时长（分）",
    "阅读时长格式化",
    "封面",
    "字数（单位：万字）",
    "简介",
    "阅读进度",
    "是否已读完",
    "阅读完成时间",
    "已读完年",
    "已读完年月",
]

NUMBER_FIELDS = {
    "价格",
    "评分",
    "阅读时长（秒）",
    "阅读时长（时）",
    "阅读时长（分）",
    "字数（单位：万字）",
    "阅读进度",
}
MULTI_OPTION_FIELDS = {"分类", "一级分类"}
OPTION_LIKE_FIELDS = {"是否可读", "是否已读完", "已读完年", "已读完年月"}
DATE_FIELDS = {"阅读完成时间"}
FETCH_EXISTING_FIELDS = ["bookId"]
IMAGE_CONTENT_TYPE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}


def eprint(*args):
    print(*args, file=sys.stderr)


def run_cmd(argv):
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"command failed: {' '.join(argv)}")
    return proc.stdout


def tencent_json(tool, args):
    out = run_cmd([
        "mcporter", "call", "tencent-docs", tool,
        "--args", json.dumps(args, ensure_ascii=False),
    ])
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"failed to parse mcporter output as JSON: {exc}\nraw: {out[:500]}")
    if data.get("error"):
        raise RuntimeError(json.dumps(data, ensure_ascii=False))
    return data


def tencent_json_resilient(tool, args):
    """Call Tencent Docs MCP and tolerate malformed huge success JSON payloads.

    Some smartsheet responses echo all submitted records. Long text containing
    unescaped control characters may make mcporter output invalid JSON even
    though the write itself has succeeded. For write tools, callers only need to
    know whether MCP reported an explicit failure, so return a minimal success
    marker when JSON parsing fails after a zero-exit command.
    """
    out = run_cmd([
        "mcporter", "call", "tencent-docs", tool,
        "--args", json.dumps(args, ensure_ascii=False),
    ])
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        if '"error": ""' in out[:200]:
            return {"error": "", "raw_json_parse_warning": True}
        raise
    if data.get("error"):
        raise RuntimeError(json.dumps(data, ensure_ascii=False))
    return data


def weread_call(payload):
    """Call WeRead data capabilities through weread-skill's Agent API Gateway."""
    api_key = os.environ.get("WEREAD_API_KEY")
    if not api_key:
        raise RuntimeError("missing WEREAD_API_KEY environment variable")

    body = dict(payload)
    body.setdefault("skill_version", WEREAD_SKILL_VERSION)
    data = json.dumps(body).encode("utf-8")
    req = request.Request(
        WEREAD_SKILL_GATEWAY_URL,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if "upgrade_info" in result:
        raise RuntimeError(f"WeRead skill needs upgrade: {json.dumps(result['upgrade_info'], ensure_ascii=False)}")
    if result.get("errcode") not in (None, 0):
        raise RuntimeError(f"WeRead skill gateway error: {json.dumps(result, ensure_ascii=False)}")
    return result


def parse_table_url(url):
    parsed = parse.urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    query = parse.parse_qs(parsed.query)
    file_id = query.get("file_id", [None])[0]
    if not file_id and parts:
        file_id = parts[-1]
    sheet_id = (
        query.get("sheet_id", [None])[0]
        or query.get("sheet", [None])[0]
        or query.get("tab", [None])[0]
        or query.get("table", [None])[0]
    )
    if not file_id:
        raise RuntimeError(f"file_id not found in Tencent Docs URL: {url}")
    return file_id, sheet_id


def is_valid_sheet_id(sheet_id):
    return bool(sheet_id and SHEET_ID_PATTERN.match(sheet_id))


def list_tables(file_id):
    data = tencent_json("smartsheet.list_tables", {"file_id": file_id})
    raw_tables = data.get("sheets") or data.get("tables") or []
    tables = []
    for item in raw_tables:
        tables.append({
            "sheet_id": item.get("sheet_id") or item.get("id"),
            "title": item.get("title") or item.get("name"),
        })
    return tables


def fetch_fields(file_id, sheet_id):
    data = tencent_json("smartsheet.list_fields", {"file_id": file_id, "sheet_id": sheet_id, "offset": 0, "limit": 100})
    return data.get("fields", [])


def normalize_field(field):
    return {
        "title": field.get("field_title") or field.get("name") or field.get("title"),
        "type": field.get("field_type") or field.get("type"),
        "field_id": field.get("field_id") or field.get("id"),
    }


def fields_by_title(fields):
    indexed = {}
    for item in fields:
        field = normalize_field(item)
        if field.get("title"):
            indexed[field["title"]] = field
    return indexed


def validate_fields(fields):
    indexed = fields_by_title(fields)
    missing = [name for name in REQUIRED_FIELDS if name not in indexed]
    if missing:
        raise RuntimeError(f"missing required fields: {', '.join(missing)}")
    return indexed


def try_validate_table(file_id, sheet_id):
    try:
        fields = fetch_fields(file_id, sheet_id)
        validate_fields(fields)
        return True
    except Exception:  # noqa: BLE001
        return False


def find_matching_book_table(file_id):
    matches = []
    for table in list_tables(file_id):
        if not table["sheet_id"]:
            continue
        if try_validate_table(file_id, table["sheet_id"]):
            matches.append(table)
    if not matches:
        raise RuntimeError("provided sheet_id format is invalid or absent, and no sheet with required 书籍列表 headers was found in the Tencent SmartSheet")
    preferred = next((item for item in matches if item.get("title") == TARGET_TABLE_TITLE), matches[0])
    return preferred, matches


def resolve_sheet_for_file(file_id, sheet_id):
    if is_valid_sheet_id(sheet_id):
        return sheet_id, None
    selected, matches = find_matching_book_table(file_id)
    return selected["sheet_id"], {
        "requested_sheet_id": sheet_id,
        "resolved_sheet_id": selected["sheet_id"],
        "resolved_sheet_title": selected.get("title"),
        "reason": "missing_or_invalid_sheet_id_fallback_to_matching_book_table",
        "candidate_sheet_ids": [item["sheet_id"] for item in matches],
    }


def text_value(text):
    return {"items": [{"text": str(text), "type": "text"}]}


def option_value(values):
    if values is None:
        items = []
    elif isinstance(values, list):
        items = [{"text": str(item)} for item in values if str(item) != ""]
    else:
        items = [{"text": str(values)}] if str(values) != "" else []
    return {"items": items}


def url_value(url, text=None):
    return {"items": [{"text": text or url, "type": "url", "link": url}]}


def string_to_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip() in {"1", "true", "True", "是", "yes", "Y"}


def make_field_value(field_name, value, field_types, warnings):
    field_type = (field_types.get(field_name) or {}).get("type")

    entry = {"field": field_name}
    if field_type in {"number", "progress", "currency", "percentage"}:
        if value is None or value == "":
            return None
        entry["number_value"] = float(value or 0)
        if field_name in {"阅读时长（秒）"}:
            entry["number_value"] = int(entry["number_value"])
    elif field_type == "checkbox":
        entry["bool_value"] = string_to_bool(value)
    elif field_type == "dateTime":
        if value:
            entry["string_value"] = str(int(value))
        else:
            entry["string_value"] = ""
    elif field_type in {"select", "singleSelect"}:
        entry["option_value"] = option_value(value)
    elif field_type == "url":
        if value:
            entry["url_value"] = url_value(str(value), "封面" if field_name == "封面" else str(value))
        else:
            entry["url_value"] = {"items": []}
    elif field_type == "image":
        if isinstance(value, str) and value and not value.startswith("http"):
            entry["image_value"] = {"items": [{"image_id": value}]}
        else:
            warnings.add("字段“封面”为 image 类型，但微信读书只返回封面 URL；未写入封面图片字段。")
            return None
    elif field_type == "text":
        if value is None:
            value = ""
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        entry["text_value"] = text_value(value)
    elif not field_type and field_name in NUMBER_FIELDS:
        if value is None or value == "":
            return None
        entry["number_value"] = float(value or 0)
        if field_name in {"阅读时长（秒）"}:
            entry["number_value"] = int(entry["number_value"])
    elif not field_type and field_name in DATE_FIELDS:
        entry["string_value"] = str(int(value)) if value else ""
    elif not field_type and (field_name in MULTI_OPTION_FIELDS or field_name in OPTION_LIKE_FIELDS):
        entry["option_value"] = option_value(value)
    elif not field_type and field_name == "封面" and isinstance(value, str) and value.startswith("http"):
        entry["url_value"] = url_value(str(value), "封面")
    else:
        if value is None:
            value = ""
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        entry["text_value"] = text_value(value)
    return entry


def field_value_to_python(entry):
    if "number_value" in entry:
        return entry.get("number_value")
    if "string_value" in entry:
        return entry.get("string_value")
    if "bool_value" in entry:
        return entry.get("bool_value")
    if "text_value" in entry:
        items = (entry.get("text_value") or {}).get("items") or []
        return "".join(str(item.get("text", "")) for item in items)
    if "url_value" in entry:
        items = (entry.get("url_value") or {}).get("items") or []
        if not items:
            return ""
        return items[0].get("link") or items[0].get("text") or ""
    if "option_value" in entry:
        items = (entry.get("option_value") or {}).get("items") or []
        return [item.get("text") or item.get("id") for item in items if item.get("text") or item.get("id")]
    if "image_value" in entry:
        items = (entry.get("image_value") or {}).get("items") or []
        return [item.get("image_id") for item in items if item.get("image_id")]
    return None


def normalize_for_compare(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, list):
        if len(value) == 1:
            return normalize_for_compare(value[0])
        return sorted(str(item) for item in value)
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value).strip()


def fetch_existing_records(file_id, sheet_id):
    offset = 0
    existing = {}
    while True:
        data = tencent_json("smartsheet.list_records", {
            "file_id": file_id,
            "sheet_id": sheet_id,
            # 只读取 bookId 定位记录。腾讯 MCP 在读取长文本/复杂选项字段时偶发输出非法 JSON；
            # 写入时仍会提交完整字段。为保证空数字字段能被真正清空，已存在记录会重建。
            "field_titles": FETCH_EXISTING_FIELDS,
            "offset": offset,
            "limit": 20,
        })
        records = data.get("records") or []
        for record in records:
            values = {}
            for entry in record.get("field_values", []):
                values[entry.get("field")] = field_value_to_python(entry)
            book_id = str(values.get("bookId") or "").strip()
            if not book_id:
                continue
            existing[book_id] = {
                "record_id": record.get("record_id"),
                "fields": values,
            }
        if not data.get("has_more"):
            break
        offset = data.get("next") or (offset + len(records))
    return existing


def get_shelf():
    return weread_call({"api_name": "/shelf/sync"})


def get_mine_read_books():
    maxidx = 0
    all_books = []
    finished = {}
    reading = {}
    while True:
        data = weread_call({
            "api_name": "/mine/readbook",
            "count": 100,
            "rating": 0,
            "star": 0,
            "listType": 3,
            "yearRange": "0_0",
            "maxidx": maxidx,
        })
        read_books = data.get("readBooks") or []
        for book in read_books:
            all_books.append(book)
            book_id = str(book.get("bookId") or "")
            if not book_id:
                continue
            if book.get("markStatus") == 4:
                finished[book_id] = book
            elif book.get("markStatus") == 2:
                reading[book_id] = book
        if not data.get("hasMore"):
            break
        maxidx += len(read_books)
        if not read_books:
            break
    return all_books, reading, finished


def get_book_detail(book_id):
    return weread_call({"api_name": "/book/info", "bookId": book_id})


def get_book_progress(book_id):
    return weread_call({"api_name": "/book/getprogress", "bookId": book_id})


def get_book_chapterinfo(book_id):
    return weread_call({"api_name": "/book/chapterinfo", "bookId": book_id})


def batch_get_book_details(book_ids, max_workers=10):
    details = {}
    if not book_ids:
        return details
    workers = max(1, int(max_workers or 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_book = {executor.submit(get_book_detail, book_id): book_id for book_id in book_ids}
        for future in concurrent.futures.as_completed(future_to_book):
            book_id = future_to_book[future]
            details[book_id] = future.result()
    return details


def batch_get_book_progresses(book_ids, max_workers=10):
    progresses = {}
    if not book_ids:
        return progresses
    workers = max(1, int(max_workers or 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_book = {executor.submit(get_book_progress, book_id): book_id for book_id in book_ids}
        for future in concurrent.futures.as_completed(future_to_book):
            book_id = future_to_book[future]
            try:
                progresses[book_id] = future.result()
            except Exception as exc:  # noqa: BLE001
                progresses[book_id] = {"error": str(exc)}
    return progresses


def batch_get_book_chapterinfos(book_ids, max_workers=10):
    chapter_infos = {}
    if not book_ids:
        return chapter_infos
    workers = max(1, int(max_workers or 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_book = {executor.submit(get_book_chapterinfo, book_id): book_id for book_id in book_ids}
        for future in concurrent.futures.as_completed(future_to_book):
            book_id = future_to_book[future]
            try:
                chapter_infos[book_id] = future.result()
            except Exception as exc:  # noqa: BLE001
                chapter_infos[book_id] = {"error": str(exc)}
    return chapter_infos


def extract_titles(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        titles = []
        for item in value:
            titles.extend(extract_titles(item))
        return titles
    if isinstance(value, dict):
        for key in ("title", "name", "category", "label", "text", "value"):
            if value.get(key):
                return extract_titles(value.get(key))
        return []
    text = str(value).strip()
    return [text] if text else []


def expand_category_value(value):
    expanded = []
    seen = set()
    for title in extract_titles(value):
        parts = [part.strip() for part in re.split(r"\s*-\s*", title) if part and part.strip()]
        candidates = parts if len(parts) > 1 else [title]
        for item in candidates:
            if item not in seen:
                expanded.append(item)
                seen.add(item)
    return expanded


def get_category_titles(item):
    categories = []
    first_levels = []
    seen_categories = set()
    seen_first_levels = set()
    if not isinstance(item, dict):
        return categories, first_levels
    for key in ("categories", "category", "subCategories", "subCategory", "categoryText", "classify", "classification"):
        value = item.get(key)
        if value is None:
            continue
        expanded = expand_category_value(value)
        if not expanded:
            continue
        first = expanded[0]
        if first and first not in seen_first_levels:
            first_levels.append(first)
            seen_first_levels.add(first)
        for category in expanded:
            if category and category not in seen_categories:
                categories.append(category)
                seen_categories.add(category)
    return categories, first_levels


def first_level_categories(categories):
    result = []
    seen = set()
    for category in categories or []:
        first = str(category).split("-")[0]
        if first and first not in seen:
            result.append(first)
            seen.add(first)
    return result


def value_from_paths(data, *paths):
    if not isinstance(data, dict):
        return None
    for path in paths:
        current = data
        found = True
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                found = False
                break
            current = current.get(part)
        if found:
            return current
    return None


def any_truthy(data, *paths):
    value = value_from_paths(data, *paths)
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "paid", "member", "vip", "on", "open", "available", "support"}
    return bool(value)


def detect_user_is_paid_member(*sources):
    for env_name in ("WEREAD_IS_PAID_MEMBER", "WEREAD_MEMBER_ACTIVE", "WEREAD_VIP_ACTIVE"):
        env_value = os.environ.get(env_name)
        if env_value is not None and str(env_value).strip() != "":
            return string_to_bool(env_value)
    member_paths = (
        "isMember",
        "isVip",
        "member",
        "vip",
        "memberActive",
        "vipActive",
        "memberInfo.isActive",
        "vipInfo.isActive",
        "user.isMember",
        "user.isVip",
    )
    for source in sources:
        if any(any_truthy(source, path) for path in member_paths):
            return True
    return False


def infer_can_read(item, detail=None, chapter_info=None, user_is_paid_member=False):
    if user_is_paid_member:
        return True
    detail = detail or {}
    chapter_info = chapter_info or {}

    explicit_free_paths = (
        "free",
        "isFree",
        "freeRead",
        "isFreeRead",
        "supportFreeRead",
        "canFreeRead",
    )
    explicit_card_paths = (
        "experienceCardReadable",
        "isExperienceCardReadable",
        "supportExperienceCardRead",
        "supportExperienceCard",
        "canUseExperienceCard",
        "trialRead",
        "isTrialRead",
    )
    explicit_purchased_paths = (
        "paid",
        "isPaid",
        "bought",
        "isBought",
        "purchased",
        "isPurchased",
        "hasPurchased",
    )
    for source in (item or {}, detail):
        if any(any_truthy(source, path) for path in explicit_free_paths):
            return True
        if any(any_truthy(source, path) for path in explicit_card_paths):
            return True
        if any(any_truthy(source, path) for path in explicit_purchased_paths):
            return True

    chapters = (chapter_info or {}).get("chapters") or []
    if chapters:
        priced_chapters = [chapter for chapter in chapters if int(chapter.get("price") or 0) != 0]
        if not priced_chapters:
            return True
        return all(int(chapter.get("paid") or 0) == 1 for chapter in priced_chapters)

    return False


def guess_image_file_name(url, content_type=None):
    parsed = parse.urlparse(url or "")
    basename = os.path.basename(parsed.path or "") or "cover"
    root, ext = os.path.splitext(basename)
    ext = ext.lower()
    if not ext:
        ext = IMAGE_CONTENT_TYPE_EXTENSIONS.get((content_type or "").split(";")[0].strip().lower(), ".jpg")
    return f"{root or 'cover'}{ext}"


def upload_image_from_url(url, image_upload_cache=None):
    if not url:
        return ""
    cache = image_upload_cache if image_upload_cache is not None else {}
    if url in cache:
        return cache[url]
    req = request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
        binary = resp.read()
        content_type = resp.headers.get("Content-Type", "")
    if not binary:
        raise RuntimeError(f"empty image body: {url}")
    file_name = guess_image_file_name(url, content_type=content_type)
    data = tencent_json("upload_image", {
        "image_base64": base64.b64encode(binary).decode("ascii"),
        "file_name": file_name,
    })
    image_id = data.get("image_id") or data.get("id") or ""
    if not image_id:
        raise RuntimeError(f"upload_image returned no image_id for {url}")
    cache[url] = image_id
    return image_id


def format_read_time(seconds):
    seconds = int(seconds or 0)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remain = seconds % 60
    text = ""
    if hours > 0:
        text += f"{hours}时"
    if minutes > 0:
        text += f"{minutes}分"
    if remain > 0:
        text += f"{remain}秒"
    return text


def yes_no(value):
    return "是" if value else "否"


def first_number(*values):
    for value in values:
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def normalize_price(*values):
    price = first_number(*values)
    if price is None:
        return None
    # 微信读书部分接口价格以“分”为单位；异常大的值按分转元，已是元的小数保持原样。
    if price >= 100:
        price = price / 100
    return round(price, 2)


def normalize_rating(value):
    rating = first_number(value)
    if rating is None or rating <= 0:
        return None
    # /book/info 的 newRating 常见为千分制分数，如 826 表示 8.26 分。
    # 兼容少量已换算成百分制/十分制的输入，统一写入 0-10 分。
    if rating > 100:
        rating = rating / 100
    elif rating > 10:
        rating = rating / 10
    return round(rating, 1)


def normalize_words(*values):
    words = first_number(*values)
    if words is None or words <= 0:
        return None
    return round(words / 10000, 2)


def progress_book(progresses, book_id):
    progress = progresses.get(book_id) or {}
    book = progress.get("book") if isinstance(progress, dict) else None
    return book or {}


def finish_year(finish_time_ms):
    if not finish_time_ms:
        return ""
    return dt.datetime.fromtimestamp(int(finish_time_ms) / 1000).strftime("%Y年")


def finish_year_month(finish_time_ms):
    if not finish_time_ms:
        return ""
    return dt.datetime.fromtimestamp(int(finish_time_ms) / 1000).strftime("%Y年%m月")


def build_books_from_weread(shelf, details, finished_books, progresses=None, chapter_infos=None, user_is_paid_member=False):
    progresses = progresses or {}
    chapter_infos = chapter_infos or {}
    book_progress = {}
    for progress in shelf.get("bookProgress") or []:
        book_id = str(progress.get("bookId") or "")
        if book_id:
            book_progress[book_id] = progress

    shelf_names = {}
    for archive in shelf.get("archive") or []:
        name = archive.get("name") or ""
        for book_id in archive.get("bookIds") or []:
            shelf_names[str(book_id)] = name

    finish_times = {}
    for book_id, book in finished_books.items():
        finish_time = int(book.get("finishTime") or 0)
        if finish_time > 0:
            finish_times[book_id] = finish_time * 1000

    rows = {}
    for item in shelf.get("books") or shelf.get("book") or []:
        book_id = str(item.get("bookId") or "")
        if not book_id:
            continue
        detail = details.get(book_id) or {}
        progress_detail = progress_book(progresses, book_id)
        chapter_info = chapter_infos.get(book_id) or {}
        merged_progress = {**(book_progress.get(book_id) or {}), **progress_detail}
        categories, first_level = get_category_titles(item)
        if not categories:
            categories, first_level = get_category_titles(detail)
        read_time = int(first_number(merged_progress.get("readingTime"), merged_progress.get("recordReadingTime")) or 0)
        progress = min(max(first_number(merged_progress.get("progress")) or 0, 0), 100) / 100
        finish_time_ms = finish_times.get(book_id, 0)
        if not finish_time_ms and int(merged_progress.get("finishTime") or 0) > 0:
            finish_time_ms = int(merged_progress.get("finishTime")) * 1000
        is_finished = bool(item.get("finishReading") == 1 or progress >= 1 or finish_time_ms)
        if is_finished:
            progress = 1
        score = normalize_rating(detail.get("newRating"))
        words = normalize_words(detail.get("totalWords"), detail.get("wordCount"))
        can_read = infer_can_read(item, detail=detail, chapter_info=chapter_info, user_is_paid_member=user_is_paid_member)
        rows[book_id] = book_to_row(
            book_id=book_id,
            title=item.get("title") or detail.get("title") or "",
            author=item.get("author") or detail.get("author") or "",
            cover=item.get("cover") or detail.get("cover") or "",
            price=normalize_price(item.get("price"), detail.get("price")),
            can_read=can_read,
            categories=categories,
            first_level_categories_value=first_level,
            read_time=read_time,
            shelf_name=shelf_names.get(book_id, ""),
            score=score,
            intro=detail.get("intro") or "",
            words=words,
            progress=progress,
            finish_time_ms=finish_time_ms,
            is_finished=is_finished,
        )

    for book_id, item in finished_books.items():
        if book_id in rows:
            continue
        detail = details.get(book_id) or {}
        chapter_info = chapter_infos.get(book_id) or {}
        categories, first_level = get_category_titles(detail)
        read_time = int(item.get("readtime") or 0)
        finish_time_ms = int(item.get("finishTime") or 0) * 1000
        score = normalize_rating(detail.get("newRating"))
        words = normalize_words(detail.get("totalWords"), detail.get("wordCount"))
        rows[book_id] = book_to_row(
            book_id=book_id,
            title=item.get("title") or detail.get("title") or "",
            author=item.get("author") or detail.get("author") or "",
            cover=item.get("cover") or detail.get("cover") or "",
            price=normalize_price(detail.get("price")),
            can_read=infer_can_read(item, detail=detail, chapter_info=chapter_info, user_is_paid_member=user_is_paid_member),
            categories=categories,
            first_level_categories_value=first_level,
            read_time=read_time,
            shelf_name="",
            score=score,
            intro=detail.get("intro") or "",
            words=words,
            progress=1,
            finish_time_ms=finish_time_ms,
            is_finished=True,
        )
    return rows


def book_to_row(book_id, title, author, cover, price, can_read, categories, read_time, shelf_name, score, intro, words, progress, finish_time_ms, is_finished=None, first_level_categories_value=None):
    read_time = int(read_time or 0)
    finish_read = bool(is_finished) if is_finished is not None else finish_time_ms > 0
    return {
        "bookId": book_id,
        "书名": title,
        "书架分类": shelf_name,
        "价格": price,
        "作者": author,
        "分类": categories or [],
        "一级分类": first_level_categories_value or first_level_categories(categories),
        "是否可读": yes_no(can_read),
        "评分": score,
        "阅读时长（秒）": read_time,
        "阅读时长（时）": float(read_time) / 3600,
        "阅读时长（分）": float(read_time) / 60,
        "阅读时长格式化": format_read_time(read_time),
        "封面": cover or "",
        "字数（单位：万字）": words,
        "简介": intro or "",
        "阅读进度": float(progress or 0),
        "是否已读完": yes_no(finish_read),
        "阅读完成时间": int(finish_time_ms or 0) if finish_read else "",
        "已读完年": finish_year(finish_time_ms),
        "已读完年月": finish_year_month(finish_time_ms),
    }


def extract_book_rows(max_workers=10):
    try:
        _, _, finished_books = get_mine_read_books()
    except Exception as exc:  # noqa: BLE001
        eprint(f"WARN: failed to fetch /mine/readbook, fallback to bookshelf only: {exc}")
        finished_books = {}
    shelf = get_shelf()
    book_ids = []
    seen = set()
    for item in shelf.get("books") or shelf.get("book") or []:
        book_id = str(item.get("bookId") or "")
        if book_id and book_id not in seen:
            book_ids.append(book_id)
            seen.add(book_id)
    for book_id in finished_books:
        if book_id and book_id not in seen:
            book_ids.append(book_id)
            seen.add(book_id)
    details = batch_get_book_details(book_ids, max_workers=max_workers)
    progresses = batch_get_book_progresses(book_ids, max_workers=max_workers)
    chapter_infos = batch_get_book_chapterinfos(book_ids, max_workers=max_workers)
    user_is_paid_member = detect_user_is_paid_member(shelf, details, finished_books, progresses)
    rows_by_id = build_books_from_weread(
        shelf,
        details,
        finished_books,
        progresses=progresses,
        chapter_infos=chapter_infos,
        user_is_paid_member=user_is_paid_member,
    )
    return [rows_by_id[key] for key in sorted(rows_by_id, key=lambda k: rows_by_id[k].get("书名") or k)]


def payload_for_row(row, field_types, warnings, upload_images=False, image_upload_cache=None):
    values = []
    for field in REQUIRED_FIELDS:
        value = row.get(field)
        if field == "封面" and upload_images:
            field_type = (field_types.get(field) or {}).get("type")
            if field_type == "image" and isinstance(value, str) and value.startswith("http"):
                try:
                    value = upload_image_from_url(value, image_upload_cache=image_upload_cache)
                except Exception as exc:  # noqa: BLE001
                    warnings.add(f"字段“封面”图片上传失败：{exc}")
                    continue
        entry = make_field_value(field, value, field_types, warnings)
        if entry is not None:
            values.append(entry)
    return values


def row_changed(existing_fields, target):
    for field in REQUIRED_FIELDS:
        if field in {"封面", "简介"}:
            # 图片字段可能无法从 URL 反向比较；长简介不参与轻量比较，避免读取时 JSON 过大。
            continue
        if normalize_for_compare(existing_fields.get(field)) != normalize_for_compare(target.get(field)):
            return True
    return False


def should_recreate_record(existing_fields, target):
    """Whether update cannot make the existing record correct.

    Tencent SmartSheet numeric/currency/progress fields cannot be reliably cleared
    by updating them to null/empty. If previous runs wrote placeholder 0 but the
    source actually has no value, recreate the row so omitted fields stay blank.
    """
    if set(existing_fields.keys()) <= {"bookId"}:
        return True
    for field in NUMBER_FIELDS:
        if field in {"阅读时长（秒）", "阅读时长（时）", "阅读时长（分）", "阅读进度"}:
            continue
        if target.get(field) is None and existing_fields.get(field) not in (None, ""):
            return True
    return False


def chunked(items, size):
    for idx in range(0, len(items), size):
        yield items[idx: idx + size]


def upsert_rows(file_id, sheet_id, rows, existing, field_types, dry_run=False, delete_missing=False):
    summary = {
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "skipped": 0,
        "created_record_ids": [],
        "updated_record_ids": [],
        "deleted_record_ids": [],
        "warnings": [],
    }
    warning_set = set()
    image_upload_cache = {}
    rows_to_create = []
    rows_to_update = []
    record_ids_to_delete = []
    row_ids = {row["bookId"] for row in rows}

    for row in rows:
        book_id = row["bookId"]
        if book_id in existing:
            if should_recreate_record(existing[book_id]["fields"], row):
                record_id = existing[book_id].get("record_id")
                if record_id:
                    record_ids_to_delete.append(record_id)
                rows_to_create.append({
                    "field_values": payload_for_row(row, field_types, warning_set, upload_images=not dry_run, image_upload_cache=image_upload_cache),
                })
                continue
            if not row_changed(existing[book_id]["fields"], row):
                summary["skipped"] += 1
                continue
            record_id = existing[book_id].get("record_id")
            if record_id:
                rows_to_update.append({
                    "record_id": record_id,
                    "field_values": payload_for_row(row, field_types, warning_set, upload_images=not dry_run, image_upload_cache=image_upload_cache),
                })
        else:
            rows_to_create.append({
                "field_values": payload_for_row(row, field_types, warning_set, upload_images=not dry_run, image_upload_cache=image_upload_cache),
            })

    if delete_missing:
        for book_id, record in existing.items():
            if book_id not in row_ids and record.get("record_id"):
                record_ids_to_delete.append(record["record_id"])

    summary["warnings"] = sorted(warning_set)
    if dry_run:
        summary["created"] = len(rows_to_create)
        summary["updated"] = len(rows_to_update)
        summary["deleted"] = len(record_ids_to_delete)
        return summary

    for batch in chunked(record_ids_to_delete, 100):
        tencent_json("smartsheet.delete_records", {"file_id": file_id, "sheet_id": sheet_id, "record_ids": batch})
        summary["deleted"] += len(batch)
        summary["deleted_record_ids"].extend(batch)
        time.sleep(0.3)

    for batch in chunked(rows_to_update, 100):
        tencent_json_resilient("smartsheet.update_records", {"file_id": file_id, "sheet_id": sheet_id, "records": batch})
        summary["updated"] += len(batch)
        summary["updated_record_ids"].extend([item["record_id"] for item in batch])
        time.sleep(0.3)

    for batch in chunked(rows_to_create, 100):
        data = tencent_json_resilient("smartsheet.add_records", {"file_id": file_id, "sheet_id": sheet_id, "records": batch})
        created_records = data.get("records") or []
        summary["created"] += len(batch)
        summary["created_record_ids"].extend([item.get("record_id") for item in created_records if item.get("record_id")])
        time.sleep(0.3)

    return summary


def copy_template_smartsheet(file_name, folder_id=None):
    args = {"file_id": TEMPLATE_FILE_ID, "title": file_name}
    if folder_id:
        args["folder_id"] = folder_id
    data = tencent_json("manage.copy_file", args)
    return {
        "title": data.get("title") or file_name,
        "file_id": data.get("id") or data.get("file_id"),
        "url": data.get("url"),
        "source_template_file_id": TEMPLATE_FILE_ID,
        "source_template_url": TEMPLATE_SMARTSHEET_URL,
    }


def create_smartsheet_from_template(file_name, folder_id=None, retries=5, delay_seconds=1.0):
    file_meta = copy_template_smartsheet(file_name, folder_id=folder_id)
    if not file_meta["file_id"]:
        raise RuntimeError("failed to parse file_id from manage.copy_file result")

    last_error = None
    for _ in range(retries):
        try:
            book_table, table_matches = find_matching_book_table(file_meta["file_id"])
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(delay_seconds)
    else:
        raise RuntimeError(f"copied template but failed to locate a valid 书籍列表 sheet: {last_error}")

    return {
        "smartsheet": file_meta,
        "sheets": {TARGET_TABLE_TITLE: book_table},
        "candidate_sheet_ids": [item["sheet_id"] for item in table_matches],
        "warnings": [],
    }


def resolve_target(args):
    scaffold = None
    sheet_resolution = None
    if args.print_only:
        return None, None, scaffold, sheet_resolution
    if args.table_url:
        file_id, sheet_id = parse_table_url(args.table_url)
        resolved_sheet_id, sheet_resolution = resolve_sheet_for_file(file_id, sheet_id)
        return file_id, resolved_sheet_id, scaffold, sheet_resolution
    if args.file_id and args.sheet_id:
        resolved_sheet_id, sheet_resolution = resolve_sheet_for_file(args.file_id, args.sheet_id)
        return args.file_id, resolved_sheet_id, scaffold, sheet_resolution
    if args.init_smartsheet:
        scaffold = create_smartsheet_from_template(args.file_name, folder_id=args.folder_id)
        book_table = scaffold["sheets"][TARGET_TABLE_TITLE]
        return scaffold["smartsheet"]["file_id"], book_table["sheet_id"], scaffold, sheet_resolution
    raise RuntimeError("provide --table-url, or both --file-id and --sheet-id, or use --init-smartsheet")


def rows_to_markdown(rows, limit=50):
    lines = [
        "| bookId | 书名 | 作者 | 书架分类 | 阅读进度 | 是否已读完 | 阅读时长格式化 |",
        "| --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for row in rows[:limit]:
        lines.append(
            f"| {row['bookId']} | {row['书名']} | {row['作者']} | {row['书架分类']} | {row['阅读进度']:.2f} | {row['是否已读完']} | {row['阅读时长格式化']} |"
        )
    if len(rows) > limit:
        lines.append(f"| ... | 其余 {len(rows) - limit} 本未展示 |  |  |  |  |  |")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Read WeRead bookshelf/finished books and optionally sync them into Tencent Docs SmartSheet.")
    parser.add_argument("--table-url", help="Tencent Docs SmartSheet URL containing file_id and optionally sheet_id")
    parser.add_argument("--file-id", help="Tencent Docs SmartSheet file_id")
    parser.add_argument("--sheet-id", help="Tencent Docs SmartSheet sheet_id")
    parser.add_argument("--dry-run", action="store_true", help="compute sync result but do not write records")
    parser.add_argument("--print-only", action="store_true", help="only read and print markdown table; skip all SmartSheet operations")
    parser.add_argument("--init-smartsheet", action="store_true", help="copy the Tencent SmartSheet template and sync into its 书籍列表 sheet")
    parser.add_argument("--file-name", default="微信读书书架", help="Tencent SmartSheet file name used with --init-smartsheet")
    parser.add_argument("--folder-id", help="optional folder id for the copied SmartSheet")
    parser.add_argument("--delete-missing", action="store_true", help="delete records whose bookId no longer exists in WeRead merged book list")
    parser.add_argument("--max-workers", type=int, default=10, help="parallel workers for /book/info calls")
    args = parser.parse_args()

    if args.print_only and args.init_smartsheet:
        raise RuntimeError("--print-only and --init-smartsheet cannot be used together")
    if args.dry_run and args.init_smartsheet:
        raise RuntimeError("--dry-run cannot be used together with --init-smartsheet")

    rows = extract_book_rows(max_workers=args.max_workers)
    markdown_table = rows_to_markdown(rows)
    file_id, sheet_id, scaffold, sheet_resolution = resolve_target(args)

    summary = {"created": 0, "updated": 0, "deleted": 0, "skipped": 0, "created_record_ids": [], "updated_record_ids": [], "deleted_record_ids": [], "warnings": []}
    mode = "print_only" if args.print_only else "sync"
    if not args.print_only:
        fields = fetch_fields(file_id, sheet_id)
        field_types = validate_fields(fields)
        existing = fetch_existing_records(file_id, sheet_id)
        summary = upsert_rows(file_id, sheet_id, rows, existing, field_types, dry_run=args.dry_run, delete_missing=(args.delete_missing or args.init_smartsheet))
        mode = "dry_run" if args.dry_run else "sync"

    output = {
        "mode": mode,
        "total_books": len(rows),
        "dry_run": args.dry_run,
        "print_only": args.print_only,
        "delete_missing": args.delete_missing,
        "file_id": file_id,
        "sheet_id": sheet_id,
        "markdown_table": markdown_table,
        **summary,
        "rows": rows,
    }
    if scaffold:
        output["copied_smartsheet"] = scaffold
    if sheet_resolution:
        output["sheet_resolution"] = sheet_resolution
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        eprint(f"ERROR: {exc}")
        sys.exit(1)
