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
        
        # Safe string replacement to avoid Markdown parser breaking the code
        raw      = raw.replace("`" * 3 + "json", "").replace("`" * 3 + "JSON", "").replace("`" * 3, "").strip()
        
        parsed   = json.loads(raw)
        score    = max(0, min(100, int(parsed.get("confidence_score", 0))))
        decision = parsed.get("decision", "Pass")
        if decision not in ("Pass", "Investigate"):
            decision = "Pass"
        return {
            "confidence_score": score,
            "rationale":        str(parsed.get("rationale", "")),
            "decision":         decision,
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
st.set_page_config(page_title="VC Sourcing Agent", page_icon="🔭", layout="wide")

st.markdown("""
<style>
  .score-badge { display:inline-block;padding:4px 12px;border-radius:20px;
                 font-weight:700;font-size:14px;margin-bottom:8px; }
  .score-high { background:#d1fae5;color:#065f46; }
  .score-mid  { background:#fef3c7;color:#92400e; }
  .score-low  { background:#fee2e2;color:#991b1b; }
  .decision-investigate { color:#2563eb;font-weight:600; }
  .decision-pass        { color:#6b7280; }
  .feedback-approved    { color:#059669;font-weight:600; }
  .feedback-rejected    { color:#dc2626;font-weight:600; }
  .section-label { font-size:11px;font-weight:700;text-transform:uppercase;
                   letter-spacing:.08em;color:#6b7280;margin-bottom:4px; }
</style>
""", unsafe_allow_html=True)

# ── SIDEBAR ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Configuration")
    st.divider()

    st.markdown('<div class="section-label">🤖 AI Engine</div>', unsafe_allow_html=True)
    api_key = st.text_input("Gemini API Key", type="password", placeholder="AIza…")

    st.divider()

    st.markdown('<div class="section-label">📋 Investment Thesis</div>', unsafe_allow_html=True)
    thesis = st.text_area("", value=DEFAULT_THESIS, height=130, label_visibility="collapsed")

    st.markdown('<div class="section-label">📡 RSS Feed Source</div>', unsafe_allow_html=True)
    feed_choice = st.selectbox("", options=list(RSS_SOURCES.keys()), label_visibility="collapsed")

    st.divider()

    db_now = load_db()
    c1, c2, c3 = st.columns(3)
    with c1: st.metric("In DB",    len(db_now["startups"]))
    with c2: st.metric("Approved", sum(1 for s in db_now["startups"] if s.get("vc_feedback") == "Approve"))
    with c3: st.metric("Rejected", sum(1 for s in db_now["startups"] if s.get("vc_feedback") == "Reject"))

    if st.button("🗑️ Clear Database", use_container_width=True):
        save_db({"startups": []})
        st.success("Database cleared.")
        st.rerun()

# ── HEADER ────────────────────────────────────────────────────────────────────
st.title("🔭 Zestflow VC Deal Sourcing Agent")
st.markdown(
    "Surfaces **net-new** startups matching your thesis · "
    "Evaluated by **Gemini 2.5 Flash**"
)
st.divider()

# ── RUN ────────────────────────────────────────────────────────────────────────
run_clicked = st.button("🚀 Run Sourcing Agent", type="primary")

if run_clicked:
    if not api_key:
        st.error("❌ Enter your Gemini API Key in the sidebar.")
        st.stop()

    db   = load_db()
    seen = get_seen_titles(db)

    with st.status("Running sourcing agent…", expanded=True) as status:
        st.write(f"📡 Fetching **{feed_choice}** …")
        entries     = fetch_feed(RSS_SOURCES[feed_choice])
        new_entries = [e for e in entries if e["title"].lower().strip() not in seen]
        st.write(f"   → {len(entries)} entries · **{len(new_entries)} net-new**")

        if not new_entries:
            status.update(label="✅ All caught up — no new startups found.", state="complete")
        else:
            new_records = []
            for i, entry in enumerate(new_entries):
                st.write(f"🤖 [{i+1}/{len(new_entries)}] *{entry['title']}* …")
                result = evaluate_startup(api_key, thesis, entry["title"], entry["description"])
                if result is None:
                    continue
                record = {
                    "startup_title":    entry["title"],
                    "description":      entry["description"],
                    "link":             entry["link"],
                    "confidence_score": result["confidence_score"],
                    "rationale":        result["rationale"],
                    "decision":         result["decision"],
                    "vc_feedback":      None,
                    "sourced_at":       datetime.utcnow().isoformat(),
                    "source":           feed_choice,
                }
                upsert_startup(db, record)
                new_records.append(record)

            save_db(db)
            st.write(f"💾 **{len(new_records)}** startups saved to cloud database.")

            status.update(
                label=f"✅ Done! {len(new_records)} startups found and evaluated.",
                state="complete",
            )
    st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
db       = load_db()
startups = db["startups"]

if not startups:
    st.info("No startups in DB yet. Configure the sidebar and click **Run Sourcing Agent**.")
    st.stop()

total     = len(startups)
invest_n  = sum(1 for s in startups if s.get("decision") == "Investigate")
high_conf = sum(1 for s in startups if s["confidence_score"] >= 80)
approved  = sum(1 for s in startups if s.get("vc_feedback") == "Approve")
rejected  = sum(1 for s in startups if s.get("vc_feedback") == "Reject")
avg_score = round(sum(s["confidence_score"] for s in startups) / total) if total else 0

m1, m2, m3, m4, m5, m6 = st.columns(6)
with m1: st.metric("Total Sourced",   total)
with m2: st.metric("Investigate",     invest_n)
with m3: st.metric("High Confidence", high_conf)
with m4: st.metric("Avg Score",       f"{avg_score}/100")
with m5: st.metric("Approved",        approved)
with m6: st.metric("Rejected",        rejected)

st.divider()

fc1, fc2, fc3 = st.columns([2, 2, 2])
with fc1: filter_decision = st.selectbox("AI Decision", ["All", "Investigate", "Pass"])
with fc2: filter_feedback = st.selectbox("VC Feedback", ["All", "Pending", "Approve", "Reject"])
with fc3: min_score       = st.slider("Min Score", 0, 100, 0)

filtered = startups
if filter_decision != "All":
    filtered = [s for s in filtered if s.get("decision") == filter_decision]
if filter_feedback == "Pending":
    filtered = [s for s in filtered if not s.get("vc_feedback")]
elif filter_feedback in ("Approve", "Reject"):
    filtered = [s for s in filtered if s.get("vc_feedback") == filter_feedback]
filtered = [s for s in filtered if s["confidence_score"] >= min_score]
filtered.sort(key=lambda s: s["confidence_score"], reverse=True)

st.markdown(f"### 📋 Deal Flow &nbsp; `{len(filtered)} results`")

if not filtered:
    st.warning("No startups match your current filters.")
    st.stop()

for startup in filtered:
    score    = startup["confidence_score"]
    title    = startup["startup_title"]
    feedback = startup.get("vc_feedback")
    decision = startup.get("decision", "Pass")
    link     = startup.get("link", "")
    source   = startup.get("source", "")
    sourced  = startup.get("sourced_at", "")[:10]

    badge  = "score-high" if score >= 80 else "score-mid" if score >= 50 else "score-low"
    fb_tag = " ✅" if feedback == "Approve" else " ❌" if feedback == "Reject" else ""

    with st.expander(f"{title}{fb_tag}", expanded=(score >= 80 and feedback is None)):
        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown(
                f'<span class="score-badge {badge}">Score: {score}/100</span>',
                unsafe_allow_html=True,
            )
            dc = "decision-investigate" if decision == "Investigate" else "decision-pass"
            st.markdown(f'<span class="{dc}">🔎 AI Decision: {decision}</span>', unsafe_allow_html=True)
        with c2:
            st.caption(f"📡 {source}")
            st.caption(f"🗓️ {sourced}")
            if link:
                st.markdown(f"[🔗 Source]({link})")

        st.markdown("**AI Rationale:**")
        st.info(startup.get("rationale", "—"))

        desc = startup.get("description", "")
        if desc:
            st.caption(desc[:400] + ("…" if len(desc) > 400 else ""))

        st.divider()

        if feedback:
            tag = (
                '<span class="feedback-approved">✅ Approved by analyst</span>'
                if feedback == "Approve"
                else '<span class="feedback-rejected">❌ Rejected by analyst</span>'
            )
            st.markdown(tag, unsafe_allow_html=True)
            if st.button("↩️ Clear Feedback", key=f"clear_{title}"):
                db2 = load_db()
                for s in db2["startups"]:
                    if s["startup_title"].lower().strip() == title.lower().strip():
                        s["vc_feedback"] = None
                        s.pop("feedback_at", None)
                save_db(db2)
                st.rerun()
        else:
            st.markdown("**Analyst Decision:**")
            b1, b2, _ = st.columns([1, 1, 4])
            with b1:
                if st.button("👍 Approve", key=f"approve_{title}", type="primary"):
                    db2 = load_db()
                    update_feedback(db2, title, "Approve")
                    st.rerun()
            with b2:
                if st.button("👎 Reject", key=f"reject_{title}"):
                    db2 = load_db()
                    update_feedback(db2, title, "Reject")
                    st.rerun()

st.divider()
col_f1, col_f2 = st.columns([1, 1])
with col_f1:
    st.caption("VC Sourcing Agent · Gemini 2.5 Flash · Streamlit")
with col_f2:
    st.caption("All data stored securely in JSONBin.")
