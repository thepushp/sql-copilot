"""Small pieces shared by the app's pages."""

import streamlit as st


NAV_CHAT = "💬 Chat"
NAV_EXPLORER = "🌳 Explorer"
NAV_ER = "🔗 ER diagram"
NAV_OPTIONS = [NAV_CHAT, NAV_EXPLORER, NAV_ER]


def current_theme():
    """"light" or "dark" when Streamlit knows it, otherwise None."""

    try:

        return getattr(st.context.theme, "type", None)

    except Exception:

        return None


def show_html(html, height):
    """Embed generated HTML (only ever built from escaped data in er_interactive)."""

    if hasattr(st, "iframe"):

        st.iframe(html, height=height)

    else:

        import streamlit.components.v1 as components

        components.html(html, height=height)
