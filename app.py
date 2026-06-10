"""VisionCare Streamlit entry point."""

import streamlit as st

from frontend.admin_dashboard import render_admin_dashboard
from frontend.user_view import render_user_view


def main() -> None:
    st.set_page_config(page_title="VisionCare", layout="wide")
    st.title("VisionCare")
    st.caption("Privacy-first multimodal customer support for hardware.")
    user_tab, admin_tab = st.tabs(["Customer support", "Admin dashboard"])
    with user_tab:
        render_user_view()
    with admin_tab:
        render_admin_dashboard()


if __name__ == "__main__":
    main()
