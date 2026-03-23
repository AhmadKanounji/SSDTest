import os
import re
import requests
from requests.auth import HTTPBasicAuth
from flask import Flask, jsonify

app = Flask(__name__)

# ✅ ENV VARIABLES (Render will provide these)
ATLASSIAN_DOMAIN = os.environ"testahmadk.atlassian.net"
EMAIL = os.environ"ahmadkanounji1@gmail.com"
API_TOKEN = os.environ"ATATT3xFfGF0Y9oQuGSBbsVlrtyrsbQ-o6v7dr-WTtlcZuj-dy1k1TAJl-nbjaEqZmSAa6T4KgSdxpwphkhy9brX-ie_9rhMntuwLvEmPBnJLf9JUwiE2srdky6scsd1ajBtDzkOlr3o2BKZ0_UkUwnISVb-LtAlKiEK3i657JZWgzAVO0OXyb8=85231503"
PROJECT_KEY = os.environ"SCRUM"
CONFLUENCE_PAGE_ID = os.environ"3309569"

JIRA_BASE = f"https://{ATLASSIAN_DOMAIN}"
CONF_BASE = f"https://{ATLASSIAN_DOMAIN}/wiki"

auth = HTTPBasicAuth(EMAIL, API_TOKEN)


def jira_search(jql: str):
    url = f"{JIRA_BASE}/rest/api/3/search/jql"
    params = {
        "jql": jql,
        "maxResults": 100,
        "fields": "summary,description,issuetype,parent,customfield_10230",
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


def build_html(epics, reqs):
    html = []

    for e in epics:
        key = e["key"]
        f = e["fields"]

        html.append(f"<h1>{f['summary']}</h1>")

        html.append("<h2>Description</h2>")
        html.append(f"<p>{adf_to_text(f.get('description'))}</p>")

        html.append("<h2>Steps</h2>")
        html.append(steps_to_html(adf_to_text(f.get("customfield_10230"))))

        html.append("<h2>Requirements</h2><ul>")

        for r in reqs.get(key, []):
            rf = r["fields"]
            html.append("<li>")
            html.append(f"<strong>{clean_req(rf['summary'])}</strong><br/>")
            html.append(adf_to_text(rf.get("description")).replace("\n", "<br/>"))
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


@app.post("/generate-ssd")
def run():
    try:
        result = generate_ssd()
        return {"status": "success", "version": result["version"]["number"]}
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500


if __name__ == "__main__":
    app.run()
