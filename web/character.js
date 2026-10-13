// 角色窗口：平时只有一个人物，鼠标移上去才浮出旁边的半透明按钮。
//
// 和控制有关的逻辑跟「置顶小条」（pet.js）是同一套：
// 这里**不直接控制声音** —— 声音在主跟读窗里，这里只发控制命令，
// 后端再广播给所有跟读窗（设计文档 6.x「远程控制」）。

const stage = document.querySelector('.stage');
const character = document.getElementById('character');
const dot = document.getElementById('dot');
const menu = document.getElementById('menu');
const canvas = document.getElementById('live2d');

/** 原生外壳（NSPanel）专属通道；pywebview / 浏览器里是 null。 */
const native = window.webkit?.messageHandlers?.nekopal ?? null;

/** 与 desktop.py 的 CHARACTER_SIZE 保持一致（重置大小用 ✗） */
const BASE_SIZE = { w: 360, h: 460 };

let petHandle = null; // mountModel 的返回值
let avatarInfo = null; // /api/avatar 的结果
let currentOutfit = null; // 当前形象目录名
let dragged = false; // 这一轮按下有没有拖动过（决定"是拖还是点"）

function toNative(action, extra = {}) {
  try {
    native?.postMessage({ action, ...extra });
  } catch (err) {
    // 窗口正在关闭时 postMessage 可能抛错 —— 忽略
  }
}

if (native) document.body.classList.add('native');

/**
 * 把消息丢到原生日志里。
 *
 * 为什么需要它：角色窗口是**没有开发者工具**的，而我又看不到你的屏幕 ——
 * 出了问题只能靠它说话 ✗
 */
function log(text) {
  if (native) toNative('log', { text: String(text) });
}

window.addEventListener('error', (e) => {
  const stack = e.error && e.error.stack ? '\n' + e.error.stack : '';
  log(
    '[js-error] ' + (e.message || e) + ' @ ' + (e.filename || '?') + ':' +
      (e.lineno || 0) + stack,
  );
});
window.addEventListener('unhandledrejection', (e) => {
  const r = e.reason;
  log('[js-reject] ' + (r && r.message ? r.message : r));
});

/**
 * **由原生调用**：显示 / 收起按钮列。
 *
 * 为什么不让网页自己用 CSS `:hover` 判断：鼠标一离开可点区域，原生就把窗口
 * 设成"点穿"，网页**收不到 mouseleave** → hover 卡住 → 按钮永远不消失 ✗
 */
window.__nekopalHover = (on) => {
  stage.classList.toggle('revealed', !!on);
  toNative('hover', { on: !!on }); // 回执，日志里能确认 JS 真跑了
};

// ── 精确命中：把"我身上"的矩形报给原生 ──────────────────────────────────
// 原生只在这些矩形里接收鼠标事件；其余地方 setIgnoresMouseEvents_(True)，
// 于是透明区域**点穿**到桌面。
// ⚠️ **菜单打开时也要算进去** ✗ —— 否则菜单会被判成透明区，点不动 ✗
function reportHotRects() {
  if (!native) return;
  const rects = [];
  const els = [character, document.querySelector('.actions')];
  if (!menu.hidden) els.push(menu);
  for (const el of els) {
    if (!el) continue;
    const r = el.getBoundingClientRect();
    rects.push({ x: r.left, y: r.top, w: r.width, h: r.height });
  }
  toNative('hot', { rects });
}

// ── 控制 ────────────────────────────────────────────────────────────────

async function send(action) {
  try {
    const res = await fetch('/api/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    });
    const data = await res.json().catch(() => ({}));
    flash(data.subscribers ?? 0 ? '' : '没有跟读窗');
  } catch (err) {
    flash('连不上服务');
  }
}

/** 按钮没反应时给个视觉回执（小圆点变红 ✗） */
function flash(msg) {
  if (!msg) return;
  dot.title = msg;
  dot.style.background = '#ffb4b4';
  dot.style.boxShadow = '0 0 9px #ffb4b4';
}

for (const [id, action] of [
  ['prev', 'prev'],
  ['toggle', 'toggle'],
  ['next', 'next'],
  ['stop', 'stop'],
]) {
  document.getElementById(id).addEventListener('click', () => send(action));
}

document.getElementById('close').addEventListener('click', () => {
  if (native) toNative('quit');
  else window.close();
});

// ── 拖动角色 ────────────────────────────────────────────────────────────
// 用 screenX / screenY（**绝对屏幕坐标**）算增量，再用 bridge 挪原生窗口。
if (native) {
  let dragging = false;
  let lastX = 0;
  let lastY = 0;
  let moved = 0;

  character.addEventListener('mousedown', (e) => {
    if (e.target.closest('button')) return;
    dragging = true;
    moved = 0;
    lastX = e.screenX;
    lastY = e.screenY;
    // 拖拽期间原生要暂停"点穿"判定，否则鼠标滑快一点就把窗口设成穿透 ✗
    toNative('dragging', { on: true });
    e.preventDefault();
  });

  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const dx = e.screenX - lastX;
    const dy = e.screenY - lastY;
    moved += Math.abs(dx) + Math.abs(dy);
    toNative('move', { dx, dy });
    lastX = e.screenX;
    lastY = e.screenY;
  });

  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    dragged = moved > 6;
    toNative('dragging', { on: false });
  });
}

// 点一下角色 → 换一个动作（**不眨眼**、**不缩放** ✗）
character.addEventListener('click', () => {
  if (dragged) {
    dragged = false;
    return;
  }
  if (petHandle && petHandle.playReaction && petHandle.playReaction()) {
    log('点击：换了一个动作 ✅');
  }
});

// ── 右键菜单 ────────────────────────────────────────────────────────────
// 原生 NSPanel 没有可用的 context menu ✗，所以自己画一个 HTML 菜单。
// 注意：菜单打开/关闭都要重报「可点区域」✗（否则点不动 ✗）

function mkButton(label, onClick, opts = {}) {
  const b = document.createElement('button');
  b.textContent = label;
  if (opts.on) b.classList.add('on');
  if (opts.dim) b.classList.add('dim');
  b.addEventListener('click', (e) => {
    e.stopPropagation();
    if (opts.dim) return;
    onClick();
  });
  return b;
}

function mkHead(text) {
  const d = document.createElement('div');
  d.className = 'head';
  d.textContent = text;
  return d;
}

function mkSep() {
  const d = document.createElement('div');
  d.className = 'sep';
  return d;
}

function openMenu(builder) {
  menu.textContent = '';
  builder();
  menu.hidden = false;
  reportHotRects();
}

function closeMenu() {
  if (menu.hidden) return;
  menu.hidden = true;
  reportHotRects();
}

function currentOutfitInfo() {
  return (avatarInfo?.outfits || []).find((o) => o.folder === currentOutfit) || null;
}

function buildRoot() {
  const o = currentOutfitInfo();
  menu.append(mkHead('猫伴 · ' + (o ? o.label : '')));
  menu.append(mkButton('换装 ▸' + (o ? '  ' + o.label : ''), () => buildOutfits()));
  menu.append(
    mkButton('动作 ▸' + (o ? '  ' + o.motions.length + ' 组' : ''), () => buildMotions()),
  );
  menu.append(
    mkButton(
      '表情 ▸' + (o ? '  ' + o.expressions.length + ' 个' : ''),
      () => buildExpressions(),
    ),
  );
  menu.append(mkSep());
  menu.append(mkButton('放大', () => zoom(1.15)));
  menu.append(mkButton('缩小', () => zoom(1 / 1.15)));
  menu.append(mkButton('重置大小', () => zoom(null)));
  menu.append(mkSep());
  menu.append(
    mkButton('打开跟读窗', () => {
      closeMenu();
      window.open('/', '_blank');
    }),
  );
  menu.append(
    mkButton('退出猫伴', () => {
      closeMenu();
      if (native) toNative('quit');
      else window.close();
    }),
  );
}

function buildOutfits() {
  menu.append(mkButton('‹ 返回', () => buildRoot()));
  menu.append(mkHead('换装（切过去会重新加载 ✗）'));
  const list = avatarInfo?.outfits || [];
  if (!list.length) menu.append(mkButton('（没有可选形象）', () => {}, { dim: true }));
  for (const o of list) {
    menu.append(
      mkButton(
        o.label + (o.motions.length ? '  ' + o.motions.length + ' 组动作' : ''),
        () => switchOutfit(o),
        { on: o.folder === currentOutfit },
      ),
    );
  }
}

function buildMotions() {
  menu.append(mkButton('‹ 返回', () => buildRoot()));
  menu.append(mkHead('动作'));
  const o = currentOutfitInfo();
  const groups = o?.motions || [];
  if (!groups.length) menu.append(mkButton('（没有动作）', () => {}, { dim: true }));
  for (const g of groups) {
    menu.append(
      mkButton(
        g.group + '  (' + g.count + ')',
        () => {
          closeMenu();
          const ok = petHandle?.playMotionGroup?.(g.group);
          log('菜单：播动作组 ' + g.group + ' → ' + ok);
        },
      ),
    );
  }
}

function buildExpressions() {
  menu.append(mkButton('‹ 返回', () => buildRoot()));
  menu.append(mkHead('表情'));
  const o = currentOutfitInfo();
  const list = o?.expressions || [];
  if (!list.length) {
    menu.append(
      mkButton('（这个形象没带表情 ✗ 换 ADV 版就有了）', () => {}, { dim: true }),
    );
  }
  for (const name of list) {
    menu.append(
      mkButton(name, () => {
        closeMenu();
        const ok = petHandle?.setExpression?.(name);
        log('菜单：切表情 ' + name + ' → ' + ok);
      }),
    );
  }
}

window.addEventListener('contextmenu', (e) => {
  e.preventDefault();
  openMenu(buildRoot);
});
window.addEventListener('mousedown', (e) => {
  if (!menu.hidden && !menu.contains(e.target)) closeMenu();
});
window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeMenu();
});

// ── 窗口缩放 ────────────────────────────────────────────────────────────

function zoom(factor) {
  closeMenu();
  let w;
  let h;
  if (!factor) {
    w = BASE_SIZE.w;
    h = BASE_SIZE.h;
  } else {
    w = Math.round((window.innerWidth || BASE_SIZE.w) * factor);
    h = Math.round((window.innerHeight || BASE_SIZE.h) * factor);
  }
  w = Math.max(200, Math.min(1200, w));
  h = Math.max(260, Math.min(1600, h));
  toNative('resize', { w, h });
  log('窗口缩放 → ' + w + '×' + h);
}

/** 窗口尺寸变了：重设画布尺寸 → 重新适配模型 → 重报可点区域 ✗ */
function onWindowResize() {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round((character.clientWidth || 1) * dpr));
  canvas.height = Math.max(1, Math.round((character.clientHeight || 1) * dpr));
  if (petHandle?.refit) petHandle.refit();
  reportHotRects();
}
window.addEventListener('resize', onWindowResize);

// ── 状态 ────────────────────────────────────────────────────────────────

async function refresh() {
  try {
    const data = await (await fetch('/api/health')).json();
    const windows = data.subscribers ?? 0;
    stage.classList.toggle('offline', windows === 0);
    dot.title = windows ? '跟读窗已连接' : '没有跟读窗连着（打开 NekoPal 主窗）';
    if (windows) {
      dot.style.background = '';
      dot.style.boxShadow = '';
    }
  } catch (err) {
    stage.classList.add('offline');
    dot.title = '连不上服务';
  }
}

// ── Live2D ──────────────────────────────────────────────────────────────
//
// 渲染外壳是 `/api/avatar-l2d/pet.js` —— **我们自己的**胶水，用 Live2D 官方
// Cubism 5 框架打包（integrations/live2d/nekopalpet.ts → scripts/build_live2d.sh）
async function mountModel(modelUrl) {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round((character.clientWidth || 1) * dpr));
  canvas.height = Math.max(1, Math.round((character.clientHeight || 1) * dpr));

  let shell;
  try {
    shell = await import('/api/avatar-l2d/pet.js');
  } catch (err) {
    return log('渲染外壳加载失败 ✗ ' + err);
  }

  log('开始挂 Live2D：' + modelUrl);
  const handle = await shell.mountPet({
    canvas,
    modelUrl,
    shaderBase: '/api/avatar-l2d/shaders/',
    onLog: log,
  });
  petHandle = handle;
  character.classList.add('has-model');
  log('✅ Live2D 就绪：' + handle.width + '×' + handle.height);
  return handle;
}

async function switchOutfit(outfit) {
  closeMenu();
  if (!avatarInfo?.active) return;
  const url = '/api/avatar/' + avatarInfo.active + '/' + outfit.path;
  log('换装 → ' + outfit.label);
  try {
    if (petHandle?.destroy) petHandle.destroy();
    petHandle = null;
    await mountModel(url);
    currentOutfit = outfit.folder;
  } catch (err) {
    log('换装失败 ✗ ' + (err && err.message ? err.message : err));
  }
}

async function boot() {
  if (!window.Live2DCubismCore) return log('没有 Cubism Core → 保持 🐱 占位');

  try {
    avatarInfo = await (await fetch('/api/avatar')).json();
  } catch (err) {
    return log('拿不到 /api/avatar：' + err);
  }

  const active = (avatarInfo.characters || []).find((c) => c.id === avatarInfo.active);
  if (!active || !active.url) return log('没有「形象就绪」的角色包');

  const o = (active.outfits || []).find((x) => active.model?.startsWith(x.path));
  currentOutfit = o ? o.folder : active.outfits?.[0]?.folder ?? null;
  log(
    '角色：' + active.name + ' · 形象 ' + (o ? o.label : '?') +
      ' · 可选 ' + (active.outfits || []).length + ' 个',
  );

  try {
    await mountModel(active.url);
  } catch (err) {
    log('Live2D 挂载失败 ✗ ' + (err && err.message ? err.message : err));
  }
}

refresh();
setInterval(refresh, 4000);
boot();
reportHotRects();
if (native) toNative('hello');
