import os
import re
import requests
from requests.auth import HTTPBasicAuth
from flask import Flask

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


def jira_search(jql: str):
    url = f"{JIRA_BASE}/rest/api/3/search/jql"
    params = {
        "jql": jql,
        "maxResults": 200,
        "fields": (
            "summary,description,issuetype,parent,attachment,"
            "customfield_10230,customfield_10265,customfield_10298,customfield_10299"
        ),
    }
    r = requests.get(url, params=params, auth=auth)
    r.raise_for_status()
    return r.json()["issues"]


def get_confluence_page():
    url = f"{CONF_BASE}/rest/api/content/{CONFLUENCE_PAGE_ID}"
    params = {"expand": "version"}
    r = requests.get(url, params=params, auth=auth)
    r.raise_for_status()
    return r.json()


def update_confluence_page(title: str, html_value: str, version_number: int):
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
    r.raise_for_status()
    return r.json()


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


def steps_to_html(text):
    text = text or ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cleaned = [line.lstrip("#").strip() for line in lines]

    if not cleaned:
        return "<ol></ol>"

    return "<ol>" + "".join(f"<li>{escape_html(line)}</li>" for line in cleaned) + "</ol>"


def get_select_value(value):
    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip().lower()

    if isinstance(value, dict):
        for key in ("value", "name"):
            v = value.get(key)
            if isinstance(v, str):
                return v.strip().lower()

    if isinstance(value, list):
        for item in value:
            result = get_select_value(item)
            if result:
                return result

    return str(value).strip().lower()


def get_issue_picker_key(value):
    if not value:
        return ""

    # Case 1: ["SCRUM-70"]
    if isinstance(value, list):
        if len(value) > 0:
            if isinstance(value[0], str):
                return value[0]
            if isinstance(value[0], dict) and value[0].get("key"):
                return value[0]["key"]

    # Case 2: "SCRUM-70"
    if isinstance(value, str):
        return value

    # Case 3: {"key": "SCRUM-70"}
    if isinstance(value, dict):
        return value.get("key", "")

    return ""


def clean_req(summary):
    m = re.match(r"\[REQ\]\[([^\]]+)\]-\s*(.*)", summary or "")
    return f"{m.group(1)} - {m.group(2)}" if m else (summary or "")


def escape_html(text: str) -> str:
    text = text or ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def is_main_requirement(value):
    if value is True:
        return True
    if value in (False, None, "", []):
        return False

    if isinstance(value, str):
        return value.strip().lower() in ("yes", "true", "y")

    if isinstance(value, dict):
        for key in ("value", "name"):
            v = value.get(key)
            if isinstance(v, str) and v.strip().lower() in ("yes", "true", "y"):
                return True

    if isinstance(value, list):
        for item in value:
            if is_main_requirement(item):
                return True

    return False


def extract_text_before_images(adf):
    if not adf:
        return ""

    if isinstance(adf, str):
        return adf.strip()

    parts = []

    def walk(node):
        if isinstance(node, dict):
            node_type = node.get("type")

            if node_type in ("media", "mediaSingle", "mediaGroup"):
                return

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


def attachment_images_to_html(attachments):
    if not attachments:
        return ""

    html = []

    for att in attachments:
        mime = (att.get("mimeType") or "").lower()
        filename = att.get("filename", "") or ""
        content_url = att.get("content")
        thumbnail_url = att.get("thumbnail")

        is_image = mime.startswith("image/") or filename.lower().endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp")
        )

        if not is_image:
            continue

        url = content_url or thumbnail_url
        if not url:
            continue

        html.append(
            f'<p><img src="{escape_html(url)}" alt="{escape_html(filename or "diagram")}" '
            f'style="max-width:100%; height:auto;" /></p>'
        )

    return "\n".join(html)


def build_html(sections, epics_by_section, general_reqs_by_section, reqs_by_epic):
    html = []

    for section in sections:
        section_key = section["key"]
        sf = section["fields"]

        section_title = sf.get("summary", "")
        section_description = adf_to_text(sf.get("description"))

        html.append(f"<h1>{escape_html(section_title)}</h1>")

        if section_description.strip():
            html.append(f"<p>{escape_html(section_description).replace(chr(10), '<br/>')}</p>")

        # General Requirements
        html.append("<h2>General Requirements</h2>")
        html.append("<ul>")

        general_reqs = general_reqs_by_section.get(section_key, [])
        general_reqs = sorted(general_reqs, key=lambda x: int(x["key"].split("-")[1]))

        for req in general_reqs:
            rf = req["fields"]
            html.append("<li>")
            html.append(f"<strong>{escape_html(clean_req(rf.get('summary', '')))}</strong><br/>")
            html.append(escape_html(adf_to_text(rf.get("description"))).replace("\n", "<br/>"))
            html.append("</li>")

        html.append("</ul>")

        # UCs under this section
        epics = epics_by_section.get(section_key, [])
        epics = sorted(epics, key=lambda x: int(x["key"].split("-")[1]))

        for epic in epics:
            epic_key = epic["key"]
            ef = epic["fields"]

            requirements = reqs_by_epic.get(epic_key, [])

            main_req = None
            specific_reqs = []

            for r in requirements:
                req_type = get_select_value(r["fields"].get("customfield_10299"))

                if req_type == "main" and main_req is None:
                    main_req = r
                elif req_type == "specific":
                    specific_reqs.append(r)

            html.append(f"<h1>{escape_html(ef.get('summary', ''))}</h1>")

            # Main requirement first
            if main_req:
                mf = main_req["fields"]
                main_summary = clean_req(mf.get("summary", ""))
                main_intro = extract_text_before_images(mf.get("description"))
                main_images_html = attachment_images_to_html(mf.get("attachment", []))

                html.append(f"<p><strong>{escape_html(main_summary)}</strong></p>")

                if main_intro.strip():
                    html.append(f"<p>{escape_html(main_intro).replace(chr(10), '<br/>')}</p>")

                if main_images_html:
                    html.append(main_images_html)

            # Description
            html.append("<h2>Description</h2>")
            html.append(f"<p>{escape_html(adf_to_text(ef.get('description'))).replace(chr(10), '<br/>')}</p>")

            # Steps
            html.append("<h2>Steps</h2>")
            html.append(steps_to_html(adf_to_text(ef.get("customfield_10230"))))

            # Specific requirements only
            html.append("<h2>Requirements</h2>")
            html.append("<ul>")

            specific_reqs = sorted(specific_reqs, key=lambda x: int(x["key"].split("-")[1]))

            for r in specific_reqs:
                rf = r["fields"]
                html.append("<li>")
                html.append(f"<strong>{escape_html(clean_req(rf.get('summary', '')))}</strong><br/>")
                html.append(escape_html(adf_to_text(rf.get("description"))).replace("\n", "<br/>"))
                html.append("</li>")

            html.append("</ul>")
            html.append("<hr/>")

    return "\n".join(html)


def generate_ssd():
    jql = f'project = {PROJECT_KEY} AND issuetype in ("SSD Section", Epic, Requirement)'
    issues = jira_search(jql)

    sections = []
    epics_by_section = {}
    general_reqs_by_section = {}
    reqs_by_epic = {}

    for issue in issues:
        issue_type = issue["fields"]["issuetype"]["name"]
        fields = issue["fields"]

        if issue_type == "SSD Section":
            sections.append(issue)

        elif issue_type == "Epic":
            section_key = get_issue_picker_key(fields.get("customfield_10298"))
            if section_key:
                epics_by_section.setdefault(section_key, []).append(issue)

        elif issue_type == "Requirement":
            req_type = get_select_value(fields.get("customfield_10299"))

            if req_type == "general":
                section_key = get_issue_picker_key(fields.get("customfield_10298"))
                if section_key:
                    general_reqs_by_section.setdefault(section_key, []).append(issue)

            elif req_type in ("main", "specific"):
                parent = fields.get("parent")
                if parent and parent.get("key"):
                    parent_key = parent["key"]
                    reqs_by_epic.setdefault(parent_key, []).append(issue)

    sections = sorted(sections, key=lambda x: int(x["key"].split("-")[1]))

    html = build_html(sections, epics_by_section, general_reqs_by_section, reqs_by_epic)

    page = get_confluence_page()

    updated = update_confluence_page(
        page["title"],
        html,
        page["version"]["number"] + 1,
    )

    return updated


@app.get("/")
def health():
    return {"status": "ok"}


@app.get("/debug-config")
def debug_config():
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
        result = generate_ssd()
        return {"status": "success", "version": result["version"]["number"]}
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500


@app.get("/debug-data")
def debug_data():
    jql = f'project = {PROJECT_KEY} AND issuetype in ("SSD Section", Epic, Requirement)'
    issues = jira_search(jql)

    output = []

    for issue in issues:
        fields = issue["fields"]
        output.append({
            "key": issue["key"],
            "type": fields["issuetype"]["name"],
            "summary": fields.get("summary"),
            "ssd_section_field": fields.get("customfield_10298"),
            "requirement_type": fields.get("customfield_10299"),
            "parent": fields.get("parent"),
        })

    return {"issues": output}, 200

if __name__ == "__main__":
    app.run()
