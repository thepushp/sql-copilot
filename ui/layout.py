"""Page-wide styling and the top bar."""

import streamlit as st

from database.dialects import get_dialect
from ui.common import NAV_CHAT, NAV_OPTIONS, current_theme as _theme
from ui.state import is_indexed


# Tighter page padding, and the status text right-aligned. Only touches
# layout, never content.
BASE_CSS = """
/* The app name appears in the empty top strip only while the sidebar (which
   shows it itself) is collapsed. */
.sqlcopilot-brand { display: none !important; }
.stApp:has([data-testid="stSidebar"][aria-expanded="false"]) .sqlcopilot-brand,
.stApp:has([data-testid="stSidebar"][data-collapsed="true"]) .sqlcopilot-brand {
  display: flex !important; position: fixed; top: 0.95rem; left: 3.6rem; z-index: 1000002;
  font-weight: 600; white-space: nowrap; pointer-events: none;
}
/* Deploy targets Streamlit Community Cloud, which a self-hosted
   tool does not use. The three-dot menu (Rerun, ...) stays. */
[data-testid="stAppDeployButton"] { display: none !important; }
/* The explorer's table list reads like a file tree: left-aligned, one line each. */
.st-key-explorer_nav button { justify-content: flex-start; padding: 0.2rem 0.6rem; min-height: 2rem; }
.st-key-explorer_nav button > div, .st-key-explorer_nav button > div > span {
  justify-content: flex-start; width: 100%; min-width: 0;
}
.st-key-explorer_nav button p {
  display: block; max-width: 100%; margin: 0; text-align: left; font-size: 0.85rem;
  line-height: 1.25; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
/* The section switcher stays in view while the page scrolls. The wrapper is
   targeted too, because sticky only works against the element's own parent. */
[data-testid="stLayoutWrapper"]:has(> .st-key-topnav), .st-key-topnav {
  position: sticky; top: 3.4rem; z-index: 999;
  background: var(--sqlcopilot-bg, white); padding: 0.3rem 0 0.45rem;
}
.block-container { padding-top: 4.5rem !important; padding-bottom: 1rem !important; }
"""


def page_background():
    """The page colour, so the pinned section bar can cover what scrolls under it."""

    configured = st.get_option("theme.backgroundColor")

    if configured:

        return configured

    return "#0e1117" if _theme() == "dark" else "#ffffff"


def render_header(settings):
    """Slim top bar: section switcher on the left, connection status on the right."""

    metadata = st.session_state.database_metadata

    with st.container(
        key="topnav",
        horizontal=True,
        vertical_alignment="center",
        horizontal_alignment="distribute",
        wrap=True,
    ):

        view = st.segmented_control(
            "Section",
            NAV_OPTIONS,
            default=NAV_CHAT,
            required=True,
            key="nav",
            label_visibility="collapsed",
        )

        if is_indexed(settings.datasource_name) and metadata:

            st.caption(
                f"{settings.datasource_name} · "
                f"{get_dialect(settings.datasource_config).label} · "
                f"{metadata.table_count} tables, "
                f"{metadata.view_count} views"
            )

        else:

            st.caption("Not indexed yet. Use the sidebar to get started.")

    return view or NAV_CHAT
