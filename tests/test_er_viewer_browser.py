"""Real-browser tests of the interactive ER viewer (needs Playwright + Chrome).

Skipped automatically when Playwright or Chrome is not available.
"""

import re

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

from config.privacy import PrivacySettings, apply_privacy  # noqa: E402
from database.database_inspector import inspect_database  # noqa: E402
from services.er_diagram import expand_around, most_connected, table_index  # noqa: E402
from services.er_interactive import graph_data, viewer_html  # noqa: E402


pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def browser():
    playwright = sync_playwright().start()

    try:
        instance = playwright.chromium.launch(channel="chrome")
    except Exception as exc:
        playwright.stop()
        pytest.skip(f"Chrome is not available: {exc}")

    yield instance

    instance.close()
    playwright.stop()


@pytest.fixture
def open_viewer(browser, tmp_path):
    pages = []

    def open_data(data, width=1200, height=700):
        path = tmp_path / f"viewer{len(pages)}.html"
        path.write_text(viewer_html(data), encoding="utf-8")

        page = browser.new_page(viewport={"width": width, "height": height})
        page.js_errors = []
        page.on("pageerror", lambda error: page.js_errors.append(str(error)))
        page.on("console", lambda m: page.js_errors.append(m.text) if m.type == "error" else None)
        page.goto(path.as_uri())
        pages.append(page)

        return page

    yield open_data

    for page in pages:
        page.close()


@pytest.fixture
def shop_data(shop_config):
    metadata, _ = apply_privacy(inspect_database("shop", shop_config), PrivacySettings())
    index = table_index(metadata)
    keys = expand_around(index, most_connected(index), 2)

    return graph_data(index, keys, "Keys only")


def table_box(page, table):
    return page.locator(
        ".table",
        has=page.locator(".title", has_text=re.compile(rf"^{re.escape(table)}( \(.*\))?$")),
    ).first


def column(page, table, name):
    return table_box(page, table).locator(
        ".col",
        has=page.locator(".name", has_text=re.compile(rf"^(PK |FK )?{re.escape(name)}$")),
    ).first


def header(page, table):
    return table_box(page, table).locator(".head").first


def click_edge(page, number):
    page.evaluate(
        "n => document.querySelectorAll('.edge')[n].dispatchEvent(new MouseEvent('click', {bubbles: true}))",
        number,
    )


def selected_tables(page):
    return sorted(page.locator(".table.on .title").all_text_contents())


class TestRendering:

    def test_draws_every_table_and_relationship(self, open_viewer, shop_data):
        page = open_viewer(shop_data)

        assert page.locator(".table").count() == len(shop_data["tables"])
        assert page.locator(".edge").count() == len(shop_data["edges"])
        assert page.js_errors == []

    def test_starts_with_nothing_selected(self, open_viewer, shop_data):
        page = open_viewer(shop_data)

        assert page.locator("#hint").is_visible()
        assert page.locator("#selected").is_hidden()
        assert page.locator(".table.on").count() == 0
        assert not page.evaluate("document.getElementById('canvas').classList.contains('has-selection')")

    def test_primary_and_foreign_keys_are_badged(self, open_viewer, shop_data):
        page = open_viewer(shop_data)

        assert "PK id" in column(page, "customer", "id").text_content()
        assert "FK customer_id" in column(page, "orders", "customer_id").text_content()

    def test_inferred_relationships_are_dashed(self, open_viewer, shop_data):
        page = open_viewer(shop_data)

        assert page.locator(".edge.inferred").count() >= 1
        dash = page.locator(".edge.inferred .line").first.evaluate("e => getComputedStyle(e).strokeDasharray")
        assert dash != "none"

    def test_empty_graph_does_not_crash(self, open_viewer):
        page = open_viewer({"focus": None, "showSchema": False, "tables": [], "edges": []})

        assert page.locator(".table").count() == 0
        assert page.js_errors == []


class TestHighlighting:

    def test_clicking_a_foreign_key_highlights_its_relationship(self, open_viewer, shop_data):
        page = open_viewer(shop_data)

        column(page, "orders", "customer_id").click()

        assert page.locator("#selected").inner_text().startswith("orders.customer_id")
        assert "orders.customer_id → customer.id" in page.locator("#lines").inner_text()
        assert page.locator(".edge.on").count() == 1
        assert selected_tables(page) == ["customer", "orders"]
        assert page.locator(".col.on").count() == 2
        assert page.locator(".col.picked").count() == 1

    def test_unrelated_parts_are_dimmed(self, open_viewer, shop_data):
        page = open_viewer(shop_data)

        column(page, "orders", "customer_id").click()

        dimmed = page.locator(".table:not(.on)").first.evaluate("e => parseFloat(getComputedStyle(e).opacity)")
        bright = page.locator(".table.on").first.evaluate("e => parseFloat(getComputedStyle(e).opacity)")
        assert dimmed < 0.5 < bright

    def test_clicking_a_primary_key_shows_every_table_that_references_it(self, open_viewer, shop_data):
        page = open_viewer(shop_data)

        column(page, "customer", "id").click()

        assert page.locator(".edge.on").count() == 2
        assert "customer" in selected_tables(page)
        assert "orders" in selected_tables(page)
        assert "2 relationships" in page.locator("#selected").inner_text()

    def test_clicking_a_table_header_highlights_all_its_relationships(self, open_viewer, shop_data):
        page = open_viewer(shop_data)

        header(page, "orders").click()

        assert page.locator(".edge.on").count() == 2
        assert selected_tables(page) == ["customer", "order_item", "orders"]

    def test_clicking_a_relationship_line_highlights_both_ends(self, open_viewer, shop_data):
        page = open_viewer(shop_data)

        click_edge(page, 0)

        assert page.locator(".edge.on").count() == 1
        assert page.locator(".table.on").count() == 2
        assert page.locator(".col.on").count() == 2
        assert page.locator("#selected").inner_text() == "Relationship"

    def test_clicking_the_same_thing_again_clears_the_selection(self, open_viewer, shop_data):
        page = open_viewer(shop_data)

        column(page, "orders", "customer_id").click()
        column(page, "orders", "customer_id").click()

        assert page.locator(".table.on").count() == 0
        assert page.locator("#hint").is_visible()

    def test_clicking_something_else_switches_the_selection(self, open_viewer, shop_data):
        page = open_viewer(shop_data)

        column(page, "orders", "customer_id").click()
        header(page, "order_item").click()

        assert selected_tables(page) == ["order_item", "orders"]

    @pytest.mark.parametrize("how", ["escape", "clear-button", "background"])
    def test_selection_can_be_cleared(self, open_viewer, shop_data, how):
        page = open_viewer(shop_data)
        column(page, "orders", "customer_id").click()

        if how == "escape":
            page.keyboard.press("Escape")
        elif how == "clear-button":
            page.click("#clear")
        else:
            page.mouse.click(1100, 650)

        assert page.locator(".table.on").count() == 0
        assert not page.evaluate("document.getElementById('canvas').classList.contains('has-selection')")

    def test_a_column_without_relationships_says_so(self, open_viewer):
        data = {
            "focus": "a.p", "showSchema": False,
            "tables": [
                {"id": "a.p", "name": "p", "schema": "a", "kind": "table",
                 "columns": [{"name": "id", "type": "int", "pk": True, "fk": False},
                             {"name": "plain", "type": "text", "pk": False, "fk": False}]},
                {"id": "a.c", "name": "c", "schema": "a", "kind": "table",
                 "columns": [{"name": "p_id", "type": "int", "pk": False, "fk": True}]},
            ],
            "edges": [{"child": "a.c", "childColumn": "p_id", "parent": "a.p", "parentColumn": "id", "inferred": False}],
        }
        page = open_viewer(data)

        column(page, "p", "plain").click()

        assert "No relationships" in page.locator("#lines").inner_text()
        assert page.locator(".edge.on").count() == 0
        assert page.locator(".table.on").count() == 1

    def test_long_relationship_lists_scroll_instead_of_covering_the_diagram(self, open_viewer):
        tables = [{"id": "s.hub", "name": "hub", "schema": "s", "kind": "table",
                   "columns": [{"name": "id", "type": "int", "pk": True, "fk": False}]}]
        edges = []
        for i in range(40):
            tables.append({"id": f"s.t{i}", "name": f"t{i}", "schema": "s", "kind": "table",
                           "columns": [{"name": "hub_id", "type": "int", "pk": False, "fk": True}]})
            edges.append({"child": f"s.t{i}", "childColumn": "hub_id", "parent": "s.hub", "parentColumn": "id", "inferred": False})
        page = open_viewer({"focus": "s.hub", "showSchema": False, "tables": tables, "edges": edges})

        column(page, "hub", "id").click()

        assert page.locator(".edge.on").count() == 40
        assert page.locator("#lines").evaluate("e => e.clientHeight <= 112 && e.scrollHeight > e.clientHeight")
        assert page.locator("#bar").evaluate("e => e.getBoundingClientRect().height") < 220


def chain_graph(count=12, focus=6):
    """c00 <- c01 <- c02 ...: a long chain lays out wide, too wide to fit at a readable scale."""

    tables = []
    edges = []

    for i in range(count):
        tables.append({"id": f"s.c{i:02d}", "name": f"c{i:02d}", "schema": "s", "kind": "table",
                       "columns": [{"name": "id", "type": "int", "pk": True, "fk": False},
                                   {"name": "prev_id", "type": "int", "pk": False, "fk": i > 0}]})
        if i:
            edges.append({"child": f"s.c{i:02d}", "childColumn": "prev_id", "parent": f"s.c{i - 1:02d}",
                          "parentColumn": "id", "inferred": False})

    return {"focus": f"s.c{focus:02d}", "showSchema": False, "tables": tables, "edges": edges}


def hub_graph(count=40):
    """One hub referenced by many tables: a single tall column."""

    tables = [{"id": "s.hub", "name": "hub", "schema": "s", "kind": "table",
               "columns": [{"name": "id", "type": "int", "pk": True, "fk": False}]}]
    edges = []

    for i in range(count):
        tables.append({"id": f"s.t{i:02d}", "name": f"t{i:02d}", "schema": "s", "kind": "table",
                       "columns": [{"name": "hub_id", "type": "int", "pk": False, "fk": True},
                                   {"name": "next_id", "type": "int", "pk": False, "fk": True}]})
        edges.append({"child": f"s.t{i:02d}", "childColumn": "hub_id", "parent": "s.hub",
                      "parentColumn": "id", "inferred": False})
        if i:
            edges.append({"child": f"s.t{i:02d}", "childColumn": "next_id", "parent": f"s.t{i - 1:02d}",
                          "parentColumn": "hub_id", "inferred": True})

    return {"focus": "s.hub", "showSchema": False, "tables": tables, "edges": edges}


class TestZoom:

    def width(self, page):
        return int(page.get_attribute("#canvas", "width"))

    def stage_width(self, page):
        return page.locator("#stage").evaluate("e => e.clientWidth")

    def test_zoom_buttons(self, open_viewer, shop_data):
        page = open_viewer(shop_data)
        start = self.width(page)

        page.click("#zoom-in")
        assert self.width(page) > start

        page.click("#zoom-out")
        page.click("#zoom-out")
        assert self.width(page) < start

    def test_fit_shrinks_a_wide_diagram_to_the_window(self, open_viewer):
        page = open_viewer(chain_graph(), width=700)

        page.click("#fit")

        assert self.width(page) <= self.stage_width(page)

    def test_a_wide_diagram_starts_readable_instead_of_tiny(self, open_viewer):
        page = open_viewer(chain_graph(), width=700)
        view_box = page.get_attribute("#canvas", "viewBox").split()
        scale = self.width(page) / float(view_box[2])

        assert scale == pytest.approx(0.65, abs=0.02)
        assert self.width(page) > self.stage_width(page), "it scrolls sideways rather than shrinking"

    def test_a_tall_narrow_diagram_opens_on_its_focus_too(self, open_viewer):
        page = open_viewer(hub_graph(), width=900, height=600)

        inside = page.evaluate("""() => {
            const stage = document.getElementById('stage').getBoundingClientRect();
            const hub = [...document.querySelectorAll('.table')].find(t => t.querySelector('.title').textContent === 'hub').getBoundingClientRect();
            return hub.top >= stage.top && hub.bottom <= stage.bottom;
        }""")

        assert inside

    def test_a_small_diagram_fits_without_scrolling(self, open_viewer, shop_data):
        page = open_viewer(shop_data, width=1200)

        assert page.locator("#stage").evaluate("e => e.scrollWidth <= e.clientWidth + 1")

    def test_the_first_view_is_centred_on_the_focus_table(self, open_viewer):
        page = open_viewer(chain_graph(), width=900, height=600)

        inside = page.evaluate("""() => {
            const stage = document.getElementById('stage').getBoundingClientRect();
            const hub = [...document.querySelectorAll('.table')].find(t => t.querySelector('.title').textContent === 'c06').getBoundingClientRect();
            return hub.left >= stage.left && hub.right <= stage.right && hub.top >= stage.top && hub.bottom <= stage.bottom;
        }""")

        assert inside

    def test_zooming_keeps_the_centre_of_the_view(self, open_viewer):
        page = open_viewer(chain_graph(), width=900, height=600)

        def measure():
            return page.evaluate("""() => {
                const s = document.getElementById('stage'), c = document.getElementById('canvas');
                const scale = parseFloat(c.getAttribute('width')) / parseFloat(c.getAttribute('viewBox').split(' ')[2]);
                return {
                    centre: [(s.scrollLeft + s.clientWidth / 2) / scale, (s.scrollTop + s.clientHeight / 2) / scale],
                    scrolls: [s.scrollWidth > s.clientWidth + 1, s.scrollHeight > s.clientHeight + 1],
                };
            }""")

        before = measure()
        page.click("#zoom-in")
        after = measure()

        # only an axis that actually scrolls has a meaningful centre to preserve
        assert before["scrolls"][0] and after["scrolls"][0]
        assert after["centre"][0] == pytest.approx(before["centre"][0], abs=25)

    def test_zooming_keeps_the_vertical_centre_of_a_tall_diagram(self, open_viewer):
        page = open_viewer(hub_graph(), width=900, height=600)

        def centre_y():
            return page.evaluate("""() => {
                const s = document.getElementById('stage'), c = document.getElementById('canvas');
                const scale = parseFloat(c.getAttribute('width')) / parseFloat(c.getAttribute('viewBox').split(' ')[2]);
                return (s.scrollTop + s.clientHeight / 2) / scale;
            }""")

        before = centre_y()
        page.click("#zoom-in")

        assert centre_y() == pytest.approx(before, abs=25)

    def test_zoom_out_has_a_floor(self, open_viewer, shop_data):
        page = open_viewer(shop_data)

        for _ in range(30):
            page.click("#zoom-out")

        view_box = page.get_attribute("#canvas", "viewBox").split()
        scale = int(page.get_attribute("#canvas", "width")) / float(view_box[2])
        assert scale == pytest.approx(0.1, abs=0.02)

    def test_zoom_in_has_a_ceiling(self, open_viewer, shop_data):
        page = open_viewer(shop_data)

        for _ in range(30):
            page.click("#zoom-in")

        view_box = page.get_attribute("#canvas", "viewBox").split()
        scale = int(page.get_attribute("#canvas", "width")) / float(view_box[2])
        assert scale == pytest.approx(2.0, abs=0.02)

    def test_summary_is_shown_until_something_is_selected(self, open_viewer, shop_data):
        data = dict(shop_data, summary="4 tables · 3 relationships")
        page = open_viewer(data)

        assert page.locator("#summary").inner_text() == "4 tables · 3 relationships"

        column(page, "orders", "customer_id").click()
        assert page.locator("#summary").is_hidden()

        page.keyboard.press("Escape")
        assert page.locator("#summary").is_visible()


class TestTheme:

    def background(self, page):
        return page.evaluate("getComputedStyle(document.body).backgroundColor")

    def test_light_theme(self, open_viewer, shop_data):
        page = open_viewer(dict(shop_data, theme="light"))

        assert self.background(page) == "rgb(255, 255, 255)"

    def test_dark_theme(self, open_viewer, shop_data):
        page = open_viewer(dict(shop_data, theme="dark"))

        assert self.background(page) == "rgb(14, 17, 23)"
        assert page.evaluate("document.documentElement.classList.contains('dark')")

    def test_follows_the_operating_system_when_the_app_does_not_say(self, browser, tmp_path, shop_data):
        path = tmp_path / "os_dark.html"
        path.write_text(viewer_html(dict(shop_data, theme=None)), encoding="utf-8")
        page = browser.new_page(color_scheme="dark")
        page.goto(path.as_uri())

        assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(14, 17, 23)"
        page.close()

    def test_explicit_light_beats_a_dark_operating_system(self, browser, tmp_path, shop_data):
        path = tmp_path / "forced_light.html"
        path.write_text(viewer_html(dict(shop_data, theme="light")), encoding="utf-8")
        page = browser.new_page(color_scheme="dark")
        page.goto(path.as_uri())

        assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(255, 255, 255)"
        page.close()

    def test_highlight_stays_visible_in_dark_mode(self, open_viewer, shop_data):
        page = open_viewer(dict(shop_data, theme="dark"))

        column(page, "orders", "customer_id").click()

        stroke = page.locator(".edge.on .line").first.evaluate("e => getComputedStyle(e).stroke")
        assert stroke == "rgb(251, 146, 60)"


HOSTILE = '"><img src=x onerror="window.__pwned=1"></script><script>window.__pwned=1</script><svg onload="window.__pwned=1">'


class TestUntrustedNames:
    """Table and column names come from the database and must stay inert."""

    def data(self):
        evil_table = {
            "id": f"{HOSTILE}.t", "name": HOSTILE, "schema": HOSTILE, "kind": "table",
            "columns": [{"name": HOSTILE, "type": HOSTILE, "pk": True, "fk": False}],
        }
        child = {
            "id": "ok.child", "name": "child", "schema": "ok", "kind": "table",
            "columns": [{"name": "ref", "type": "int", "pk": False, "fk": True}],
        }
        return {
            "focus": evil_table["id"], "showSchema": True,
            "tables": [evil_table, child],
            "edges": [{"child": "ok.child", "childColumn": "ref",
                       "parent": evil_table["id"], "parentColumn": HOSTILE, "inferred": False}],
        }

    def test_nothing_executes_and_no_elements_are_injected(self, open_viewer):
        page = open_viewer(self.data())
        header(page, "ok.child").click()
        page.locator(".col").first.click()
        click_edge(page, 0)

        assert page.evaluate("window.__pwned") is None
        assert page.locator("img").count() == 0
        assert page.evaluate("document.querySelectorAll('script').length") == 2
        assert page.js_errors == []

    def test_hostile_text_is_shown_literally(self, open_viewer):
        page = open_viewer(self.data())

        page.locator(".col").first.click()

        assert HOSTILE in page.locator("#selected").inner_text() or "onerror" in page.locator("#lines").inner_text()
        assert "<img" in page.locator("#lines").inner_text()

    def test_very_long_names_are_truncated(self, open_viewer):
        long_name = "x" * 500
        data = {"focus": "s.t", "showSchema": False,
                "tables": [{"id": "s.t", "name": long_name, "schema": "s", "kind": "table",
                            "columns": [{"name": long_name, "type": long_name, "pk": True, "fk": False}]}],
                "edges": []}
        page = open_viewer(data)

        assert page.locator(".table .title").text_content().endswith("…")
        assert page.locator(".table").evaluate("e => e.getBoundingClientRect().width") < 600


class TestExport:
    """Real downloads from the viewer: what comes out is what is on screen."""

    @pytest.fixture
    def data(self, shop_data):
        return dict(
            shop_data,
            exports={"mermaid": "erDiagram\n  a ||--o{ b : \"x\"", "dot": "digraph ER {\n}"},
        )

    def download(self, page, kind, tmp_path):
        page.click("#export-button")

        with page.expect_download(timeout=15000) as info:
            page.click(f'[data-export="{kind}"]')

        path = tmp_path / info.value.suggested_filename
        info.value.save_as(path)

        return info.value.suggested_filename, path

    def test_menu_is_closed_until_opened(self, open_viewer, data):
        page = open_viewer(data)

        assert page.locator("#export-menu").is_hidden()

        page.click("#export-button")

        assert page.locator("#export-menu").is_visible()
        assert page.get_attribute("#export-button", "aria-expanded") == "true"
        assert page.locator("#export-menu [data-export]:visible").all_inner_texts() == [
            "PNG image", "SVG vector", "Mermaid (.mmd)", "Graphviz (.dot)",
        ]

    @pytest.mark.parametrize("how", ["escape", "outside", "toggle"])
    def test_menu_closes(self, open_viewer, data, how):
        page = open_viewer(data)
        page.click("#export-button")

        if how == "escape":
            page.keyboard.press("Escape")
        elif how == "outside":
            page.mouse.click(600, 600)
        else:
            page.click("#export-button")

        assert page.locator("#export-menu").is_hidden()

    def test_text_formats_are_hidden_when_not_provided(self, open_viewer, shop_data):
        page = open_viewer(dict(shop_data, exports={}))
        page.click("#export-button")

        assert page.locator("#export-menu [data-export]:visible").all_inner_texts() == [
            "PNG image", "SVG vector",
        ]

    def test_png_download_is_a_sharp_image_of_the_whole_diagram(self, open_viewer, data, tmp_path):
        from PIL import Image

        page = open_viewer(data)
        view_box = [float(v) for v in page.get_attribute("#canvas", "viewBox").split()]

        name, path = self.download(page, "png", tmp_path)

        assert name == "er_diagram.png"
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        image = Image.open(path)
        assert image.size == (round(view_box[2] * 2), round(view_box[3] * 2)), "twice the on-screen size"
        assert image.getpixel((2, 2))[:3] == (255, 255, 255)
        assert len({image.getpixel((x, y))[:3] for x in range(0, image.width, 7) for y in range(0, image.height, 7)}) > 5

    def test_png_exports_the_whole_diagram_even_when_scrolled_and_zoomed(self, open_viewer, tmp_path):
        from PIL import Image

        page = open_viewer(chain_graph(), width=700, height=500)
        view_box = [float(v) for v in page.get_attribute("#canvas", "viewBox").split()]
        page.click("#zoom-in")

        _, path = self.download(page, "png", tmp_path)

        assert Image.open(path).size == (round(view_box[2] * 2), round(view_box[3] * 2))

    def test_png_of_a_huge_diagram_stays_within_safe_limits(self, open_viewer, tmp_path):
        from PIL import Image

        page = open_viewer(hub_graph(60))

        _, path = self.download(page, "png", tmp_path)
        image = Image.open(path)

        assert max(image.size) <= 8000
        assert image.width * image.height <= 40e6

    def test_png_follows_the_dark_theme(self, open_viewer, data, tmp_path):
        from PIL import Image

        page = open_viewer(dict(data, theme="dark"))

        _, path = self.download(page, "png", tmp_path)

        assert Image.open(path).getpixel((2, 2))[:3] == (14, 17, 23)

    def test_svg_download_is_standalone_xml(self, open_viewer, data, tmp_path):
        import xml.etree.ElementTree as ET

        page = open_viewer(data)

        name, path = self.download(page, "svg", tmp_path)
        text = path.read_text(encoding="utf-8")
        root = ET.fromstring(text)

        assert name == "er_diagram.svg"
        assert root.tag.endswith("svg")
        assert "var(" not in text, "styles are resolved, not left as CSS variables"
        assert "class=" not in text
        assert text.count("<title") == 0, "tooltips are not exported"
        for table in ("customer", "orders", "order_item"):
            assert f">{table}<" in text
        assert float(root.get("width")) > 0 and float(root.get("height")) > 0

    def test_svg_has_a_background_so_it_is_readable_anywhere(self, open_viewer, data, tmp_path):
        import xml.etree.ElementTree as ET

        page = open_viewer(dict(data, theme="dark"))

        _, path = self.download(page, "svg", tmp_path)
        first = list(ET.fromstring(path.read_text(encoding="utf-8")))[0]

        assert first.tag.endswith("rect")
        assert first.get("fill") == "rgb(14, 17, 23)"

    def test_exports_include_the_current_highlight(self, open_viewer, data, tmp_path):
        page = open_viewer(data)

        _, plain = self.download(page, "svg", tmp_path)
        column(page, "orders", "customer_id").click()
        page.click("#export-button")
        with page.expect_download() as info:
            page.click('[data-export="svg"]')
        highlighted = tmp_path / "highlighted.svg"
        info.value.save_as(highlighted)

        accent = "rgb(249, 115, 22)"
        assert accent not in plain.read_text(encoding="utf-8")
        assert accent in highlighted.read_text(encoding="utf-8")
        assert "opacity:0.22" in highlighted.read_text(encoding="utf-8")

    def test_hit_areas_are_not_exported(self, open_viewer, data, tmp_path):
        page = open_viewer(data)

        _, path = self.download(page, "svg", tmp_path)

        assert "stroke-width:14px" not in path.read_text(encoding="utf-8")

    def test_mermaid_and_dot_download_the_provided_text(self, open_viewer, data, tmp_path):
        page = open_viewer(data)

        mermaid_name, mermaid = self.download(page, "mermaid", tmp_path)
        dot_name, dot = self.download(page, "dot", tmp_path)

        assert (mermaid_name, mermaid.read_text()) == ("er_diagram.mmd", data["exports"]["mermaid"])
        assert (dot_name, dot.read_text()) == ("er_diagram.dot", data["exports"]["dot"])

    def test_hostile_names_cannot_break_the_exported_svg(self, open_viewer, tmp_path):
        import xml.etree.ElementTree as ET

        data = TestUntrustedNames().data()
        data["exports"] = {}
        page = open_viewer(data)

        _, path = self.download(page, "svg", tmp_path)
        text = path.read_text(encoding="utf-8")
        root = ET.fromstring(text)

        assert "<img" not in text and "<script" not in text
        tags = {element.tag.split("}")[-1] for element in root.iter()}
        assert not tags & {"img", "script", "foreignObject"}
        assert page.evaluate("window.__pwned") is None

    def test_exporting_does_not_disturb_the_selection(self, open_viewer, data, tmp_path):
        page = open_viewer(data)
        column(page, "orders", "customer_id").click()

        self.download(page, "png", tmp_path)

        assert page.locator(".edge.on").count() == 1
        assert page.js_errors == []


class TestMenusAndFullscreen:

    def test_only_one_menu_is_open_at_a_time(self, open_viewer, shop_data):
        page = open_viewer(shop_data)

        page.click("#legend-button")
        page.click("#export-button")
        assert page.locator("#legend-body").is_hidden()
        assert page.locator("#export-menu").is_visible()

        page.click("#legend-button")
        assert page.locator("#export-menu").is_hidden()
        assert page.locator("#legend-body").is_visible()

    def test_clicking_the_diagram_closes_menus(self, open_viewer, shop_data):
        page = open_viewer(shop_data)

        page.click("#legend-button")
        page.mouse.click(600, 500)
        assert page.locator("#legend-body").is_hidden()

    def test_fullscreen_button_is_hidden_outside_an_app_frame(self, open_viewer, shop_data):
        page = open_viewer(shop_data)

        assert page.locator("#fullscreen").is_hidden()
        assert not page.js_errors
