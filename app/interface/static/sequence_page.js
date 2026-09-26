// Behaviour for the exported call-sequence page.
//
// Inlined verbatim into the document, so it must work with no network
// and no server. Everything it surfaces is already in the evidence table
// as plain HTML: with scripts off a reader loses convenience, never
// information.
//
// Nothing here builds markup by concatenating strings. Symbol names and
// file paths come from whatever repository was ingested, and a file
// named with an HTML tag would otherwise run as script in a document
// people are meant to be able to open and share. Everything goes in as
// text.
(function () {
  var svg = document.querySelector('svg.rp-seq');
  var detail = document.getElementById('detail');
  if (!svg || !detail) return;

  var messages = Array.prototype.slice.call(svg.querySelectorAll('.rp-msg'));
  var hits = Array.prototype.slice.call(svg.querySelectorAll('.rp-msg-hit'));
  var repo = svg.getAttribute('data-repo') || '';
  var sha = svg.getAttribute('data-sha') || '';

  function node(tag, className, text) {
    var el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined && text !== null) el.textContent = text;
    return el;
  }
  function sep(text) { return document.createTextNode(text); }

  function clear() {
    messages.forEach(function (m) { m.classList.remove('lit-msg', 'dim'); });
  }

  function focusMessage(hit) {
    clear();
    var caller = hit.getAttribute('data-caller');
    var callee = hit.getAttribute('data-symbol');
    var from = hit.getAttribute('data-from');
    var line = hit.getAttribute('data-line');
    var depth = hit.getAttribute('data-depth');

    var key = caller + '\u0000' + line;
    messages.forEach(function (m) {
      var mine = m.getAttribute('data-caller') + '\u0000' + m.getAttribute('data-line');
      m.classList.add(mine === key ? 'lit-msg' : 'dim');
    });

    detail.textContent = '';
    detail.appendChild(node('strong', 'mono', caller + '()'));
    detail.appendChild(sep(' calls '));
    detail.appendChild(node('strong', 'mono', callee + '()'));
    detail.appendChild(sep(' · '));

    var where = from + ':' + line;
    if (repo && sha) {
      var a = node('a', 'mono', where);
      // Assembled with encodeURIComponent per segment so a path can
      // never close the attribute or introduce another scheme.
      a.href = 'https://github.com/' + repo + '/blob/' + sha + '/'
        + from.split('/').map(encodeURIComponent).join('/') + '#L' + encodeURIComponent(line);
      a.target = '_blank';
      a.rel = 'noreferrer noopener';
      detail.appendChild(a);
    } else {
      detail.appendChild(node('span', 'mono', where));
    }
    if (depth && depth !== '0') {
      detail.appendChild(sep(' · '));
      detail.appendChild(node('span', 'note', depth + ' level(s) deep'));
    }
  }

  hits.forEach(function (hit) {
    function activate(ev) { ev.stopPropagation(); focusMessage(hit); }
    hit.addEventListener('click', activate);
    hit.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); activate(ev); }
    });
  });

  svg.addEventListener('click', function () { clear(); detail.textContent = ''; });
})();
