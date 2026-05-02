import streamlit as st
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.db import init_db

init_db()

st.set_page_config(
    page_title="TenderLens",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.sidebar.title("TenderLens")
st.sidebar.caption("AI-Powered Tender Eligibility Analysis")
st.sidebar.markdown("---")
st.sidebar.markdown(
    """
    **How to use:**
    1. Upload tender & bidder docs
    2. Review extracted criteria
    3. View evaluation results
    4. Export report
    """
)

st.title("TenderLens")
st.subheader("AI-Powered Eligibility Analysis for Government Procurement")

st.markdown(
    """
    Welcome to **TenderLens** — a platform that helps procurement officers evaluate
    tender bids faster, more consistently, and with full auditability.

    **Get started:** Use the sidebar to navigate to the pages, or click below.
    """
)

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.markdown("### Step 1")
    st.markdown("**Upload Documents**")
    st.markdown("Upload the tender document and all bidder submissions.")

with col2:
    st.markdown("### Step 2")
    st.markdown("**Review Criteria**")
    st.markdown("Review the eligibility criteria extracted from the tender.")

with col3:
    st.markdown("### Step 3")
    st.markdown("**Evaluation**")
    st.markdown("View criterion-by-criterion evaluation for each bidder.")

with col4:
    st.markdown("### Step 4")
    st.markdown("**Report**")
    st.markdown("Export the consolidated report with audit trail.")
