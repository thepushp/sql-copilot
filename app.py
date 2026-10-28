import streamlit as st

from services.audit import configure_logging
from ui.chat import render_chat_view
from ui.common import APP_ICON, NAV_ER, NAV_EXPLORER, brand_html
from ui.er_page import render_er_diagram
from ui.explorer import render_explorer
from ui.layout import BASE_CSS, page_background, render_header
from ui.share import decode as decode_link
from ui.sidebar import render_sidebar
from ui.state import init_state


configure_logging()


st.set_page_config(
    page_title="SQL Copilot",
    page_icon=APP_ICON,
    layout="wide",
)


def main():

    init_state()

    # Open a shared ER link once per browser session.
    if not st.session_state.get("link_read"):

        st.session_state["link_read"] = True

        link = decode_link(dict(st.query_params))

        if link:

            st.session_state["nav"] = NAV_ER
            st.session_state["pending_link"] = link

    settings = render_sidebar()

    st.html(
        f"<style>:root {{ --sqlcopilot-bg: {page_background()}; }}"
        f"{BASE_CSS}</style>"
    )

    st.html(brand_html(20, "sqlcopilot-brand"))

    view = render_header(settings)

    if view != NAV_ER and "view" in st.query_params:

        st.query_params.clear()

    # Only the active section is built, so switching away from the chat does
    # not redraw the diagram on every message.
    if view == NAV_EXPLORER:

        render_explorer(settings)

    elif view == NAV_ER:

        render_er_diagram(settings)

    else:

        render_chat_view(settings)


main()
