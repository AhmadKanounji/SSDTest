import os
import re
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

REVISION_START = "<!-- REVISION_HISTORY_START -->"
REVISION_END = "<!-- REVISION_HISTORY_END -->"


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


def adf_to_html(adf):
    if not adf:
        return ""

    if isinstance(adf, str):
        return f"<p>{escape_html(adf)}</p>"

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

        if node_type in ("mediaSingle", "media", "mediaGroup"):
            return ""

        return "".join(render_node(child) for child in content)

    return render_node(adf)


def escape_html(text: str) -> str:
    text = text or ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def clean_req(summary):
    m = re.match(r"\[REQ\]\[([^\]]+)\]\s*-\s*(.*)", summary or "")
    return f"{m.group(1)} - {m.group(2)}" if m else (summary or "")


def strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return html_lib.unescape(text).strip()


def extract_existing_revision_rows(existing_html: str):
    if not existing_html:
        return []

    start_idx = existing_html.find(REVISION_START)
    end_idx = existing_html.find(REVISION_END)

    if start_idx == -1 or end_idx == -1:
        return []

    block = existing_html[start_idx:end_idx]

    rows = []
    tr_matches = re.findall(r"<tr>(.*?)</tr>", block, flags=re.DOTALL | re.IGNORECASE)
    for tr in tr_matches:
        td_matches = re.findall(r"<td>(.*?)</td>", tr, flags=re.DOTALL | re.IGNORECASE)
        if len(td_matches) != 4:
            continue

        version = strip_tags(td_matches[0])
        date = strip_tags(td_matches[1])
        author = strip_tags(td_matches[2])
        modification = strip_tags(td_matches[3])

        rows.append({
            "version": version,
            "date": date,
            "author": author,
            "modification": modification,
        })

    return rows


def get_next_revision_version(existing_rows):
    if not existing_rows:
        return "0.1"

    versions = []
    for row in existing_rows:
        try:
            versions.append(float(row["version"]))
        except Exception:
            pass

    if not versions:
        return "0.1"

    next_version = round(max(versions) + 0.1, 1)
    return f"{next_version:.1f}"


def build_revision_history_html(existing_html: str, author: str):
    existing_rows = extract_existing_revision_rows(existing_html)
    next_version = get_next_revision_version(existing_rows)
    today = datetime.now(ZoneInfo("Asia/Beirut")).strftime("%d/%m/%Y")

    new_row = {
        "version": next_version,
        "date": today,
        "author": author or EMAIL,
        "modification": "SSD regenerated from Jira data",
    }

    all_rows = [new_row] + existing_rows

    rows_html = []
    for row in all_rows:
        rows_html.append(
            "<tr>"
            f"<td>{escape_html(row['version'])}</td>"
            f"<td>{escape_html(row['date'])}</td>"
            f"<td>{escape_html(row['author'])}</td>"
            f"<td>{escape_html(row['modification'])}</td>"
            "</tr>"
        )

    table_html = (
        f"{REVISION_START}"
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
        f"{REVISION_END}"
    )

    return table_html


def has_png_attachment(issue):
    attachments = issue["fields"].get("attachment", []) or []

    for att in attachments:
        filename = (att.get("filename") or "").lower()
        mime = (att.get("mimeType") or "").lower()

        if filename.endswith(".png") or mime == "image/png":
            return True

    return False


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


def upload_attachment_to_confluence(filename, file_bytes, mime_type):
    log(f"upload_attachment_to_confluence started - {filename}")

    existing = get_existing_confluence_attachments().get(filename)

    if existing:
        attachment_id = existing["id"]
        url = f"{CONF_BASE}/rest/api/content/{CONFLUENCE_PAGE_ID}/child/attachment/{attachment_id}/data"
        log(f"upload_attachment_to_confluence updating existing attachment - {filename}, id={attachment_id}")
    else:
        url = f"{CONF_BASE}/rest/api/content/{CONFLUENCE_PAGE_ID}/child/attachment"
        log(f"upload_attachment_to_confluence creating new attachment - {filename}")

    headers = {"X-Atlassian-Token": "no-check"}
    files = {
        "file": (filename, file_bytes, mime_type)
    }

    r = requests.post(url, auth=auth, headers=headers, files=files)
    log(f"upload_attachment_to_confluence response status for {filename}: {r.status_code}")
    r.raise_for_status()

    log(f"upload_attachment_to_confluence finished - {filename}")
    return r.json()


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


def build_requirement_html(req):
    rf = req["fields"]
    req_key = req.get("key", "UNKNOWN")
    req_title = clean_req(rf.get("summary", ""))

    log(f"build_requirement_html - processing requirement {req_key} - {req_title}")

    req_description = adf_to_text(rf.get("description"))
    req_images_html = attachment_images_to_html(rf.get("attachment", []))

    html = [f"<h3>{escape_html(req_title)}</h3>"]

    if req_description.strip():
        html.append(f"<p>{escape_html(req_description).replace(chr(10), '<br/>')}</p>")

    if req_images_html:
        html.append(req_images_html)

    return "\n".join(html)


def build_html(epics, reqs_by_epic):
    html = []

    epics = sorted(epics, key=lambda x: int(x["key"].split("-")[1]))

    for epic in epics:
        epic_key = epic["key"]
        ef = epic["fields"]
        requirements = reqs_by_epic.get(epic_key, [])
        requirements = sorted(requirements, key=lambda x: int(x["key"].split("-")[1]))

        log(f"build_html - processing epic {epic_key} with {len(requirements)} requirements")

        html.append(f"<h1>{escape_html(ef.get('summary', ''))}</h1>")

        png_req = None
        other_reqs = []

        for req in requirements:
            if png_req is None and has_png_attachment(req):
                png_req = req
            else:
                other_reqs.append(req)

        if png_req:
            log(f"build_html - epic {epic_key} has diagram requirement {png_req.get('key', 'UNKNOWN')}")
            html.append(build_requirement_html(png_req))

        epic_description_html = adf_to_html(ef.get("description"))
        if epic_description_html.strip():
            html.append("<h2>Description</h2>")
            html.append(epic_description_html)

        if other_reqs:
            html.append("<h2>Requirements</h2>")
            for req in other_reqs:
                html.append(build_requirement_html(req))

        html.append("<hr/>")

    return "\n".join(html)


def generate_ssd(author: str):
    log("generate_ssd started")

    jql = f'project = {PROJECT_KEY} AND issuetype in (Epic, Requirement)'
    issues = jira_search(jql)

    epics = []
    reqs_by_epic = {}

    for issue in issues:
        issue_type = issue["fields"]["issuetype"]["name"]
        fields = issue["fields"]

        if issue_type == "Epic":
            epics.append(issue)

        elif issue_type == "Requirement":
            parent = fields.get("parent")
            if parent and parent.get("key"):
                parent_key = parent["key"]
                reqs_by_epic.setdefault(parent_key, []).append(issue)

    log(f"generate_ssd - epics count: {len(epics)}")
    log(f"generate_ssd - parent buckets count: {len(reqs_by_epic)}")

    page = get_confluence_page()
    existing_html = page.get("body", {}).get("storage", {}).get("value", "")

    log("generate_ssd - building revision history html")
    revision_html = build_revision_history_html(existing_html, author)

    log("generate_ssd - building content html")
    content_html = build_html(epics, reqs_by_epic)

    full_html = revision_html + content_html
    log(f"generate_ssd - final html length: {len(full_html)}")

    updated = update_confluence_page(
        page["title"],
        full_html,
        page["version"]["number"] + 1,
    )

    log("generate_ssd finished")
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
