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

// 原生外壳：给 body 打个记号，让 CSS 知道"按钮由原生驱动，别用 :hover"
if (native) document.body.classList.add('native');

/**
 * 把消息丢到原生日志里。
 *
 * 为什么需要它：角色窗口是**没有开发者工具**的（我们只做接口，不做调试面板），
 * 而我又看不到你的屏幕 —— 出了问题只能靠它说话 ✗
 * 这是这个项目里"能说话 > 聪明"的又一次实践。
 */
function log(text) {
  if (native) toNative('log', { text: String(text) });
}

// 任何未捕获的 JS 错误 / Promise 拒绝，都送进原生日志
window.addEventListener('error', (e) => {
  // 带上调用栈 —— 压缩库里只有栈能看出是哪个函数炸的
  const stack = e.error && e.error.stack ? '\n' + e.error.stack : '';
  log('[js-error] ' + (e.message || e) + ' @ ' + (e.filename || '?') + ':' +
      (e.lineno || 0) + stack);
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
 * （用户实测踩到）。只有原生才知道鼠标到底还在不在角色身上。
 */
window.__nekopalHover = (on) => {
  stage.classList.toggle('revealed', !!on);
  // 回执给原生：日志里能看到"按钮已显示/已收起"，就证明 JS 真的被执行了 ✅
  toNative('hover', { on: !!on });
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

/**
 * ⚠️ Cubism 4 / 5 的**版本桥**。
 *
 * Cubism **Core 5** 把 moc 里的 `renderOrders` 改名成了 `drawOrders`，
 * 而 `pixi-live2d-display@0.4.0` 是照 **Cubism 4** 写的、仍然读 `renderOrders`：
 * 拿到 undefined → 渲染直接炸（但模型"看起来"加载成功了，很难查 ✗）。
 * 我们逐字段比对过 Core 给出的 drawables，**只差这一个名字**。
 *
 * 所以在 Core 外面套一层：`Model.fromMoc` 拿到模型后把旧名字补上。
 * 这样**不用改第三方文件**、升级库时也不会丢，将来有配 Cubism 5 的库就能整段删掉。
 */
function bridgeCubismVersion() {
  const core = window.Live2DCubismCore;
  if (!core || !core.Model || core.__nekopalBridged) return;

  const original = core.Model.fromMoc;
  core.Model.fromMoc = function (moc) {
    const model = original.call(this, moc);
    const d = model && model.drawables;
    if (d && d.renderOrders === undefined && d.drawOrders !== undefined) {
      d.renderOrders = d.drawOrders;
      log(
        '版本桥已套用：renderOrders ← drawOrders (' +
          d.renderOrders.length + ' 个)' +
          (d.renderOrders ? '' : ' ❌ 还是 undefined（字段只读？）'),
      );
    }
    return model;
  };
  core.__nekopalBridged = true;
}

/**
 * 直接问 Cubism Core 几个问题 —— 用来判断"是不是版本不匹配"。
 *
 * 为什么值得单独做：`pixi-live2d-display@0.4.0` 是照着 **Cubism 4** 写的，
 * 而我们用的是 **Cubism 5** 的 Core。两者万一对不上，表现出来就是
 * `renderOrder` 之类的字段变成 undefined（渲染直接炸，但模型"看起来"加载成功了 ✗）。
 * 所以开机先自检一次，把版本号摊开 ✗
 */
async function probeCore(modelUrl) {
  try {
    const dir = modelUrl.slice(0, modelUrl.lastIndexOf('/') + 1);
    const meta = await (await fetch(modelUrl)).json();
    const mocName = (meta.FileReferences || {}).Moc;
    log('model3.json: moc=' + mocName + ' 贴图=' +
        ((meta.FileReferences || {}).Textures || []).length + ' 张');

    const core = window.Live2DCubismCore;
    log('Core 版本 0x' + core.Version.csmGetVersion().toString(16) +
        ' · 最高支持 moc v' + core.Version.csmGetLatestMocVersion());

    const buf = await (await fetch(dir + mocName)).arrayBuffer();
    log('moc3 读到 ' + buf.byteLength + ' 字节 · 自身版本 v' +
        core.Version.csmGetMocVersion(buf, buf.byteLength));

    const moc = core.Moc.fromArrayBuffer(buf);
    const model = core.Model.fromMoc(moc);
    const d = model.drawables;

    // ── 🛑 版本闸门 ──────────────────────────────────────────────────
    // `pixi-live2d-display@0.4.0` 是按 **Cubism 4** 的 Core API 写的，
    // 而你下的是 **Cubism 5** 的 Core —— 我们逐项比对过，**不是补一个名字的事**：
    //
    //   moc.canvasWidth        → Core 5 挪到了 model.canvasinfo   ✗
    //   drawables.vertexPositions → Core 5 返回的不是扁平 Float32Array ✗
    //   drawables.renderOrders → Core 5 叫 drawOrders              ✗
    //
    // 硬跑的结果不是"没形象"，而是**一屏碎片**（比 🐱 还难看 ✗）。
    // 所以这里主动认出"不兼容" → 保持 🐱 占位，并**大声说明原因**。
    const hasCanvas = moc.canvasWidth !== undefined || model.canvasWidth !== undefined;
    if (!hasCanvas) {
      log(
        '⚠️ Core 版本不兼容 → 保持 🐱 占位。' +
          'Core 5 把画布信息放到了 model.canvasinfo，而 pixi-live2d-display@0.4.0 ' +
          '读的是 moc.canvasWidth（还有顶点数组与 renderOrders 也对不上）。' +
          '要真正接上，需要一个配 Cubism 5 的渲染器。',
      );
      return false;
    }
    log('renderOrders: ' +
        (d.renderOrders ? d.renderOrders.length + ' 个' : '❌ undefined'));

    // ── 把 Core 5 的对象"长相"摊开：一次看全 API 差异 ────────────────
    log('moc 字段: ' + Object.keys(moc).join(','));
    log('moc.canvasWidth=' + moc.canvasWidth +
        ' · getCanvasWidth=' +
        (typeof moc.getCanvasWidth === 'function' ? moc.getCanvasWidth() : 'n/a') +
        ' · pixelsPerUnit=' + moc.pixelsPerUnit);
    log('model 字段: ' + Object.keys(model).join(','));
    log('drawables.resetDynamicFlags 是 ' + typeof d.resetDynamicFlags);

    model.update();
    const vp2 = model.drawables.vertexPositions;
    log('update 后 vertexPositions: ' + Object.prototype.toString.call(vp2) +
        ' · length=' + vp2.length +
        ' · 前 8 个=' + Array.from(vp2).slice(0, 8).map((n) => Math.round(n)).join(','));
    log('drawable 顶点总数（vertexCounts 求和）=' +
        Array.from(d.vertexCounts).reduce((a, b) => a + b, 0));
    return true;
  } catch (err) {
    log('Core 自检失败 ✗ ' + (err && err.message ? err.message : err));
    return false;
  }
}

// ── Live2D：把角色包里的模型挂到 canvas 上 ──────────────────────────────
// 挂不上就安静退回 🐱 占位 —— 朗读、按钮、拖拽、精确命中全都不受影响。
async function mountLive2D() {
  const canvas = document.getElementById('live2d');

  if (!window.Live2DCubismCore) return log('没有 Cubism Core → 保持 🐱 占位');
  if (!window.PIXI || !window.PIXI.live2d) {
    return log('没有 pixi / pixi-live2d-display → 保持 🐱 占位');
  }

  let info;
  try {
    info = await (await fetch('/api/avatar')).json();
  } catch (err) {
    return log('拿不到 /api/avatar：' + err);
  }

  const active = (info.characters || []).find((c) => c.id === info.active);
  if (!active || !active.url) return log('没有「形象就绪」的角色包');

  const url = active.url;
  log('开始加载模型：' + url);
  bridgeCubismVersion();
  if (!(await probeCore(url))) return; // 版本不兼容 → 停在 🐱 占位

  try {
    // ⚠️ 尺寸**显式给**，不要用 resizeTo：canvas 现在还是 display:none，
    //    resizeTo 量到 0 → app.screen 为 0 → 缩放算出 0 → 什么都看不见 ✗
    const app = new PIXI.Application({
      view: canvas,
      width: character.clientWidth || 224,
      height: character.clientHeight || 420,
      transparent: true,
      antialias: true,
      backgroundAlpha: 0,
      resolution: window.devicePixelRatio || 1,
      autoDensity: true,
    });

    const model = await PIXI.live2d.Live2DModel.from(url);
    app.stage.addChild(model);

    // 按**高度**填满角色区（桌宠要的是人明显，宽度随它去）
    model.scale.set(1);
    const raw = app.screen.height / (model.height || 1);
    const scale = Number.isFinite(raw) && raw > 0 ? raw : 1;
    model.scale.set(scale);
    model.anchor.set(0.5, 0.5);
    model.x = app.screen.width / 2;
    model.y = app.screen.height / 2;

    character.classList.add('has-model');
    log(
      'Live2D 挂上了 ✅ 模型原生 ' + Math.round(model.width) + '×' +
      Math.round(model.height) + ' · 画布 ' + Math.round(app.screen.width) +
      '×' + Math.round(app.screen.height) + ' · 缩放 ' + scale.toFixed(4),
    );
  } catch (err) {
    log('Live2D 加载失败 ✗ ' + (err && err.message ? err.message : err));
  }
}

refresh();
setInterval(refresh, 4000);
mountLive2D();

// 向原生报到：日志里出现这句，就说明页面**真的渲染出来了**（而不是个空壳窗口）
if (native) toNative('hello');
