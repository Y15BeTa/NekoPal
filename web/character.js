// 角色窗口：平时只有一个人物，鼠标移上去才浮出旁边的半透明按钮。
//
// 和控制有关的逻辑跟「置顶小条」（pet.js）是同一套：
// 这里**不直接控制声音** —— 声音在主跟读窗里，这里只发控制命令，
// 后端再广播给所有跟读窗（设计文档 6.x「远程控制」）。
//
// 多出来的三件事：
//   ① 拖动角色 → 通过原生 bridge 挪窗口
//   ② 把「我身上是哪几个矩形」报给原生 → 原生据此做**精确命中**（其余地方点穿）
//   ③ 挂 Live2D（用**我们自己的**渲染外壳 `/api/avatar-l2d/pet.js`）

const stage = document.querySelector('.stage');
const character = document.getElementById('character');
const dot = document.getElementById('dot');

/** 原生外壳（NSPanel）专属通道；pywebview / 浏览器里是 null。 */
const native = window.webkit?.messageHandlers?.nekopal ?? null;

function toNative(action, extra = {}) {
  try {
    native?.postMessage({ action, ...extra });
  } catch (err) {
    // 窗口正在关闭时 postMessage 可能抛错 —— 忽略
  }
}

// 原生外壳：给 body 打个记号，让 CSS 知道"按钮由原生驱动，别用 :hover"
if (native) document.body.classList.add('native');

/**
 * 把消息丢到原生日志里。
 *
 * 为什么需要它：角色窗口是**没有开发者工具**的（我们只做接口，不做调试面板），
 * 而我又看不到你的屏幕 —— 出了问题只能靠它说话 ✗
 */
function log(text) {
  if (native) toNative('log', { text: String(text) });
}

// 任何未捕获的 JS 错误 / Promise 拒绝，都送进原生日志（带调用栈 ✗）
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

/** 按钮没反应时给个视觉回执（角色轻轻抖一下）。 */
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
let dragged = false; // 这一轮按下有没有拖动过（决定"是拖还是点"）
let petHandle = null; // mountPet 的返回值，点一下角色时用它播动作

if (native) {
  let dragging = false;
  let lastX = 0;
  let lastY = 0;
  let moved = 0;

  character.addEventListener('mousedown', (e) => {
    if (e.target.closest('button')) return; // 按钮是"点"，不是"拖"
    dragging = true;
    moved = 0;
    lastX = e.screenX;
    lastY = e.screenY;
    // 告诉原生「开始拖了」—— 拖拽期间它要暂停"点穿"判定，
    // 否则鼠标稍快滑出角色范围就会把窗口设成穿透，拖到一半断掉 ✗
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
    dragged = moved > 6; // 挪了几像素才算"拖"，否则当"点"
    toNative('dragging', { on: false });
  });
}

// 点一下角色 → 换动作 + 眨眼（点击反馈）。
// ⚠️ **不做整体缩放** —— 用户明确说过不要"点一下整个人缩一下" ✗
character.addEventListener('click', () => {
  if (dragged) {
    dragged = false;
    return;
  }
  if (!petHandle) return;
  const played = petHandle.playReaction && petHandle.playReaction();
  const blinked = petHandle.blink && petHandle.blink();
  if (played || blinked) log('点击反应：动作=' + played + ' 眨眼=' + blinked);
});

// ── 精确命中：把"我身上"的矩形报给原生 ──────────────────────────────────
// 原生只在这些矩形里接收鼠标事件；其余地方 setIgnoresMouseEvents_(True)，
// 于是透明区域**点穿**到桌面。
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

// ── Live2D：挂上就用角色，挂不上就退回 🐱 ────────────────────────────────
//
// 渲染外壳是 `/api/avatar-l2d/pet.js` —— **我们自己的**胶水，用 Live2D 官方
// Cubism 5 框架打包（`src/nekopalpet.ts` → esbuild）。为什么不直接用
// pixi-live2d-display：那个库按 Cubism 4 写，和 Core 5 的多处结构对不上 ✗
async function mountLive2D() {
  const canvas = document.getElementById('live2d');

  if (!window.Live2DCubismCore) return log('没有 Cubism Core → 保持 🐱 占位');

  // ⚠️ 画布尺寸要**显式设成 attribute**：canvas 默认 300×150，
  //    只靠 CSS 的话 WebGL 会在 300×150 上渲染再被拉伸（糊）
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round((character.clientWidth || 224) * dpr);
  canvas.height = Math.round((character.clientHeight || 420) * dpr);

  let shell;
  try {
    shell = await import('/api/avatar-l2d/pet.js');
  } catch (err) {
    return log('渲染外壳加载失败 ✗ ' + err);
  }
  if (typeof shell.mountPet !== 'function') {
    return log('渲染外壳没有导出 mountPet ✗');
  }

  let info;
  try {
    info = await (await fetch('/api/avatar')).json();
  } catch (err) {
    return log('拿不到 /api/avatar：' + err);
  }
  const active = (info.characters || []).find((c) => c.id === info.active);
  if (!active || !active.url) return log('没有「形象就绪」的角色包');

  log('开始挂 Live2D：' + active.url);
  try {
    const handle = await shell.mountPet({
      canvas,
      modelUrl: active.url,
      shaderBase: '/api/avatar-l2d/shaders/',
      onLog: log,
    });
    petHandle = handle;
    character.classList.add('has-model');
    log('✅ Live2D 就绪：模型画布 ' + handle.width + '×' + handle.height);
  } catch (err) {
    log('Live2D 挂载失败 ✗ ' + (err && err.message ? err.message : err));
  }
}

refresh();
setInterval(refresh, 4000);
mountLive2D();

// 向原生报到：日志里出现这句，就说明页面**真的渲染出来了**
if (native) toNative('hello');
