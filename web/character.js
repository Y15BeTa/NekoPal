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

/** 取台词（角色包可以在 character.yaml 里覆盖 ✗） */
async function loadVoiceLines() {
  try {
    const d = await (await fetch('/api/avatar/lines')).json();
    voiceLines = d.lines || [];
    log('台词：' + voiceLines.length + ' 句（' + voiceLines.join(' / ') + '）');
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
    //    配了 LLM → 按角色性格 + 私人信息**现生成一句** ✅
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
    const audio = new Audio(data.url);
    await audio.play();
    log('点击：说了「' + data.text + '」（音色 ' + data.voice + '）✅');
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
character.addEventListener('click', () => {
  if (dragged) {
    dragged = false; // 拖完松手不算点击 ✗
    return;
  }
  if (!petHandle) return;

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
    const ui = await (await fetch('/api/ui')).json();
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
  } catch (err) {
    // 后端刚好在重启之类的 —— 下一轮再试
  }
}

refresh();
setInterval(refresh, 4000);
boot();
loadVoiceLines();
setInterval(pollUi, 3000);
reportHotRects();
if (native) toNative('hello');

// ── 口型同步：跟后端要电平（约 70ms 一次）──────────────────────────────
// 音频在主跟读窗里 ✗，它算好电平 POST 给后端 ✗，这里轮询着张嘴 ✗。
// ⚠️ 用**递归 setTimeout** 而不是 setInterval —— fetch 慢的时候不会堆起来 ✗。
let lipSeen = -1;
async function lipLoop() {
  try {
    if (petHandle?.setMouth) {
      const d = await (await fetch('/api/lip')).json();
      petHandle.setMouth(d.level || 0);
      // 偶尔记一笔，方便确认真的在动 ✗
      if (lipSeen < 0 && (d.level || 0) > 0.05) {
        lipSeen = d.level;
        log('口型：收到电平 ' + d.level.toFixed(3) + ' ✅');
      }
    }
  } catch (err) {
    // 后端在重启之类的 —— 下一轮再试
  }
  setTimeout(lipLoop, 70);
}
lipLoop();
