/* 截图排版编辑器 ———————————————————————————————————
   依赖宿主页面已有的 toast() / poll() / showProgress() / doneProgress()。
   坐标系统统一为「页面归一化比例」，跟服务端 layout.py 用同一套数学，
   保证手机上摆的位置和打出来的位置完全一致。
   ——————————————————————————————————————————————————— */
const ED = {
  open: false, tab: 'mat',
  pages: [], p: 0, sel: -1,
  mats: [],
  canvas: { w: 1600, h: 2290, aspect: 0.7, mmW: 204, mmH: 291 },
  gap: 5,
  mode: 'auto',
  grid: [2, 2],
  orientation: 'portrait',
  opts: {},
  autoTrim: false,
  capPageNum: true,
  cropMode: false,
  trimCache: {},
};

const $E = s => document.querySelector(s);
const clampE = (v, a, b) => Math.max(a, Math.min(b, v));

/* ---------- 几何 ---------- */
function contentSize(it) {
  const H = 1 / (it.ar || 1);
  return {
    cw: Math.max(0.02, 1 - it.crop.l - it.crop.r),
    ch: Math.max(0.02, (1 - it.crop.t - it.crop.b) * H),
  };
}
function rotSize(it) {
  const { cw, ch } = contentSize(it);
  const rad = (it.rot || 0) * Math.PI / 180;
  const ca = Math.abs(Math.cos(rad)), sa = Math.abs(Math.sin(rad));
  return { rw: cw * ca + ch * sa, rh: cw * sa + ch * ca };
}
/* 标题横条高度，必须和服务端 layout._draw_caption 完全一致 */
function capBand(it, slotH, pageH) {
  if (!it.caption) return 0;
  let band = Math.min(Math.floor(slotH * 0.26), Math.floor(pageH * 0.042));
  band = Math.max(band, Math.floor(pageH * 0.018));
  return band < 8 ? 0 : band;
}

/* ---------- 打开 / 关闭 ---------- */
async function EDopen() {
  const _S = window.S || (typeof S !== 'undefined' ? S : null);
  const jobs = (_S && _S.jobs) || [];
  if (!jobs.length) { toast('先选几张图片再排版'); return; }
  ED.opts = Object.assign({}, (_S && _S.opts) || {});
  ED.orientation = (ED.opts.orientation === 'landscape') ? 'landscape' : 'portrait';

  $E('#edWrap').hidden = false;
  document.body.style.overflow = 'hidden';
  ED.open = true;
  ED.mats = [];
  ED.trimCache = {};
  ED.sel = -1;

  // 素材列表：把每个任务的每一页都摊平成一个可排版对象
  const queue = [];
  for (const j of jobs) {
    const n = Math.max(1, Math.min(j.pages || 1, 24));
    for (let i = 0; i < n && queue.length < 24; i++) queue.push({ jid: j.id, idx: i, name: j.name });
  }
  toast('正在读取素材…', 6000);
  await Promise.all(queue.map(async q => {
    const url = `/api/source/${q.jid}/${q.idx}?w=760&_=${Date.now()}`;
    try {
      const img = await new Promise((res, rej) => {
        const im = new Image();
        im.onload = () => res(im); im.onerror = () => rej(new Error('load'));
        im.src = url;
      });
      q.img = img; q.ar = img.naturalWidth / img.naturalHeight; q.url = url;
      ED.mats.push(q);
    } catch (e) { /* 读不出来就跳过 */ }
  }));
  if (!ED.mats.length) { toast('没有可排版的图片'); EDclose(); return; }

  await EDfetchCanvas();
  EDrelayout(true);
  EDrender();
  EDtab('mat');
}
function EDclose() {
  $E('#edWrap').hidden = true;
  document.body.style.overflow = '';
  ED.open = false;
}
async function EDfetchCanvas() {
  const q = ED.opts.paper || 'A4';
  const o = ED.orientation;
  const qt = ED.opts.quality || 'standard';
  try {
    const r = await fetch(`/api/canvas?paper=${encodeURIComponent(q)}&orientation=${o}&quality=${qt}`).then(x => x.json());
    if (r && r.w) {
      ED.canvas = { w: r.w, h: r.h, aspect: r.aspect, mmW: r.mm[0], mmH: r.mm[1] };
    }
  } catch (e) { }
}

function mkItem(m, r, caption) {
  return {
    jid: m.jid, idx: m.idx, ar: m.ar,
    x: r.x, y: r.y, w: r.w, h: r.h,
    rot: 0,
    crop: { t: 0, r: 0, b: 0, l: 0 },
    caption: caption || '',
  };
}

/* ---------- 自动排版 ---------- */
const GRID_TPL = [[1, 1], [2, 1], [3, 1], [2, 2], [3, 2], [4, 1], [2, 3]];

function gapFrac(mmW, mmH) {
  return { gw: ED.gap / (mmW || ED.canvas.mmW), gh: ED.gap / (mmH || ED.canvas.mmH) };
}
function gridRects(cols, rows, mmW, mmH) {
  const { gw, gh } = gapFrac(mmW, mmH);
  const cw = (1 - (cols + 1) * gw) / cols;
  const ch = (1 - (rows + 1) * gh) / rows;
  const out = [];
  for (let r = 0; r < rows; r++)
    for (let c = 0; c < cols; c++)
      out.push({ x: gw + c * (cw + gw), y: gh + r * (ch + gh), w: cw, h: ch });
  return out;
}
/* 候选网格在特定画布下的评分：返回页数与每张图落到纸上的最小边长(mm) */
function evalGrid(pieces, cols, rows, canvasW, canvasH, mmW, mmH) {
  const per = cols * rows;
  if (per > 9) return null;
  const rects = gridRects(cols, rows, mmW, mmH);
  let minSide = Infinity, ok = true;
  for (let i = 0; i < pieces.length && ok; i++) {
    const r = rects[i % per];
    const cellW = r.w * mmW, cellH = r.h * mmH;
    const cellA = (r.w * canvasW) / (r.h * canvasH);
    const { rw, rh } = rotSize(pieces[i]);
    const contA = rw / rh;
    let fW, fH;
    if (contA > cellA) { fW = cellW; fH = cellW / contA; }
    else { fH = cellH; fW = cellH * contA; }
    const side = Math.min(fW, fH);
    if (side < 52) ok = false;          // 再小就糊了，舍不得这么排
    if (side < minSide) minSide = side;
  }
  if (!ok) return null;
  return { cols, rows, per, pages: Math.ceil(pieces.length / per), minSide };
}

function EDrelayout(silent) {
  // 1) 展开成「可排版的块」：长图如果被要求分页，就切成好几块
  const pieces = [];
  const longMode = ED.mode === 'long';
  for (const m of ED.mats) {
    const base = { jid: m.jid, idx: m.idx, ar: m.ar, rot: 0, crop: { t: 0, r: 0, b: 0, l: 0 }, caption: '' };
    if (ED.autoTrim && ED.trimCache[m.jid + ':' + m.idx]) {
      const d = ED.trimCache[m.jid + ':' + m.idx];
      base.crop = { t: d.t, r: d.r, b: d.b, l: d.l };
    }
    if (longMode) {
      const srcOf = { ar: m.ar, rot: 0, crop: base.crop };
      const n = EDpieceCount(srcOf);
      pieces.push(...EDslice(srcOf, n));
    } else pieces.push(base);
  }

  // 2) 选网格
  if (ED.mode === 'grid') {
    ED.pages = EDfillGrid(pieces, ED.grid || [2, 2]);
  } else if (ED.mode === 'long') {
    ED.pages = pieces.map(p => ({ items: [Object.assign(p, { x: 0.02, y: 0.02, w: 0.96, h: 0.96 })] }));
  } else {
    const best = EDpickAuto(pieces);
    ED.pages = EDfillGrid(pieces, [best.cols, best.rows]);
  }
  ED.p = 0; ED.sel = -1;
  if (!silent) { EDrender(); }
}

/* 长图该切几块：源图铺满纸宽时，需要几页才装得下 */
function EDpieceCount(src) {
  const cfw = Math.max(0.02, 1 - src.crop.l - src.crop.r);
  const cfh = Math.max(0.02, 1 - src.crop.t - src.crop.b);
  const AR = (src.ar || 1) * (cfw / cfh);   // 裁完之后的真实宽高比
  const pa = ED.canvas.aspect;
  // 铺满纸宽时，需要几页才装得下
  return Math.max(1, Math.ceil(pa / Math.max(1e-6, AR)));
}
function EDslice(src, n) {
  if (n <= 1) return [Object.assign({}, src)];
  const out = [];
  const top0 = src.crop.t, bot0 = src.crop.b;
  const remain = Math.max(0.02, 1 - top0 - bot0);
  for (let k = 0; k < n; k++) {
    const t = top0 + remain * (k / n);
    const b = 1 - (top0 + remain * ((k + 1) / n));
    out.push(Object.assign({}, src, { crop: { t, r: src.crop.r, b: Math.max(0, b), l: src.crop.l } }));
  }
  return out;
}

function EDpickAuto(pieces) {
  // 竖版 / 横版都试一遍，挑页数最少的；页数一样就挑图更大的
  const cands = [];
  const configs = [
    { o: 'portrait', w: ED.canvas.w, h: ED.canvas.h, mmW: ED.canvas.mmW, mmH: ED.canvas.mmH },
    { o: 'landscape', w: ED.canvas.h, h: ED.canvas.w, mmW: ED.canvas.mmH, mmH: ED.canvas.mmW },
  ];
  for (const cfg of configs) {
    for (const [c, r] of GRID_TPL) {
      const e = evalGrid(pieces, c, r, cfg.w, cfg.h, cfg.mmW, cfg.mmH);
      if (e) cands.push(Object.assign(e, { o: cfg.o }));
    }
  }
  if (!cands.length) return { cols: 1, rows: 1, o: ED.orientation };
  cands.sort((a, b) => (a.pages - b.pages) || (b.minSide - a.minSide));
  const best = cands[0];
  if (best.o !== ED.orientation) {
    // 同步把画布尺寸换过来，不能异步——否则下面排版还在用旧方向的比例
    ED.orientation = best.o;
    const w = ED.canvas.w, h = ED.canvas.h, mw = ED.canvas.mmW, mh = ED.canvas.mmH;
    ED.canvas.w = h; ED.canvas.h = w;
    ED.canvas.mmW = mh; ED.canvas.mmH = mw;
    ED.canvas.aspect = ED.canvas.w / ED.canvas.h;
    if (typeof toast === 'function') toast('自动转成了横向，这样更省纸');
  }
  return best;
}

function EDfillGrid(pieces, wh) {
  const cols = wh[0], rows = wh[1];
  const rects = gridRects(cols, rows);
  const per = cols * rows;
  const pages = [];
  const totalP = Math.ceil(pieces.length / per);
  for (let i = 0; i < pieces.length; i += per) {
    const chunk = pieces.slice(i, i + per);
    const pageNo = Math.floor(i / per) + 1;
    pages.push({
      items: chunk.map((o, k) => {
        const it = Object.assign({}, o, {
          x: rects[k].x, y: rects[k].y, w: rects[k].w, h: rects[k].h,
          caption: '',
        });
        if (ED.capPageNum && totalP > 1 && ED.mode === 'long') it.caption = `第 ${pageNo}/${totalP} 页`;
        return it;
      }),
    });
  }
  if (!pages.length) pages.push({ items: [] });
  return pages;
}

/* ---------- 渲染 ---------- */
function EDrender() {
  const page = ED.pages[ED.p] || { items: [] };
  const el = $E('#edPage');
  el.style.aspectRatio = `${ED.canvas.w} / ${ED.canvas.h}`;
  const box = $E('#edItems');
  box.innerHTML = '';
  page.items.forEach((it, i) => {
    const d = document.createElement('div');
    d.className = 'it';
    d.dataset.i = i;
    d.innerHTML = `<div class="rot"><img src="/api/source/${it.jid}/${it.idx}?w=760" draggable="false"></div>
      <div class="cap"></div>
      <div class="hd br" data-h="br"></div>
      <div class="hd2 top" data-h="ct"></div><div class="hd2 bot" data-h="cb"></div>
      <div class="hd2 left" data-h="cl"></div><div class="hd2 right" data-h="cr"></div>`;
    box.appendChild(d);
    EDpaintItem(d, it);
  });
  EDselect(ED.sel);
  EDmeta();
  EDrenderPanel();
}
function EDmeta() {
  const n = ED.pages.length;
  const cnt = ED.pages.reduce((a, p) => a + p.items.length, 0);
  $E('#edMeta').textContent = `第 ${ED.p + 1}/${n} 页 · ${cnt} 张`;
  $E('#edPages').textContent = n ? `${n} 页` : '';
}
function EDpaintItem(d, it) {
  const host = d.parentElement;
  const pw = host.clientWidth, ph = host.clientHeight;
  if (!pw || !ph) return;
  d.style.left = (it.x * pw) + 'px';
  d.style.top = (it.y * ph) + 'px';
  d.style.width = (it.w * pw) + 'px';
  d.style.height = (it.h * ph) + 'px';
  const slotW = it.w * pw, slotH = it.h * ph;
  const band = capBand(it, slotH, ph);
  const availH = Math.max(2, slotH - band);
  const { rw, rh } = rotSize(it);
  const { cw, ch } = contentSize(it);
  const s = Math.min(slotW / Math.max(rw, 1e-6), availH / Math.max(rh, 1e-6));
  const rot = d.querySelector('.rot');
  rot.style.width = (cw * s) + 'px';
  rot.style.height = (ch * s) + 'px';
  rot.style.left = '50%';
  rot.style.top = (availH / 2) + 'px';
  rot.style.transform = `translate(-50%,-50%) rotate(${it.rot || 0}deg)`;
  const img = rot.querySelector('img');
  const H = 1 / (it.ar || 1);
  img.style.width = s + 'px';
  img.style.height = (H * s) + 'px';
  img.style.left = (-(it.crop.l || 0) * s) + 'px';
  img.style.top = (-(it.crop.t || 0) * H * s) + 'px';
  const cap = d.querySelector('.cap');
  if (it.caption && band > 0) {
    cap.style.display = 'block';
    cap.textContent = it.caption;
    cap.style.height = band + 'px';
    cap.style.fontSize = Math.max(7, Math.round(band * 0.62)) + 'px';
    cap.style.lineHeight = Math.max(9, band - 2) + 'px';
  } else cap.style.display = 'none';
}
function EDrepaintSel() {
  const items = $E('#edItems').children;
  const cur = (ED.pages[ED.p] || { items: [] }).items;
  for (const d of items) {
    const i = +d.dataset.i;
    EDpaintItem(d, cur[i]);
  }
}

/* ---------- 选中 ---------- */
function EDselect(i) {
  ED.sel = i;
  const items = $E('#edItems').children;
  const cur = (ED.pages[ED.p] || { items: [] }).items;
  for (const d of items) {
    const k = +d.dataset.i;
    d.classList.toggle('sel', k === i);
    d.classList.toggle('cropmode', k === i && ED.cropMode);
  }
  EDrenderPanel();
}
function curItems() { return (ED.pages[ED.p] || { items: [] }).items; }
function selItem() { const c = curItems(); return ED.sel >= 0 && ED.sel < c.length ? c[ED.sel] : null; }

/* ---------- 触摸交互 ---------- */
const ptrs = new Map();
let drag = null;
let pinch = null;

/* 设成指定宽度，高度按内容比例跟上：盒子在页面坐标里保持内容的宽高比。
   页面宽高比 = canvas.w/canvas.h，别用反了。 */
function EDscaleTo(it, wf) {
  const { rw, rh } = rotSize(it);
  const arBox = rw / rh;
  const pageAR = ED.canvas.w / ED.canvas.h;
  const h = clampE(wf * pageAR / arBox, 0.02, 1.05);
  it.w = clampE(h * arBox / pageAR, 0.02, 1.05);
  it.h = h;
}

function EDstageDown(ev) {
  ptrs.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
  if (ptrs.size >= 2) {
    const it0 = selItem();
    if (it0) {
      const [a, b] = [...ptrs.values()];
      pinch = {
        d0: Math.hypot(a.x - b.x, a.y - b.y) || 1,
        w0: it0.w, h0: it0.h, x0: it0.x, y0: it0.y,
      };
    }
    drag = null;
    return;
  }
  const t = ev.target.closest('.it');
  if (!t) { EDselect(-1); return; }
  const i = +t.dataset.i;
  EDselect(i);
  const it = curItems()[i];
  if (!it) return;
  const h = ev.target.dataset && ev.target.dataset.h;
  drag = {
    type: h ? (h === 'br' ? 'resize' : 'crop') : 'move',
    h: h || '', i,
    sx: ev.clientX, sy: ev.clientY,
    x0: it.x, y0: it.y, w0: it.w, h0: it.h,
    c0: { t: it.crop.t, r: it.crop.r, b: it.crop.b, l: it.crop.l },
  };
  try { ev.target.setPointerCapture(ev.pointerId); } catch (e) { }
  ev.preventDefault();
}

function EDstageMove(ev) {
  if (ptrs.has(ev.pointerId)) ptrs.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
  const host = $E('#edItems');
  const pw = host.clientWidth, ph = host.clientHeight;
  if (!pw || !ph) return;

  if (pinch && ptrs.size >= 2) {
    const [a, b] = [...ptrs.values()];
    const d = Math.hypot(a.x - b.x, a.y - b.y);
    const it = selItem(); if (!it) return;
    EDscaleTo(it, pinch.w0 * clampE(d / pinch.d0, 0.15, 6));
    const cx = pinch.x0 + pinch.w0 / 2, cy = pinch.y0 + pinch.h0 / 2;
    it.x = clampE(cx - it.w / 2, -0.08, 0.99);
    it.y = clampE(cy - it.h / 2, -0.08, 0.99);
    EDrepaintSel();
    return;
  }
  if (!drag) return;

  const dx = (ev.clientX - drag.sx) / pw;
  const dy = (ev.clientY - drag.sy) / ph;
  const it = curItems()[drag.i]; if (!it) return;
  if (drag.type === 'move') {
    it.x = clampE(drag.x0 + dx, -0.08, 0.99);
    it.y = clampE(drag.y0 + dy, -0.08, 0.99);
  } else if (drag.type === 'resize') {
    EDscaleTo(it, drag.w0 + dx);
  } else if (drag.type === 'crop') {
    const which = drag.h.slice(1);
    if (which === 't') it.crop.t = clampE(drag.c0.t + dy, 0, 0.45);
    if (which === 'b') it.crop.b = clampE(drag.c0.b - dy, 0, 0.45);
    if (which === 'l') it.crop.l = clampE(drag.c0.l + dx, 0, 0.45);
    if (which === 'r') it.crop.r = clampE(drag.c0.r - dx, 0, 0.45);
    if (it.crop.t + it.crop.b > 0.88) { it.crop.t = drag.c0.t; it.crop.b = drag.c0.b; }
    if (it.crop.l + it.crop.r > 0.88) { it.crop.l = drag.c0.l; it.crop.r = drag.c0.r; }
  }
  EDrepaintSel();
}

function EDstageUp(ev) {
  ptrs.delete(ev.pointerId);
  if (ptrs.size < 2) pinch = null;
  if (ptrs.size === 0) drag = null;
  if (ED.open) EDmeta();
}

/* ---------- 面板 ---------- */
function EDtab(t) {
  ED.tab = t;
  document.querySelectorAll('.edtab').forEach(e => e.classList.toggle('on', e.dataset.tab === t));
  EDrenderPanel();
}
function EDrenderPanel() {
  const box = $E('#edPanel');
  const it = selItem();
  if (ED.tab === 'mat') {
    box.innerHTML = `<div class="lab">点一下加进当前页</div>
      <div class="matstrip">${ED.mats.map((m, i) => `
        <div class="mwrap" onclick="EDadd(${i})">
          <img src="${m.url}" alt="">
          <span class="badge">${m.ar < 0.6 ? '长图' : '图'}</span>
        </div>`).join('')}</div>
      <div class="hintline">共 ${ED.mats.length} 张可用素材。长图建议切成多页排，别硬塞。</div>`;
    return;
  }
  if (ED.tab === 'lay') {
    const tplRow = [
      { k: 'auto', n: '智能' },
      { k: 'long', n: '长图分页' },
      { k: 'g1', n: '1格' }, { k: 'g2', n: '2格' },
      { k: 'g4', n: '4格' }, { k: 'g6', n: '6格' }, { k: 'g9', n: '9格' },
    ];
    box.innerHTML = `<div class="lab">排版方式</div>
      <div class="chips">${tplRow.map(t => `<div class="chip ${EDchipOn(t.k)}" onclick="EDmode('${t.k}')">${t.n}</div>`).join('')}</div>
      <div class="lab">图间距 ${ED.gap}mm</div>
      <div class="slider"><input type="range" min="0" max="14" step="1" value="${ED.gap}"
        oninput="EDgap(this.value)"><span class="val">${ED.gap}mm</span></div>
      <div class="lab">页面</div>
      <div class="chips">
        <div class="chip" onclick="EDaddPage()">＋ 加一页</div>
        <div class="chip" onclick="EDdelPage()">删除本页</div>
        <div class="chip ${ED.orientation === 'landscape' ? 'on' : ''}" onclick="EDflip()">横向纸张</div>
        <div class="chip ${ED.capPageNum ? 'on' : ''}" onclick="EDtoggleCap()">标注页码</div>
      </div>
      <div class="hintline">「智能」会按少用纸、图又看得清这两个条件自动挑方案，必要时自动转横向。</div>`;
    return;
  }
  if (ED.tab === 'proc') {
    box.innerHTML = `<div class="lab">自动处理</div>
      <div class="chips">
        <div class="chip" onclick="EDautoTrimAll()">去白边（全部）</div>
        <div class="chip ${ED.autoTrim ? 'on' : ''}" onclick="EDtoggleTrim()">去白边（新排）</div>
        <div class="chip" onclick="EDcropBar('sel')">裁状态栏（选中）</div>
        <div class="chip" onclick="EDcropBar('all')">裁状态栏（全部）</div>
      </div>
      <div class="lab">选中图片的标题</div>
      <input class="edinput" id="edCapInput" placeholder="${it ? '给这张图写个说明' : '先选中一张图'}"
             value="${it ? escapeHtml(it.caption || '') : ''}" ${it ? '' : 'disabled'}
             oninput="EDsetCap(this.value)">
      <div class="hintline">标题会打印在图片下方。多行分页时自动带上「第 x/N 页」。</div>`;
    return;
  }
  // 选中项
  if (!it) { box.innerHTML = `<div class="stat" style="padding:14px 2px">在画布上点一张图，这里就能调它。</div>`; return; }
  box.innerHTML = `<div class="lab">这一张</div>
    <div class="chips">
      <div class="chip" onclick="EDrot(-90)">⟲ 左转</div>
      <div class="chip" onclick="EDrot(90)">右转 ⟳</div>
      <div class="chip ${ED.cropMode ? 'on' : ''}" onclick="EDtoggleCropMode()">裁剪${ED.cropMode ? '（拖动四边）' : ''}</div>
      <div class="chip" onclick="EDtighten()">贴合内容</div>
      <div class="chip" onclick="EDdel()">删除</div>
    </div>
    <div class="lab">旋转 ${Math.round(it.rot || 0)}°</div>
    <div class="slider"><input type="range" min="-180" max="180" step="1" value="${Math.round(it.rot || 0)}"
      oninput="EDrotSet(this.value)"><span class="val">${Math.round(it.rot || 0)}°</span></div>
    <div class="lab">缩放 ${Math.round(it.w * 100)}%</div>
    <div class="slider"><input type="range" min="5" max="100" step="1" value="${Math.round(it.w * 100)}"
      oninput="EDscale(this.value)"><span class="val">${Math.round(it.w * 100)}%</span></div>
    <div class="lab">裁四边（%）</div>
    ${['t:上', 'r:右', 'b:下', 'l:左'].map(s => {
      const k = s.split(':')[0], n = s.split(':')[1];
      return `<div class="slider"><span class="stat" style="min-width:22px">${n}</span>
        <input type="range" min="0" max="45" step="0.5" value="${(it.crop[k] * 100).toFixed(1)}"
          oninput="EDcrop1('${k}',this.value/100)"><span class="val">${(it.crop[k] * 100).toFixed(0)}%</span></div>`;
    }).join('')}
    <div class="hintline">拖动图片移动位置，拖右下角缩放，开「裁剪」后拖四条边。</div>`;
}

/* ---------- 各种动作 ---------- */
function EDchipOn(k) {
  if (k === 'auto') return ED.mode === 'auto';
  if (k === 'long') return ED.mode === 'long';
  if (k[0] === 'g') {
    if (ED.mode !== 'grid') return false;
    const n = +k.slice(1);
    return (ED.grid || [1, 1])[0] * (ED.grid || [1, 1])[1] === n;
  }
  return false;
}
function EDmode(k) {
  if (k === 'auto') ED.mode = 'auto';
  else if (k === 'long') ED.mode = 'long';
  else {
    ED.mode = 'grid';
    const n = +k.slice(1);
    ED.grid = { 1: [1, 1], 2: [2, 1], 4: [2, 2], 6: [3, 2], 9: [3, 3] }[n] || [2, 2];
  }
  EDrelayout();
}
function EDgap(v) { ED.gap = +v; EDrelayout(); }
function EDflip() {
  ED.orientation = ED.orientation === 'landscape' ? 'portrait' : 'landscape';
  EDfetchCanvas().then(() => { EDrelayout(); });
}
function EDtoggleCap() { ED.capPageNum = !ED.capPageNum; EDrelayout(); }
function EDaddPage() { ED.pages.push({ items: [] }); ED.p = ED.pages.length - 1; ED.sel = -1; EDrender(); }
function EDdelPage() {
  if (ED.pages.length <= 1) { toast('至少留一页'); return; }
  ED.pages.splice(ED.p, 1);
  ED.p = Math.max(0, ED.p - 1); ED.sel = -1; EDrender();
}
function EDprev() { ED.p = Math.max(0, ED.p - 1); ED.sel = -1; EDrender(); }
function EDnext() { ED.p = Math.min(ED.pages.length - 1, ED.p + 1); ED.sel = -1; EDrender(); }
function EDadd(mi) {
  const m = ED.mats[mi]; if (!m) return;
  const page = ED.pages[ED.p];
  const item = mkItem(m, { x: 0.18, y: 0.18, w: 0.64, h: 0.5 }, '');
  EDtightenItem(item);
  page.items.push(item);
  EDrender(); EDselect(page.items.length - 1);
  toast('已加入，可拖动调整');
}

function EDtightenItem(it) { EDscaleTo(it, it.w); }
function EDtighten() { const it = selItem(); if (!it) return; EDtightenItem(it); EDrepaintSel(); EDrenderPanel(); }
function EDrot(d) { const it = selItem(); if (!it) return; it.rot = (it.rot || 0) + d; EDtightenItem(it); EDrepaintSel(); EDrenderPanel(); }
function EDrotSet(v) { const it = selItem(); if (!it) return; it.rot = +v; EDrepaintSel(); }
function EDscale(v) {
  const it = selItem(); if (!it) return;
  EDscaleTo(it, +v / 100);
  EDrepaintSel();
}
function EDcrop1(k, v) {
  const it = selItem(); if (!it) return;
  it.crop[k] = clampE(v, 0, 0.45);
  if (it.crop.t + it.crop.b > 0.88) it.crop.t = it.crop.b = 0.4;
  if (it.crop.l + it.crop.r > 0.88) it.crop.l = it.crop.r = 0.4;
  EDrepaintSel();
}
function EDsetCap(v) { const it = selItem(); if (!it) return; it.caption = v; EDrepaintSel(); }
function EDdel() {
  const it = selItem(); if (!it) return;
  curItems().splice(ED.sel, 1);
  ED.sel = -1; EDrender();
}
function EDtoggleCropMode() { ED.cropMode = !ED.cropMode; EDselect(ED.sel); }
function EDtoggleTrim() { ED.autoTrim = !ED.autoTrim; EDrelayout(); }

/* ---------- 去白边 ---------- */
function detectBorder(m) {
  const img = m.img;
  const S = 320;
  const sc = Math.min(1, S / Math.max(img.naturalWidth, img.naturalHeight));
  const w = Math.max(2, Math.round(img.naturalWidth * sc));
  const h = Math.max(2, Math.round(img.naturalHeight * sc));
  const cv = document.createElement('canvas');
  cv.width = w; cv.height = h;
  const cx = cv.getContext('2d', { willReadFrequently: true });
  cx.drawImage(img, 0, 0, w, h);
  const d = cx.getImageData(0, 0, w, h).data;
  const px = (x, y) => { const o = (y * w + x) * 4; return [d[o], d[o + 1], d[o + 2]]; };
  // 背景色取四角采样中值
  const samples = [px(1, 1), px(w - 2, 1), px(1, h - 2), px(w - 2, h - 2),
  px(Math.floor(w / 2), 1), px(Math.floor(w / 2), h - 2)];
  const bg = [0, 1, 2].map(c => Math.round(samples.reduce((a, s) => a + s[c], 0) / samples.length));
  const TOL = 22;
  const isBg = (x, y) => { const p = px(x, y); return Math.abs(p[0] - bg[0]) + Math.abs(p[1] - bg[1]) + Math.abs(p[2] - bg[2]) < TOL * 3; };
  const rowClean = y => {
    let bad = 0;
    for (let x = 0; x < w; x += 2) if (!isBg(x, y)) { bad++; if (bad > w * 0.012) return false; }
    return true;
  };
  const colClean = x => {
    let bad = 0;
    for (let y = 0; y < h; y += 2) if (!isBg(x, y)) { bad++; if (bad > h * 0.012) return false; }
    return true;
  };
  let top = 0, bot = 0, left = 0, right = 0;
  while (top < h * 0.45 && rowClean(Math.round(top))) top++;
  while (bot < h * 0.45 && rowClean(Math.round(h - 1 - bot))) bot++;
  while (left < w * 0.45 && colClean(Math.round(left))) left++;
  while (right < w * 0.45 && colClean(Math.round(w - 1 - right))) right++;
  return { t: top / h, b: bot / h, l: left / w, r: right / w };
}
function EDautoTrimAll() {
  let n = 0;
  for (const m of ED.mats) {
    const key = m.jid + ':' + m.idx;
    let d = ED.trimCache[key];
    if (!d) { try { d = detectBorder(m); ED.trimCache[key] = d; } catch (e) { continue; } }
    if (d.t + d.b + d.l + d.r < 0.002) continue;
    let changed = false;
    for (const page of ED.pages) for (const it of page.items) {
      if (it.jid === m.jid && it.idx === m.idx) {
        it.crop.t = Math.max(it.crop.t, d.t); it.crop.b = Math.max(it.crop.b, d.b);
        it.crop.l = Math.max(it.crop.l, d.l); it.crop.r = Math.max(it.crop.r, d.r);
        changed = true;
      }
    }
    if (changed) n++;
  }
  EDrepaintSel();
  toast(n ? `已给 ${n} 张图去掉白边` : '没检测到明显白边');
}
/* 状态栏：不闷头瞎裁。按「状态栏高度约为屏宽的 6.5%」估算，
   换算成占图高的比例后应用，并且一定留给用户手动微调的入口。 */
function EDcropBar(scope) {
  const targets = [];
  if (scope === 'all') ED.pages.forEach(p => p.items.forEach(it => targets.push(it)));
  else { const s = selItem(); if (s) targets.push(s); }
  if (!targets.length) { toast(scope === 'all' ? '版面里还没有图' : '先选一张图'); return; }
  let n = 0, sample = 0;
  for (const it of targets) {
    const mat = ED.mats.find(m => m.jid === it.jid && m.idx === it.idx);
    if (!mat) continue;
    const key = mat.jid + ':' + mat.idx;
    let d = ED.trimCache[key];
    if (!d) { try { d = detectBorder(mat); ED.trimCache[key] = d; } catch (e) { d = null; } }
    // 从「去完白边」的位置再往下量，避免把白边算进状态栏
    const base = d ? Math.max(it.crop.t, d.t) : it.crop.t;
    const room = Math.max(0.02, 1 - base - it.crop.b);
    const add = Math.min(0.065 * Math.max(0.2, mat.ar || 0.5), 0.06, room * 0.2);
    const nt = clampE(base + add, it.crop.t, 0.45);
    if (nt > it.crop.t) { it.crop.t = nt; n++; sample = add; }
  }
  EDrepaintSel();
  toast(n ? `已裁掉顶部约 ${(sample * 100).toFixed(1)}%，可在「选中」里微调` : '顶部已经没有可裁的了');
}

/* ---------- 输出 ---------- */
function EDbuildSpec() {
  return {
    pages: ED.pages.map(p => ({
      items: p.items.map(it => ({
        jid: it.jid, idx: it.idx,
        x: +it.x.toFixed(5), y: +it.y.toFixed(5), w: +it.w.toFixed(5), h: +it.h.toFixed(5),
        rot: +(it.rot || 0).toFixed(2),
        crop: {
          t: +it.crop.t.toFixed(5), r: +it.crop.r.toFixed(5),
          b: +it.crop.b.toFixed(5), l: +it.crop.l.toFixed(5),
        },
        caption: (it.caption || '').slice(0, 60),
      })),
    })),
    options: Object.assign({}, ED.opts, {
      orientation: ED.orientation,
      layout: 'fill',
      copies: Math.max(1, Math.min(99, +(ED.opts.copies || 1))),
      // 服务端 run_compose_task 认 options.duplex；不显式带上时，
      // 主页那套 duplex 恰好也可能被 Object.assign 带进来，但值可能不合法
      duplex: DUPLEX_OK(ED.opts.duplex) || 'off',
    }),
    // /api/compose 认的是 body 顶层的 printer，不是 options 里的
    printer: ED.opts.printer || '',
  };
}
/* 排完版不直接打。中间必须隔一层「打印选项」，用户要能在真正出纸前
   改打印机 / 纸张 / 方向 / 颜色 / 清晰度 / 份数，并且先看一眼成品。
   之前是直接 /api/compose 一把梭，纸打出来才发现选错了。 */
const EDPO = { open: false, busy: false, objUrl: null, prevIdx: -1 };

function EDcount() {
  return ED.pages.reduce((a, p) => a + p.items.length, 0);
}
async function EDnextStep() {
  const cnt = EDcount();
  if (!cnt) { toast('版面里还没有图'); return; }
  await EDpoLoadOpts();
  EDpoSync();
  $E('#edPoMask').classList.add('show');
  EDPO.open = true;
  EDpoHidePrev();
}
function EDpoClose() {
  $E('#edPoMask').classList.remove('show');
  EDPO.open = false;
}
function EDpoHidePrev() {
  $E('#edPoPrevWrap').hidden = true;
  $E('#edPoPrevImg').removeAttribute('src');
}

/* 打印机与纸张下拉：纸张用主页已经拉好的 S.papers，打印机单独取一次 */
async function EDpoLoadOpts() {
  const selP = $E('#edPoPaper');
  const list = (window.S && S.papers) || [];
  if (selP.dataset.filled !== '1') {
    selP.innerHTML = list.map(p => `<option value="${p.key}">${p.label}</option>`).join('');
    if (!list.length) selP.innerHTML = `<option value="A4">A4</option>`;
    selP.dataset.filled = '1';
  }
  const selPr = $E('#edPoPrinter');
  if (selPr.dataset.filled !== '1') {
    let names = [];
    try {
      const r = await fetch('/api/info').then(x => x.json());
      names = r.printers || (r.printer ? [r.printer] : []);
    } catch (e) { }
    selPr.innerHTML = names.length
      ? names.map(n => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join('')
      : `<option value="">（默认打印机）</option>`;
    selPr.dataset.filled = '1';
    if (names.length && !ED.opts.printer) ED.opts.printer = names[0];
  }
}

function EDpoSync() {
  const o = ED.opts;
  if (!o.paper) o.paper = 'A4';
  if (!o.copies) o.copies = 1;
  $E('#edPoPaper').value = o.paper;
  if ($E('#edPoPrinter').querySelector(`option[value="${o.printer || ''}"]`)) {
    $E('#edPoPrinter').value = o.printer || '';
  }
  EDpoMarkSeg('#edPoOri', ED.orientation === 'landscape' ? 'landscape' : 'portrait');
  EDpoMarkSeg('#edPoColor', o.color === 'mono' ? 'mono' : 'color');
  EDpoMarkSeg('#edPoQual', QUAL_OK(o.quality) || 'standard');
  EDpoMarkSeg('#edPoDuplex', DUPLEX_OK(o.duplex) || 'off');
  $E('#edPoCopies').textContent = o.copies;
  EDpoSummary();
}
function QUAL_OK(q) { return ['draft', 'standard', 'high'].indexOf(q) >= 0 ? q : null; }
/* 双面取值和主页 index.html 里的 seg 完全一致：off / long / short */
function DUPLEX_OK(d) { return ['off', 'long', 'short'].indexOf(d) >= 0 ? d : null; }
const DUP_LABEL = { long: '双面·长边翻转', short: '双面·短边翻转' };
function EDduplexOn() {
  return ED.opts.duplex && ED.opts.duplex !== 'off' && ED.pages.length > 1;
}
function EDpoMarkSeg(sel, v) {
  $E(sel).querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.v === v));
}
function EDpoSummary() {
  const pages = ED.pages.length, cnt = EDcount();
  const cp = Math.max(1, +(ED.opts.copies || 1));
  const pl = (((window.S && S.papers) || []).find(p => p.key === ED.opts.paper) || {}).label || ED.opts.paper;
  const dup = EDduplexOn();
  // 双面时一张纸承两页，所以是 ceil 而不是乘
  const per = dup ? Math.ceil(pages / 2) : pages;
  $E('#edPoSum').innerHTML =
    `<b>${pages}</b> 页 · ${cnt} 张图 · ${pl} ${ED.orientation === 'landscape' ? '横向' : '竖向'}` +
    (dup ? ` · ${DUP_LABEL[ED.opts.duplex]}` : '') +
    ` · 共 ${per * cp} 张纸` + (cp > 1 ? `（${cp} 份）` : '');
}

/* 改选项：纸张 / 方向会改变画布比例，必须重新取画布再排一遍，
   否则用户在手机上是按 A4 摆的，打出来是另一个比例，位置全歪。 */
function EDpoOpt(key, v) {
  const relayout = (key === 'paper' || key === 'orientation');
  if (key === 'orientation') ED.orientation = v;
  else ED.opts[key] = v;
  EDpoSummary();
  if (relayout) {
    toast('正在按新尺寸重排…', 1500);
    EDfetchCanvas().then(() => { EDrelayout(); EDpoSummary(); });
  } else {
    EDfetchCanvas().then(() => { EDrender(); });
  }
}
function EDcopies(d) {
  ED.opts.copies = Math.max(1, Math.min(99, (+(ED.opts.copies) || 1) + d));
  $E('#edPoCopies').textContent = ED.opts.copies;
  EDpoSummary();
}

async function EDpoPreview() {
  if (EDPO.busy) return;
  const cnt = EDcount();
  if (!cnt) { toast('版面里还没有图'); return; }
  EDPO.busy = true;
  $E('#edPoPrevWrap').hidden = false;
  $E('#edPoPrevTxt').textContent = '正在合成这一页…';
  try {
    const spec = EDbuildSpec();
    const idx = Math.max(0, Math.min(ED.p, spec.pages.length - 1));
    const r = await fetch('/api/compose-preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pages: spec.pages, options: spec.options, index: idx, edge: 1200 }),
    });
    if (!r.ok) {
      let msg = '预览没出来（' + r.status + '）';
      try { const j = await r.json(); if (j.error) msg = j.error; } catch (e) { }
      throw new Error(msg);
    }
    const blob = await r.blob();
    if (EDPO.objUrl) URL.revokeObjectURL(EDPO.objUrl);
    EDPO.objUrl = URL.createObjectURL(blob);
    $E('#edPoPrevImg').src = EDPO.objUrl;
    EDPO.prevIdx = idx;
    $E('#edPoPrevTxt').textContent = `第 ${idx + 1}/${spec.pages.length} 页 · 这张就是拿到纸上的样子`;
  } catch (e) {
    $E('#edPoPrevWrap').hidden = true;
    toast('❌ ' + e.message, 3000);
  } finally {
    EDPO.busy = false;
  }
}

async function EDpoConfirm() {
  EDpoClose();
  await EDprintConfirm();
  // 顺手把这次选的同步回主页的打印选项，出去后看到的就是同一套设置
  try {
    if (window.S && S.opts) {
      Object.assign(S.opts, {
        paper: ED.opts.paper, color: ED.opts.color, quality: ED.opts.quality,
        copies: ED.opts.copies, orientation: ED.orientation,
        duplex: DUPLEX_OK(ED.opts.duplex) || 'off',
      });
      if (typeof syncSummary === 'function') syncSummary();
    }
  } catch (e) { }
}

/* 发一次排版打印。phase 就是手动双面的那一轮：all / odd / even */
async function EDcompose(phase) {
  const spec = EDbuildSpec();
  spec.options = Object.assign({}, spec.options, { phase: phase || 'all' });
  return fetch('/api/compose', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(spec),
  }).then(x => x.json());
}

async function EDprintConfirm() {
  const total = ED.pages.length;
  const cnt = ED.pages.reduce((a, p) => a + p.items.length, 0);
  if (!cnt) { toast('版面里还没有图'); return; }
  // 手动双面：第一轮只打奇数页，停下来等翻面，第二轮才打偶数页。
  // 和主页 runPrint 的做法一致 —— 不能一口气把 all 发过去，否则纸全出完了才提示翻面。
  const duplex = EDduplexOn();
  const first = duplex ? 'odd' : 'all';
  showProgress('正在排版打印',
    duplex ? `共 ${total} 页，先打奇数页…` : `共 ${total} 页 / ${cnt} 张，正在合成…`, 0);
  try {
    const r = await EDcompose(first);
    if (r.error) throw new Error(r.error);
    const ok = await poll(r.taskId);
    if (!ok) return;
    if (duplex) { EDwaitFlip(); return; }
    doneProgress(true, `✅ 已打印 ${total} 页`);
  } catch (e) {
    doneProgress(false, '❌ ' + e.message);
  }
}

function EDwaitFlip() {
  $E('#spin').classList.add('hidden');
  $E('#progTitle').textContent = '请把纸翻面';
  $E('#progMsg').innerHTML =
    '奇数页已经打完。<br>把出纸的那叠纸<b>整体翻面</b>放回进纸器，再点下面打偶数页。' +
    (ED.opts.duplex === 'short' ? '<br><small>这次是短边翻转，翻的方向跟长边不一样，注意别翻错。</small>' : '');
  $E('#bar').style.width = '100%';
  $E('#progAfter').classList.remove('hidden');
  $E('#progAfter').innerHTML =
    `<button class="btn primary wide" onclick="EDcontinueEven()">继续打印偶数页</button>
     <button class="btn ghost wide" style="margin-top:10px" onclick="closeProgress()">先这样，算了</button>`;
}

async function EDcontinueEven() {
  closeProgress();
  const total = ED.pages.length;
  showProgress('正在打印偶数页', '准备中…', 0);
  try {
    const r = await EDcompose('even');
    if (r.error) throw new Error(r.error);
    if (await poll(r.taskId)) {
      doneProgress(true, `✅ 双面完成，共 ${total} 页`);
    }
  } catch (e) {
    doneProgress(false, '❌ ' + e.message);
  }
}

/* ---------- 绑定 ---------- */
(function bindEditor() {
  window.addEventListener('load', () => {
    const stage = $E('#edStage');
    if (!stage) return;
    stage.addEventListener('pointerdown', EDstageDown);
    stage.addEventListener('pointermove', EDstageMove);
    stage.addEventListener('pointerup', EDstageUp);
    stage.addEventListener('pointercancel', EDstageUp);
    window.addEventListener('resize', () => { if (ED.open) EDrepaintSel(); });

    // 「下一步」面板里的分段按钮：这些 .seg 故意没写 data-key，
    // 免得被主页那段统一代理抢去改了 S.opts 而不是 ED.opts。
    document.querySelectorAll('#edPoMask .seg').forEach(seg => {
      seg.addEventListener('click', ev => {
        const b = ev.target.closest('button'); if (!b) return;
        seg.querySelectorAll('button').forEach(x => x.classList.remove('on'));
        b.classList.add('on');
        EDpoOpt(seg.dataset.ed, b.dataset.v);
      });
    });
    const paper = $E('#edPoPaper');
    if (paper) paper.addEventListener('change', () => EDpoOpt('paper', paper.value));
    const prn = $E('#edPoPrinter');
    if (prn) prn.addEventListener('change', () => { ED.opts.printer = prn.value; });
  });
})();
