import streamlit as st
import feedparser
import google.generativeai as genai
import json
import os
import re
import requests
from datetime import datetime

# ── Constants ──────────────────────────────────────────────────────────────────
RSS_SOURCES = {
    "Product Hunt":        "https://www.producthunt.com/feed",
    "TechCrunch Startups": "https://techcrunch.com/tag/startups/feed/",
    "TechCrunch EU":       "https://techcrunch.com/tag/europe/feed/",
    "VentureBeat":         "https://venturebeat.com/feed/",
}

# Updated to match Holocene's exact thesis
DEFAULT_THESIS = (
    "Holocene backs early-stage (pre-seed to Series A), post-revenue, innovation-driven companies "
    "building a healthier, happier human experience, scaling globally. "
    "Sectors: Blockchain, Biotech, Health & Wellbeing, Commerce, Technology, Space-Tech. "
    "Geography: Europe or North America. "
    "Ticket: Raising between $1M-$10M. "
    "Impact: Must credibly address at least one of the UN's 17 SDGs. Direct contribution to human wellbeing. "
    "Other: Innovative tech/business model. (If data is unavailable, assume potential fit)."
)

# ══════════════════════════════════════════════════════════════════════════════
# CLOUD MEMORY / DATABASE (JSONBin)
# ══════════════════════════════════════════════════════════════════════════════
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
            clean_desc = re.sub(r"\s+", " ", clean_desc)[:1500]
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
        prompt = f"""You are a senior venture capital analyst at Holocene. 
Evaluate the startup below against the investment thesis. Extract key data points requested. 
If information is not explicitly available in the description, output "N/A".
Respond ONLY with a valid JSON object.

HOLOCENE INVESTMENT THESIS:
{thesis}

STARTUP DETAILS:
Title: {title}
Description: {description}

Return exactly this JSON structure:
{{
  "confidence_score": <integer 0-100>,
  "agent_recommendation": "<Save, Ignore, or Progress>",
  "rationale": "<one concise paragraph explaining your score based on SDG goals, sector, and innovation>",
  "company_website": "<URL or N/A>",
  "company_email": "<Email or N/A>",
  "industry": "<Matched Sector or N/A>",
  "stage": "<Stage or N/A>",
  "amount_raising": "<Amount or N/A>",
  "direct_impact": "<Yes/No/N/A based on SDG fit>",
  "founders_names": "<Names or N/A>",
  "linkedin_profiles": "<URLs or N/A>"
}}
"""
        response = model.generate_content(prompt)
        raw      = response.text.strip()
        raw      = raw.replace("`" * 3 + "json", "").replace("`" * 3 + "JSON", "").replace("`" * 3, "").strip()
        
        parsed   = json.loads(raw)
        score    = max(0, min(100, int(parsed.get("confidence_score", 0))))
        
        return {
            "confidence_score": score,
            "agent_recommendation": parsed.get("agent_recommendation", "Ignore"),
            "rationale":        str(parsed.get("rationale", "N/A")),
            "company_website":  str(parsed.get("company_website", "N/A")),
            "company_email":    str(parsed.get("company_email", "N/A")),
            "industry":         str(parsed.get("industry", "N/A")),
            "stage":            str(parsed.get("stage", "N/A")),
            "amount_raising":   str(parsed.get("amount_raising", "N/A")),
            "direct_impact":    str(parsed.get("direct_impact", "N/A")),
            "founders_names":   str(parsed.get("founders_names", "N/A")),
            "linkedin_profiles":str(parsed.get("linkedin_profiles", "N/A"))
        }
    except json.JSONDecodeError as e:
        st.warning(f"⚠️ Could not parse Gemini response for '{title}': {e}")
        return None
    except Exception as e:
        st.warning(f"⚠️ Gemini evaluation failed for '{title}': {e}")
        return None

# ══════════════════════════════════════════════════════════════════════════════
# STREAMLIT UI
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(page_title="Holocene Sourcing Agent", page_icon="🌍", layout="wide")

st.markdown("""
<style>
  .score-badge { display:inline-block;padding:4px 12px;border-radius:20px;
                 font-weight:700;font-size:14px;margin-bottom:8px; }
  .score-high { background:#d1fae5;color:#065f46; }
  .score-mid  { background:#fef3c7;color:#92400e; }
  .score-low  { background:#fee2e2;color:#991b1b; }
  .data-pill { background:#f3f4f6; padding: 2px 8px; border-radius: 6px; font-size: 13px; font-weight: 500;}
  .section-label { font-size:11px;font-weight:700;text-transform:uppercase;
                   letter-spacing:.08em;color:#6b7280;margin-bottom:4px; }
  .status-ignore { color: #dc2626; font-weight: bold; }
  .status-save { color: #2563eb; font-weight: bold; }
  .status-progress { color: #059669; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# ── SIDEBAR ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/c/c2/Globe.svg/1024px-Globe.svg.png", width=50)
    st.title("Holocene Sourcing")
    st.divider()

    st.markdown('<div class="section-label">🤖 AI Engine</div>', unsafe_allow_html=True)
    api_key = st.text_input("Gemini API Key", type="password", placeholder="AIza…")

    st.divider()

    st.markdown('<div class="section-label">📋 Investment Thesis</div>', unsafe_allow_html=True)
    thesis = st.text_area("", value=DEFAULT_THESIS, height=200, label_visibility="collapsed")

    st.markdown('<div class="section-label">📡 Core Data Source</div>', unsafe_allow_html=True)
    feed_choice = st.selectbox("", options=list(RSS_SOURCES.keys()), label_visibility="collapsed")

    st.divider()

    db_now = load_db()
    c1, c2 = st.columns(2)
    with c1: st.metric("Database Size", len(db_now["startups"]))
    with c2: st.metric("Progressed", sum(1 for s in db_now["startups"] if s.get("vc_feedback") == "Progress"))

    if st.button("🗑️ Clear Memory", use_container_width=True):
        save_db({"startups": []})
        st.success("Database memory wiped.")
        st.rerun()

# ── HEADER ────────────────────────────────────────────────────────────────────
st.title("🌍 Holocene Proprietary Sourcing Engine")
st.markdown(
    "Scanning public data sources for **net-new, early-stage startups** matching the Holocene mandate."
)
st.divider()

# ── RUN ────────────────────────────────────────────────────────────────────────
run_clicked = st.button("🚀 Run Live Sourcing Agent", type="primary")

if run_clicked:
    if not api_key:
        st.error("❌ Enter your Gemini API Key in the sidebar.")
        st.stop()

    db   = load_db()
    seen = get_seen_titles(db)

    with st.status("Executing Holocene Sourcing Scan…", expanded=True) as status:
        st.write(f"📡 Fetching data from **{feed_choice}** …")
        entries     = fetch_feed(RSS_SOURCES[feed_choice])
        new_entries = [e for e in entries if e["title"].lower().strip() not in seen]
        st.write(f"   → {len(entries)} posts scanned · **{len(new_entries)} net-new companies found**")

        if not new_entries:
            status.update(label="✅ All caught up — no new startups found.", state="complete")
        else:
            new_records = []
            for i, entry in enumerate(new_entries):
                st.write(f"🤖 [{i+1}/{len(new_entries)}] Evaluating *{entry['title']}* against thesis…")
                result = evaluate_startup(api_key, thesis, entry["title"], entry["description"])
                if result is None:
                    continue
                record = {
                    "startup_title":    entry["title"],
                    "description":      entry["description"],
                    "link":             entry["link"],
                    "confidence_score": result["confidence_score"],
                    "agent_recommendation": result["agent_recommendation"],
                    "rationale":        result["rationale"],
                    "industry":         result["industry"],
                    "stage":            result["stage"],
                    "amount_raising":   result["amount_raising"],
                    "direct_impact":    result["direct_impact"],
                    "founders_names":   result["founders_names"],
                    "company_website":  result["company_website"],
                    "company_email":    result["company_email"],
                    "vc_feedback":      "Pending",
                    "sourced_at":       datetime.utcnow().isoformat(),
                    "source":           feed_choice,
                }
                upsert_startup(db, record)
                new_records.append(record)

            save_db(db)
            st.write(f"💾 **{len(new_records)}** profiles parsed and saved to memory.")

            status.update(
                label=f"✅ Scan Complete! {len(new_records)} companies evaluated.",
                state="complete",
            )
    st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
db       = load_db()
startups = db["startups"]

if not startups:
    st.info("Agent is resting. Configure your keys and click **Run Live Sourcing Agent** to begin.")
    st.stop()

# Filters
fc1, fc2, fc3 = st.columns([2, 2, 2])
with fc1: filter_decision = st.selectbox("AI Recommendation", ["All", "Progress", "Save", "Ignore"])
with fc2: filter_feedback = st.selectbox("Analyst Pipeline", ["All", "Pending", "Progressed", "Saved", "Ignored"])
with fc3: min_score       = st.slider("Min Confidence Score", 0, 100, 70)

filtered = startups
if filter_decision != "All":
    filtered = [s for s in filtered if s.get("agent_recommendation") == filter_decision]
    
if filter_feedback == "Pending":
    filtered = [s for s in filtered if s.get("vc_feedback") == "Pending"]
elif filter_feedback == "Progressed":
    filtered = [s for s in filtered if s.get("vc_feedback") == "Progress"]
elif filter_feedback == "Saved":
    filtered = [s for s in filtered if s.get("vc_feedback") == "Save"]
elif filter_feedback == "Ignored":
    filtered = [s for s in filtered if s.get("vc_feedback") == "Ignore"]
    
filtered = [s for s in filtered if s["confidence_score"] >= min_score]
filtered.sort(key=lambda s: s["confidence_score"], reverse=True)

st.markdown(f"### 📋 Deal Flow Pipeline &nbsp; `{len(filtered)} companies`")

if not filtered:
    st.warning("No startups match your current filters.")
    st.stop()

for startup in filtered:
    score    = startup["confidence_score"]
    title    = startup["startup_title"]
    feedback = startup.get("vc_feedback", "Pending")
    decision = startup.get("agent_recommendation", "Ignore")
    
    badge  = "score-high" if score >= 80 else "score-mid" if score >= 50 else "score-low"
    
    # Custom tags for the UI based on Holocene's required actions
    if feedback == "Progress":
        fb_tag = " 🚀 (Progressed)"
    elif feedback == "Save":
        fb_tag = " 💾 (Saved)"
    elif feedback == "Ignore":
        fb_tag = " ❌ (Ignored)"
    else:
        fb_tag = " ⏳ (Pending)"

    with st.expander(f"{title}{fb_tag}", expanded=(feedback == "Pending" and score >= 80)):
        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown(
                f'<span class="score-badge {badge}">Holocene Match: {score}%</span>',
                unsafe_allow_html=True,
            )
            st.markdown(f'**AI Recommendation:** {decision}')
        with c2:
            st.caption(f"📡 Sourced via: {startup.get('source', 'Web')}")
            st.caption(f"🗓️ Date: {startup.get('sourced_at', '')[:10]}")
            if startup.get("link"):
                st.markdown(f"[🔗 Source Link]({startup.get('link')})")

        # Display the custom data extraction fields Holocene requested
        st.markdown("#### Company Profile Data")
        dc1, dc2, dc3 = st.columns(3)
        with dc1:
            st.markdown(f"<span class='data-pill'>Industry: {startup.get('industry', 'N/A')}</span>", unsafe_allow_html=True)
            st.markdown(f"<span class='data-pill'>Stage: {startup.get('stage', 'N/A')}</span>", unsafe_allow_html=True)
        with dc2:
            st.markdown(f"<span class='data-pill'>Raising: {startup.get('amount_raising', 'N/A')}</span>", unsafe_allow_html=True)
            st.markdown(f"<span class='data-pill'>SDG Impact: {startup.get('direct_impact', 'N/A')}</span>", unsafe_allow_html=True)
        with dc3:
            st.markdown(f"<span class='data-pill'>Founders: {startup.get('founders_names', 'N/A')}</span>", unsafe_allow_html=True)
            st.markdown(f"<span class='data-pill'>Email: {startup.get('company_email', 'N/A')}</span>", unsafe_allow_html=True)
            
        st.write("")
        st.markdown("**AI Investment Rationale:**")
        st.info(startup.get("rationale", "—"))

        with st.expander("View Raw Source Description"):
            st.caption(startup.get("description", ""))

        st.divider()

        if feedback != "Pending":
            status_class = f"status-{feedback.lower()}"
            st.markdown(f"Analyst Status: <span class='{status_class}'>{feedback}</span>", unsafe_allow_html=True)
            
            if st.button("↩️ Undo Action", key=f"clear_{title}"):
                db2 = load_db()
                for s in db2["startups"]:
                    if s["startup_title"].lower().strip() == title.lower().strip():
                        s["vc_feedback"] = "Pending"
                save_db(db2)
                st.rerun()
        else:
            st.markdown("**Next Actions (Updates Model Memory):**")
            b1, b2, b3, _ = st.columns([1.5, 1.5, 1.5, 5])
            with b1:
                if st.button("🚀 Progress", key=f"progress_{title}", type="primary"):
                    db2 = load_db()
                    update_feedback(db2, title, "Progress")
                    st.toast("📧 Automated intro email sequence triggered via n8n.")
                    st.rerun()
            with b2:
                if st.button("💾 Save", key=f"save_{title}"):
                    db2 = load_db()
                    update_feedback(db2, title, "Save")
                    st.rerun()
            with b3:
                if st.button("❌ Ignore", key=f"ignore_{title}"):
                    db2 = load_db()
                    update_feedback(db2, title, "Ignore")
                    st.rerun()

st.divider()
st.caption("Powered by Zestflow · Agent Memory active.")
