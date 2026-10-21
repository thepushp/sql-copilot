// Run with: node --test tests/js/er_viewer.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const er = require(path.join(__dirname, "..", "..", "services", "assets", "er_viewer.js"));

function col(name, extra) {
  return Object.assign({ name: name, type: "bigint", pk: false, fk: false }, extra || {});
}

function table(id, columns, extra) {
  const [schema, name] = id.split(".");
  return Object.assign({ id: id, schema: schema, name: name, kind: "table", columns: columns }, extra || {});
}

// customer <- orders <- item ; customer <- review ; lonely has no relationships
function sample() {
  return er.buildModel({
    focus: "public.orders",
    showSchema: false,
    tables: [
      table("public.customer", [col("id", { pk: true })]),
      table("public.orders", [col("id", { pk: true }), col("customer_id", { fk: true }), col("total")]),
      table("public.item", [col("id", { pk: true }), col("order_id", { fk: true })]),
      table("public.review", [col("id", { pk: true }), col("customer_id", { fk: true })]),
      table("public.lonely", [col("id", { pk: true })]),
    ],
    edges: [
      { child: "public.orders", childColumn: "customer_id", parent: "public.customer", parentColumn: "id", inferred: false },
      { child: "public.item", childColumn: "order_id", parent: "public.orders", parentColumn: "id", inferred: true },
      { child: "public.review", childColumn: "customer_id", parent: "public.customer", parentColumn: "id", inferred: false },
    ],
  });
}

function overlaps(a, b) {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

test("referenced tables are laid out to the left of the tables that reference them", () => {
  const model = sample();
  er.layout(model);
  const by = (id) => model.byId.get(id);

  assert.ok(by("public.customer").x < by("public.orders").x);
  assert.ok(by("public.orders").x < by("public.item").x);
  assert.ok(by("public.customer").x < by("public.review").x);
});

test("tables never overlap and all have positions", () => {
  const model = sample();
  const size = er.layout(model);

  model.tables.forEach((t) => {
    assert.ok(Number.isFinite(t.x) && Number.isFinite(t.y) && t.w > 0 && t.h > 0);
    assert.ok(t.x + t.w <= size.width && t.y + t.h <= size.height);
  });
  for (let i = 0; i < model.tables.length; i++) {
    for (let j = i + 1; j < model.tables.length; j++) {
      assert.ok(!overlaps(model.tables[i], model.tables[j]), model.tables[i].id + " overlaps " + model.tables[j].id);
    }
  }
});

test("tables without relationships go in a column at the far right", () => {
  const model = sample();
  er.layout(model);
  const lonely = model.byId.get("public.lonely");

  model.tables.filter((t) => t !== lonely).forEach((t) => assert.ok(t.x < lonely.x));
});

test("layout is deterministic", () => {
  const first = sample();
  const second = sample();
  er.layout(first);
  er.layout(second);

  assert.deepEqual(first.tables.map((t) => [t.x, t.y]), second.tables.map((t) => [t.x, t.y]));
});

test("a large graph lays out without overlaps", () => {
  const tables = [table("s.hub", [col("id", { pk: true })])];
  const edges = [];
  for (let i = 0; i < 60; i++) {
    tables.push(table("s.t" + i, [col("id", { pk: true }), col("hub_id", { fk: true })]));
    edges.push({ child: "s.t" + i, childColumn: "hub_id", parent: "s.hub", parentColumn: "id" });
  }
  const model = er.buildModel({ focus: "s.hub", tables: tables, edges: edges });
  er.layout(model);

  for (let i = 0; i < model.tables.length; i++) {
    for (let j = i + 1; j < model.tables.length; j++) {
      assert.ok(!overlaps(model.tables[i], model.tables[j]));
    }
  }
});

test("empty and single-table graphs work", () => {
  const empty = er.buildModel({ tables: [], edges: [] });
  assert.doesNotThrow(() => er.layout(empty));

  const one = er.buildModel({ focus: "a.b", tables: [table("a.b", [col("id", { pk: true })])], edges: [] });
  const size = er.layout(one);
  assert.ok(size.width > 0 && size.height > 0);
});

test("edges to unknown tables are ignored", () => {
  const model = er.buildModel({
    tables: [table("a.x", [col("id")])],
    edges: [{ child: "a.x", childColumn: "id", parent: "a.ghost", parentColumn: "id" }],
  });

  assert.equal(model.edges.length, 0);
});

test("edge geometry leaves facing sides for tables in different columns", () => {
  const model = sample();
  er.layout(model);
  const edge = model.edges[0]; // orders -> customer: the parent is to the left

  assert.equal(edge.geometry.s1, -1);
  assert.equal(edge.geometry.s2, 1);
  assert.match(edge.geometry.d, /^M[\d.\-]+,[\d.\-]+ C/);
});

test("edge ends sit on the rows of the columns they connect", () => {
  const model = sample();
  er.layout(model);
  const edge = model.edges[0];
  const c = edge.childTable;
  const rowTop = c.y + er.CFG.headerH + c.colIndex.get("customer_id") * er.CFG.rowH;

  assert.ok(edge.geometry.y1 > rowTop && edge.geometry.y1 < rowTop + er.CFG.rowH);
});

test("self references loop out of the same side", () => {
  const model = er.buildModel({
    focus: "a.emp",
    tables: [table("a.emp", [col("id", { pk: true }), col("manager_id", { fk: true })])],
    edges: [{ child: "a.emp", childColumn: "manager_id", parent: "a.emp", parentColumn: "id" }],
  });
  er.layout(model);

  assert.equal(model.edges[0].geometry.s1, 1);
  assert.equal(model.edges[0].geometry.s2, 1);
});

test("an edge to a column that is not shown attaches to the table header", () => {
  const model = er.buildModel({
    focus: "a.c",
    tables: [table("a.p", []), table("a.c", [])],
    edges: [{ child: "a.c", childColumn: "p_id", parent: "a.p", parentColumn: "id" }],
  });
  er.layout(model);

  assert.equal(model.edges[0].geometry.y1, model.byId.get("a.c").y + er.CFG.headerH / 2);
});

test("clicking a primary key selects every relationship that references it", () => {
  const model = sample();
  const selection = er.selectColumn(model, "public.customer", "id");

  assert.equal(selection.edges.size, 2);
  const names = [...selection.tables].map((i) => model.tables[i].id).sort();
  assert.deepEqual(names, ["public.customer", "public.orders", "public.review"]);
  assert.ok(selection.columns.has(model.byId.get("public.orders").index + "|customer_id"));
  assert.ok(selection.columns.has(model.byId.get("public.review").index + "|customer_id"));
  assert.match(selection.title, /customer\.id — 2 relationships/);
  assert.equal(selection.lines.length, 2);
});

test("clicking a foreign key selects its relationship and the referenced primary key", () => {
  const model = sample();
  const selection = er.selectColumn(model, "public.orders", "customer_id");

  assert.equal(selection.edges.size, 1);
  assert.ok(selection.columns.has(model.byId.get("public.customer").index + "|id"));
  assert.match(selection.title, /1 relationship$/);
  assert.match(selection.lines[0], /orders\.customer_id → customer\.id\s+\(foreign key\), many-to-one/);
});

test("inferred relationships are described as such", () => {
  const model = sample();
  const selection = er.selectColumn(model, "public.item", "order_id");

  assert.match(selection.lines[0], /inferred from the column name/);
});

test("a column in the middle of a chain highlights both directions", () => {
  const model = sample();
  const selection = er.selectColumn(model, "public.orders", "id");

  assert.equal(selection.edges.size, 1); // referenced by item.order_id
  assert.ok(selection.tables.has(model.byId.get("public.item").index));
});

test("a column without relationships says so", () => {
  const model = sample();
  const selection = er.selectColumn(model, "public.orders", "total");

  assert.equal(selection.edges.size, 0);
  assert.deepEqual([...selection.tables], [model.byId.get("public.orders").index]);
  assert.match(selection.lines[0], /No relationships/);
  assert.doesNotMatch(selection.title, /relationship/);
});

test("clicking a relationship selects both ends", () => {
  const model = sample();
  const selection = er.selectEdge(model, 0);

  assert.deepEqual([...selection.edges], [0]);
  assert.equal(selection.tables.size, 2);
  assert.equal(selection.columns.size, 2);
});

test("clicking a table selects all its relationships and neighbours", () => {
  const model = sample();
  const selection = er.selectTable(model, "public.customer");

  assert.equal(selection.edges.size, 2);
  assert.equal(selection.tables.size, 3);
  assert.match(selection.title, /customer — 2 relationships/);
});

test("clicking a table with no relationships says so", () => {
  const model = sample();
  const selection = er.selectTable(model, "public.lonely");

  assert.equal(selection.edges.size, 0);
  assert.match(selection.lines[0], /No relationships/);
});

test("schema is part of the displayed name only when several schemas are shown", () => {
  const data = {
    focus: "a.x",
    showSchema: true,
    tables: [table("a.x", [col("id", { pk: true })]), table("b.y", [col("x_id", { fk: true })])],
    edges: [{ child: "b.y", childColumn: "x_id", parent: "a.x", parentColumn: "id" }],
  };
  const model = er.buildModel(data);

  assert.match(er.describeEdge(model, model.edges[0]), /^b\.y\.x_id → a\.x\.id/);

  data.showSchema = false;
  const plain = er.buildModel(data);
  assert.match(er.describeEdge(plain, plain.edges[0]), /^y\.x_id → x\.id/);
});

test("truncate keeps short text and shortens long text with an ellipsis", () => {
  assert.equal(er.truncate("short", 10), "short");
  assert.equal(er.truncate("a_very_long_column_name", 10), "a_very_lo…");
  assert.equal(er.truncate("a_very_long_column_name", 10).length, 10);
});
