// Behaviour for the exported architecture page: highlighting, the
// detail panel, filtering, route tracing and the image exports.
//
// Inlined verbatim into the document, so everything here has to work
// with no network and no server. It is a real file rather than a string
// inside the renderer so the frontend suite can load it, and so adding
// to it does not mean escaping JavaScript through Python.
(function () {
  var svg = document.querySelector('svg.rp-arch');
  var detail = document.getElementById('detail');
  if (!svg || !detail) return;
  var edges = Array.prototype.slice.call(svg.querySelectorAll('.rp-edge'));
  var hits = Array.prototype.slice.call(svg.querySelectorAll('.rp-edge-hit'));
  var nodes = Array.prototype.slice.call(svg.querySelectorAll('.rp-node'));
  var repo = svg.getAttribute('data-repo') || '';
  var sha = svg.getAttribute('data-sha') || '';

  // Adjacency, built once. Reach is transitive: the question a reader
  // actually has is "what does changing this touch", and the answer
  // rarely stops at the first hop.
  var out = {}, into = {};
  edges.forEach(function (e) {
    var f = e.getAttribute('data-from'), t = e.getAttribute('data-to');
    (out[f] = out[f] || []).push(t);
    (into[t] = into[t] || []).push(f);
  });
  function reach(start, graph) {
    var seen = {}, stack = [start];
    while (stack.length) {
      var n = stack.pop();
      (graph[n] || []).forEach(function (m) {
        if (!seen[m] && m !== start) { seen[m] = 1; stack.push(m); }
      });
    }
    return seen;
  }

  function clear() {
    edges.concat(nodes).forEach(function (el) {
      el.classList.remove('dim', 'lit', 'lit-node');
    });
  }
  function linkFor(path, line) {
    return repo && sha
      ? 'https://github.com/' + repo + '/blob/' + sha + '/' + path + '#L' + line
      : '';
  }
  function focusEdge(from, to, lines) {
    clear();
    edges.forEach(function (e) {
      var match = e.getAttribute('data-from') === from && e.getAttribute('data-to') === to;
      e.classList.add(match ? 'lit' : 'dim');
    });
    nodes.forEach(function (n) {
      var p = n.getAttribute('data-path');
      if (p === from || p === to) n.classList.add('lit-node'); else n.classList.add('dim');
    });
    var numbers = lines ? lines.split(',').filter(Boolean) : [];
    var html = '<strong class="mono">' + from + '</strong> imports <strong class="mono">'
      + to + '</strong>';
    if (numbers.length && repo && sha) {
      html += ' &middot; ' + numbers.map(function (n) {
        return '<a class="mono" target="_blank" rel="noreferrer noopener" href="'
          + linkFor(from, n) + '">line ' + n + '</a>';
      }).join(', ');
    } else if (numbers.length) {
      html += ' &middot; <span class="mono">line ' + numbers.join(', ') + '</span>';
    } else {
      html += ' &middot; <span class="note">no single line accounts for this edge</span>';
    }
    detail.innerHTML = html;
  }
  function focusNode(path) {
    clear();
    var downstream = reach(path, out);   // what this file leans on
    var upstream = reach(path, into);    // what leans on this file
    var involved = {};
    involved[path] = 1;
    Object.keys(downstream).forEach(function (k) { involved[k] = 1; });
    Object.keys(upstream).forEach(function (k) { involved[k] = 1; });

    edges.forEach(function (e) {
      var f = e.getAttribute('data-from'), t = e.getAttribute('data-to');
      e.classList.add(involved[f] && involved[t] ? 'lit' : 'dim');
    });
    nodes.forEach(function (n) {
      var p = n.getAttribute('data-path');
      if (p === path) n.classList.add('lit-node');
      else if (!involved[p]) n.classList.add('dim');
    });
    var up = Object.keys(upstream).length, down = Object.keys(downstream).length;
    detail.innerHTML = '<strong class="mono">' + path + '</strong> &middot; '
      + up + ' file(s) reach it, it reaches ' + down + ' &middot; '
      + '<span class="note">transitive, not just direct neighbours</span>';
  }

  hits.forEach(function (hit) {
    function activate(ev) {
      ev.stopPropagation();
      focusEdge(hit.getAttribute('data-from'), hit.getAttribute('data-to'),
                hit.getAttribute('data-lines'));
    }
    hit.addEventListener('click', activate);
    hit.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); activate(ev); }
    });
  });
  nodes.forEach(function (node) {
    function activate(ev) {
      ev.stopPropagation();
      focusNode(node.getAttribute('data-path'));
    }
    node.addEventListener('click', activate);
    node.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); activate(ev); }
    });
  });
  svg.addEventListener('click', function () { clear(); detail.innerHTML = ''; });

  // ---- filter -------------------------------------------------------
  // Archify's own stated weakness is that a large repository draws a
  // diagram too dense to read. Filtering is the cheapest answer to that:
  // the whole graph stays on the page, and everything not matching
  // recedes rather than disappearing, so context is never lost.
  var box = document.getElementById('rp-filter');
  if (box) {
    box.addEventListener('input', function () {
      var q = box.value.trim().toLowerCase();
      clear();
      if (!q) { detail.innerHTML = ''; return; }
      var matched = {};
      nodes.forEach(function (n) {
        var p = n.getAttribute('data-path');
        if (p.toLowerCase().indexOf(q) !== -1) matched[p] = 1;
        else n.classList.add('dim');
      });
      edges.forEach(function (e) {
        var f = e.getAttribute('data-from'), t = e.getAttribute('data-to');
        e.classList.add(matched[f] || matched[t] ? 'lit' : 'dim');
      });
      var count = Object.keys(matched).length;
      detail.innerHTML = count
        ? count + ' file(s) match <strong class="mono">' + q + '</strong>'
        : '<span class="note">nothing matches ' + q + '</span>';
    });
  }

  // ---- route tracing ------------------------------------------------
  // The diagram shows that two files are connected. Past a dozen nodes it
  // will not show you HOW, and "how" is the question worth asking before
  // changing either end. The answer is a specific chain of imports, each
  // with the line it was written on.
  var routeState = null;  // the last traced route, for the share card
  var fromSel = document.getElementById('rp-from');
  var toSel = document.getElementById('rp-to');
  var traceBtn = document.getElementById('rp-trace');
  var routeCardBtn = document.getElementById('rp-route-card');
  var routeOut = document.getElementById('rp-route');

  // On a delta page the drawing also carries the dependencies that were
  // REMOVED, so they can be seen. They must not be traversable: a route
  // through an import that no longer exists is a false claim about the
  // head commit, and this is the one feature whose whole value is that
  // its claims check out.
  var edgeData = edges.filter(function (e) {
    return e.getAttribute('data-status') !== 'removed';
  }).map(function (e) {
    var raw = e.getAttribute('data-lines');
    return {
      from: e.getAttribute('data-from'),
      to: e.getAttribute('data-to'),
      lines: raw ? raw.split(',').filter(Boolean).map(Number) : [],
    };
  });

  if (fromSel && toSel) {
    var paths = nodes.map(function (n) { return n.getAttribute('data-path'); }).sort();
    paths.forEach(function (p) {
      [fromSel, toSel].forEach(function (sel) {
        var opt = document.createElement('option');
        opt.value = p; opt.textContent = p;
        sel.appendChild(opt);
      });
    });
    if (paths.length > 1) toSel.value = paths[paths.length - 1];
  }

  function litRoute(hops) {
    clear();
    var onRoute = {};
    hops.forEach(function (h) { onRoute[h.from] = 1; onRoute[h.to] = 1; });
    var pairs = {};
    hops.forEach(function (h) { pairs[h.from + '\u0000' + h.to] = 1; });
    edges.forEach(function (e) {
      var key = e.getAttribute('data-from') + '\u0000' + e.getAttribute('data-to');
      e.classList.add(pairs[key] ? 'lit' : 'dim');
    });
    nodes.forEach(function (n) {
      var p = n.getAttribute('data-path');
      if (onRoute[p]) n.classList.add('lit-node'); else n.classList.add('dim');
    });
  }

  function renderRoute(result, from, to) {
    if (!routeOut) return;
    if (result.sameNode) {
      routeOut.innerHTML = '<span class="note">Pick two different files.</span>';
      return;
    }
    if (!result.found) {
      routeOut.innerHTML = '<span class="note"><strong class="mono">' + from
        + '</strong> does not reach <strong class="mono">' + to + '</strong>'
        + (result.reverseExists
            ? ' &mdash; but the dependency runs the other way. Swap them.'
            : ' by any chain of imports in this diagram.')
        + '</span>';
      return;
    }
    var parts = ['<ol class="route">'];
    result.hops.forEach(function (h) {
      var cited = h.lines.length && repo && sha
        ? h.lines.map(function (n) {
            return '<a class="mono" target="_blank" rel="noreferrer noopener" href="'
              + linkFor(h.from, n) + '">line ' + n + '</a>';
          }).join(', ')
        : (h.lines.length
            ? '<span class="mono">line ' + h.lines.join(', ') + '</span>'
            : '<span class="note">no single line</span>');
      parts.push('<li><span class="mono">' + h.from + '</span> imports <span class="mono">'
        + h.to + '</span> &middot; ' + cited + '</li>');
    });
    parts.push('</ol>');
    parts.push('<p class="note">' + result.hops.length + ' hop(s)'
      + (result.alternatives
          ? ' &middot; ' + result.alternatives + ' other route(s) of the same length exist'
          : ' &middot; the only route this short')
      + '.</p>');
    routeOut.innerHTML = parts.join('');
  }

  function doTrace() {
    if (!fromSel || !toSel || typeof RPRoute === 'undefined') return;
    var from = fromSel.value, to = toSel.value;
    var result = RPRoute.trace(edgeData, from, to);
    renderRoute(result, from, to);
    if (result.found) {
      litRoute(result.hops);
      routeState = { from: from, to: to, hops: result.hops };
      if (routeCardBtn) routeCardBtn.hidden = false;
      detail.innerHTML = '';
    } else {
      clear();
      routeState = null;
      if (routeCardBtn) routeCardBtn.hidden = true;
    }
  }
  if (traceBtn) traceBtn.addEventListener('click', doTrace);

  // ---- image export -------------------------------------------------
  // The SVG on the page paints from CSS custom properties defined on the
  // document. Once it is serialised into an <img> it has no document to
  // read them from, so the resolved values are baked into a <style> on
  // the clone. Without that step the PNG comes out unstyled.
  var NS = 'http://www.w3.org/2000/svg';
  var VARS = ['--rp-edge', '--rp-node-bg', '--rp-node-line', '--rp-ink', '--rp-ink-dim',
              '--rp-accent', '--rp-added', '--rp-removed'];
  function serialised(keepState) {
    var clone = svg.cloneNode(true);
    var cs = getComputedStyle(document.documentElement);
    var decls = VARS.map(function (v) {
      var value = cs.getPropertyValue(v).trim();
      return value ? v + ':' + value : '';
    }).filter(Boolean).join(';');
    var style = document.createElementNS(NS, 'style');
    style.textContent = 'svg{' + decls + '}'
      + '.rp-edge-cycle{stroke-dasharray:5 4}'
      + '.rp-edge-added{stroke:var(--rp-added);stroke-width:2.2}'
      + '.rp-edge-removed{stroke:var(--rp-removed);stroke-width:2.2;stroke-dasharray:6 4}'
      + '.rp-node-added rect{stroke:var(--rp-added);stroke-width:2}'
      + '.rp-node-removed rect{stroke:var(--rp-removed);stroke-width:2;stroke-dasharray:5 3}'
      + '.rp-node-moved rect{stroke:var(--rp-accent);stroke-width:2}'
      // On screen a reader can click to undim. A shared image cannot be
      // interrogated, so the context behind a route is drawn stronger
      // there than it is here or it may as well not be in the picture.
      + (keepState ? '.dim{opacity:.38}' : '.dim{opacity:.18}')
      + '.lit{stroke:var(--rp-accent);stroke-width:2.8}'
      + '.lit-node rect{stroke:var(--rp-accent);stroke-width:2.4}';
    clone.insertBefore(style, clone.firstChild);
    // Highlighting is a viewer's temporary choice, not part of the
    // diagram, so an ordinary export never carries it. The route card is
    // the exception: there the highlighting IS the subject.
    if (!keepState) {
      Array.prototype.forEach.call(clone.querySelectorAll('.dim,.lit,.lit-node'),
        function (el) { el.classList.remove('dim', 'lit', 'lit-node'); });
    }
    return new XMLSerializer().serializeToString(clone);
  }
  function rasterise(draw, name, keepState) {
    var img = new Image();
    img.onload = function () { draw(img, name); };
    img.onerror = function () {
      detail.innerHTML = '<span class="note">this browser refused to rasterise the diagram; '
        + 'the page itself is still complete</span>';
    };
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(serialised(keepState));
  }
  function save(canvas, name) {
    canvas.toBlob(function (blob) {
      if (!blob) return;
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url; a.download = name; a.click();
      URL.revokeObjectURL(url);
    }, 'image/png');
  }
  function pageBackground() {
    var bg = getComputedStyle(document.body).backgroundColor;
    return bg && bg !== 'rgba(0, 0, 0, 0)' ? bg : '#ffffff';
  }
  var base = (repo || 'architecture').replace(/[^\w-]/g, '-') + '-' + sha.slice(0, 7);

  var pngBtn = document.getElementById('rp-png');
  if (pngBtn) {
    pngBtn.addEventListener('click', function () {
      rasterise(function (img, name) {
        var scale = 2;  // legible when dropped into a doc or a ticket
        var canvas = document.createElement('canvas');
        canvas.width = svg.width.baseVal.value * scale;
        canvas.height = svg.height.baseVal.value * scale;
        var ctx = canvas.getContext('2d');
        ctx.fillStyle = pageBackground();
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        save(canvas, name);
      }, base + '.png');
    });
  }

  var cardBtn = document.getElementById('rp-card');
  if (cardBtn) {
    cardBtn.addEventListener('click', function () {
      rasterise(function (img, name) {
        var W = 1200, H = 630, PAD = 56, HEADER = 104;
        var canvas = document.createElement('canvas');
        canvas.width = W; canvas.height = H;
        var ctx = canvas.getContext('2d');
        var cs = getComputedStyle(document.documentElement);
        ctx.fillStyle = pageBackground();
        ctx.fillRect(0, 0, W, H);

        ctx.fillStyle = cs.getPropertyValue('--rp-ink').trim() || '#0B1420';
        ctx.font = '600 34px system-ui, sans-serif';
        ctx.fillText(repo || 'architecture', PAD, PAD + 26);
        ctx.fillStyle = cs.getPropertyValue('--rp-ink-dim').trim() || '#515D72';
        ctx.font = '19px ui-monospace, Menlo, monospace';
        ctx.fillText(sha.slice(0, 7) + '  ·  ' + edges.length + ' dependencies  ·  '
                     + nodes.length + ' files', PAD, PAD + 60);

        // Fit the whole diagram rather than cropping it: a share card
        // that shows a corner of a graph tells the reader nothing.
        var availW = W - PAD * 2, availH = H - HEADER - PAD;
        var w = svg.width.baseVal.value, h = svg.height.baseVal.value;
        var k = Math.min(availW / w, availH / h);
        var dw = w * k, dh = h * k;
        ctx.drawImage(img, PAD + (availW - dw) / 2, HEADER + (availH - dh) / 2, dw, dh);
        save(canvas, name);
      }, base + '-card.png');
    });
  }

  if (routeCardBtn) {
    routeCardBtn.addEventListener('click', function () {
      if (!routeState) return;
      rasterise(function (img, name) {
        var W = 1200, H = 630, PAD = 56;
        var canvas = document.createElement('canvas');
        canvas.width = W; canvas.height = H;
        var ctx = canvas.getContext('2d');
        var cs = getComputedStyle(document.documentElement);
        var ink = cs.getPropertyValue('--rp-ink').trim() || '#0B1420';
        var dim = cs.getPropertyValue('--rp-ink-dim').trim() || '#515D72';
        var accent = cs.getPropertyValue('--rp-accent').trim() || '#0B5FD0';
        ctx.fillStyle = pageBackground();
        ctx.fillRect(0, 0, W, H);

        ctx.fillStyle = ink;
        ctx.font = '600 28px system-ui, sans-serif';
        ctx.fillText(routeState.from + '  \u2192  ' + routeState.to, PAD, PAD + 22);
        ctx.fillStyle = dim;
        ctx.font = '18px ui-monospace, Menlo, monospace';
        ctx.fillText((repo || '') + '  \u00b7  ' + sha.slice(0, 7) + '  \u00b7  '
                     + routeState.hops.length + ' hop(s)', PAD, PAD + 52);

        // The chain, with the line behind each hop. That is the part a
        // reviewer can check, so it goes on the card rather than only in
        // the page the card came from.
        var y = PAD + 92;
        ctx.font = '17px ui-monospace, Menlo, monospace';
        var shown = routeState.hops.slice(0, 6);
        shown.forEach(function (h) {
          ctx.fillStyle = accent;
          ctx.fillText('\u21b3', PAD, y);
          ctx.fillStyle = ink;
          var label = h.from + ' \u2192 ' + h.to;
          ctx.fillText(label, PAD + 26, y);
          if (h.lines.length) {
            ctx.fillStyle = dim;
            ctx.fillText('  :' + h.lines.join(','), PAD + 26 + ctx.measureText(label).width, y);
          }
          y += 26;
        });
        if (routeState.hops.length > shown.length) {
          ctx.fillStyle = dim;
          ctx.fillText('\u2026 ' + (routeState.hops.length - shown.length) + ' more',
                       PAD + 26, y);
          y += 26;
        }

        // The whole diagram underneath, route lit and the rest dimmed, so
        // the chain is read in the context it came from.
        var top = y + 14;
        var availW = W - PAD * 2, availH = H - top - PAD / 2;
        if (availH > 60) {
          var w = svg.width.baseVal.value, h = svg.height.baseVal.value;
          var k = Math.min(availW / w, availH / h);
          ctx.drawImage(img, PAD + (availW - w * k) / 2, top, w * k, h * k);
        }
        save(canvas, name);
      }, base + '-route.png', true);
    });
  }

  // The controls are inert without this script, so they ship hidden and
  // are revealed only once it has wired them up.
  Array.prototype.forEach.call(document.querySelectorAll('[data-needs-js]'),
    function (el) { el.hidden = false; });
})();
