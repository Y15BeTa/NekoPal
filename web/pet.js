// 置顶小条：点一下就暂停 / 继续。
//
// 它**不直接控制声音** —— 声音在主跟读窗（浏览器）里。
// 这里只发一个控制命令，后端再广播给所有跟读窗（设计文档 6.x「远程控制」）。
//
// 好处：**不管当前焦点在哪个窗口、主窗是不是全屏**，它都能用 ——
// 这正是它存在的理由（切窗口很烦）。
//
// 这个页面会跑在两种外壳里，行为略有不同（见下面的 `native`）：
//   ① macOS 原生 NSPanel（src/nekopal/pet_panel.py）—— 能**浮在别人全屏之上** ✨
//   ② pywebview（非 macOS；或原生外壳起不来时的兜底）
// 拖拽和「关掉」在两种外壳下走的路不一样，所以要判断一下。

const pet = document.querySelector('.pet');
const hint = document.querySelector('.hint');

/** 原生外壳（NSPanel）专属通道；pywebview 下是 null。 */
const native = window.webkit?.messageHandlers?.nekopal ?? null;

function toNative(action, extra = {}) {
  try {
    native?.postMessage({ action, ...extra });
  } catch (err) {
    // 窗口正在关闭时 postMessage 可能抛错 —— 无关紧要，忽略
  }
}

async function send(action) {
  try {
    const res = await fetch('/api/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    });
    const data = await res.json().catch(() => ({}));
    const windows = data.subscribers ?? 0;
    hint.textContent = windows
      ? action === 'stop'
        ? '已停止'
        : '已发送「' + action + '」'
      : '没有跟读窗连着';
  } catch (err) {
    hint.textContent = '连不上服务';
  }
}

document.getElementById('toggle').addEventListener('click', () => send('toggle'));
document.getElementById('stop').addEventListener('click', () => send('stop'));
document.getElementById('close').addEventListener('click', () => {
  // 原生外壳里 window.close() 不管用 —— 得让原生去关它自己
  if (native) toNative('quit');
  else window.close();
});

// ── 拖动窗口 ────────────────────────────────────────────────────────────
// pywebview 有现成的 easy_drag；原生 NSPanel 没有，所以自己实现：
//   卡片上按下鼠标 → 把「屏幕坐标增量」发给原生 → 原生 setFrameOrigin 挪窗口。
// 用 screenX / screenY（绝对屏幕坐标）而不是 clientX —— 否则窗口一动，
// 元素自身的坐标系也跟着动，会互相"追尾"抖动。
if (native) {
  let dragging = false;
  let lastX = 0;
  let lastY = 0;

  pet.style.cursor = 'grab';

  pet.addEventListener('mousedown', (e) => {
    if (e.target.closest('button')) return; // 按钮是"点"，不是"拖"
    dragging = true;
    lastX = e.screenX;
    lastY = e.screenY;
    pet.style.cursor = 'grabbing';
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
    pet.style.cursor = 'grab';
  });
}

/** 顺带显示「有没有跟读窗连着」——省得点了没反应还不知道为什么。 */
async function refresh() {
  try {
    const data = await (await fetch('/api/health')).json();
    const windows = data.subscribers ?? 0;
    pet.classList.toggle('offline', windows === 0);
    if (windows === 0) hint.textContent = '没有跟读窗连着（打开 NekoPal 主窗）';
  } catch (err) {
    pet.classList.add('offline');
    hint.textContent = '连不上服务';
  }
}

refresh();
setInterval(refresh, 4000);

// 向原生外壳报到：日志里出现这句，就说明页面**真的渲染出来了**（而不是个空壳窗口）
if (native) toNative('hello');
