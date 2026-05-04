import streamlit as st
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.db import init_db
from modules.ui_theme import apply_theme, page_kicker_step
from modules.session_workspace import maybe_bind_ephemeral_session

st.set_page_config(
    page_title="TenderLens",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_db()
maybe_bind_ephemeral_session()

apply_theme()

st.sidebar.markdown("### TenderLens")
st.sidebar.caption("Eligibility analysis for procurement")
st.sidebar.markdown("---")
st.sidebar.markdown(
    """
**Workflow**

1. **Intake** — tender and bidder files  
2. **Criteria** — review extracted rules  
3. **Evaluation** — automated assessment  
4. **Report** — export summary  
"""
)

page_kicker_step("Overview")
st.title("Tender eligibility workspace")
st.markdown(
    "Support consistent, auditable preliminary checks on bidder submissions "
    "against the tender’s stated criteria."
)

st.markdown("---")
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.markdown("**Intake**")
    st.caption("Upload tender and bidder evidence.")

with col2:
    st.markdown("**Criteria**")
    st.caption("Review and confirm rule text.")

with col3:
    st.markdown("**Evaluation**")
    st.caption("Run assessment per bidder.")

with col4:
    st.markdown("**Report**")
    st.caption("Download consolidated output.")

st.markdown("---")
if st.button("Open document intake →", type="primary", width="stretch"):
    st.switch_page("pages/1_Upload_Documents.py")
