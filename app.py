import os
import re
import requests
from requests.auth import HTTPBasicAuth
from flask import Flask, jsonify

app = Flask(__name__)

# ✅ ENV VARIABLES (Render will provide these)
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
        "maxResults": 100,
        "fields": "summary,description,issuetype,parent,attachment,customfield_10230,customfield_10264",
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
            if node.get("type") == "text":
                parts.append(node.get("text", ""))
            elif node.get("type") == "hardBreak":
                parts.append("\n")

            for c in node.get("content", []):
                walk(c)

            if node.get("type") in ("paragraph", "heading"):
                parts.append("\n")

        elif isinstance(node, list):
            for i in node:
                walk(i)

    walk(adf)
    return "".join(parts).strip()


def steps_to_html(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    cleaned = [l.lstrip("#").strip() for l in lines]
    return "<ol>" + "".join(f"<li>{l}</li>" for l in cleaned) + "</ol>"


def clean_req(summary):
    m = re.match(r"\[REQ\]\[([^\]]+)\]-\s*(.*)", summary or "")
    return f"{m.group(1)} - {m.group(2)}" if m else summary

def is_main_requirement(value):
    if value is True:
        return True
    if value is False or value is None:
        return False

    if isinstance(value, str):
        return value.strip().lower() in ("yes", "true", "y")

    if isinstance(value, dict):
        for k in ("value", "name"):
            v = value.get(k)
            if isinstance(v, str) and v.strip().lower() in ("yes", "true", "y"):
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
        filename = (att.get("filename") or "").lower()

        is_image = mime.startswith("image/") or filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
        if not is_image:
            continue

        content_url = att.get("content")
        if not content_url:
            continue

        html.append(
            f'<p><img src="{content_url}" alt="{escape_html(att.get("filename", "diagram"))}" '
            f'style="max-width:100%; height:auto;" /></p>'
        )

    return "\n".join(html)
def build_html(epics, reqs):
    html = []

    for e in epics:
        key = e["key"]
        f = e["fields"]

        html.append(f"<h1>{escape_html(f['summary'])}</h1>")

        requirements = reqs.get(key, [])

        main_req = None
        normal_reqs = []

        for r in requirements:
            if is_main_requirement(r["fields"].get("customfield_10264")) and main_req is None:
                main_req = r
            else:
                normal_reqs.append(r)

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

        html.append("<h2>Description</h2>")
        html.append(f"<p>{adf_to_text(f.get('description')).replace(chr(10), '<br/>')}</p>")

        html.append("<h2>Steps</h2>")
        html.append(steps_to_html(adf_to_text(f.get("customfield_10230"))))

        html.append("<h2>Requirements</h2><ul>")

        normal_reqs = sorted(normal_reqs, key=lambda x: int(x["key"].split("-")[1]))

        for r in normal_reqs:
            rf = r["fields"]
            html.append("<li>")
            html.append(f"<strong>{escape_html(clean_req(rf['summary']))}</strong><br/>")
            html.append(escape_html(adf_to_text(rf.get("description"))).replace("\n", "<br/>"))
            html.append("</li>")

        html.append("</ul><hr/>")

    return "\n".join(html)


def generate_ssd():
    jql = f'project = {PROJECT_KEY} AND issuetype in (Epic, Requirement)'
    issues = jira_search(jql)

    epics = []
    reqs = {}

    for i in issues:
        t = i["fields"]["issuetype"]["name"]

        if t == "Epic":
            epics.append(i)

        elif t == "Requirement":
            parent = i["fields"].get("parent")
            if parent:
                reqs.setdefault(parent["key"], []).append(i)
                
    epics.sort(key=lambda x: int(x["key"].split("-")[1]))
    html = build_html(epics, reqs)

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


if __name__ == "__main__":
    app.run()
