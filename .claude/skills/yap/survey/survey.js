'use strict';
let Q = [],
  i = 0,
  kitName = '';
const A = {},
  $ = (id) => document.getElementById(id);
const el = (t, c, txt) => {
  const e = document.createElement(t);
  if (c) e.className = c;
  if (txt != null) e.textContent = txt;
  return e;
};
const DEF = {
  trim: ['tight'],
  palette: ['#F1C92F', '#FFF4C1', '#FFFFFF'],
  font: ['Inter Tight'],
  accent: ['Instrument Serif:ital'],
  photos: ['cinematic', 'oldhollywood'],
  photoLook: ['frame'],
  gifs: ['silentcomedy', 'colormovie'],
  emoji: ['few'],
  captions: ['all-calm'],
  behind: ['some'],
  busy: ['medium'],
  sound: ['soft'],
};
const oid = (o) => (typeof o === 'string' ? o : o.id),
  fam = (f) => f.replace(':ital', '');
function fonts() {
  const s = new Set(['Inter Tight:wght@400;600', 'Instrument Serif:ital@1']);
  Q.filter((q) => q.type === 'font').forEach((q) =>
    q.options.forEach((f) => {
      if (!f.startsWith('Same')) s.add(fam(f) + (f.endsWith(':ital') ? ':ital@1' : ''));
    }),
  );
  $('gf').href =
    'https://fonts.googleapis.com/css2?' +
    [...s].map((f) => 'family=' + f.replaceAll(' ', '+')).join('&') +
    '&display=swap';
}
function pic(o) {
  if (o.icon || o.id === 'none') {
    const k = el('span', 'ic', o.icon || '🚫');
    return k;
  }
  const m = el('img');
  m.alt = '';
  m.loading = 'lazy';
  m.src = 'img/' + o.img + '.jpg';
  m.onerror = () => m.remove();
  if (!o.img.startsWith('gif_')) return m;
  const v = el('video');
  v.muted = v.loop = v.autoplay = v.playsInline = true;
  v.src = 'img/' + o.img + '.mp4';
  v.onerror = () => v.replaceWith(m);
  return v;
}
function inside(q, o, b) {
  const id = oid(o),
    t = el('span', 't');
  if (q.type === 'palette') {
    const w = el('span', 'sw');
    o.colors.forEach((c) => {
      const x = el('i');
      x.style.background = c;
      w.append(x);
    });
    b.append(w);
    t.append(el('b', '', o.name));
  } else if (q.type === 'font') {
    const f = el('span', 'f', 'I felt so lost');
    if (!id.startsWith('Same')) {
      f.style.fontFamily = `"${fam(id)}"`;
      if (id.endsWith(':ital')) f.style.fontStyle = 'italic';
    } else f.style.fontFamily = `"${fam((A.font || DEF.font)[0])}"`;
    b.append(f);
    t.append(el('small', '', fam(id) + (id.endsWith(':ital') ? ' Italic' : '')));
  } else if (q.type === 'cards') {
    b.append(pic(o));
    t.append(el('b', '', o.name), el('small', '', o.say));
  } else if (q.type === 'look') {
    const w = el('span', 'lk ' + id);
    w.append(el('i'));
    b.append(w);
    t.append(el('b', '', o.name));
  } else if (q.type === 'behind') {
    const w = el('span', 'st ' + id);
    w.append(el('i', 'p'), el('i', 'p p2'), el('i', 'h'));
    b.append(w);
    t.append(el('b', '', o.name));
  } else if (q.type === 'caption') {
    const w = el('span', 'cp' + (['center-line', 'center-box'].includes(id) ? ' slow' : '')),
      P = {
        'all-fast': [
          ['I felt', 14, 18, 0],
          ['so', 190, 46, 0.35],
          ['lost', 60, 88, 0.7, 1],
          ['today', 168, 112, 1.05],
        ],
        'all-calm': [
          ['I felt so', 18, 24, 0],
          ['lost', 120, 60, 0.8, 1],
        ],
        'center-word': [
          ['I', 0, 60, 0],
          ['felt', 0, 60, 1],
          ['lost', 0, 60, 2],
        ],
        'center-line': [['I felt so lost', 0, 60, 0]],
        'center-box': [['I felt so lost', 0, 56, 0]],
      }[id];
    P.forEach(([s, x, y, d, big]) => {
      const e = el('span');
      e.append(big ? el('em', '', s) : document.createTextNode(s));
      e.style.top = y + 'px';
      e.style.animationDelay = d + 's';
      if (id.startsWith('center')) {
        e.style.left = 0;
        e.style.right = 0;
        e.style.textAlign = 'center';
      } else e.style.left = x + 'px';
      if (id === 'center-box') {
        const k = el('span', '', s);
        e.textContent = '';
        k.style.cssText = 'position:static;opacity:1;animation:none;background:#FFF4C1;color:#1B1813;padding:6px 12px';
        e.append(k);
      }
      w.append(e);
    });
    b.append(w);
    t.append(el('b', '', o.name));
  } else {
    b.append(el('span', 'say', o.say));
    t.append(el('b', '', o.name));
  }
  b.append(t);
}
function chosen(q) {
  return A[q.id] || (q.type === 'palette' ? null : []);
}
function draw() {
  const q = Q[i],
    m = $('main');
  m.textContent = '';
  $('count').textContent = `Question ${i + 1} of ${Q.length}`;
  document.querySelector('#bar i').style.width = (100 * (i + 1)) / Q.length + '%';
  m.append(el('h1', '', q.q), el('p', '', q.help));
  const g = el(
    'div',
    'grid' + (['look', 'behind', 'caption', 'big'].includes(q.type) ? ' wide' : '') + (q.type === 'big' ? ' big' : ''),
  );
  const opts = q.options.concat(q.none ? [{ id: 'none', name: q.none, say: '', img: 'none' }] : []);
  opts.forEach((o) => {
    const b = el('button', 'o'),
      id = oid(o);
    b.type = 'button';
    b.dataset.id = id;
    inside(q, o, b);
    b.onclick = () => {
      if (q.type === 'palette') A.palette = o.colors.slice();
      else {
        let c = chosen(q).slice();
        if (q.pick === 1) c = [id];
        else if (c.includes(id)) c = c.filter((x) => x !== id);
        else if (id === 'none') c = ['none'];
        else {
          c = c.filter((x) => x !== 'none');
          if (c.length < q.pick) c.push(id);
        }
        A[q.id] = c;
      }
      mark();
    };
    g.append(b);
  });
  m.append(g);
  if (q.type === 'palette') {
    const w = el('div', 'own');
    w.append(el('b', '', 'Your own colors'));
    ['Primary', 'Secondary', 'Text'].forEach((n, k) => {
      const l = el('label', '', n),
        x = el('input'),
        h = el('input', 'hex');
      x.type = 'color';
      h.type = 'text';
      h.maxLength = 7;
      h.placeholder = '#F1C92F';
      h.setAttribute('aria-label', n + ' hex code');
      x.setAttribute('aria-label', n + ' color');
      const set = (v) => {
        A.palette = (A.palette || DEF.palette).slice();
        A.palette[k] = v.toUpperCase();
        mark();
      };
      x.oninput = () => set(x.value);
      h.oninput = () => {
        const v = (h.value.trim().startsWith('#') ? '' : '#') + h.value.trim();
        if (/^#[0-9a-f]{6}$/i.test(v)) set(v);
        h.classList.toggle('bad', h.value.trim() !== '' && !/^#[0-9a-f]{6}$/i.test(v));
      };
      l.append(x, h);
      w.append(l);
    });
    m.append(w);
    const u = el('div', 'own'),
      f = el('input'),
      st = el('span', '', '');
    f.type = 'file';
    f.id = 'kit';
    f.accept = '.png,.jpg,.jpeg,.webp,.pdf';
    const l = el('label', 'up', kitName ? 'Uploaded: ' + kitName : 'Upload my brand kit (PDF or picture)');
    l.htmlFor = 'kit';
    f.onchange = async () => {
      const x = f.files[0];
      if (!x) return;
      if (x.size > 15e6) {
        st.textContent = 'That file is over 15 MB.';
        return;
      }
      const r = await fetch('kit', { method: 'POST', body: x }).catch(() => null);
      if (r && r.ok) {
        kitName = x.name;
        l.textContent = 'Uploaded: ' + x.name;
        st.textContent = 'Claude will read your colors and fonts from it and show you before using them.';
        mark();
      } else st.textContent = 'Use a PDF, PNG, JPG or WEBP file.';
    };
    u.append(l, f, st);
    m.append(u);
  }
  $('back').style.visibility = i ? 'visible' : 'hidden';
  $('next').textContent = i === Q.length - 1 ? 'Save my style' : 'Next';
  mark();
  m.scrollTop = 0;
  m.focus();
}
function mark() {
  const q = Q[i],
    c = chosen(q);
  document.querySelectorAll('.o').forEach((b) => {
    const on =
      q.type === 'palette'
        ? !!c && JSON.stringify(q.options.find((o) => o.id === b.dataset.id).colors) === JSON.stringify(c)
        : c.includes(b.dataset.id);
    b.setAttribute('aria-pressed', on);
  });
  $('next').disabled = !(c && c.length);
  if (q.type === 'palette') {
    document.querySelectorAll('.own input[type=color]').forEach((x, k) => {
      if (c) x.value = c[k];
    });
    document.querySelectorAll('.own .hex').forEach((x, k) => {
      if (c && document.activeElement !== x) {
        x.value = c[k];
        x.classList.remove('bad');
      }
    });
    if (kitName && !c) $('next').disabled = false;
  }
}
async function save() {
  $('next').disabled = true;
  const r = await fetch('save', { method: 'POST', body: JSON.stringify(A) }).catch(() => null),
    m = $('main');
  if (r && r.ok) {
    m.textContent = '';
    m.append(
      el('h1', '', 'Saved. You’re done here.'),
      el('p', '', 'Close this page and go back to Claude. Then put your video in the raw folder.'),
    );
    document.querySelector('footer').remove();
    $('count').textContent = '';
  } else {
    m.prepend(el('p', '', 'That did not save. Go back to Claude and type: open the survey again'));
    $('next').disabled = false;
  }
}
$('next').onclick = () => {
  if (Q[i].type === 'palette' && !A.palette) A.palette = DEF.palette.slice();
  if (i < Q.length - 1) {
    i++;
    draw();
  } else save();
};
$('back').onclick = () => {
  if (i) {
    i--;
    draw();
  }
};
$('skip').onclick = () => {
  A[Q[i].id] = DEF[Q[i].id].slice();
  $('next').onclick();
};
fetch('q.json')
  .then((r) => r.json())
  .then((d) => {
    Q = d;
    fonts();
    draw();
  });
