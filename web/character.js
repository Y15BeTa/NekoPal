// 角色窗口：平时只有一个人物，鼠标移上去才浮出旁边的半透明按钮。
//
// 和控制有关的逻辑和「置顶小条」（pet.js）是同一套：
// 这里**不直接控制声音** —— 声音在主跟读窗里，这里只发控制命令，
// 后端再广播给所有跟读窗（设计文档 6.x「远程控制」）。
//
// 多出来的两件事：
//   ① 拖动角色 → 通过原生 bridge 挪窗口（原生外壳下 window 自己不会动）
//   ② 把「我身上是哪几个矩形」报给原生 → 原生据此做**精确命中**：
//      角色和按钮之外的地方**点穿**到桌面，不会挡住你干别的 ✨

const stage = document.querySelector('.stage');
const character = document.getElementById('character');
const dot = document.getElementById('dot');

/** 原生外壳（NSPanel）专属通道；pywebview 下是 null。 */
const native = window.webkit?.messageHandlers?.nekopal ?? null;

function toNative(action, extra = {}) {
  try {
    native?.postMessage({ action, ...extra });
  } catch (err) {
    // 窗口正在关闭时 postMessage 可能抛错 —— 忽略
  }
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

/** 按钮没反应时给个视觉回执（角色轻轻抖一下），省得你以为没点着。 */
function flash(msg) {
  character.animate(
    [{ transform: 'scale(1)' }, { transform: 'scale(0.94)' }, { transform: 'scale(1)' }],
    { duration: 240, easing: 'ease-out' },
  );
  if (msg) {
    dot.title = msg;
    dot.style.background = '#ffb4b4';
    dot.style.boxShadow = '0 0 9px #ffb4b4';
  }
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
  // 原生外壳里 window.close() 不管用 —— 让原生去关它自己
  if (native) toNative('quit');
  else window.close();
});

// ── 拖动角色 ────────────────────────────────────────────────────────────
// 用 screenX / screenY（**绝对屏幕坐标**）算增量，再用 bridge 挪原生窗口。
// 不能用 clientX：窗口一动，元素坐标系也跟着动，会互相"追尾"抖动。
if (native) {
  let dragging = false;
  let lastX = 0;
  let lastY = 0;

  character.addEventListener('mousedown', (e) => {
    if (e.target.closest('button')) return; // 按钮是"点"，不是"拖"
    dragging = true;
    lastX = e.screenX;
    lastY = e.screenY;
    // 告诉原生「开始拖了」—— 拖拽期间它要暂停"点穿"判定，
    // 否则鼠标稍快滑出角色范围就会把窗口设成穿透，拖到一半断掉 ✗
    toNative('dragging', { on: true });
    e.preventDefault();
  });

  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    toNative('move', { dx: e.screenX - lastX, dy: e.screenY - lastY });
    lastX = e.screenX;
    lastY = e.screenY;
  });

  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    toNative('dragging', { on: false });
  });
}

// ── 精确命中：把"我身上"的矩形报给原生 ──────────────────────────────────
// 原生只在这些矩形里接收鼠标事件；其余地方 setIgnoresMouseEvents_(True)，
// 于是透明区域**点穿**到桌面（不然一个大透明窗口会挡住你点别的东西）。
function reportHotRects() {
  if (!native) return;
  const rects = [];
  for (const el of [character, document.querySelector('.actions')]) {
    if (!el) continue;
    const r = el.getBoundingClientRect();
    rects.push({ x: r.left, y: r.top, w: r.width, h: r.height });
  }
  toNative('hot', { rects });
}

window.addEventListener('load', reportHotRects);
window.addEventListener('resize', reportHotRects);
reportHotRects();

// ── 状态 ────────────────────────────────────────────────────────────────

async function refresh() {
  try {
    const data = await (await fetch('/api/health')).json();
    const windows = data.subscribers ?? 0;
    stage.classList.toggle('offline', windows === 0);
    dot.title = windows ? '跟读窗已连接' : '没有跟读窗连着（点左上图标开主窗）';
    if (windows) {
      dot.style.background = '';
      dot.style.boxShadow = '';
    }
  } catch (err) {
    stage.classList.add('offline');
    dot.title = '连不上服务';
  }
}

refresh();
setInterval(refresh, 4000);

// 向原生报到：日志里出现这句，就说明页面**真的渲染出来了**（而不是个空壳窗口）
if (native) toNative('hello');
