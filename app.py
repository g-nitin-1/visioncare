"""VisionCare entry point.

Wires the Streamlit user view and admin dashboard. Concrete views are
implemented starting in Phase 7; this file currently boots the app shell.
"""

import streamlit as st


def main() -> None:
    st.set_page_config(page_title="VisionCare", layout="wide")
    st.title("VisionCare")
    st.caption("Multimodal customer support agent — bootstrap shell.")
    st.info("UI is added in Phase 7. Backend phases are tested via pytest.")


if __name__ == "__main__":
    main()
