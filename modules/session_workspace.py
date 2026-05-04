"""Ephemeral workspace: optionally clear SQLite when a Streamlit session begins."""
import streamlit as st

from config import PERSIST_WORKSPACE
from database.db import wipe_all_workspaces

_EPHEMERAL_BOOT = "_tenderlens_ephemeral_boot"


def maybe_bind_ephemeral_session():
    """First run in this browser session: empty DB if not persisting to disk."""
    if PERSIST_WORKSPACE:
        return
    if st.session_state.get(_EPHEMERAL_BOOT):
        return
    wipe_all_workspaces()
    for k in (
        "active_tender_id",
        "upload_complete",
        "pdf_report_bytes",
        "pdf_report_name",
    ):
        st.session_state.pop(k, None)
    st.session_state[_EPHEMERAL_BOOT] = True


def wipe_before_new_registration():
    """Clear stored tender(s) before ingesting another procurement (same tab)."""
    if PERSIST_WORKSPACE:
        return
    wipe_all_workspaces()
    st.session_state.pop("active_tender_id", None)
    st.session_state.pop("upload_complete", None)
