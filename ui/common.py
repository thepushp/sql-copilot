"""Small pieces shared by the app's pages."""

import base64
from pathlib import Path

import streamlit as st


ASSETS = Path(__file__).resolve().parent.parent / "assets"
APP_ICON = str(ASSETS / "icon.png")

NAV_CHAT = ":material/chat: Chat"
NAV_EXPLORER = ":material/account_tree: Explorer"
NAV_ER = ":material/schema: ER diagram"
NAV_OPTIONS = [NAV_CHAT, NAV_EXPLORER, NAV_ER]


def brand_html(size=22, css_class=""):
    """The logo and app name as inline HTML (the logo is embedded, so no file serving is needed)."""

    logo = base64.b64encode((ASSETS / "icon.svg").read_bytes()).decode()

    return (
        f'<div class="{css_class}" style="display:flex;align-items:center;gap:0.45rem;font-weight:600">'
        f'<img src="data:image/svg+xml;base64,{logo}" width="{size}" height="{size}" alt=""/>'
        "<span>SQL Copilot</span></div>"
    )


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
