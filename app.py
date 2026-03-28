import streamlit as st
import feedparser
import google.generativeai as genai
import json
import os
import re
import requests
from datetime import datetime

# ── Constants ──────────────────────────────────────────────────────────────────
SHEET_ID    = "1Nc37l2Zz4J5vW4OO0koOENMibQuUb9ST9XOttRQtkhs"
N8N_WEBHOOK = "https://aswathd.app.n8n.cloud/webhook/vc-agent"

RSS_SOURCES = {
    "TechCrunch Startups": "https://techcrunch.com/tag/startups/feed/",
    "Product Hunt":        "https://www.producthunt.com/feed",
    "TechCrunch AI":       "https://techcrunch.com/tag/artificial-intelligence/feed/",
    "VentureBeat":         "https://venturebeat.com/feed/",
}

DEFAULT_THESIS = (
    "Early-stage B2B SaaS companies with strong technical founders, "
    "clear market need, and scalable business models."
)

# ══════════════════════════════════════════════════════════════════════════════
# CLOUD MEMORY / DATABASE (JSONBin)
# ══════════════════════════════════════════════════════════════════════════════
# Using os.environ.get allows you to hide these in Render's environment variables later, 
# but defaults to your live keys so it works immediately.
JSONBIN_ID = os.environ.get("JSONBIN_ID", "69c77436c3097a1dd56b831b")
JSONBIN_KEY = os.environ.get("JSONBIN_KEY", "$2a$10$8folQ4/lgdcUEw.QAwgQXOiRriBnH/otSWRqc4O/SFUPvc3X4MFmW")
JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}"

def load_db() -> dict:
    headers = {"X-Access-Key": JSONBIN_KEY}
    try:
        response = requests.get(JSONBIN_URL, headers=headers)
        if response.status_code == 200:
            data = response.json().get("record", {})
            if "startups" not in data:
                data["startups"] = []
            return data
        return {"startups": []}
    except Exception as e:
        st.error(f"Database Load Error: {e}")
        return {"startups": []}

def save_db(db: dict) -> None:
    headers = {
        "Content-Type": "application/json",
        "X-Access-Key": JSONBIN_KEY
    }
    try:
        requests.put(JSONBIN_URL, json=db, headers=headers)
    except Exception as e:
        st.error(f"Database Save Error: {e}")

def get_seen_titles(db: dict) -> set:
    return {s["startup_title"].lower().strip() for s in db["startups"]}

def upsert_startup(db: dict, record: dict) -> None:
    db["startups"].append(record)

def update_feedback(db: dict, title: str, feedback: str) -> None:
    for s in db["startups"]:
        if s["startup_title"].lower().strip() == title.lower().strip():
            s["vc_feedback"] = feedback
            s["feedback_at"] = datetime.utcnow().isoformat()
            break
    save_db(db)

# ══════════════════════════════════════════════════════════════════════════════
# RSS INGESTION
# ══════════════════════════════════════════════════════════════════════════════
def fetch_feed(url: str) -> list[dict]:
    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            raise ValueError(f"Feed parse error: {feed.bozo_exception}")
        results = []
        for entry in feed.entries:
            title      = entry.get("title", "").strip()
            raw_desc   = entry.get("summary", entry.get("description", ""))
            clean_desc = re.sub(r"<[^>]+>", " ", raw_desc).strip()
            clean_desc = re.sub(r"\s+", " ", clean_desc)[:1000]
            link       = entry.get("link", "")
            if title:
                results.append({"title": title, "description": clean_desc, "link": link})
        return results
    except Exception as e:
        st.error(f"❌ RSS fetch failed: {e}")
        return []

# ══════════════════════════════════════════════════════════════════════════════
# GEMINI EVALUATION
# ══════════════════════════════════════════════════════════════════════════════
def evaluate_startup(api_key: str, thesis: str, title: str, description: str) -> dict | None:
    try:
        genai.configure(api_key=api_key)
        model  = genai.GenerativeModel("gemini-2.5-flash")
        prompt = f"""You are a senior venture capital analyst. Evaluate the startup below against the investment thesis and respond ONLY with a valid JSON object — no markdown, no explanation outside the JSON.

INVESTMENT THESIS:
{thesis}

STARTUP:
Title: {title}
Description: {description}

Return exactly this JSON structure:
{{
  "confidence_score": <integer 0-100>,
  "rationale": "<one concise paragraph explaining your score>",
  "decision": "<Pass or Investigate>"
}}

Rules:
- confidence_score 0-100 (higher = stronger fit)
- decision must be exactly "Pass" or "Investigate"
- rationale should reference specific thesis criteria
"""
        response = model.generate_content(prompt)
        raw      = response.text.strip()
        raw      = re.sub(r"^
http://googleusercontent.com/immersive_entry_chip/0

Save this, run your `git` commands, and watch the deployment go live on Render. 

Would you like me to help you draft the email Naison will send to the VC alongside the live link once it's deployed?
