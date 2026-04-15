import os
import re
import json
import html as html_lib
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from requests.auth import HTTPBasicAuth
from flask import Flask, request

app = Flask(__name__)

# ENV VARIABLES
ATLASSIAN_DOMAIN = os.environ["ATLASSIAN_DOMAIN"].strip()
EMAIL = os.environ["ATLASSIAN_EMAIL"].strip()
API_TOKEN = os.environ["ATLASSIAN_API_TOKEN"].strip()
PROJECT_KEY = os.environ["PROJECT_KEY"].strip()
CONFLUENCE_PAGE_ID = os.environ["CONFLUENCE_PAGE_ID"].strip()

JIRA_BASE = f"https://{ATLASSIAN_DOMAIN}"
CONF_BASE = f"https://{ATLASSIAN_DOMAIN}/wiki"

auth = HTTPBasicAuth(EMAIL, API_TOKEN)

ATTACHMENT_CACHE = None
REVISION_META_FILENAME = "ssd_revision_meta.json"


def get_existing_confluence_attachments_cached():
    global ATTACHMENT_CACHE
    if ATTACHMENT_CACHE is None:
        ATTACHMENT_CACHE = get_existing_confluence_attachments()
    return ATTACHMENT_CACHE


def reset_attachment_cache():
    global ATTACHMENT_CACHE
    ATTACHMENT_CACHE = None


def log(message: str):
    print(f"[SSD] {datetime.now(ZoneInfo('Asia/Beirut')).isoformat()} - {message}", flush=True)


def jira_search(jql: str):
    log(f"jira_search started - JQL: {jql}")

    url = f"{JIRA_BASE}/rest/api/3/search/jql"
    params = {
        "jql": jql,
        "maxResults": 200,
        "fields": "summary,description,issuetype,parent,attachment",
    }

    r = requests.get(url, params=params, auth=auth)
    log(f"jira_search response status: {r.status_code}")
    log(f"jira_search response body: {r.text[:1000]}")
    r.raise_for_status()

    issues = r.json()["issues"]
    log(f"jira_search finished - {len(issues)} issues returned")
    return issues


def get_confluence_page():
    log(f"get_confluence_page started - page id: {CONFLUENCE_PAGE_ID}")

    url = f"{CONF_BASE}/rest/api/content/{CONFLUENCE_PAGE_ID}"
    params = {"expand": "version,body.storage"}

    r = requests.get(url, params=params, auth=auth)
    log(f"get_confluence_page response status: {r.status_code}")
    log(f"get_confluence_page response body: {r.text[:1000]}")
    r.raise_for_status()

    page = r.json()
    log(f"get_confluence_page finished - current version: {page['version']['number']}")
    return page


def update_confluence_page(title: str, html_value: str, version_number: int):
    log(f"update_confluence_page started - target version: {version_number}")

    url = f"{CONF_BASE}/rest/api/content/{CONFLUENCE_PAGE_ID}"
    payload = {
        "id": CONFLUENCE_PAGE_ID,
        "type": "page",
        "title": title,
        "body": {
            "storage": {
                "value": html_value,
                "representation": "storage",
            }
        },
        "version": {"number": version_number},
    }

    r = requests.put(url, json=payload, auth=auth)
    log(f"update_confluence_page response status: {r.status_code}")
    log(f"update_confluence_page response body: {r.text[:1000]}")
    r.raise_for_status()

    updated = r.json()
    log(f"update_confluence_page finished - new version: {updated['version']['number']}")
    return updated


def adf_to_text(adf):
    if not adf:
        return ""
    if isinstance(adf, str):
        return adf

    parts = []

    def walk(node):
        if isinstance(node, dict):
            node_type = node.get("type")

            if node_type == "text":
                parts.append(node.get("text", ""))
            elif node_type == "hardBreak":
                parts.append("\n")

            for child in node.get("content", []):
                walk(child)

            if node_type in ("paragraph", "heading"):
                parts.append("\n")

        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(adf)
    return "".join(parts).strip()


def adf_to_html(adf, attachments=None):
    if not adf:
        return ""

    if isinstance(adf, str):
        return f"<p>{escape_html(adf)}</p>"

    attachments = attachments or []

    image_attachments = [
        att for att in attachments
        if (att.get("mimeType") or "").lower().startswith("image/")
        or (att.get("filename") or "").lower().endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp")
        )
    ]

    image_index = {"value": 0}

    def consume_next_image():
        idx = image_index["value"]
        if idx >= len(image_attachments):
            return ""
        image_index["value"] += 1
        return render_confluence_image_from_attachment(image_attachments[idx])

    def render_node(node):
        if not isinstance(node, dict):
            return ""

        node_type = node.get("type")
        content = node.get("content", [])

        if node_type == "doc":
            return "".join(render_node(child) for child in content)

        if node_type == "paragraph":
            inner = "".join(render_node(child) for child in content).strip()
            return f"<p>{inner}</p>" if inner else ""

        if node_type == "text":
            text = escape_html(node.get("text", ""))
            marks = node.get("marks", [])

            for mark in marks:
                mark_type = mark.get("type")
                if mark_type == "strong":
                    text = f"<strong>{text}</strong>"
                elif mark_type == "em":
                    text = f"<em>{text}</em>"
                elif mark_type == "underline":
                    text = f"<u>{text}</u>"
                elif mark_type == "strike":
                    text = f"<s>{text}</s>"
                elif mark_type == "code":
                    text = f"<code>{text}</code>"
                elif mark_type == "link":
                    href = mark.get("attrs", {}).get("href", "")
                    if href:
                        text = f'<a href="{escape_html(href)}">{text}</a>'

            return text

        if node_type == "hardBreak":
            return "<br/>"

        if node_type == "heading":
            level = node.get("attrs", {}).get("level", 1)
            level = min(max(level, 1), 6)
            inner = "".join(render_node(child) for child in content)
            return f"<h{level}>{inner}</h{level}>"

        if node_type == "orderedList":
            inner = "".join(render_node(child) for child in content)
            return f"<ol>{inner}</ol>"

        if node_type == "bulletList":
            inner = "".join(render_node(child) for child in content)
            return f"<ul>{inner}</ul>"

        if node_type == "listItem":
            inner = "".join(render_node(child) for child in content)
            return f"<li>{inner}</li>"

        if node_type == "blockquote":
            inner = "".join(render_node(child) for child in content)
            return f"<blockquote>{inner}</blockquote>"

        if node_type == "rule":
            return "<hr/>"

        if node_type == "codeBlock":
            inner = "".join(render_node(child) for child in content)
            return f"<pre><code>{inner}</code></pre>"

        # Insert images where Jira placed them
        if node_type == "mediaSingle":
            return consume_next_image()

        if node_type == "mediaGroup":
            return "".join(consume_next_image() for _ in content if isinstance(_, dict))

        if node_type == "media":
            return consume_next_image()

        return "".join(render_node(child) for child in content)

    return render_node(adf)


def escape_html(text: str) -> str:
    text = text or ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def clean_req(summary: str) -> str:
    summary = (summary or "").strip()
    cleaned = re.sub(r"^(?:\[[^\]]+\])+\s*-\s*", "", summary).strip()
    return cleaned or summary


def strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return html_lib.unescape(text).strip()


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def summarize_issue(snapshot_item):
    key = snapshot_item.get("key", "")
    summary = snapshot_item.get("summary", "")
    return f"{key} - {summary}" if key else summary


def build_jira_snapshot(issues):
    snapshot = {}

    for issue in issues:
        key = issue["key"]
        fields = issue["fields"]
        issue_type = fields["issuetype"]["name"]

        snapshot[key] = {
            "key": key,
            "type": issue_type,
            "summary": fields.get("summary", ""),
            "description": adf_to_text(fields.get("description")),
            "parent_key": (fields.get("parent") or {}).get("key"),
            "attachments": sorted(
                (att.get("filename") or "").strip().lower()
                for att in (fields.get("attachment") or [])
            ),
        }

    return snapshot


def parse_version_string(version_str: str) -> float:
    try:
        return round(float(version_str), 1)
    except Exception:
        return 0.0


def format_version(version_num: float) -> str:
    return f"{version_num:.1f}"


def extract_existing_revision_rows(existing_html: str):
    if not existing_html:
        return []

    match = re.search(
        r"<h1>\s*Revision History\s*</h1>\s*(<table\b.*?</table>)",
        existing_html,
        flags=re.DOTALL | re.IGNORECASE
    )

    if not match:
        return []

    table_html = match.group(1)

    rows = []
    tr_matches = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, flags=re.DOTALL | re.IGNORECASE)

    for tr in tr_matches:
        td_matches = re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.DOTALL | re.IGNORECASE)
        if len(td_matches) != 4:
            continue

        version = strip_tags(td_matches[0])
        date = strip_tags(td_matches[1])
        author = strip_tags(td_matches[2])

        modification = re.sub(r"<br\s*/?>", "\n", td_matches[3], flags=re.IGNORECASE)
        modification = strip_tags(modification)

        rows.append({
            "version": version,
            "date": date,
            "author": author,
            "modification": modification,
        })

    return rows


def build_revision_history_html(existing_rows, author: str, new_version: str, change_lines=None):
    today = datetime.now(ZoneInfo("Asia/Beirut")).strftime("%d/%m/%Y")

    cleaned_rows = []
    seen_zero_one = False

    for row in existing_rows:
        version = row.get("version", "")
        modification = row.get("modification", "")

        if version == "0.1":
            if seen_zero_one:
                continue
            seen_zero_one = True
            cleaned_rows.append(row)
            continue

        if modification == "Initial generation of the SSD":
            continue

        cleaned_rows.append(row)

    if not change_lines:
        modification_text = "Initial generation of the SSD"
    else:
        modification_text = "\n".join(change_lines)

    new_row = {
        "version": new_version,
        "date": today,
        "author": author or EMAIL,
        "modification": modification_text,
    }

    all_rows = [new_row] + cleaned_rows

    rows_html = []
    for row in all_rows:
        safe_modification = escape_html(row["modification"]).replace("\n", "<br/>")
        rows_html.append(
            "<tr>"
            f"<td>{escape_html(row['version'])}</td>"
            f"<td>{escape_html(row['date'])}</td>"
            f"<td>{escape_html(row['author'])}</td>"
            f"<td>{safe_modification}</td>"
            "</tr>"
        )

    return (
        "<h1>Revision History</h1>"
        '<table border="1" style="border-collapse:collapse; width:100%;">'
        "<thead>"
        "<tr>"
        "<th>Version</th>"
        "<th>Date</th>"
        "<th>Author</th>"
        "<th>Modification</th>"
        "</tr>"
        "</thead>"
        "<tbody>"
        + "".join(rows_html) +
        "</tbody>"
        "</table>"
    )


def get_existing_confluence_attachments():
    log("get_existing_confluence_attachments started")

    attachments = {}
    start = 0
    limit = 100

    while True:
        url = f"{CONF_BASE}/rest/api/content/{CONFLUENCE_PAGE_ID}/child/attachment"
        params = {"start": start, "limit": limit}

        r = requests.get(url, params=params, auth=auth)
        log(f"get_existing_confluence_attachments page start={start}, status={r.status_code}")
        log(f"get_existing_confluence_attachments body: {r.text[:1000]}")
        r.raise_for_status()

        data = r.json()

        for result in data.get("results", []):
            title = result.get("title")
            if title:
                attachments[title] = result

        size = len(data.get("results", []))
        if size < limit:
            break

        start += limit

    log(f"get_existing_confluence_attachments finished - total={len(attachments)}")
    return attachments


def download_confluence_attachment_by_filename(filename: str):
    attachments = get_existing_confluence_attachments_cached()
    existing = attachments.get(filename)

    if not existing:
        log(f"download_confluence_attachment_by_filename - attachment not found: {filename}")
        return None

    download_link = existing.get("_links", {}).get("download")
    if not download_link:
        log(f"download_confluence_attachment_by_filename - no download link for: {filename}")
        return None

    if download_link.startswith("/"):
        url = f"{CONF_BASE}{download_link}"
    else:
        url = download_link

    log(f"download_confluence_attachment_by_filename started - {filename}")
    log(f"Resolved attachment download URL: {url}")

    r = requests.get(url, auth=auth)
    log(f"download_confluence_attachment_by_filename response status for {filename}: {r.status_code}")
    log(f"download_confluence_attachment_by_filename response body for {filename}: {r.text[:1000]}")
    r.raise_for_status()

    return r.content


def load_existing_meta_from_attachment():
    filename = REVISION_META_FILENAME

    log("=== LOADING SNAPSHOT FROM ATTACHMENT ===")

    attachments = get_existing_confluence_attachments_cached()
    log(f"Available attachments: {list(attachments.keys())}")

    existing = attachments.get(filename)

    if not existing:
        log("❌ Snapshot attachment NOT FOUND")
        return None

    log(f"✅ Snapshot attachment FOUND: {filename}")

    download_link = existing.get("_links", {}).get("download")
    log(f"Download link: {download_link}")

    if not download_link:
        log("❌ Snapshot attachment has no download link")
        return None

    if download_link.startswith("/"):
        url = f"{CONF_BASE}{download_link}"
    else:
        url = download_link

    r = requests.get(url, auth=auth)
    log(f"Download status: {r.status_code}")
    log(f"Download body preview: {r.text[:1000] if hasattr(r, 'text') else 'binary'}")
    r.raise_for_status()

    try:
        data = json.loads(r.content.decode("utf-8"))
        log("✅ Snapshot loaded successfully")
        return data
    except Exception as e:
        log(f"❌ Failed to parse snapshot: {repr(e)}")
        return None


def upload_attachment_to_confluence(filename, file_bytes, mime_type):
    log(f"upload_attachment_to_confluence started - {filename}")

    url = f"{CONF_BASE}/rest/api/content/{CONFLUENCE_PAGE_ID}/child/attachment"
    headers = {"X-Atlassian-Token": "nocheck"}

    files = {
        "file": (filename, file_bytes, mime_type)
    }
    data = {
        "minorEdit": "true",
        "comment": "Updated by SSD automation"
    }

    r = requests.put(url, auth=auth, headers=headers, files=files, data=data)

    log(f"upload_attachment response: {r.status_code}")
    log(f"upload_attachment body: {r.text[:1000]}")
    r.raise_for_status()

    reset_attachment_cache()

    log("upload_attachment_to_confluence finished")
    return r.json()


def save_meta_to_attachment(revision_version: str, snapshot: dict):
    payload = {
        "revision_version": revision_version,
        "snapshot": snapshot,
    }

    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")

    upload_attachment_to_confluence(
        filename=REVISION_META_FILENAME,
        file_bytes=data,
        mime_type="application/json",
    )

    log(f"save_meta_to_attachment finished - {REVISION_META_FILENAME}")


def download_jira_attachment(attachment):
    content_url = attachment.get("content")
    filename = attachment.get("filename") or "attachment.bin"
    mime_type = attachment.get("mimeType") or "application/octet-stream"

    if not content_url:
        log(f"download_jira_attachment skipped - no content URL for {filename}")
        return None

    log(f"download_jira_attachment started - {filename}")

    r = requests.get(content_url, auth=auth)
    log(f"download_jira_attachment response status for {filename}: {r.status_code}")
    r.raise_for_status()

    log(f"download_jira_attachment finished - {filename}, size={len(r.content)} bytes")

    return {
        "filename": filename,
        "mime_type": mime_type,
        "content": r.content,
    }


def ensure_attachment_on_confluence(attachment):
    filename = attachment.get("filename") or "unknown"
    log(f"ensure_attachment_on_confluence started - {filename}")

    downloaded = download_jira_attachment(attachment)
    if not downloaded:
        log(f"ensure_attachment_on_confluence skipped - could not download {filename}")
        return None

    upload_attachment_to_confluence(
        filename=downloaded["filename"],
        file_bytes=downloaded["content"],
        mime_type=downloaded["mime_type"],
    )

    log(f"ensure_attachment_on_confluence finished - {downloaded['filename']}")
    return downloaded["filename"]

def render_confluence_image_from_attachment(att):
    if not att:
        return ""

    mime = (att.get("mimeType") or "").lower()
    filename = att.get("filename") or ""

    is_image = mime.startswith("image/") or filename.lower().endswith(
        (".png", ".jpg", ".jpeg", ".gif", ".webp")
    )

    if not is_image:
        return ""

    confluence_filename = ensure_attachment_on_confluence(att)
    if not confluence_filename:
        return ""

    return (
        f'<p><ac:image ac:width="900">'
        f'<ri:attachment ri:filename="{escape_html(confluence_filename)}" />'
        f'</ac:image></p>'
    )

def attachment_images_to_html(attachments):
    if not attachments:
        return ""

    html_parts = []

    for att in attachments:
        mime = (att.get("mimeType") or "").lower()
        filename = att.get("filename") or ""

        is_image = mime.startswith("image/") or filename.lower().endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp")
        )

        if not is_image:
            log(f"attachment_images_to_html skipped non-image attachment - {filename}")
            continue

        log(f"attachment_images_to_html processing image attachment - {filename}")

        confluence_filename = ensure_attachment_on_confluence(att)
        if not confluence_filename:
            log(f"attachment_images_to_html skipped - failed to ensure attachment on Confluence for {filename}")
            continue

        html_parts.append(
            f'<p><ac:image ac:width="900"><ri:attachment ri:filename="{escape_html(confluence_filename)}" /></ac:image></p>'
        )

    return "\n".join(html_parts)


def has_png_attachment(issue):
    attachments = issue["fields"].get("attachment", []) or []

    for att in attachments:
        filename = (att.get("filename") or "").lower()
        mime = (att.get("mimeType") or "").lower()

        if filename.endswith(".png") or mime == "image/png":
            return True

    return False


def build_requirement_html(req):
    rf = req["fields"]
    req_key = req.get("key", "UNKNOWN")
    req_title = clean_req(rf.get("summary", ""))
    full_title = f"{req_key} - {req_title}"

    log(f"build_requirement_html - processing requirement {req_key} - {req_title}")

    html_parts = [f"<h3>{escape_html(full_title)}</h3>"]

    req_description_html = adf_to_html(
        rf.get("description"),
        attachments=rf.get("attachment", [])
    )

    if req_description_html.strip():
        html_parts.append(req_description_html)

    return "\n".join(html_parts)


def build_document_header_html():
    return """
    <div style="text-align:center; margin-bottom:30px;">
        <h1 style="color:#163A70; margin-bottom:12px;">Industrial Property Rights</h1>

        <p style="font-size:12px; max-width:900px; margin:0 auto 24px auto; line-height:1.5;">
            The information in this document is iDAKTO France Proprietary Information and is Confidential.
            It is the property of iDAKTO Identity &amp; Security France and shall not be used, disclosed to others,
            or reproduced without the express written consent of iDAKTO France. The information contained in this
            document may also be controlled by export control laws and regulations.
            This constraint applies to all pages of this document.
        </p>

        <h1 style="color:#163A70; margin-bottom:16px;">Distribution List</h1>

        <table style="border-collapse:collapse; margin:0 auto; width:420px; font-size:12px;">
            <thead>
                <tr>
                    <th style="border:1px solid #000; background-color:#163A70; color:#fff; padding:6px;">Name</th>
                    <th style="border:1px solid #000; background-color:#163A70; color:#fff; padding:6px;">Company</th>
                </tr>
            </thead>
            <tbody>
                <tr><td style="border:1px solid #000; padding:6px;">&nbsp;</td><td style="border:1px solid #000; padding:6px;">&nbsp;</td></tr>
                <tr><td style="border:1px solid #000; padding:6px;">&nbsp;</td><td style="border:1px solid #000; padding:6px;">&nbsp;</td></tr>
                <tr><td style="border:1px solid #000; padding:6px;">&nbsp;</td><td style="border:1px solid #000; padding:6px;">&nbsp;</td></tr>
                <tr><td style="border:1px solid #000; padding:6px;">&nbsp;</td><td style="border:1px solid #000; padding:6px;">&nbsp;</td></tr>
                <tr><td style="border:1px solid #000; padding:6px;">&nbsp;</td><td style="border:1px solid #000; padding:6px;">&nbsp;</td></tr>
                <tr><td style="border:1px solid #000; padding:6px;">&nbsp;</td><td style="border:1px solid #000; padding:6px;">&nbsp;</td></tr>
                <tr><td style="border:1px solid #000; padding:6px;">&nbsp;</td><td style="border:1px solid #000; padding:6px;">&nbsp;</td></tr>
                <tr><td style="border:1px solid #000; padding:6px;">&nbsp;</td><td style="border:1px solid #000; padding:6px;">&nbsp;</td></tr>
            </tbody>
        </table>
    </div>
    <hr/>
    """
def build_introduction_html():
    return """
    <h1>1. Introduction</h1>

    <h2>1.1 Introduction</h2>

    <p>
        This document outlines the database model for a Digital ID solution designed to securely
        store and manage user identity information. The primary purpose of this database is to
        facilitate the verification and authentication of individual identities while ensuring
        compliance with relevant regulations.
    </p>

    <p>
        Through a structured approach, this database model will define the necessary entities,
        relationships, and data constraints needed to create a robust and efficient system.
        By leveraging this model, the Digital ID solution aims to provide a seamless user
        experience, enhance security, and support scalability as user demands grow.
    </p>

    <h2>1.2 Purpose of the document</h2>

    <p>
        The purpose of this document is to outline the database model solution for Digital ID.
        This document serves as a comprehensive guide that details the design, structure,
        and implementation of the database system to support the functionalities required by
        Digital ID.
    </p>

    <p>
        The scope of this database model encompasses the identification of entities and their
        relationships, normalization of data to reduce redundancy, and the establishment of a
        robust schema that ensures data integrity and optimal performance.
    </p>

    <p>
        Throughout this document, we will provide insights into the requirements that guided the
        design process, along with detailed tables that reflect the database structure. By adhering
        to best practices in database design, this document aims to lay the groundwork for a
        reliable, scalable, and maintainable database system that meets the current and future
        needs of the Digital ID project.
    </p>

    <h2>1.3 Reference Document</h2>

    <p>
        This section presents all reference documents used to build this document.
    </p>

    <table style="border-collapse:collapse; width:100%; font-size:12px;">
        <thead>
            <tr>
                <th style="border:1px solid #000; padding:6px;">Id</th>
                <th style="border:1px solid #000; padding:6px;">Document Name</th>
                <th style="border:1px solid #000; padding:6px;">Release</th>
                <th style="border:1px solid #000; padding:6px;">Date</th>
                <th style="border:1px solid #000; padding:6px;">Reference</th>
            </tr>
        </thead>
        <tbody>
            <tr><td style="border:1px solid #000; padding:6px;">&nbsp;</td><td style="border:1px solid #000; padding:6px;"></td><td style="border:1px solid #000; padding:6px;"></td><td style="border:1px solid #000; padding:6px;"></td><td style="border:1px solid #000; padding:6px;"></td></tr>
            <tr><td style="border:1px solid #000; padding:6px;">&nbsp;</td><td style="border:1px solid #000; padding:6px;"></td><td style="border:1px solid #000; padding:6px;"></td><td style="border:1px solid #000; padding:6px;"></td><td style="border:1px solid #000; padding:6px;"></td></tr>
            <tr><td style="border:1px solid #000; padding:6px;">&nbsp;</td><td style="border:1px solid #000; padding:6px;"></td><td style="border:1px solid #000; padding:6px;"></td><td style="border:1px solid #000; padding:6px;"></td><td style="border:1px solid #000; padding:6px;"></td></tr>
        </tbody>
    </table>

    <hr/>
    """


def extract_use_case_sort_key(summary: str, fallback_key: str = ""):
    text = (summary or "").strip()
    normalized = text.lower()

    if normalized == "exigences générales" or normalized == "exigences generales":
        return (0, [], normalized, fallback_key)

    patterns = [
        r"\bUC\s*([0-9]+(?:\.[0-9]+)*)\b",
        r"\bUse\s*Case\s*([0-9]+(?:\.[0-9]+)*)\b",
        r"^\s*([0-9]+(?:\.[0-9]+)*)\b",
    ]

    value = None
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = match.group(1)
            break

    if value is None:
        return (2, [999999], normalized, fallback_key)

    try:
        numeric_parts = [int(part) for part in value.split(".")]
        return (1, numeric_parts, normalized, fallback_key)
    except Exception:
        return (2, [999999], normalized, fallback_key)


def extract_requirement_sort_key(req):
    summary = req["fields"].get("summary", "") or ""
    key = req.get("key", "")
    return (summary.lower(), key)


def build_html(use_cases, reqs_by_uc):
    html_parts = []

    use_cases = sorted(
        use_cases,
        key=lambda x: extract_use_case_sort_key(
            x["fields"].get("summary", ""),
            x.get("key", "")
        )
    )

    general_use_case = None
    regular_use_cases = []

    for use_case in use_cases:
        title = (use_case["fields"].get("summary", "") or "").strip().lower()
        if title in ["exigences générales", "exigences generales"]:
            general_use_case = use_case
        else:
            regular_use_cases.append(use_case)

    # Section 2 - Exigences Générales
    if general_use_case:
        html_parts.append('<p style="page-break-before: always; margin:0;"></p>')

        uc_key = general_use_case["key"]
        uf = general_use_case["fields"]
        requirements = reqs_by_uc.get(uc_key, [])
        requirements = sorted(requirements, key=extract_requirement_sort_key)

        log(f"build_html - processing general section {uc_key} with {len(requirements)} requirements")

        html_parts.append(f"<h1>2. {escape_html(uf.get('summary', ''))}</h1>")

        use_case_description_html = adf_to_html(uf.get("description"))
        if use_case_description_html.strip():
            html_parts.append("<h2>2.1 Description</h2>")
            html_parts.append(use_case_description_html)

        if requirements:
            html_parts.append("<h2>2.2 Requirements</h2>")
            for req in requirements:
                html_parts.append(build_requirement_html(req))

        html_parts.append("<hr/>")

    # Section 3 - Use Cases
    if regular_use_cases:
        html_parts.append('<p style="page-break-before: always; margin:0;"></p>')
        html_parts.append("<h1>3. Use Cases</h1>")

        for index, use_case in enumerate(regular_use_cases, start=1):
            html_parts.append('<p style="page-break-before: always; margin:0;"></p>')

            uc_key = use_case["key"]
            uf = use_case["fields"]
            requirements = reqs_by_uc.get(uc_key, [])
            requirements = sorted(requirements, key=extract_requirement_sort_key)

            section_number = f"3.{index}"

            log(f"build_html - processing use case {uc_key} as section {section_number} with {len(requirements)} requirements")

            html_parts.append(f"<h2>{section_number} {escape_html(uf.get('summary', ''))}</h2>")

            for req in requirements:
                html_parts.append(build_requirement_html(req))

            html_parts.append("<hr/>")

    return "\n".join(html_parts)


def detect_changes(old_snapshot, new_snapshot):
    changes = []

    old_keys = set(old_snapshot.keys())
    new_keys = set(new_snapshot.keys())

    for key in sorted(new_keys - old_keys):
        item = new_snapshot[key]
        changes.append(f"Creation of {item['type']} {summarize_issue(item)}")

    for key in sorted(old_keys - new_keys):
        item = old_snapshot[key]
        changes.append(f"Removal of {item['type']} {summarize_issue(item)}")

    for key in sorted(old_keys & new_keys):
        old = old_snapshot[key]
        new = new_snapshot[key]

        diffs = []

        if normalize_text(old.get("summary")) != normalize_text(new.get("summary")):
            diffs.append("summary updated")

        if normalize_text(old.get("description")) != normalize_text(new.get("description")):
            diffs.append("description updated")

        if old.get("parent_key") != new.get("parent_key"):
            diffs.append("parent updated")

        if set(old.get("attachments", [])) != set(new.get("attachments", [])):
            diffs.append("attachments updated")

        if diffs:
            formatted = ", ".join(diffs)
            changes.append(f"Update of {new['type']} {summarize_issue(new)}: {formatted}")

    return changes


def generate_ssd(author: str):
    reset_attachment_cache()

    log("generate_ssd started")

    jql = f'project = {PROJECT_KEY} AND issuetype in ("Use Case", Requirement)'
    issues = jira_search(jql)

    use_cases = []
    reqs_by_uc = {}

    for issue in issues:
        issue_type = issue["fields"]["issuetype"]["name"]
        fields = issue["fields"]

        if issue_type == "Use Case":
            use_cases.append(issue)

        elif issue_type == "Requirement":
            parent = fields.get("parent")
            if parent and parent.get("key"):
                parent_key = parent["key"]
                reqs_by_uc.setdefault(parent_key, []).append(issue)

    page = get_confluence_page()
    existing_html = page.get("body", {}).get("storage", {}).get("value", "")

    existing_rows = extract_existing_revision_rows(existing_html)
    existing_meta = load_existing_meta_from_attachment()

    if existing_rows:
        current_version_str = existing_rows[0].get("version", "0.0")
    else:
        current_version_str = "0.0"

    if existing_meta and isinstance(existing_meta, dict):
        old_snapshot = existing_meta.get("snapshot", {}) or {}
    else:
        old_snapshot = {}

    new_snapshot = build_jira_snapshot(issues)

    if not old_snapshot:
        change_lines = None
        log("No previous snapshot found -> modification text will be 'Initial generation of the SSD'")
    else:
        change_lines = detect_changes(old_snapshot, new_snapshot)
        log(f"Detected {len(change_lines)} changes")
        for line in change_lines:
            log(f"CHANGE: {line}")

    next_version_num = round(parse_version_string(current_version_str) + 0.1, 1)
    if next_version_num <= 0:
        next_version_num = 0.1

    new_revision_version = format_version(next_version_num)

    document_header_html = build_document_header_html()
    revision_html = build_revision_history_html(
        existing_rows=existing_rows,
        author=author,
        new_version=new_revision_version,
        change_lines=change_lines,
    )
    content_html = build_html(use_cases, reqs_by_uc)
    introduction_html = build_introduction_html()

    page_break = '<p style="page-break-before: always; margin:0;"></p>'

    full_html = (
        document_header_html +
        page_break +
        revision_html +
        page_break +
        introduction_html +
        page_break +
        content_html
    )

    updated = update_confluence_page(
        page["title"],
        full_html,
        page["version"]["number"] + 1,
    )

    save_meta_to_attachment(new_revision_version, new_snapshot)

    log(f"generate_ssd finished - revision version {new_revision_version}")
    return updated


@app.get("/")
def health():
    log("GET / health check")
    return {"status": "ok"}


@app.get("/debug-config")
def debug_config():
    log("GET /debug-config")
    return {
        "domain": ATLASSIAN_DOMAIN,
        "email": EMAIL,
        "project_key": PROJECT_KEY,
        "page_id": CONFLUENCE_PAGE_ID,
        "token_length": len(API_TOKEN),
        "revision_meta_filename": REVISION_META_FILENAME,
    }, 200


@app.post("/generate-ssd")
def run():
    try:
        data = request.get_json(silent=True) or {}
        author = data.get("author", EMAIL)

        log("POST /generate-ssd received")
        log(f"Author = {author}")

        result = generate_ssd(author)

        log(f"POST /generate-ssd completed successfully - new page version: {result['version']['number']}")

        return {
            "status": "success",
            "version": result["version"]["number"],
        }
    except Exception as e:
        log(f"POST /generate-ssd failed: {repr(e)}")
        return {"status": "error", "message": str(e)}, 500


if __name__ == "__main__":
    log("Starting Flask app")
    app.run()
