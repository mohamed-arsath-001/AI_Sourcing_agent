import streamlit as st
import feedparser
import json
import os
import re
import requests
from datetime import datetime
from urllib.parse import urlparse

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG — OpenRouter (key baked in; move to .streamlit/secrets.toml for prod)
# ══════════════════════════════════════════════════════════════════════════════
OPENROUTER_API_KEY = "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"
OPENROUTER_MODEL   = "z-ai/glm-4.5-air:free"       # free tier via OpenRouter
OPENROUTER_URL     = "https://openrouter.ai/api/v1/chat/completions"

# ── Data Sources (8 public RSS feeds — max without paid APIs) ─────────────────
RSS_SOURCES = {
    "Product Hunt":            "https://www.producthunt.com/feed",
    "TechCrunch — Startups":   "https://techcrunch.com/tag/startups/feed/",
    "TechCrunch — Europe":     "https://techcrunch.com/tag/europe/feed/",
    "VentureBeat":             "https://venturebeat.com/feed/",
    "EU-Startups":             "https://www.eu-startups.com/feed/",
    "Sifted (EU Tech)":        "https://sifted.eu/feed",
    "Reddit — r/startups":     "https://www.reddit.com/r/startups/.rss",
    "Reddit — r/entrepreneur": "https://www.reddit.com/r/entrepreneur/.rss",
}

# ── Investment Thesis ──────────────────────────────────────────────────────────
DEFAULT_THESIS = (
    "Holocene backs early-stage (pre-seed to Series A), post-revenue, innovation-driven companies "
    "building a healthier, happier human experience, with a bias toward founders who can scale globally. "
    "Sectors: Blockchain, Biotech, Health & Wellbeing, Commerce, Technology, Space-Tech. "
    "Geography: Europe or North America. "
    "Raising: between $1M–$10M at the current round. "
    "Impact: Must credibly address at least one UN SDG. Direct contribution to human wellbeing required. "
    "Innovation: Through technology, business model, or both. "
    "Founders must hold majority equity and have the right to work in their country of operation. "
    "LinkedIn followers must be under 20,000 (emerging momentum, not yet mainstream). "
    "If a data point is unavailable, assume potential fit rather than penalise."
)

# ══════════════════════════════════════════════════════════════════════════════
# CLOUD MEMORY — JSONBin
# ══════════════════════════════════════════════════════════════════════════════
JSONBIN_ID  = os.environ.get("JSONBIN_ID",  "69c77436c3097a1dd56b831b")
JSONBIN_KEY = os.environ.get("JSONBIN_KEY", "$2a$10$8folQ4/lgdcUEw.QAwgQXOiRriBnH/otSWRqc4O/SFUPvc3X4MFmW")
JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}"


def load_db() -> dict:
    try:
        r = requests.get(JSONBIN_URL, headers={"X-Access-Key": JSONBIN_KEY}, timeout=10)
        if r.status_code == 200:
            data = r.json().get("record", {})
            data.setdefault("startups", [])
            data.setdefault("feedback_memory", {"ignored_domains": [], "na_domains": []})
            return data
    except Exception as e:
        st.error(f"DB Load Error: {e}")
    return {"startups": [], "feedback_memory": {"ignored_domains": [], "na_domains": []}}


def save_db(db: dict) -> None:
    try:
        requests.put(
            JSONBIN_URL,
            json=db,
            headers={"Content-Type": "application/json", "X-Access-Key": JSONBIN_KEY},
            timeout=10,
        )
    except Exception as e:
        st.error(f"DB Save Error: {e}")


def _extract_domain(url: str) -> str:
    """Pull bare domain from a URL for deduplication."""
    try:
        parsed = urlparse(url if url.startswith("http") else f"https://{url}")
        return parsed.netloc.lower().replace("www.", "").strip()
    except Exception:
        return url.lower().strip()


def get_seen_keys(db: dict) -> set:
    """Return set of (lowercased title + domain) seen before."""
    keys = set()
    for s in db["startups"]:
        keys.add(s["startup_title"].lower().strip())
        if s.get("company_website") and s["company_website"] != "N/A":
            keys.add(_extract_domain(s["company_website"]))
    return keys


def upsert_startup(db: dict, record: dict) -> None:
    db["startups"].append(record)


def update_feedback(db: dict, title: str, feedback: str) -> None:
    mem = db.setdefault("feedback_memory", {"ignored_domains": [], "na_domains": []})
    for s in db["startups"]:
        if s["startup_title"].lower().strip() == title.lower().strip():
            s["vc_feedback"]  = feedback
            s["feedback_at"]  = datetime.utcnow().isoformat()
            domain = _extract_domain(s.get("company_website", ""))
            if feedback == "Ignore" and domain and domain not in mem["ignored_domains"]:
                mem["ignored_domains"].append(domain)
            if feedback == "Not Applicable" and domain and domain not in mem["na_domains"]:
                mem["na_domains"].append(domain)
            break
    save_db(db)


# ══════════════════════════════════════════════════════════════════════════════
# RSS INGESTION
# ══════════════════════════════════════════════════════════════════════════════
def fetch_feed(url: str) -> list[dict]:
    try:
        feed = feedparser.parse(url)
        results = []
        for entry in feed.entries:
            title     = entry.get("title", "").strip()
            raw_desc  = entry.get("summary", entry.get("description", ""))
            clean     = re.sub(r"<[^>]+>", " ", raw_desc)
            clean     = re.sub(r"\s+", " ", clean).strip()[:2000]
            link      = entry.get("link", "")
            if title:
                results.append({"title": title, "description": clean, "link": link})
        return results
    except Exception as e:
        st.error(f"RSS fetch failed ({url}): {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# OPENROUTER EVALUATION
# ══════════════════════════════════════════════════════════════════════════════
def evaluate_startup(
    thesis: str,
    title: str,
    description: str,
    ignored_domains: list[str],
    na_domains: list[str],
) -> dict | None:
    avoid_hint = ""
    if ignored_domains or na_domains:
        avoid_hint = (
            f"\n\nFEEDBACK MEMORY — domains previously rejected by the analyst "
            f"(Ignored: {ignored_domains[:20]}; Not Applicable: {na_domains[:20]}). "
            "If this company's domain or sector pattern matches these, lower your confidence score accordingly."
        )

    prompt = f"""You are a senior VC analyst at Holocene. Evaluate the startup below against the investment thesis.
Extract all requested data points. If a field is not stated in the description, output "N/A".
Respond ONLY with a valid JSON object — no markdown fences, no prose.

HOLOCENE INVESTMENT THESIS:
{thesis}{avoid_hint}

STARTUP DETAILS:
Title: {title}
Description: {description}

Return EXACTLY this JSON:
{{
  "confidence_score": <integer 0–100>,
  "score_breakdown": {{
    "sector_match": <0–20>,
    "geography_match": <0–20>,
    "raise_size_match": <0–20>,
    "sdg_impact": <0–20>,
    "innovation_score": <0–20>
  }},
  "agent_recommendation": "<Progress | Save | Ignore>",
  "rationale": "<one concise paragraph>",
  "company_website": "<URL or N/A>",
  "company_email": "<email or N/A>",
  "industry": "<matched sector or N/A>",
  "stage": "<stage or N/A>",
  "amount_raising": "<amount or N/A>",
  "direct_impact": "<Yes | No | N/A>",
  "sdg_goals": "<list of matched SDG goals or N/A>",
  "founders_names": "<names or N/A>",
  "linkedin_profiles": "<URLs or N/A>",
  "linkedin_followers_est": "<estimated follower count or N/A>"
}}
"""

    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://holocene.vc",
                "X-Title": "Holocene Sourcing Agent",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 900,
                "temperature": 0.2,
            },
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = raw.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
        parsed = json.loads(raw)

        score = max(0, min(100, int(parsed.get("confidence_score", 0))))
        breakdown = parsed.get("score_breakdown", {})

        return {
            "confidence_score":      score,
            "score_breakdown":       breakdown,
            "agent_recommendation":  parsed.get("agent_recommendation", "Ignore"),
            "rationale":             str(parsed.get("rationale", "N/A")),
            "company_website":       str(parsed.get("company_website", "N/A")),
            "company_email":         str(parsed.get("company_email", "N/A")),
            "industry":              str(parsed.get("industry", "N/A")),
            "stage":                 str(parsed.get("stage", "N/A")),
            "amount_raising":        str(parsed.get("amount_raising", "N/A")),
            "direct_impact":         str(parsed.get("direct_impact", "N/A")),
            "sdg_goals":             str(parsed.get("sdg_goals", "N/A")),
            "founders_names":        str(parsed.get("founders_names", "N/A")),
            "linkedin_profiles":     str(parsed.get("linkedin_profiles", "N/A")),
            "linkedin_followers_est": str(parsed.get("linkedin_followers_est", "N/A")),
        }

    except json.JSONDecodeError as e:
        st.warning(f"⚠️ JSON parse error for '{title}': {e}")
        return None
    except requests.HTTPError as e:
        st.warning(f"⚠️ OpenRouter API error for '{title}': {e}")
        return None
    except Exception as e:
        st.warning(f"⚠️ Evaluation failed for '{title}': {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# STREAMLIT UI — PAGE CONFIG
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(page_title="Holocene Sourcing Agent", page_icon="🌍", layout="wide")

st.markdown("""
<style>
  /* Score badges */
  .score-badge{display:inline-block;padding:5px 14px;border-radius:20px;
               font-weight:700;font-size:14px;margin-bottom:8px;}
  .score-high{background:#d1fae5;color:#065f46;}
  .score-mid{background:#fef3c7;color:#92400e;}
  .score-low{background:#fee2e2;color:#991b1b;}
  /* Data pills */
  .data-pill{background:#f3f4f6;padding:3px 10px;border-radius:6px;
             font-size:13px;font-weight:500;display:inline-block;margin:2px;}
  /* Section label */
  .section-label{font-size:11px;font-weight:700;text-transform:uppercase;
                 letter-spacing:.08em;color:#6b7280;margin-bottom:4px;}
  /* Sub-score bar */
  .bar-wrap{background:#e5e7eb;border-radius:4px;height:8px;margin-top:2px;}
  .bar-fill{background:#059669;border-radius:4px;height:8px;}
  /* Feedback status */
  .status-pill{display:inline-block;padding:3px 12px;border-radius:12px;
               font-size:13px;font-weight:700;}
  .s-progress{background:#d1fae5;color:#065f46;}
  .s-save{background:#dbeafe;color:#1e40af;}
  .s-ignore{background:#fee2e2;color:#991b1b;}
  .s-na{background:#f3f4f6;color:#374151;}
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.image(
        "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c2/Globe.svg/1024px-Globe.svg.png",
        width=52,
    )
    st.title("Holocene Sourcing")
    st.caption(f"Model: `{OPENROUTER_MODEL}` · Free tier 🟢")
    st.divider()

    st.markdown('<div class="section-label">📋 Investment Thesis</div>', unsafe_allow_html=True)
    thesis = st.text_area("", value=DEFAULT_THESIS, height=200, label_visibility="collapsed")

    st.markdown('<div class="section-label">📡 Data Source</div>', unsafe_allow_html=True)
    feed_choices = st.multiselect(
        "",
        options=list(RSS_SOURCES.keys()),
        default=["Product Hunt", "TechCrunch — Startups"],
        label_visibility="collapsed",
    )

    st.markdown('<div class="section-label">⚙️ Run Settings</div>', unsafe_allow_html=True)
    max_per_run = st.slider("Max evaluations per run", 5, 30, 10)
    min_score_run = st.slider("Only store if score ≥", 0, 100, 50)

    st.divider()

    db_now = load_db()
    total   = len(db_now["startups"])
    prog    = sum(1 for s in db_now["startups"] if s.get("vc_feedback") == "Progress")
    saved   = sum(1 for s in db_now["startups"] if s.get("vc_feedback") == "Save")
    ignored = sum(1 for s in db_now["startups"] if s.get("vc_feedback") == "Ignore")
    na      = sum(1 for s in db_now["startups"] if s.get("vc_feedback") == "Not Applicable")

    c1, c2 = st.columns(2)
    c1.metric("Total Scanned", total)
    c2.metric("Progressed", prog)
    c1.metric("Saved", saved)
    c2.metric("Ignored", ignored + na)

    st.caption(
        f"🧠 Memory: {len(db_now.get('feedback_memory', {}).get('ignored_domains', []))} ignored domains, "
        f"{len(db_now.get('feedback_memory', {}).get('na_domains', []))} N/A domains"
    )

    if st.button("🗑️ Clear All Memory", use_container_width=True):
        save_db({"startups": [], "feedback_memory": {"ignored_domains": [], "na_domains": []}})
        st.success("Memory wiped.")
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════
st.title("🌍 Holocene Proprietary Sourcing Engine")
st.markdown(
    "Scanning **8 public data sources** for net-new, early-stage startups matching the Holocene mandate. "
    "Agent memory improves with every analyst action."
)
st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# RUN AGENT
# ══════════════════════════════════════════════════════════════════════════════
run_clicked = st.button("🚀 Run Live Sourcing Agent", type="primary", use_container_width=False)

if run_clicked:
    if not feed_choices:
        st.error("Select at least one data source in the sidebar.")
        st.stop()

    db   = load_db()
    seen = get_seen_keys(db)
    mem  = db.get("feedback_memory", {"ignored_domains": [], "na_domains": []})

    with st.status("Executing Holocene Sourcing Scan…", expanded=True) as status:
        all_entries = []
        for source_name in feed_choices:
            st.write(f"📡 Fetching **{source_name}**…")
            entries = fetch_feed(RSS_SOURCES[source_name])
            new = [e for e in entries if e["title"].lower().strip() not in seen]
            st.write(f"   → {len(entries)} posts · **{len(new)} net-new**")
            for e in new:
                e["source"] = source_name
            all_entries.extend(new)

        # Deduplicate across sources by title
        seen_titles = set()
        deduped = []
        for e in all_entries:
            key = e["title"].lower().strip()
            if key not in seen_titles:
                seen_titles.add(key)
                deduped.append(e)

        if len(deduped) > max_per_run:
            st.write(f"   → 🛑 Capping at **{max_per_run}** evaluations this run.")
            deduped = deduped[:max_per_run]

        if not deduped:
            status.update(label="✅ All caught up — no new startups found.", state="complete")
        else:
            new_records = []
            for i, entry in enumerate(deduped):
                st.write(f"🤖 [{i+1}/{len(deduped)}] Evaluating *{entry['title']}*…")
                result = evaluate_startup(
                    thesis,
                    entry["title"],
                    entry["description"],
                    mem.get("ignored_domains", []),
                    mem.get("na_domains", []),
                )
                if result is None:
                    continue
                if result["confidence_score"] < min_score_run:
                    st.write(f"   → Score {result['confidence_score']}% below threshold — skipped.")
                    continue

                record = {
                    "startup_title":          entry["title"],
                    "description":            entry["description"],
                    "link":                   entry["link"],
                    "source":                 entry.get("source", "Unknown"),
                    "confidence_score":       result["confidence_score"],
                    "score_breakdown":        result["score_breakdown"],
                    "agent_recommendation":   result["agent_recommendation"],
                    "rationale":              result["rationale"],
                    "company_website":        result["company_website"],
                    "company_email":          result["company_email"],
                    "industry":               result["industry"],
                    "stage":                  result["stage"],
                    "amount_raising":         result["amount_raising"],
                    "direct_impact":          result["direct_impact"],
                    "sdg_goals":              result["sdg_goals"],
                    "founders_names":         result["founders_names"],
                    "linkedin_profiles":      result["linkedin_profiles"],
                    "linkedin_followers_est": result["linkedin_followers_est"],
                    "vc_feedback":            "Pending",
                    "feedback_at":            None,
                    "sourced_at":             datetime.utcnow().isoformat(),
                }
                upsert_startup(db, record)
                new_records.append(record)

            save_db(db)
            st.write(f"💾 **{len(new_records)}** profiles saved to memory.")
            status.update(
                label=f"✅ Scan complete — {len(new_records)} companies evaluated.",
                state="complete",
            )

    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD — FILTERS
# ══════════════════════════════════════════════════════════════════════════════
db       = load_db()
startups = db["startups"]

if not startups:
    st.info("Agent is resting. Select sources in the sidebar and click **Run Live Sourcing Agent** to begin.")
    st.stop()

fc1, fc2, fc3, fc4 = st.columns([2, 2, 2, 2])
with fc1:
    filter_decision = st.selectbox("AI Recommendation", ["All", "Progress", "Save", "Ignore"])
with fc2:
    filter_feedback = st.selectbox(
        "Analyst Pipeline",
        ["All", "Pending", "Progressed", "Saved", "Ignored", "Not Applicable"],
    )
with fc3:
    filter_source = st.selectbox("Source", ["All"] + list(RSS_SOURCES.keys()))
with fc4:
    min_score = st.slider("Min Confidence Score", 0, 100, 70)

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
elif filter_feedback == "Not Applicable":
    filtered = [s for s in filtered if s.get("vc_feedback") == "Not Applicable"]

if filter_source != "All":
    filtered = [s for s in filtered if s.get("source") == filter_source]

filtered = [s for s in filtered if s["confidence_score"] >= min_score]
filtered.sort(key=lambda s: s["confidence_score"], reverse=True)

st.markdown(f"### 📋 Deal Flow Pipeline &nbsp; `{len(filtered)} companies`")

if not filtered:
    st.warning("No startups match your current filters.")
    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# STARTUP CARDS
# ══════════════════════════════════════════════════════════════════════════════
def render_score_bar(label: str, value: int, max_val: int = 20) -> str:
    pct = int((value / max_val) * 100)
    return (
        f"<div style='margin-bottom:6px'>"
        f"<span style='font-size:12px;color:#374151'>{label}: <b>{value}/{max_val}</b></span>"
        f"<div class='bar-wrap'><div class='bar-fill' style='width:{pct}%'></div></div>"
        f"</div>"
    )


FEEDBACK_LABELS = {
    "Progress":        ("s-progress", "🚀 Progressed"),
    "Save":            ("s-save",     "💾 Saved"),
    "Ignore":          ("s-ignore",   "❌ Ignored"),
    "Not Applicable":  ("s-na",       "⛔ Not Applicable"),
    "Pending":         ("s-na",       "⏳ Pending"),
}

for startup in filtered:
    score    = startup["confidence_score"]
    title    = startup["startup_title"]
    feedback = startup.get("vc_feedback", "Pending")
    decision = startup.get("agent_recommendation", "Ignore")

    badge_cls = "score-high" if score >= 80 else "score-mid" if score >= 50 else "score-low"
    fb_cls, fb_label = FEEDBACK_LABELS.get(feedback, ("s-na", feedback))

    expand = feedback == "Pending" and score >= 80

    with st.expander(f"{title}  ·  {fb_label}", expanded=expand):

        # ── Top Row ────────────────────────────────────────────────────────
        col_l, col_r = st.columns([3, 1])
        with col_l:
            st.markdown(
                f'<span class="score-badge {badge_cls}">Holocene Match: {score}%</span> '
                f'<span class="status-pill {fb_cls}">{fb_label}</span>',
                unsafe_allow_html=True,
            )
            st.markdown(f"**AI Recommendation:** `{decision}`")
        with col_r:
            st.caption(f"📡 {startup.get('source', 'Web')}")
            st.caption(f"🗓️ {startup.get('sourced_at', '')[:10]}")
            if startup.get("link"):
                st.markdown(f"[🔗 Source]({startup['link']})")

        st.divider()

        # ── Score Breakdown ────────────────────────────────────────────────
        bd = startup.get("score_breakdown", {})
        if bd:
            st.markdown("**Score Breakdown**")
            bar_html = (
                render_score_bar("Sector Match",    bd.get("sector_match", 0))
                + render_score_bar("Geography",     bd.get("geography_match", 0))
                + render_score_bar("Raise Size",    bd.get("raise_size_match", 0))
                + render_score_bar("SDG Impact",    bd.get("sdg_impact", 0))
                + render_score_bar("Innovation",    bd.get("innovation_score", 0))
            )
            st.markdown(bar_html, unsafe_allow_html=True)
            st.divider()

        # ── Company Data Pills ─────────────────────────────────────────────
        st.markdown("**Company Profile**")
        d1, d2, d3 = st.columns(3)
        with d1:
            st.markdown(f"<span class='data-pill'>🏭 {startup.get('industry','N/A')}</span>", unsafe_allow_html=True)
            st.markdown(f"<span class='data-pill'>📈 {startup.get('stage','N/A')}</span>", unsafe_allow_html=True)
            st.markdown(f"<span class='data-pill'>💰 {startup.get('amount_raising','N/A')}</span>", unsafe_allow_html=True)
        with d2:
            st.markdown(f"<span class='data-pill'>🌱 SDG: {startup.get('direct_impact','N/A')}</span>", unsafe_allow_html=True)
            st.markdown(f"<span class='data-pill'>🎯 {startup.get('sdg_goals','N/A')}</span>", unsafe_allow_html=True)
            st.markdown(f"<span class='data-pill'>👥 LinkedIn ~{startup.get('linkedin_followers_est','N/A')}</span>", unsafe_allow_html=True)
        with d3:
            st.markdown(f"<span class='data-pill'>👤 {startup.get('founders_names','N/A')}</span>", unsafe_allow_html=True)
            st.markdown(f"<span class='data-pill'>✉️ {startup.get('company_email','N/A')}</span>", unsafe_allow_html=True)
            if startup.get("company_website") and startup["company_website"] != "N/A":
                st.markdown(f"[🌐 Website]({startup['company_website']})")
            if startup.get("linkedin_profiles") and startup["linkedin_profiles"] != "N/A":
                st.markdown(f"[🔗 LinkedIn]({startup['linkedin_profiles']})")

        st.divider()

        # ── AI Rationale ───────────────────────────────────────────────────
        st.markdown("**AI Investment Rationale**")
        st.info(startup.get("rationale", "—"))

        with st.expander("📄 View Raw Source"):
            st.caption(startup.get("description", ""))

        st.divider()

        # ── Action Buttons ─────────────────────────────────────────────────
        if feedback != "Pending":
            if st.button("↩️ Undo Action", key=f"undo_{title}"):
                db2 = load_db()
                for s in db2["startups"]:
                    if s["startup_title"].lower().strip() == title.lower().strip():
                        s["vc_feedback"] = "Pending"
                        s["feedback_at"] = None
                save_db(db2)
                st.rerun()
        else:
            st.markdown("**Next Action** *(updates agent memory)*")
            b1, b2, b3, b4, _ = st.columns([1.5, 1.5, 1.5, 1.8, 4])

            with b1:
                if st.button("🚀 Progress", key=f"prog_{title}", type="primary"):
                    db2 = load_db()
                    update_feedback(db2, title, "Progress")
                    st.toast("📧 Intro email sequence triggered (connect n8n to automate).")
                    st.rerun()
            with b2:
                if st.button("💾 Save", key=f"save_{title}"):
                    db2 = load_db()
                    update_feedback(db2, title, "Save")
                    st.rerun()
            with b3:
                if st.button("❌ Ignore", key=f"ign_{title}"):
                    db2 = load_db()
                    update_feedback(db2, title, "Ignore")
                    st.rerun()
            with b4:
                if st.button("⛔ Not Applicable", key=f"na_{title}"):
                    db2 = load_db()
                    update_feedback(db2, title, "Not Applicable")
                    st.toast("🧠 Feedback stored — agent will deprioritise similar companies.")
                    st.rerun()

# ── Footer ─────────────────────────────────────────────────────────────────────
st.divider()
st.caption("🌍 Holocene Sourcing Engine · Powered by OpenRouter · Agent memory active.")
