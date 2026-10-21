/* Interactive ER diagram viewer. No dependencies.
 *
 * All text from the database (table/column names, types) is untrusted. It is
 * only ever inserted with textContent / setAttribute, never as HTML.
 *
 * Layout and selection logic are pure functions (exported for tests); the DOM
 * code at the bottom only runs in a browser.
 */
(function (root) {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var MIN_START_SCALE = 0.65;
  var MIN_SCALE = 0.1;
  var MAX_SCALE = 2;

  var CFG = {
    rowH: 22,
    headerH: 30,
    padX: 12,
    padBottom: 6,
    charW: 7.3,
    minW: 150,
    maxName: 34,
    maxType: 22,
    layerGap: 150,
    rowGap: 28,
    margin: 28,
  };

  /* ---------------------------------------------------------------- model */

  function truncate(text, max) {
    text = String(text);
    return text.length > max ? text.slice(0, max - 1) + "…" : text;
  }

  function buildModel(data) {
    var tables = (data.tables || []).map(function (table, index) {
      var copy = {
        index: index,
        id: table.id,
        name: table.name,
        schema: table.schema,
        kind: table.kind || "table",
        columns: table.columns || [],
        colIndex: new Map(),
      };
      copy.columns.forEach(function (column, i) {
        copy.colIndex.set(column.name, i);
      });
      return copy;
    });

    var byId = new Map();
    tables.forEach(function (table) {
      byId.set(table.id, table);
    });

    var edges = [];
    (data.edges || []).forEach(function (edge) {
      var child = byId.get(edge.child);
      var parent = byId.get(edge.parent);
      if (!child || !parent) {
        return;
      }
      edges.push({
        index: edges.length,
        child: edge.child,
        childColumn: edge.childColumn,
        parent: edge.parent,
        parentColumn: edge.parentColumn,
        inferred: !!edge.inferred,
        childTable: child,
        parentTable: parent,
      });
    });

    return {
      tables: tables,
      edges: edges,
      byId: byId,
      focus: data.focus,
      showSchema: !!data.showSchema,
    };
  }

  function displayName(model, table) {
    return model.showSchema ? table.schema + "." + table.name : table.name;
  }

  function adjacency(model) {
    var adj = model.tables.map(function () {
      return [];
    });
    model.edges.forEach(function (edge) {
      var a = edge.childTable.index;
      var b = edge.parentTable.index;
      if (a !== b) {
        adj[a].push({ other: b, edge: edge });
        adj[b].push({ other: a, edge: edge });
      }
    });
    return adj;
  }

  /* --------------------------------------------------------------- layout */

  // Referenced (parent) tables go left, referencing (child) tables go right.
  function assignLayers(model) {
    var n = model.tables.length;
    var adj = adjacency(model);
    var layer = new Array(n).fill(null);
    var focus = model.byId.get(model.focus);
    var roots = [];

    if (focus) {
      roots.push(focus.index);
    }
    model.tables
      .map(function (t) {
        return t.index;
      })
      .sort(function (a, b) {
        return adj[b].length - adj[a].length || a - b;
      })
      .forEach(function (i) {
        roots.push(i);
      });

    var connectedMax = -1;

    roots.forEach(function (root) {
      if (layer[root] !== null || adj[root].length === 0) {
        return;
      }
      var members = [root];
      var local = {};
      local[root] = 0;
      layer[root] = 0;
      var queue = [root];
      while (queue.length) {
        var current = queue.shift();
        adj[current].forEach(function (link) {
          if (layer[link.other] !== null) {
            return;
          }
          var step = link.edge.childTable.index === current ? -1 : 1;
          layer[link.other] = layer[current] + step;
          members.push(link.other);
          queue.push(link.other);
        });
      }
      var low = Math.min.apply(
        null,
        members.map(function (i) {
          return layer[i];
        })
      );
      members.forEach(function (i) {
        layer[i] -= low;
        connectedMax = Math.max(connectedMax, layer[i]);
      });
    });

    // Tables with no drawn relationships go into one column at the end.
    var lonely = connectedMax + 1;
    layer.forEach(function (value, i) {
      if (value === null) {
        layer[i] = lonely;
      }
    });

    return layer;
  }

  function orderLayers(model, layer) {
    var adj = adjacency(model);
    var count = Math.max.apply(null, layer.concat([0])) + 1;
    var columns = [];
    for (var l = 0; l < count; l++) {
      columns.push([]);
    }
    model.tables
      .slice()
      .sort(function (a, b) {
        return adj[b.index].length - adj[a.index].length || a.index - b.index;
      })
      .forEach(function (table) {
        columns[layer[table.index]].push(table.index);
      });

    function positions() {
      var pos = {};
      columns.forEach(function (column) {
        column.forEach(function (index, i) {
          pos[index] = column.length > 1 ? i / (column.length - 1) : 0.5;
        });
      });
      return pos;
    }

    for (var sweep = 0; sweep < 4; sweep++) {
      var order =
        sweep % 2 === 0
          ? columns.map(function (_, i) {
              return i;
            })
          : columns
              .map(function (_, i) {
                return i;
              })
              .reverse();
      order.forEach(function (c) {
        var pos = positions();
        var keyed = columns[c].map(function (index, i) {
          var near = adj[index].filter(function (link) {
            return layer[link.other] !== c;
          });
          var key = near.length
            ? near.reduce(function (sum, link) {
                return sum + pos[link.other];
              }, 0) / near.length
            : pos[index];
          return { index: index, key: key, i: i };
        });
        keyed.sort(function (a, b) {
          return a.key - b.key || a.i - b.i;
        });
        columns[c] = keyed.map(function (item) {
          return item.index;
        });
      });
    }
    return columns;
  }

  function measureTable(model, table) {
    var title = truncate(displayName(model, table), CFG.maxName);
    if (table.kind !== "table") {
      title += " (" + table.kind + ")";
    }
    var widest = title.length + 1;
    table.columns.forEach(function (column) {
      var badge = column.pk || column.fk ? 3 : 0;
      var length =
        badge +
        truncate(column.name, CFG.maxName).length +
        2 +
        truncate(column.type || "", CFG.maxType).length;
      widest = Math.max(widest, length);
    });
    table.title = title;
    table.w = Math.max(CFG.minW, Math.ceil(widest * CFG.charW + 2 * CFG.padX));
    table.h = CFG.headerH + table.columns.length * CFG.rowH + CFG.padBottom;
  }

  function layout(model) {
    model.tables.forEach(function (table) {
      measureTable(model, table);
    });

    var layer = assignLayers(model);
    var columns = orderLayers(model, layer);

    var heights = columns.map(function (column) {
      return column.reduce(function (sum, index, i) {
        return sum + model.tables[index].h + (i ? CFG.rowGap : 0);
      }, 0);
    });
    var tallest = Math.max.apply(null, heights.concat([0]));

    var x = CFG.margin;
    columns.forEach(function (column, c) {
      var width = 0;
      var y = CFG.margin + (tallest - heights[c]) / 2;
      column.forEach(function (index) {
        var table = model.tables[index];
        table.layer = c;
        table.x = x;
        table.y = y;
        y += table.h + CFG.rowGap;
        width = Math.max(width, table.w);
      });
      x += width + CFG.layerGap;
    });

    var total = {
      width: Math.max(x - CFG.layerGap + CFG.margin, 2 * CFG.margin),
      height: tallest + 2 * CFG.margin,
    };

    model.edges.forEach(function (edge) {
      edge.geometry = edgeGeometry(edge);
    });

    return total;
  }

  function anchorY(table, column) {
    var i = table.colIndex.get(column);
    return i === undefined
      ? table.y + CFG.headerH / 2
      : table.y + CFG.headerH + i * CFG.rowH + CFG.rowH / 2;
  }

  function edgeGeometry(edge) {
    var c = edge.childTable;
    var p = edge.parentTable;
    var y1 = anchorY(c, edge.childColumn);
    var y2 = anchorY(p, edge.parentColumn);
    var x1, x2, s1, s2, k1, k2;

    if (c === p || !(c.x + c.w + 10 <= p.x || p.x + p.w + 10 <= c.x)) {
      x1 = c.x + c.w;
      x2 = p.x + p.w;
      s1 = 1;
      s2 = 1;
      k1 = k2 = 50 + 14 * (edge.index % 4);
    } else if (c.x + c.w + 10 <= p.x) {
      x1 = c.x + c.w;
      x2 = p.x;
      s1 = 1;
      s2 = -1;
      k1 = k2 = Math.max(30, Math.min(120, Math.abs(x2 - x1) / 2));
    } else {
      x1 = c.x;
      x2 = p.x + p.w;
      s1 = -1;
      s2 = 1;
      k1 = k2 = Math.max(30, Math.min(120, Math.abs(x2 - x1) / 2));
    }

    return {
      x1: x1,
      y1: y1,
      x2: x2,
      y2: y2,
      s1: s1,
      s2: s2,
      d:
        "M" + x1 + "," + y1 +
        " C" + (x1 + s1 * k1) + "," + y1 +
        " " + (x2 + s2 * k2) + "," + y2 +
        " " + x2 + "," + y2,
    };
  }

  /* ------------------------------------------------------------ selection */

  function describeEdge(model, edge) {
    return (
      displayName(model, edge.childTable) + "." + edge.childColumn +
      " → " +
      displayName(model, edge.parentTable) + "." + edge.parentColumn +
      (edge.inferred ? "  (inferred from the column name)" : "  (foreign key)") +
      ", many-to-one"
    );
  }

  function colKey(tableIndex, column) {
    return tableIndex + "|" + column;
  }

  function selectionFromEdges(model, edges, title, picked) {
    var selection = {
      title: title,
      edges: new Set(),
      tables: new Set(),
      columns: new Set(),
      picked: picked || null,
      lines: [],
    };
    edges.forEach(function (edge) {
      selection.edges.add(edge.index);
      selection.tables.add(edge.childTable.index);
      selection.tables.add(edge.parentTable.index);
      selection.columns.add(colKey(edge.childTable.index, edge.childColumn));
      selection.columns.add(colKey(edge.parentTable.index, edge.parentColumn));
      selection.lines.push(describeEdge(model, edge));
    });
    return selection;
  }

  function withCount(selection) {
    var n = selection.edges.size;
    if (n) {
      selection.title += " — " + n + (n === 1 ? " relationship" : " relationships");
    }
    return selection;
  }

  function selectColumn(model, tableId, column) {
    var table = model.byId.get(tableId);
    var related = model.edges.filter(function (edge) {
      return (
        (edge.child === tableId && edge.childColumn === column) ||
        (edge.parent === tableId && edge.parentColumn === column)
      );
    });
    var selection = selectionFromEdges(
      model,
      related,
      displayName(model, table) + "." + column,
      colKey(table.index, column)
    );
    selection.tables.add(table.index);
    selection.columns.add(colKey(table.index, column));
    if (!related.length) {
      selection.lines.push("No relationships are drawn for this column.");
    }
    return withCount(selection);
  }

  function selectEdge(model, edgeIndex) {
    var edge = model.edges[edgeIndex];
    return selectionFromEdges(model, [edge], "Relationship", null);
  }

  function selectTable(model, tableId) {
    var table = model.byId.get(tableId);
    var related = model.edges.filter(function (edge) {
      return edge.child === tableId || edge.parent === tableId;
    });
    var selection = selectionFromEdges(
      model,
      related,
      displayName(model, table),
      null
    );
    selection.tables.add(table.index);
    if (!related.length) {
      selection.lines.push("No relationships are drawn for this table.");
    }
    return withCount(selection);
  }

  /* ------------------------------------------------------------------ DOM */

  function el(tag, attrs, parent, text) {
    var node = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach(function (name) {
      node.setAttribute(name, attrs[name]);
    });
    if (text !== undefined) {
      node.textContent = text;
    }
    if (parent) {
      parent.appendChild(node);
    }
    return node;
  }

  function glyphMany(parent, x, y, s) {
    var tip = x + s * 12;
    el("path", {
      "class": "glyph",
      d: "M" + tip + "," + y + " L" + x + "," + (y - 6) +
         " M" + tip + "," + y + " L" + x + "," + y +
         " M" + tip + "," + y + " L" + x + "," + (y + 6),
    }, parent);
  }

  function glyphOne(parent, x, y, s) {
    el("path", {
      "class": "glyph",
      d: "M" + (x + s * 6) + "," + (y - 6) + " L" + (x + s * 6) + "," + (y + 6) +
         " M" + (x + s * 10) + "," + (y - 6) + " L" + (x + s * 10) + "," + (y + 6),
    }, parent);
  }

  function render(model, svg, size, handlers) {
    while (svg.firstChild) {
      svg.removeChild(svg.firstChild);
    }
    svg.setAttribute("viewBox", "0 0 " + size.width + " " + size.height);

    var refs = { tables: [], columns: new Map(), edges: [] };

    var edgeLayer = el("g", { "class": "edges" }, svg);
    var tableLayer = el("g", { "class": "tables" }, svg);

    model.edges.forEach(function (edge) {
      var g = el("g", { "class": "edge" + (edge.inferred ? " inferred" : "") }, edgeLayer);
      var geo = edge.geometry;
      el("title", {}, g, describeEdge(model, edge));
      el("path", { "class": "line", d: geo.d }, g);
      glyphMany(g, geo.x1, geo.y1, geo.s1);
      glyphOne(g, geo.x2, geo.y2, geo.s2);
      el("path", { "class": "hit", d: geo.d }, g);
      g.addEventListener("click", function (event) {
        event.stopPropagation();
        handlers.edge(edge.index);
      });
      refs.edges[edge.index] = g;
    });

    model.tables.forEach(function (table) {
      var g = el("g", {
        "class": "table",
        transform: "translate(" + table.x + "," + table.y + ")",
      }, tableLayer);
      el("rect", { "class": "frame", width: table.w, height: table.h, rx: 6 }, g);
      var head = el("g", { "class": "head" }, g);
      el("rect", { "class": "head-bg", width: table.w, height: CFG.headerH, rx: 6 }, head);
      el("text", { "class": "title", x: CFG.padX, y: CFG.headerH / 2 + 4 }, head, table.title);
      el("title", {}, head, table.schema + "." + table.name);
      head.addEventListener("click", function (event) {
        event.stopPropagation();
        handlers.table(table.id);
      });

      table.columns.forEach(function (column, i) {
        var row = el("g", {
          "class": "col" + (column.pk ? " pk" : "") + (column.fk ? " fk" : ""),
          transform: "translate(0," + (CFG.headerH + i * CFG.rowH) + ")",
        }, g);
        el("rect", { "class": "bg", width: table.w, height: CFG.rowH }, row);
        var label = (column.pk ? "PK " : column.fk ? "FK " : "") + truncate(column.name, CFG.maxName);
        el("text", { "class": "name", x: CFG.padX, y: CFG.rowH / 2 + 4 }, row, label);
        el("text", {
          "class": "type",
          x: table.w - CFG.padX,
          y: CFG.rowH / 2 + 4,
          "text-anchor": "end",
        }, row, truncate(column.type || "", CFG.maxType));
        el("title", {}, row, table.schema + "." + table.name + "." + column.name + " : " + (column.type || ""));
        row.addEventListener("click", function (event) {
          event.stopPropagation();
          handlers.column(table.id, column.name);
        });
        refs.columns.set(colKey(table.index, column.name), row);
      });

      refs.tables[table.index] = g;
    });

    return refs;
  }

  function applySelection(svg, refs, selection) {
    svg.classList.toggle("has-selection", !!selection);
    refs.tables.forEach(function (g, i) {
      g.classList.toggle("on", !!selection && selection.tables.has(i));
    });
    refs.edges.forEach(function (g, i) {
      g.classList.toggle("on", !!selection && selection.edges.has(i));
    });
    refs.columns.forEach(function (g, key) {
      g.classList.toggle("on", !!selection && selection.columns.has(key));
      g.classList.toggle("picked", !!selection && selection.picked === key);
    });
  }

  /* --------------------------------------------------------------- export */

  var STYLE_PROPS = [
    "fill", "stroke", "stroke-width", "stroke-dasharray", "opacity",
    "font-family", "font-size", "font-weight", "text-anchor",
  ];
  var MAX_SIDE = 8000;
  var MAX_PIXELS = 40e6;

  // A standalone SVG has no access to this page's stylesheet, so every
  // visual property is copied onto the element as it looks right now.
  function standaloneSvg(svg) {
    var clone = svg.cloneNode(true);
    var from = svg.querySelectorAll("*");
    var to = clone.querySelectorAll("*");
    var drop = [];

    for (var i = 0; i < from.length; i++) {
      var computed = getComputedStyle(from[i]);
      var css = STYLE_PROPS.map(function (name) {
        return name + ":" + computed.getPropertyValue(name);
      }).join(";");
      to[i].setAttribute("style", css);
      to[i].removeAttribute("class");
      if (from[i].classList.contains("hit") || from[i].tagName === "title") {
        drop.push(to[i]);
      }
    }
    drop.forEach(function (node) {
      node.parentNode.removeChild(node);
    });

    var box = svg.viewBox.baseVal;
    clone.setAttribute("width", box.width);
    clone.setAttribute("height", box.height);
    clone.removeAttribute("class");

    var background = document.createElementNS(SVG_NS, "rect");
    background.setAttribute("width", box.width);
    background.setAttribute("height", box.height);
    background.setAttribute("fill", getComputedStyle(document.body).backgroundColor);
    clone.insertBefore(background, clone.firstChild);

    return { text: new XMLSerializer().serializeToString(clone), width: box.width, height: box.height };
  }

  function pngFromSvg(image, done) {
    var scale = Math.min(2, MAX_SIDE / Math.max(image.width, image.height),
      Math.sqrt(MAX_PIXELS / (image.width * image.height)));
    var canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(image.width * scale));
    canvas.height = Math.max(1, Math.round(image.height * scale));

    var picture = new Image();
    picture.onload = function () {
      canvas.getContext("2d").drawImage(picture, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(done, "image/png");
    };
    picture.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(image.text);
  }

  function saveBlob(blob, name) {
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 2000);
  }

  function exportDiagram(kind, svg, exportsData) {
    if (kind === "png") {
      pngFromSvg(standaloneSvg(svg), function (blob) {
        saveBlob(blob, "er_diagram.png");
      });
    } else if (kind === "svg") {
      saveBlob(
        new Blob([standaloneSvg(svg).text], { type: "image/svg+xml" }),
        "er_diagram.svg"
      );
    } else if (kind === "mermaid" && exportsData.mermaid) {
      saveBlob(new Blob([exportsData.mermaid], { type: "text/plain" }), "er_diagram.mmd");
    } else if (kind === "dot" && exportsData.dot) {
      saveBlob(new Blob([exportsData.dot], { type: "text/plain" }), "er_diagram.dot");
    }
  }

  function initExportMenu(svg, exportsData) {
    var button = document.getElementById("export-button");
    var menu = document.getElementById("export-menu");

    function setOpen(open) {
      menu.hidden = !open;
      button.setAttribute("aria-expanded", open ? "true" : "false");
    }

    menu.querySelectorAll("[data-export]").forEach(function (item) {
      var kind = item.getAttribute("data-export");
      if ((kind === "mermaid" || kind === "dot") && !exportsData[kind]) {
        item.hidden = true;
      }
      item.addEventListener("click", function (event) {
        event.stopPropagation();
        setOpen(false);
        exportDiagram(kind, svg, exportsData);
      });
    });

    button.addEventListener("click", function (event) {
      event.stopPropagation();
      setOpen(menu.hidden);
    });
    document.addEventListener("click", function () {
      setOpen(false);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    });
  }

  function init() {
    var data = JSON.parse(document.getElementById("er-data").textContent);
    if (data.theme === "dark" || data.theme === "light") {
      document.documentElement.classList.add(data.theme);
    }
    var model = buildModel(data);
    var size = layout(model);

    var svg = document.getElementById("canvas");
    var stage = document.getElementById("stage");
    var hint = document.getElementById("hint");
    var lines = document.getElementById("lines");
    var title = document.getElementById("selected");
    var summary = document.getElementById("summary");

    var state = { key: null, scale: 1 };

    // Zooming keeps whatever is in the middle of the window in the middle.
    function setScale(scale) {
      var old = state.scale;
      var cx = (stage.scrollLeft + stage.clientWidth / 2) / old;
      var cy = (stage.scrollTop + stage.clientHeight / 2) / old;

      state.scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale));
      svg.setAttribute("width", Math.round(size.width * state.scale));
      svg.setAttribute("height", Math.round(size.height * state.scale));

      stage.scrollLeft = Math.max(0, cx * state.scale - stage.clientWidth / 2);
      stage.scrollTop = Math.max(0, cy * state.scale - stage.clientHeight / 2);
    }

    function centerOnFocus() {
      var table = model.byId.get(model.focus);
      if (!table) {
        return;
      }
      stage.scrollLeft = Math.max(
        0, (table.x + table.w / 2) * state.scale - stage.clientWidth / 2);
      stage.scrollTop = Math.max(
        0, (table.y + table.h / 2) * state.scale - stage.clientHeight / 2);
    }

    function fitScale() {
      return Math.min(1, (stage.clientWidth - 8) / size.width);
    }

    function fit() {
      setScale(fitScale());
    }

    function show(selection) {
      while (lines.firstChild) {
        lines.removeChild(lines.firstChild);
      }
      hint.hidden = !!selection;
      summary.hidden = !!selection;
      title.hidden = !selection;
      lines.hidden = !selection;
      if (selection) {
        title.textContent = selection.title;
        selection.lines.forEach(function (line) {
          var div = document.createElement("div");
          div.textContent = line;
          lines.appendChild(div);
        });
      }
      applySelection(svg, refs, selection);
    }

    function choose(key, build) {
      if (state.key === key) {
        state.key = null;
        show(null);
        return;
      }
      state.key = key;
      show(build());
    }

    var refs = render(model, svg, size, {
      column: function (tableId, column) {
        choose("c:" + tableId + "|" + column, function () {
          return selectColumn(model, tableId, column);
        });
      },
      edge: function (index) {
        choose("e:" + index, function () {
          return selectEdge(model, index);
        });
      },
      table: function (tableId) {
        choose("t:" + tableId, function () {
          return selectTable(model, tableId);
        });
      },
    });

    function clear() {
      state.key = null;
      show(null);
    }

    svg.addEventListener("click", clear);
    stage.addEventListener("click", function (event) {
      if (event.target === stage) {
        clear();
      }
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        clear();
      }
    });
    document.getElementById("clear").addEventListener("click", clear);
    document.getElementById("zoom-in").addEventListener("click", function () {
      setScale(state.scale * 1.2);
    });
    document.getElementById("zoom-out").addEventListener("click", function () {
      setScale(state.scale / 1.2);
    });
    document.getElementById("fit").addEventListener("click", fit);

    // Start readable: a very wide diagram scrolls sideways instead of shrinking
    // its text to nothing. "Fit" gives the overview.
    setScale(Math.max(MIN_START_SCALE, fitScale()));
    centerOnFocus();
    show(null);
    summary.textContent = data.summary || "";
    initExportMenu(svg, data.exports || {});

    root.__er = { model: model, state: state, select: { column: selectColumn, edge: selectEdge, table: selectTable } };
  }

  var api = {
    CFG: CFG,
    buildModel: buildModel,
    layout: layout,
    assignLayers: assignLayers,
    edgeGeometry: edgeGeometry,
    selectColumn: selectColumn,
    selectEdge: selectEdge,
    selectTable: selectTable,
    describeEdge: describeEdge,
    truncate: truncate,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (typeof document !== "undefined" && document.getElementById("er-data")) {
    init();
  }
})(typeof window !== "undefined" ? window : globalThis);
