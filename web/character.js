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
let lastUiOutfit = null; // 上次从 ui.json 读到的形象（用来判断"是不是别人改的" ✗）
let voiceLines = []; // 角色的台词（开窗时从后端取一次 ✗）

/** 取台词（**优先预生成池** ✗，角色包可以在 character.yaml 里覆盖 ✗） */
async function loadVoiceLines() {
  try {
    // ⚠️ `limit=8` ✗ —— 这里取来的台词会被**逐个预热合成** ✗，
    //    而 RVC 一句约 1.7 秒 ✗，池子有 30 句就是 50 秒 CPU ✗。热前 8 句足够了 ✅
    const d = await (await fetch('/api/avatar/lines?limit=8', { cache: 'no-store' })).json();
    voiceLines = d.lines || [];
    log('台词 ' + voiceLines.length + ' 句（来源 ' + (d.source || '?') +
        (d.total ? ' · 共 ' + d.total + ' ✗' : '') + '）：' + voiceLines.join(' / '));
    prewarmLines();
  } catch (err) {
    voiceLines = [];
  }
}

/**
 * 后台把台词**先合成一遍** ✗。
 *
 * 为什么：RVC 变声一句约 1.7 秒 ✗，第一次点会干等 ✗。
 * 这里在开窗后**顺序**跑一遍（不并发 ✗ —— 变声服务吃 CPU ✗），
 * 之后每次点击都是**缓存秒回** ✅
 */
async function prewarmLines() {
  for (const text of voiceLines) {
    try {
      const res = await fetch('/api/avatar/say', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      const d = await res.json();
      if (d.cached !== undefined) {
        // 只报一句"预热完成"，别刷屏 ✗
      }
    } catch (err) {
      return; // 预热失败无所谓 —— 点的时候再合成 ✗
    }
  }
  log('台词预热完成 ✅（以后点击秒回 ✗）');
}

/**
 * 随机说一句 ✗。
 *
 * 走的是 `/api/avatar/say` ✗ —— 那是**只合成、不推送**的接口 ✗，
 * 所以**不会打扰跟读窗**、也不会打断你正在听的内容 ✅
 * （而且进同一个缓存 ✗，同一句第二次秒回 ✅）
 */
async function speakRandom() {
  try {
    // ⚠️ 走 `/api/avatar/chat` ✗ —— **由后端决定**：
    //    配了 LLM → 从**预生成台词池**抽一句 / 或现编 ✅
    //    没配 / 失败 → 退回角色包的静态台词 ✅
    //    所以前端只有这一条路 ✗，不用判断 ✗（也不会因为没配就哑掉 ✗）
    const res = await fetch('/api/avatar/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ character: avatarInfo?.active_character || '' }),
    });
    const data = await res.json();
    if (!data.url) {
      return log('说话失败 ✗ ' + JSON.stringify(data).slice(0, 90));
    }
    // ⚠️ 复用同一个 <audio> 元素 ✗ —— 口型分析（createMediaElementSource ✗）
    //    对每个元素只能接一次 ✗，每次 new Audio 就没法量了 ✗。
    const el = ensureVoiceEl();
    el.src = data.url;
    el.currentTime = 0;
    if (lipCtx && lipCtx.state === 'suspended') lipCtx.resume().catch(() => {});
    await el.play();
    log(
      '点击：说了「' + data.text + '」（' + data.source + ' · ' + data.voice + '）✅',
    );
    if (data.fell_back) {
      // ⚠️ 这条一定要喊出来 ✗ —— 不然"要楽奈悄悄变成系统朗读"你只能靠耳朵发现 ✗
      log(
        '⚠️ 音色退回了兜底！想要 ' + data.wanted_voice + '，实际用的是 ' + data.voice +
          '（多半是 edge-tts 连不上 / 版本过旧，见 /api/voice-check）',
      );
    }

    // 🔎 口型体检：这次说话期间，本地电平的**峰值**是多少。
    //    没有它的话，"看不到张嘴"根本分不清是「没电平」还是「有电平但嘴没动」——
    //    前者是分析器的问题，后者是渲染层的问题，排查方向完全不同。
    let peak = 0;
    let frames = 0;
    const t0 = performance.now();
    const watch = () => {
      frames += 1;
      peak = Math.max(peak, localLevel());
      const done = voiceEl.ended || voiceEl.paused || performance.now() - t0 > 8000;
      if (done || frames > 900) {
        log(
          '口型：这次峰值 ' +
          peak.toFixed(3) +
          (peak > 0.05 ? ' ✅' : ' ✗（分析器一直没拿到电平）') +
          ' · ctx=' + (lipCtx ? lipCtx.state : '无') +
          ' · analyser=' + (lipAnalyser ? '有' : '无')
        );
        return;
      }
      requestAnimationFrame(watch);
    };
    requestAnimationFrame(watch);
  } catch (err) {
    // ⚠️ 自动播放策略：可能因为"没有用户手势"被拦 ✗ —— 如实记下来 ✗
    log('说话播放失败 ✗ ' + err);
  }
}

/** 写回主窗/角色窗共享的偏好 ✗ */
async function saveUi(patch) {
  try {
    await fetch('/api/ui', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
  } catch (err) {
    // 失败算了 —— 下次再点
  }
}
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

// 点一下角色 → **随机动作 + 随机表情** ✗
// ⚠️ 不眨眼 ✗（眨眼是自动的，用户明确说过"不要点一下就眨"✗）
// ⚠️ 不缩放 ✗（用户明确说过不要"点一下整个人缩一下"✗）

// ── ✨ 点击粒子特效 ──────────────────────────────────────────────────
// 用户要求：「鼠标在角色身上点击时设计一个合适的粒子散开特效」。
//
// 为什么用工整的 `<canvas>` 而不是一堆 `<div>`：
//   一次点下去要 20 个粒子，div 每次都要新建 / 销毁 DOM，点快了会明显卡；
//   canvas 只画一帧、画完就停（没有粒子时**整个 rAF 停掉**，完全不占 CPU ✗）。
//
// ⚠️ 它画在"透明区域"上也没关系 —— 窗口的透明是**背景**透明，
//    我们自己画的像素照样显示 ✅（所以粒子可以飞出她的轮廓 ✗）。
const fxCanvas = document.getElementById('fx');
const fxCtx = fxCanvas ? fxCanvas.getContext('2d') : null;
let fxOn = true;
let fxParts = [];
let fxRaf = 0;
let fxLast = 0;
const fxPaw = new Image();
fxPaw.src = '/paw.png'; // 顺手用那只猫爪当粒子，萌一点

function fxResize() {
  if (!fxCanvas) return;
  const dpr = window.devicePixelRatio || 1;
  fxCanvas.width = Math.max(1, Math.round(window.innerWidth * dpr));
  fxCanvas.height = Math.max(1, Math.round(window.innerHeight * dpr));
  if (fxCtx) fxCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
fxResize();
window.addEventListener('resize', fxResize);

/** 在她身上点一下 → 散开一簇粒子。 */
function fxBurst(x, y) {
  if (!fxOn || !fxCanvas) return;
  const n = 18;
  // 一圈粉色小圆点（主）+ 两三只小爪子（点缀）
  for (let i = 0; i < n; i++) {
    const a = (Math.PI * 2 * i) / n + (Math.random() - 0.5) * 0.5;
    const sp = 1.6 + Math.random() * 3.6;
    fxParts.push({
      kind: i % 7 === 0 ? 'paw' : 'dot',
      x,
      y,
      vx: Math.cos(a) * sp,
      vy: Math.sin(a) * sp - 1.1, // 整体略微往上飞
      r: 1.6 + Math.random() * 2.6,
      life: 0.75 + Math.random() * 0.45,
      age: 0,
      hue: 335 + Math.random() * 18,
    });
  }
  // 一个快速扩散的圆环（"啪"一下的打击感）
  fxParts.push({ kind: 'ring', x, y, r: 4, life: 0.34, age: 0, vx: 0, vy: 0 });
  if (!fxRaf) {
    fxLast = performance.now();
    fxRaf = requestAnimationFrame(fxFrame);
  }
}

function fxFrame(now) {
  fxRaf = 0;
  if (!fxCtx || !fxCanvas) return;
  const dt = Math.min((now - fxLast) / 1000, 0.05);
  fxLast = now;

  fxCtx.clearRect(0, 0, window.innerWidth, window.innerHeight);
  const keep = [];
  for (const p of fxParts) {
    p.age += dt;
    if (p.age >= p.life) continue;
    const t = p.age / p.life; // 0→1
    const alpha = 1 - t;

    if (p.kind === 'ring') {
      fxCtx.beginPath();
      fxCtx.arc(p.x, p.y, p.r + t * 34, 0, Math.PI * 2);
      fxCtx.strokeStyle = 'rgba(255, 150, 190, ' + alpha * 0.55 + ')';
      fxCtx.lineWidth = 1.6;
      fxCtx.stroke();
    } else {
      p.vy += 7.5 * dt; // 轻微重力，飞出去再落回来
      p.x += p.vx;
      p.y += p.vy;
      if (p.kind === 'paw' && fxPaw.complete && fxPaw.naturalWidth) {
        const s = 11 * (1 - t * 0.35);
        fxCtx.globalAlpha = alpha * 0.9;
        fxCtx.drawImage(fxPaw, p.x - s / 2, p.y - s / 2, s, s);
        fxCtx.globalAlpha = 1;
      } else {
        fxCtx.beginPath();
        fxCtx.arc(p.x, p.y, p.r * (1 - t * 0.4), 0, Math.PI * 2);
        fxCtx.fillStyle = 'hsla(' + p.hue + ', 100%, 78%, ' + alpha + ')';
        fxCtx.fill();
      }
    }
    keep.push(p);
  }
  fxParts = keep;

  if (fxParts.length) {
    fxRaf = requestAnimationFrame(fxFrame);
  } else {
    fxCtx.clearRect(0, 0, window.innerWidth, window.innerHeight);
  }
}

character.addEventListener('click', (event) => {
  if (dragged) {
    dragged = false; // 拖完松手不算点击 ✗
    return;
  }
  if (!petHandle) return;

  // ✨ 先在点击处炸一簇粒子（和后面的动作/表情/语音各管各的 ✗）
  fxBurst(event.clientX, event.clientY);

  const played = petHandle.playReaction ? petHandle.playReaction() : false;

  const list = petHandle.listExpressions ? petHandle.listExpressions() : [];
  const name = list.length ? list[Math.floor(Math.random() * list.length)] : null;
  const changed =
    name && petHandle.setExpression ? petHandle.setExpression(name) : false;

  log(
    '点击反馈：动作=' + (played ? '随机换了一个 ✅' : '无 ✗') +
      ' · 表情=' +
      (changed
        ? name + ' ✅'
        : list.length
          ? '切失败 ✗'
          : '这个形象没带表情 ✗（换 ADV 版 ✗）'),
  );

  // 顺带**随机说一句** ✗（用她自己的音色 ✗）
  speakRandom();
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

/**
 * 换一页：**先清空再画** ✗。
 *
 * ⚠️ 踩过：钻进子页时只写了 `menu.append(...)` ✗ —— 没清空 ✗ ——
 * 于是每点一次「换装 / 动作 / 表情」✗，新一页就**叠**在旧内容下面 ✗，
 * 最后菜单里 3 页全挤在一起（用户截图确认过 ✗）。
 */
function showPage(builder) {
  menu.textContent = '';
  builder();
  reportHotRects(); // 菜单高度变了 → 可点区域要重报 ✗
}

function openMenu(builder) {
  showPage(builder);
  menu.hidden = false;
  toNative('menu', { open: true }); // 告诉原生：菜单开着 ✗（关菜单要靠它 ✗）
  reportHotRects();
}

function closeMenu() {
  if (menu.hidden) return;
  menu.hidden = true;
  toNative('menu', { open: false });
  reportHotRects();
}

/**
 * **由原生调用**：光标在窗口外面点了鼠标 → 关掉菜单 ✗。
 *
 * 为什么需要：窗口外面是"点穿"的 ✗，网页**收不到**那次点击 ✗，
 * 所以只能由原生（它能读到全局鼠标按键 ✗）来通知 ✗。
 */
window.__nekopalCloseMenu = () => closeMenu();

/**
 * 当前角色（`/api/avatar` 的结构是 `characters[{id, outfits[]}]` ✗）。
 *
 * ⚠️ **踩过**：菜单里我曾读 `avatarInfo.outfits` ✗ —— 那是**顶层**，
 *    而 `outfits` 其实在 **`characters[i]`** 里面 ✗ → 永远读到空 ✗
 *    → 菜单里换装/动作/表情全显示「没有素材」✗（明明数据到了 ✗）。
 *    以后取形象一律走这里 ✗。
 */
function activeCharacter() {
  const list = avatarInfo?.characters || [];
  const id = avatarInfo?.active_character || avatarInfo?.active;
  return list.find((c) => c.id === id) || list[0] || null;
}

function currentOutfitInfo() {
  const c = activeCharacter();
  return (c?.outfits || []).find((o) => o.folder === currentOutfit) || null;
}

/**
 * 保证手里有 /api/avatar 的数据。
 *
 * ⚠️ 踩过：`boot()` 因为别的原因提前返回时 ✗，`avatarInfo` 会是 null ✗，
 * 菜单里就全变成「（没有可选形象）」「（没有动作）」✗ ——
 * 看起来像"没有素材"，其实是"没拿到数据" ✗。
 * 所以菜单打开时**再兜一次底** ✗。
 */
async function ensureAvatarInfo() {
  // ⚠️ 注意判 length ✗：空数组 `[]` 是 **truthy** ✗，
  //    写成 `if (activeCharacter()?.outfits)` 的话永远不重取 ✗（踩过）
  if (activeCharacter()?.outfits?.length) return avatarInfo;
  try {
    // ⚠️ `cache:'no-store'` —— 后端现在也发了 no-store ✗，
    //    但这里再写一道更保险 ✗（之前就是被启发式缓存坑了很久 ✗）
    const res = await fetch('/api/avatar', { cache: 'no-store' });
    avatarInfo = await res.json();
    log(
      '补取 /api/avatar（' + res.status + '）：顶层键 [' +
        Object.keys(avatarInfo || {}).join(',') + '] · characters=' +
        (avatarInfo?.characters?.length ?? '无') + ' · 角色0的键 [' +
        Object.keys(avatarInfo?.characters?.[0] || {}).join(',') + ']' +
        ' · outfits=' + (avatarInfo?.characters?.[0]?.outfits?.length ?? '无 ✗'),
    );
  } catch (err) {
    log('补取 /api/avatar 失败 ✗ ' + err);
  }
  return avatarInfo;
}

function buildRoot() {
  const o = currentOutfitInfo();
  log(
    '菜单：avatarInfo=' + (avatarInfo ? '有' : 'null ✗') +
      ' · 形象 ' + (activeCharacter()?.outfits?.length ?? '-') +
      ' 个 · currentOutfit=' + (currentOutfit ?? '(未设) ✗') +
      ' · 匹配到 ' + (o ? o.label : 'null ✗') +
      ' · 动作组 ' + (o ? o.motions.length : '-') +
      ' · 表情 ' + (o ? o.expressions.length : '-'),
  );
  menu.append(mkHead('猫伴 · ' + (o ? o.label : '')));
  menu.append(mkButton('换装 ▸' + (o ? '  ' + o.label : ''), () => showPage(buildOutfits)));
  menu.append(
    mkButton('动作 ▸' + (o ? '  ' + o.motions.length + ' 组' : ''), () => showPage(buildMotions)),
  );
  menu.append(
    mkButton(
      '表情 ▸' + (o ? '  ' + o.expressions.length + ' 个' : ''),
      () => showPage(buildExpressions),
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
  menu.append(mkButton('‹ 返回', () => showPage(buildRoot)));
  menu.append(mkHead('换装（切过去会重新加载 ✗）'));
  const list = activeCharacter()?.outfits || [];
  log(
    '换装页：' + list.length + ' 项' +
      (list.length ? ' → ' + list.map((o) => o.label).join(' / ') : ' ❌ 空 ✗'),
  );
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
  menu.append(mkButton('‹ 返回', () => showPage(buildRoot)));
  menu.append(mkHead('动作'));
  const o = currentOutfitInfo();
  const groups = o?.motions || [];
  log('动作页：' + groups.length + ' 组 · currentOutfit=' + (currentOutfit ?? '(null) ✗'));
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
  menu.append(mkButton('‹ 返回', () => showPage(buildRoot)));
  menu.append(mkHead('表情'));
  const o = currentOutfitInfo();
  const list = o?.expressions || [];
  log('表情页：' + list.length + ' 个');
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

window.addEventListener('contextmenu', async (e) => {
  e.preventDefault();
  await ensureAvatarInfo(); // 兜底：菜单必须有数据 ✗
  openMenu(buildRoot);
});
window.addEventListener('mousedown', (e) => {
  if (!menu.hidden && !menu.contains(e.target)) closeMenu();
});
window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeMenu();
});

// ── 窗口缩放 ────────────────────────────────────────────────────────────
// 尺寸上下限
const MIN_W = 200;
const MAX_W = 1200;
const MIN_H = 260;
const MAX_H = 1600;

// ⚠️ **自己记着"想要的尺寸"**，不要从 `window.innerWidth` 推 ——
//    踩过的坑：原生 `setFrame` 之后，页面里的 `innerWidth` **不一定会立刻更新**，
//    于是"连点两次缩小"会拿**上一次的旧值**去算 → 尺寸来回跳 →
//    看起来就是"点缩小反而放大"（用户实测报的）。
let wantSize = null;

function zoom(factor) {
  closeMenu();
  let w;
  let h;
  if (!factor) {
    wantSize = null;
    w = BASE_SIZE.w;
    h = BASE_SIZE.h;
  } else {
    const cur = wantSize || {
      w: window.innerWidth || BASE_SIZE.w,
      h: window.innerHeight || BASE_SIZE.h,
    };
    w = Math.max(MIN_W, Math.min(MAX_W, Math.round(cur.w * factor)));
    h = Math.max(MIN_H, Math.min(MAX_H, Math.round(cur.h * factor)));
    wantSize = { w, h };
  }
  toNative('resize', { w, h });
  log('窗口缩放 → ' + w + '×' + h + '（×' + (factor || '重置') + '）');
}

// 🖐️ 触控板**双指捏合** = 缩放（macOS 把捏合报成 `ctrl+wheel`）
//    ⚠️ 只在"她身上"收得到 —— 角色窗的透明区是点穿的，捏合到不了网页。
//    所以是"捏在她身上缩放"，这也最合直觉。
if (character) {
  character.addEventListener(
    'wheel',
    (e) => {
      if (!e.ctrlKey) return; // 普通滚动不管
      e.preventDefault();
      zoom(e.deltaY < 0 ? 1.07 : 1 / 1.07); // 捏合步长细一点，手感才跟手
    },
    { passive: false },
  );
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
    lastUiOutfit = outfit.folder; // 别让轮询以为自己该换 ✗
    // ⚠️ **必须写回 ui.json** ✗ —— 否则 3 秒后轮询读到旧的 ui.outfit ✗，
    //    就会把你刚换的衣服**又换回去** ✗（用户报的正是这个 ✗）
    await saveUi({ outfit: outfit.folder, show_avatar: true });
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

  const active =
    (avatarInfo.characters || []).find((c) => c.id === avatarInfo.active_character) ||
    (avatarInfo.characters || []).find((c) => c.id === avatarInfo.active);
  if (!active || !active.url) return log('没有「形象就绪」的角色包');

  // 主窗选中的形象优先（ui.json ✗）；没选过就用 character.yaml 里配的那个
  const chosen = (active.outfits || []).find(
    (x) => x.folder === avatarInfo.active_outfit,
  );
  const o = chosen || (active.outfits || []).find((x) => active.model?.startsWith(x.path));
  currentOutfit = o ? o.folder : null;
  lastUiOutfit = avatarInfo.active_outfit || currentOutfit;
  log(
    '角色：' + active.name + ' · 形象 ' + (o ? o.label : '?') +
      ' · 可选 ' + (active.outfits || []).length + ' 个' +
      ' · currentOutfit=' + (currentOutfit ?? '(null) ✗') +
      ' · 清单 ' + (active.outfits || []).map((x) => x.label).join(' / '),
  );

  // 🔎 自检：**用菜单那条路径**再取一次（menu 用的是 activeCharacter() ✗）
  //    这样不用点菜单，日志就能证明"菜单里会有东西" ✅
  const menuChar = activeCharacter();
  const menuOutfit = currentOutfitInfo();
  log(
    '菜单自检：activeCharacter=' + (menuChar ? menuChar.id : 'null ✗') +
      ' · outfits=' + (menuChar?.outfits?.length ?? '无 ✗') +
      ' · currentOutfit匹配到=' + (menuOutfit ? menuOutfit.label : 'null ✗') +
      ' · 动作组=' + (menuOutfit?.motions.length ?? '-') +
      ' · 表情=' + (menuOutfit?.expressions.length ?? '-'),
  );

  const url = chosen ? '/api/avatar/' + avatarInfo.active + '/' + chosen.path : active.url;
  try {
    await mountModel(url);
  } catch (err) {
    log('Live2D 挂载失败 ✗ ' + (err && err.message ? err.message : err));
  }
}

/**
 * 跟主窗保持同步：主窗的「形象」下拉改了，这里要跟着换 ✗；
 * 主窗取消勾选 → 自己退出 ✗。
 *
 * 为什么要轮询：主窗和角色窗口是**两个进程** ✗，
 * 中间只有后端这一条路（设计文档 6.9 ✗）。
 */
async function pollUi() {
  try {
    const ui = await (await fetch('/api/ui', { cache: 'no-store' })).json();
    if (ui.show_avatar === false) {
      log('主窗要求关掉人物 → 退出角色窗口');
      if (native) toNative('quit');
      else window.close();
      return;
    }
    if (ui.outfit && ui.outfit !== currentOutfit) {
      const o = (activeCharacter()?.outfits || []).find((x) => x.folder === ui.outfit);
      if (o) await switchOutfit(o);
    }
    applyLookFlags(ui);
  } catch (err) {
    // 后端刚好在重启之类的 —— 下一轮再试
  }
}

// ── 外观开关（都在主窗「设置」里，改完这个窗口 3 秒内跟着变）───────────
// 为什么放到界面上：这类"感觉型"的开关，用户不该去编辑 json。
let pawOn = null;
let gazeOn = true;

function applyLookFlags(ui) {
  // 🐾 猫爪光标
  const paw = ui.paw_cursor !== false;
  if (paw !== pawOn) {
    pawOn = paw;
    document.body.classList.toggle('paw-cursor', paw);
    log('猫爪光标 ' + (paw ? '开 ✅' : '关 ✗'));
  }
  // 视线跟随
  const gaze = ui.gaze_follow !== false;
  if (gaze !== gazeOn) {
    gazeOn = gaze;
    log('视线跟随 ' + (gaze ? '开 ✅' : '关 ✗'));
  }
}

// ── 视线跟随：眼睛和头**看向光标** ────────────────────────────────────
// ⚠️ 光标位置由**原生面板**推过来 ✗（见 pet_panel.py 的 _tell_page_cursor ✗）——
//    因为透明区域是"点穿"的 ✗，网页在那些地方**收不到 mousemove** ✗。
//    网页自己也听一层 `mousemove` 做兜底 ✅（原生挂了也还能动 ✗）。
let cursorPt = null;   // 窗口内坐标（px ✗）
let gazeLogged = false;

window.__nekopalCursor = (x, y) => {
  cursorPt = [Number(x) || 0, Number(y) || 0];
};
window.addEventListener('mousemove', (e) => {
  cursorPt = [e.clientX, e.clientY];
});

refresh();
setInterval(refresh, 4000);
boot();
loadVoiceLines();
setInterval(pollUi, 3000);
reportHotRects();
if (native) toNative('hello');

// ⚠️ **定期重报热区**（自愈 ✗）：窗口尺寸、换装、菜单开合都会让矩形变；
//    而更糟的是——**万一某次报了个空的**，原生那边就再也收不到"在身上"，
//    于是猫爪光标和"精确命中"会**一起静默失效，而且永远不恢复**（2026-10-14 踩到 ✗）。
//    每 3 秒重报一次，成本可以忽略，但能自己爬回来。
setInterval(reportHotRects, 3000);

// ── 口型同步 ─────────────────────────────────────────────────────────
// ⚠️ 2026-10-14 修 ✗：以前这里**只**轮询 `/api/lip` ✗，而**没有任何代码在 POST 它** ✗
//    （主窗那段实现丢了 ✗）→ 电平恒为 0 ✗ → 口型**从来就没动过** ✗。
//
// 现在分两路 ✗：
//   ① **本地**：点击反馈的音频是在**这个窗口**里播的 ✗
//      → 直接分析它自己 ✅（零延迟、也不依赖后端 ✗）
//   ② **远端**：跟读窗 / opencode 的音频在主窗里 ✗
//      → 那边算好电平 POST `/api/lip` ✗，这里轮询着取 ✅
let voiceEl = null;      // 复用一个 <audio>：口型分析只能对同一个元素接一次 ✗
let lipCtx = null;
let lipAnalyser = null;
let lipData = null;
let remoteLevel = 0;     // 主窗上报的电平 ✗
let lipLocalSeen = false;
let lipRemoteSeen = false;

/** 建（或复用）那个唯一的 <audio>，并把口型分析接上去 ✗。 */
function ensureVoiceEl() {
  if (voiceEl) return voiceEl;
  voiceEl = document.createElement('audio');
  voiceEl.preload = 'auto';
  voiceEl.style.display = 'none';
  document.body.appendChild(voiceEl);
  return voiceEl;
}

function ensureLipAnalyser() {
  if (lipAnalyser) return true;
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return false;
    lipCtx = new Ctx();
    const src = lipCtx.createMediaElementSource(ensureVoiceEl());
    lipAnalyser = lipCtx.createAnalyser();
    lipAnalyser.fftSize = 512;
    lipAnalyser.smoothingTimeConstant = 0.5;
    src.connect(lipAnalyser);
    lipAnalyser.connect(lipCtx.destination); // ⚠️ 不接就没声音 ✗
    lipData = new Uint8Array(lipAnalyser.fftSize);
    if (lipCtx.state === 'suspended') lipCtx.resume().catch(() => {});
    return true;
  } catch (err) {
    log('口型：本地分析不可用（' + err + '）—— 退回远端电平 ✗');
    lipAnalyser = null;
    return false;
  }
}

/** 本地音量的 RMS（0..1 ✗）。 */
function localLevel() {
  if (!lipAnalyser || !voiceEl || voiceEl.paused) return 0;
  try {
    lipAnalyser.getByteTimeDomainData(lipData);
    let sum = 0;
    for (let i = 0; i < lipData.length; i++) {
      const v = (lipData[i] - 128) / 128;
      sum += v * v;
    }
    return Math.min(1, Math.sqrt(sum / lipData.length) * 3.4);
  } catch (err) {
    return 0;
  }
}

// 远端电平：低频轮询（本地有声音时就不问了 ✗，省得白跑 ✗）
async function pollRemoteLip() {
  try {
    const d = await (await fetch('/api/lip', { cache: 'no-store' })).json();
    remoteLevel = d.level || 0;
    if (!lipRemoteSeen && remoteLevel > 0.05) {
      lipRemoteSeen = true;
      log('口型：收到主窗电平 ' + remoteLevel.toFixed(3) + ' ✅');
    }
  } catch (err) {
    remoteLevel = 0;
  }
  setTimeout(pollRemoteLip, 110);
}

// 开口幅度：本地优先（更跟得上 ✗），没在放就走远端 ✅
function mouthLoop() {
  const local = localLevel();
  if (!lipLocalSeen && local > 0.05) {
    lipLocalSeen = true;
    log('口型：本地电平 ' + local.toFixed(3) + ' ✅（点击反馈走这条 ✗）');
  }
  const level = local > 0.02 ? local : remoteLevel;
  if (petHandle?.setMouth) petHandle.setMouth(level);

  // 视线：归一化到 -1..1（+x 右、+y 下 ✗），再给一点增益让偏移看得出 ✗
  if (gazeOn && cursorPt && petHandle?.setGaze) {
    const w = Math.max(1, window.innerWidth);
    const h = Math.max(1, window.innerHeight);
    petHandle.setGaze(
      ((cursorPt[0] / w) * 2 - 1) * 1.3,
      ((cursorPt[1] / h) * 2 - 1) * 1.3
    );
    if (!gazeLogged) {
      gazeLogged = true;
      log('视线跟随：收到光标 ✅');
    }
  }
  requestAnimationFrame(mouthLoop);
}

ensureLipAnalyser();
pollRemoteLip();
requestAnimationFrame(mouthLoop);
