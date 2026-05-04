"""Shared Streamlit styling and layout helpers for a consistent, professional UI."""
import streamlit as st


def apply_theme():
    """Inject global CSS (Streamlit theme is extended via .streamlit/config.toml)."""
    st.markdown(
        """
<style>
    .block-container {
        padding-top: 1.25rem;
        padding-bottom: 2rem;
        max-width: 1200px;
    }
    h1 {
        font-weight: 600;
        letter-spacing: -0.02em;
        color: #0f172a;
        font-size: 1.75rem;
        margin-bottom: 0.35rem;
    }
    h2, h3 {
        font-weight: 600;
        color: #1e293b;
    }
    .tl-kicker {
        font-size: 0.8rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: #64748b;
        margin-bottom: 0.15rem;
    }
    .tl-session {
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 1rem 1.25rem;
        background: linear-gradient(180deg, #f8fafc 0%, #ffffff 100%);
        margin-bottom: 1.25rem;
    }
    .tl-session-title {
        font-weight: 600;
        color: #0f172a;
        margin: 0 0 0.25rem 0;
    }
    .tl-session-meta {
        font-size: 0.88rem;
        color: #64748b;
        margin: 0 0 0.75rem 0;
    }
    hr.tl-rule {
        margin: 1.5rem 0;
        border: none;
        border-top: 1px solid #e2e8f0;
    }
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #f1f5f9 0%, #f8fafc 100%);
    }
    .tl-report-hero {
        background: linear-gradient(135deg, #1e3a8a 0%, #1d4ed8 55%, #2563eb 100%);
        color: #f8fafc;
        border-radius: 16px;
        padding: 1.75rem 2rem 1.5rem;
        margin-bottom: 1.75rem;
        box-shadow: 0 10px 40px -10px rgba(30, 64, 175, 0.45);
    }
    .tl-report-hero h2.tl-hero-title {
        color: #ffffff !important;
        font-size: 1.55rem;
        font-weight: 600;
        margin: 0 0 0.35rem 0;
        letter-spacing: -0.02em;
        line-height: 1.25;
    }
    .tl-report-hero .tl-hero-sub {
        margin: 0 0 1.1rem 0;
        font-size: 0.92rem;
        opacity: 0.88;
        font-weight: 400;
    }
    .tl-kpi-row {
        display: flex;
        flex-wrap: wrap;
        gap: 0.75rem;
    }
    .tl-kpi {
        background: rgba(255, 255, 255, 0.14);
        border: 1px solid rgba(255, 255, 255, 0.22);
        border-radius: 12px;
        padding: 0.65rem 1.15rem;
        min-width: 5.5rem;
    }
    .tl-kpi-val {
        font-size: 1.28rem;
        font-weight: 700;
        line-height: 1.2;
    }
    .tl-kpi-lab {
        font-size: 0.68rem;
        opacity: 0.85;
        text-transform: uppercase;
        letter-spacing: 0.07em;
        margin-top: 0.15rem;
    }
    .tl-section-label {
        font-size: 0.72rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #64748b;
        margin: 0 0 0.35rem 0;
    }
    .tl-criterion-block {
        border-left: 3px solid #cbd5e1;
        padding: 0.6rem 0.85rem 0.65rem 1rem;
        margin: 0.45rem 0;
        background: #f8fafc;
        border-radius: 0 10px 10px 0;
    }
    .tl-criterion-block.pass { border-left-color: #16a34a; }
    .tl-criterion-block.fail { border-left-color: #dc2626; }
    .tl-criterion-block.review { border-left-color: #ca8a04; }
</style>
""",
        unsafe_allow_html=True,
    )


def page_kicker_step(step_label: str):
    st.markdown(f'<p class="tl-kicker">{step_label}</p>', unsafe_allow_html=True)


def render_session_resume_card(
    tender_name: str,
    tender_filename: str,
    *,
    criteria_count: int | None = None,
    criteria_confirmed: bool = False,
    bidders_count: int | None = None,
):
    """Prominent resume workflow — avoids forcing a new upload when session exists."""
    meta_parts = []
    if criteria_count is not None:
        meta_parts.append(f"{criteria_count} criteria extracted")
    if criteria_confirmed:
        meta_parts.append("criteria confirmed")
    else:
        meta_parts.append("awaiting criteria confirmation")
    if bidders_count is not None:
        meta_parts.append(f"{bidders_count} bidders")
    meta = " · ".join(meta_parts)

    st.markdown(
        f"""
<div class="tl-session">
  <p class="tl-session-title">Current session · {tender_name}</p>
  <p class="tl-session-meta">{tender_filename} — {meta}</p>
</div>
""",
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if st.button("Review criteria", type="primary", width="stretch", key="sess_review"):
            st.switch_page("pages/2_Review_Criteria.py")
    with c2:
        if st.button("Run evaluation", type="secondary", width="stretch", key="sess_eval"):
            st.switch_page("pages/3_Evaluation.py")
    with c3:
        if st.button("Report", type="secondary", width="stretch", key="sess_report"):
            st.switch_page("pages/4_Report.py")
    with c4:
        if st.button("Home", type="secondary", width="stretch", key="sess_home"):
            st.switch_page("app.py")

    st.markdown('<hr class="tl-rule" />', unsafe_allow_html=True)
