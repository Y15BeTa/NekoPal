// NekoPal 跟读窗前端的胶水层：音色加载 + 发起朗读 + 消费 SSE + 响应外部推送
import { Reader } from './player.js';

const $ = (id) => document.getElementById(id);

const els = {
  voice: $('voice'),
  rate: $('rate'),
  prev: $('prev'),
  playPause: $('playPause'),
  next: $('next'),
  input: $('input'),
  readBtn: $('readBtn'),
  clearBtn: $('clearBtn'),
  status: $('status'),
  reader: $('reader'),
  audio: $('audio'),
  soundGate: $('soundGate'),
  soundGateBtn: $('soundGateBtn'),
  packWarn: $('packWarn'),
  tray: $('tray'),
  activityList: $('activityList'),
  activityRefresh: $('activityRefresh'),
};

//: 当前正在读的任务；用于区分「自己点的」与「外部推来的」
let currentTaskId = null;
//: 浏览器是否已解锁自动播放
let audioUnlocked = false;

//: 0.1 秒真实静音 WAV——用来试探/解锁自动播放，本身不出声。
//: ⚠️ 别用 0 字节的 data URL：Chrome 能忍，但 **WebKit 里 play() 的 Promise 永不 resolve**
//: （踩过：连续三轮探针都被它误导，以为 WKWebView 不允许自动播放）。
const SILENT_PRIMER = '/silence.wav';

//: 记住音色与语速（否则每次重开窗口都要重选）
const PREFS_KEY = 'nekopal.prefs';

function savePrefs() {
  try {
    localStorage.setItem(
      PREFS_KEY,
      JSON.stringify({ voice: els.voice.value, rate: els.rate.value })
    );
  } catch (err) { /* 隐私模式等场景忽略 */ }
}

function loadPrefs() {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') || {};
  } catch (err) {
    return {};
  }
}

/** 把界面选的音色同步给服务端 —— 这样「opencode 自动推送」的任务也会用它。 */
async function syncVoiceToServer(voiceId) {
  try {
    await fetch('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ default_voice: voiceId }),
    });
    return true;
  } catch (err) {
    return false; // 同步失败不影响手动朗读
  }
}

/** 页面是否跑在原生外壳（nekopal app）里。 */
function isShell() {
  return new URLSearchParams(location.search).get('shell') === 'app' || !!window.pywebview;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

/** 把前端自己的关键节点也记进活动记录，便于一条命令排查整条链路。 */
function report(event, detail) {
  try {
    fetch('/api/activity', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event, source: 'reader', detail }),
    }).catch(() => { /* 忽略 */ });
  } catch (err) { /* 忽略 */ }
}

function setStatus(text) {
  els.status.textContent = text;
  // 播放被浏览器拦下时，把解锁提示亮出来
  if (typeof text === 'string' && text.includes('需要先与页面交互')) showSoundGate();
}

function showSoundGate() {
  if (!audioUnlocked && els.soundGate) els.soundGate.hidden = false;
}

/**
 * 显示「音色包没被加载」的原因。
 *
 * 后端一直在返回 `pack_problems`，但界面从来没展示过 —— 结果用户的包坏掉时
 * 毫无提示，只能去跑 `nekopal packs list` 才知道。这是审计发现的一个可用性缺口。
 */
function renderPackProblems(problems) {
  const box = els.packWarn;
  if (!box) return;

  if (!problems || !problems.length) {
    box.hidden = true;
    box.innerHTML = '';
    return;
  }

  box.hidden = false;
  box.innerHTML =
    '<div class="warn-head">⚠️ 有 ' + problems.length + ' 个音色包/角色包没被加载</div>' +
    '<ul class="warn-list">' +
    problems
      .map(function (item) {
        return (
          '<li><code>' + escapeHtml(item.path) + '</code>' +
          '<div class="warn-reason">' + escapeHtml(item.reason) + '</div></li>'
        );
      })
      .join('') +
    '</ul>' +
    '<div class="warn-foot">修好之后运行 <code>nekopal packs list</code> 复查，或在终端重启服务。</div>';
}

/**
 * 解锁自动播放。
 *
 * 浏览器规定：媒体播放要么发生在用户手势里，要么该页面已被用户交互过。
 * opencode 的推送是「异步」到达的，不可能落在手势里 —— 所以必须提前点一次。
 */
async function unlockAudio() {
  if (audioUnlocked) return;
  audioUnlocked = true;
  document.body.classList.add('unlocked');
  if (els.soundGate) els.soundGate.hidden = true;

  try {
    // 播一小段真实静音：满足"在用户手势中播放过"的条件，又不出声。
    // 用**独立的 Audio 对象**，避免污染主播放器的状态（曾导致误报"已读完"）。
    const primer = new Audio(SILENT_PRIMER);
    primer.muted = true;
    await primer.play();
    primer.pause();
    setStatus('声音已启用 · opencode 回答完就会自动朗读');
    // 音频解锁了 → 现在可以安全地把跟读窗接上 WebAudio（给口型用 ✗）
    startLipReporting();
  } catch (err) {
    setStatus('解锁失败，请直接点一次「朗读」');
  }
}

/**
 * 自动播放探针（启动时跑一次）。
 *
 * 浏览器策略通常在**没有用户手势**时拒绝播放；但原生外壳（pywebview / WKWebView）
 * 的策略可能更宽松。探针成功就自动解锁 —— 用户**不用再点那个提示条**。
 * 结果写进 `/api/activity`，出问题时能直接看到。
 */
async function probeAutoplay() {
  let ok = false;
  let err = '';
  try {
    const primer = new Audio(SILENT_PRIMER);
    primer.muted = true;
    await primer.play();
    primer.pause();
    ok = true;
  } catch (e) {
    err = String(e);
  }

  report(ok ? 'autoplay-ok' : 'autoplay-blocked', {
    shell: isShell(),
    err: err.slice(0, 160),
  });

  if (ok) {
    audioUnlocked = true;
    document.body.classList.add('unlocked');
    if (els.soundGate) els.soundGate.hidden = true;
    startLipReporting(); // 探针成功 = 音频能用 → 接上口型 ✗
  } else {
    showSoundGate();
  }
  return ok;
}

// ---- 口型：把跟读窗的音频电平报给角色窗 ------------------------------
// ⚠️ 2026-07-14 之前**这段根本不存在** ✗ —— 角色窗一直在轮询 `/api/lip` ✗，
//    但没人 POST 它 ✗ → 电平恒为 0 ✗ → 口型从来没动过 ✗。
//
// 为什么非得走 WebAudio：浏览器没有别的办法知道"正在播的音频有多大"✗。
// ⚠️ 两个坑：
//   ① `createMediaElementSource` 会把元素**永久**改道到 WebAudio ✗ ——
//      若 AudioContext 处于 suspended（自动播放策略 ✗），声音会**直接变哑** ✗
//      → 所以只在**音频已解锁之后**才创建它 ✅
//   ② 创建后必须 `connect(destination)` ✗，否则没声音 ✗。
let lip = null;

function startLipReporting() {
  if (lip) return true;
  if (!els.audio) return false;
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) {
    report('lip-no-webaudio', {});
    return false;
  }
  try {
    const ctx = new Ctx();
    const src = ctx.createMediaElementSource(els.audio);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.55;
    src.connect(analyser);
    analyser.connect(ctx.destination); // ⚠️ 不接就没声音 ✗
    lip = { ctx, analyser, data: new Uint8Array(analyser.fftSize), last: 0, seen: false };
  } catch (err) {
    report('lip-init-failed', { err: String(err).slice(0, 160) });
    return false;
  }
  if (lip.ctx.state === 'suspended') lip.ctx.resume().catch(() => {});

  const tick = () => {
    if (!lip) return;
    let level = 0;
    try {
      lip.analyser.getByteTimeDomainData(lip.data);
      let sum = 0;
      for (let i = 0; i < lip.data.length; i++) {
        const v = (lip.data[i] - 128) / 128;
        sum += v * v;
      }
      level = Math.min(1, Math.sqrt(sum / lip.data.length) * 3.4);
    } catch (err) { /* 忽略 */ }

    // 约 90ms 一次、每次一个小 POST —— 够跟上口型，也不至于把后端刷爆。
    const now = performance.now();
    if (now - lip.last > 90 || level === 0) {
      lip.last = now;
      fetch('/api/lip', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ level }),
      }).catch(() => { /* 后端忙就丢这一帧 */ });
    }
    if (!lip.seen && level > 0.08) {
      lip.seen = true;
      report('lip-first-level', { level: Number(level.toFixed(2)) });
    }
    requestAnimationFrame(tick);
  };
  tick();
  report('lip-started', { state: lip.ctx.state });
  return true;
}

// ---- 最近推送 / 活动记录 --------------------------------------------
function fmtTime(seconds) {
  return new Date(seconds * 1000).toLocaleTimeString('zh-CN', { hour12: false });
}

async function loadActivity() {
  const box = els.activityList;
  if (!box) return;
  try {
    const res = await fetch('/api/activity?limit=20');
    const data = await res.json();
    const rows = data.activity || [];
    if (!rows.length) {
      box.innerHTML = '<div class="act-empty">（暂无记录）</div>';
      return;
    }
    box.innerHTML = rows
      .map((row) => {
        const extra = row.detail ? JSON.stringify(row.detail) : '';
        return (
          '<div class="act-row">' +
          '<span class="act-time">' + fmtTime(row.at) + '</span>' +
          '<span class="act-event">' + escapeHtml(row.event) + '</span>' +
          '<span class="act-detail" title="' + escapeHtml(extra) + '">' +
          escapeHtml(extra).slice(0, 140) +
          '</span></div>'
        );
      })
      .join('');
  } catch (err) {
    box.textContent = '读取失败：' + err.message;
  }
}

const reader = new Reader({
  container: els.reader,
  audio: els.audio,
  onStatus: setStatus,
});

function closeStream() {
  if (stream) { stream.close(); stream = null; }
}

const WARN = '⚠️';

async function loadVoices() {
  setStatus('加载音色…');
  try {
    const res = await fetch('/api/voices');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();

    els.voice.innerHTML = '';
    const groups = new Map();
    for (const voice of data.voices || []) {
      if (!groups.has(voice.backend)) groups.set(voice.backend, []);
      groups.get(voice.backend).push(voice);
    }

    for (const [backend, list] of groups) {
      const group = document.createElement('optgroup');
      group.label = backend;
      for (const voice of list) {
        const option = document.createElement('option');
        option.value = voice.id;
        option.textContent = voice.license === 'unofficial'
          ? voice.name + ' ' + WARN
          : voice.name;
        if (voice.license === 'unofficial') option.title = '未授权来源：仅供个人使用，请自行确认合法性';
        group.appendChild(option);
      }
      els.voice.appendChild(group);
    }

    const all = data.voices || [];
    const prefs = loadPrefs();
    const preferred = all.find((v) => v.id === prefs.voice)      // 上次选的
      || all.find((v) => v.id === data.default)
      || all.find((v) => v.backend === 'say' && (v.lang || []).includes('zh'))
      || all.find((v) => v.backend === 'say')
      || all[0];
    if (preferred) els.voice.value = preferred.id;
    if (prefs.rate) {
      els.rate.value = prefs.rate;
      reader.setRate(parseFloat(prefs.rate));
    }

    // 关键：把当前音色同步给服务端 —— 否则「opencode 自动推送」的任务
    // 会用服务端默认音色（晓晓），而不是你在这里选的（踩过这个 bug）。
    if (preferred) syncVoiceToServer(preferred.id);

    renderPackProblems(data.pack_problems || []);
    setStatus('就绪 · ' + all.length + ' 个音色');
  } catch (err) {
    setStatus('音色加载失败：' + err.message);
  }
}

//: 当前任务的 SSE
let stream = null;

function subscribe(taskId, startIndex) {
  closeStream();
  currentTaskId = taskId;
  stream = new EventSource('/api/read/' + taskId + '/events');

  stream.addEventListener('plan', (event) => {
    const data = JSON.parse(event.data);
    const sentences = data.sentences.map((s) => s.text);

    if (data.append) {
      // Follow（边写边读）：只把新句子接在后面，不打断正在念的内容
      reader.appendPlan(sentences, data.sentences[0]?.i ?? reader.sentences.length);
      setStatus('又接上 ' + sentences.length + ' 句，继续念…');
      return;
    }

    reader.setPlan(sentences, startIndex);
    setStatus(
      sentences.length
        ? '文本已就绪（' + sentences.length + ' 句），正在合成…'
        : '正在等对方写第一句…'
    );
  });

  stream.addEventListener('segment', (event) => {
    reader.addSegment(JSON.parse(event.data));
  });

  stream.addEventListener('done', () => {
    reader.markDone();
    closeStream();
  });

  stream.addEventListener('error', (event) => {
    if (event.data) {
      try {
        const detail = JSON.parse(event.data);
        const index = detail.segment_index;
        if (Number.isInteger(index)) {
          // 关键：告诉播放器"这一句没有声音"，让它跳过 ——
          // 否则播放会永久停在"合成中"，因为那一句的 segment 永远不会到。
          reader.markMissing(index);
          setStatus(
            '第 ' + (index + 1) + ' 句合成失败，已跳过：' + (detail.message || detail.code)
          );
        } else {
          setStatus('错误：' + (detail.message || detail.code));
        }
        return;
      } catch (err) { /* 不是命名错误事件，按连接问题处理 */ }
    }
    setStatus('连接中断');
  });
}

async function startRead(text) {
  if (!text || !text.trim()) {
    setStatus('先粘贴一些文字');
    return;
  }

  closeStream();
  reader.reset();

  let res;
  try {
    res = await fetch('/api/read', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        voice: els.voice.value,
        source: { kind: 'block', app: 'web' },
      }),
    });
  } catch (err) {
    setStatus('请求失败：' + err.message);
    return;
  }

  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    setStatus('失败：' + (detail.message || ('HTTP ' + res.status)));
    return;
  }

  const data = await res.json();
  if (data.deduped) {
    setStatus('这段内容刚刚已经在读了，忽略重复请求');
    return;
  }
  setStatus('共 ' + data.sentences + ' 句，正在合成…');
  subscribe(data.task_id, data.start_index || 0);
}

/**
 * 订阅全服事件流。
 *
 * 这是「opencode 回答完自动朗读」能被听见的关键：插件是绕开浏览器直接 POST 的，
 * 跟读窗必须被**通知**才知道有新任务，然后自己接上去播放。
 */
function connectLive() {
  const live = new EventSource('/api/events');

  live.addEventListener('task-created', (event) => {
    let data = {};
    try { data = JSON.parse(event.data); } catch (err) { return; }
    if (!data.task_id || data.task_id === currentTaskId) return; // 自己发起的，跳过

    setStatus('收到外部推送（' + (data.source || '?') + '，' + (data.sentences || 0) + ' 句），自动接上…');
    reader.reset();
    subscribe(data.task_id, 0);
  });

  live.addEventListener('control', (event) => {
    // 远程控制（全局快捷键 → 后端 → 广播到这里）
    let data = {};
    try { data = JSON.parse(event.data); } catch (err) { return; }
    const action = data.action || 'toggle';
    if (action === 'toggle') reader.toggle();
    else if (action === 'pause') reader.pause();
    else if (action === 'resume') reader.resume();
    else if (action === 'next') reader.next();
    else if (action === 'prev') reader.prev();
    else if (action === 'stop') { reader.pause(); setStatus('已停止'); }
  });

  live.addEventListener('error', () => {
    // EventSource 自带重连；这里不打扰用户
  });
}

// ---- 事件绑定 --------------------------------------------------------
// 跟读窗自己聚焦时，空格 = 暂停/继续（不用去点按钮）
document.addEventListener('keydown', (event) => {
  if (event.code !== 'Space') return;
  const tag = (event.target && event.target.tagName) || '';
  if (tag === 'TEXTAREA' || tag === 'INPUT' || (event.target && event.target.isContentEditable)) return;
  event.preventDefault();
  reader.toggle();
});
els.readBtn.addEventListener('click', () => startRead(els.input.value));
els.clearBtn.addEventListener('click', () => {
  els.input.value = '';
  closeStream();
  currentTaskId = null;
  reader.reset();
  els.reader.innerHTML = '<p class="empty">还没有内容。</p>';
  setStatus('就绪');
});
els.playPause.addEventListener('click', () => reader.toggle());
els.prev.addEventListener('click', () => reader.prev());
els.next.addEventListener('click', () => reader.next());
els.rate.addEventListener('change', () => {
  reader.setRate(parseFloat(els.rate.value));
  savePrefs();
});
els.voice.addEventListener('change', async () => {
  savePrefs();
  const ok = await syncVoiceToServer(els.voice.value);
  const name = els.voice.selectedOptions?.[0]?.textContent || els.voice.value;
  setStatus(
    ok
      ? '音色已换成「' + name.trim() + '」· 自动朗读也会用它'
      : '音色已换（但同步给自动朗读失败）'
  );
});

// 用户第一次点页面任意处即解锁自动播放
document.addEventListener('pointerdown', unlockAudio, { once: true });
els.soundGateBtn.addEventListener('click', (event) => {
  event.stopPropagation();
  unlockAudio();
});

els.input.addEventListener('keydown', (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
    startRead(els.input.value);
  }
});

loadVoices();
connectLive();
probeAutoplay();
loadActivity();

if (els.activityRefresh) {
  els.activityRefresh.addEventListener('click', loadActivity);
}

// ── 托盘：标签页切换 ──────────────────────────────────────────────────
// 点标签 → 打开那一块（再点同一个 → 收起）；打开时才去拉数据，省得白跑。
function initTray() {
  const tray = document.getElementById('tray');
  if (!tray) return;
  const tabs = Array.from(tray.querySelectorAll('.tab[data-tab]'));
  const panes = Array.from(tray.querySelectorAll('[data-pane]'));
  const closeBtn = document.getElementById('trayClose');

  function openTab(name) {
    const already = tray.dataset.active === name;
    if (already) {
      tray.classList.remove('open');
      tray.dataset.active = '';
      tabs.forEach((t) => t.setAttribute('aria-selected', 'false'));
      return;
    }
    tray.dataset.active = name;
    tray.classList.add('open');
    tabs.forEach((t) => t.setAttribute('aria-selected', String(t.dataset.tab === name)));
    panes.forEach((p) => { p.hidden = p.dataset.pane !== name; });
    // 打开那一刻加载（活动记录 / 聊天状态都要现拉）
    if (name === 'activity') loadActivity();
    if (name === 'chat') { loadChatStatus(); loadLines(); }
    if (name === 'settings') loadSettings();
    if (name === 'export') refreshCacheInfo();
  }

  tabs.forEach((t) => t.addEventListener('click', () => openTab(t.dataset.tab)));
  closeBtn?.addEventListener('click', () => {
    tray.classList.remove('open');
    tray.dataset.active = '';
    tabs.forEach((t) => t.setAttribute('aria-selected', 'false'));
  });
}

initTray();


// ── 人物形象：开关 + 角色下拉 + 换装下拉（两个窗口共享）────────────────
// 和音色一样写进后端 /api/ui；角色窗口每 3 秒轮询 → 跟着换 / 自己退出。
// ⚠️ 「6 套衣服」不是 6 个形象 ✗ —— 它们都属于同一个角色（要楽奈 ✗），
//    那是**换装**的差别 ✗。所以这里分成「角色」和「换装」两个下拉 ✗。
async function initAvatarPickers() {
  const on = document.getElementById('avatarOn');
  const charSel = document.getElementById('character');
  const outfitSel = document.getElementById('outfit');
  if (!on || !charSel || !outfitSel) return;

  let info;
  try {
    info = await (await fetch('/api/avatar')).json();
  } catch (err) {
    on.disabled = charSel.disabled = outfitSel.disabled = true;
    return;
  }

  const characters = info.characters || [];
  on.checked = info.show_avatar !== false;
  on.disabled = characters.length === 0;

  function fillOutfits() {
    const c = characters.find((x) => x.id === charSel.value);
    const outfits = c?.outfits || [];
    outfitSel.innerHTML = '';
    for (const o of outfits) {
      const opt = document.createElement('option');
      opt.value = o.folder;
      // 把「几组动作 / 几个表情 / 有没有物理」写在选项里 —— 差异很大，选之前就该看见
      const bits = [];
      if (o.motions.length) bits.push(o.motions.length + ' 组动作');
      if (o.expressions.length) bits.push(o.expressions.length + ' 表情');
      if (o.physics) bits.push('物理');
      opt.textContent = o.label + (bits.length ? '　' + bits.join(' · ') : '');
      outfitSel.append(opt);
    }
    const byUi = outfits.find((o) => o.folder === info.active_outfit);
    const byCfg = outfits.find((o) => c?.model?.startsWith(o.path));
    outfitSel.value = (byUi || byCfg)?.folder || outfits[0]?.folder || '';
    outfitSel.disabled = !on.checked || !outfits.length;
  }

  charSel.innerHTML = '';
  for (const c of characters) {
    const opt = document.createElement('option');
    opt.value = c.id;
    opt.textContent = c.name;
    charSel.append(opt);
  }
  charSel.value = info.active_character || characters[0]?.id || '';
  fillOutfits();

  async function save() {
    try {
      await fetch('/api/ui', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          show_avatar: on.checked,
          character: charSel.value || null,
          outfit: outfitSel.value || null,
        }),
      });
    } catch (err) {
      // 失败算了 —— 下次再点
    }
  }

  on.addEventListener('change', async () => {
    outfitSel.disabled = !on.checked;
    await save();
  });
  charSel.addEventListener('change', async () => {
    fillOutfits();
    await save();
  });
  outfitSel.addEventListener('change', save);
}

initAvatarPickers();

// ── 角色聊天：状态 + 一键打开配置文件夹 ────────────────────────────────
// 为什么放在界面上：那两个配置文件在 ~/.config/nekopal/ 下，普通人根本找不到。
// 不配也完全能用（退回静态台词），所以这里只做「指路」+「说清现状」。
async function loadChatStatus() {
  const body = document.getElementById('chatBody');
  if (!body) return;
  let d;
  try {
    d = await (await fetch('/api/chat')).json();
  } catch (err) {
    body.textContent = '读不到配置：' + err;
    return;
  }

  const pool = Number(d.lines_count || 0);
  const src = await loadLineSource();
  const modeText = src === 'live'
    ? '每次点击让模型现编一句（慢几秒，但更应景）'
    : (pool
      ? '点击时从下面的台词池里抽（快）· 池里 ' + pool + ' 条'
      : '点击时想现编，但池子是空的 —— 会退回固定那几句');

  const rows = [
    d.enabled
      ? '✅ 已启用 —— ' + modeText
      : (d.config_error
        ? '⚠️ **chat.yaml 读不了** —— ' + d.config_error + '（修好才能用 AI ✗）'
        : '⬜ 未配置大模型 —— 现在用固定的那几句静态台词（完全能玩，想升级就填下面两个文件）'),
    d.enabled ? '🧠 模型：' + d.model : '',
    '⚙️ 大模型配置：' + d.chat_file,
    '   · api_key：' + (d.has_key
      ? '已填 ✅（接口只回报“有没有”，不会返回内容）'
      : '未填 ⬜'),
    '📄 我的信息：' + d.profile_file +
      (d.has_profile ? '（已填 ' + d.profile_chars + ' 字 ✅）' : '（还是空模板 ⬜）'),
    '🎭 角色性格：' +
      (d.has_persona
        ? '已填 ✅ ~/.config/nekopal/characters/<角色 id>/persona.md'
        : '未填 ⬜（会用仓库里的示例 characters/example-rana/persona.md）'),
    '⚠️ 两条红线：api_key 别提交进任何仓库；profile.md 里别放不该外发的东西',
  ].filter(Boolean);

  // 「生成时到底会用到什么」—— 摊开写，省得你猜 ✗
  const recipe = document.getElementById('linesRecipe');
  if (recipe) {
    if (d.config_error) {
      recipe.textContent = '⚠️ chat.yaml 有语法错误，生成会失败：' + d.config_error;
      recipe.classList.add('bad');
    } else {
      recipe.classList.remove('bad');
      recipe.textContent = '生成时会用到：' +
        (d.enabled ? '✅ 模型 ' + d.model : '⬜ 模型（没配）') + ' · ' +
        (d.has_persona ? '✅ 角色人设' : '⬜ 角色人设（退回示例）') + ' · ' +
        (d.has_profile ? '✅ 你的个人信息（' + d.profile_chars + ' 字）' : '⬜ 个人信息（空）') +
        ' ＋ 上面那句需求 ＋ 条数';
    }
  }

  body.innerHTML = '';
  for (const t of rows) {
    const div = document.createElement('div');
    div.textContent = t;
    body.append(div);
  }
}

function initChatPanel() {
  const openBtn = document.getElementById('chatOpen');
  const refreshBtn = document.getElementById('chatRefresh');

  openBtn?.addEventListener('click', async () => {
    try {
      await fetch('/api/chat/open', { method: 'POST' });
    } catch (err) {
      // 打不开就算了 —— 界面上本来就写了路径
    }
  });
  refreshBtn?.addEventListener('click', () => {
    refreshBtn.disabled = true;
    Promise.all([loadChatStatus(), loadLines()]).finally(() => {
      refreshBtn.disabled = false;
    });
  });
}

initChatPanel();

// ── 🎬 预生成台词池 ────────────────────────────────────────────────────
// 起因：`/api/avatar/chat` 每次点击都现打一次 LLM —— 点一下要等好几秒，
// 完全不像「随口一句」。改成**一次生成几十条存档**，点击时从池子里抽：
//   · 零延迟（同一句第二次连音频都走缓存）
//   · 一轮内不重复（后端用「抽签袋」）
//   · **看得见、改得动** —— 不喜欢的句子（比如叹气太多的）直接删
let linesData = null;

function fmtWhen(ts) {
  if (!ts) return '—';
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch (err) {
    return '—';
  }
}

async function loadLines() {
  const meta = document.getElementById('linesMeta');
  const list = document.getElementById('linesList');
  if (!list) return;

  let d;
  try {
    d = await (await fetch('/api/lines')).json();
  } catch (err) {
    if (meta) meta.textContent = '读不到台词池';
    return;
  }
  linesData = d;

  if (meta) {
    if (!d.count) {
      meta.textContent = '还没有 —— 点「生成台词」';
    } else {
      const bits = ['已存 ' + d.count + ' 条', fmtWhen(d.generated_at), d.model || '?'];
      if (d.stale) bits.push('⚠️ 人设或私人信息改过了，建议重新生成');
      meta.textContent = bits.join(' · ');
    }
  }

  list.innerHTML = '';
  if (!d.count) {
    const empty = document.createElement('div');
    empty.className = 'act-empty';
    empty.textContent = d.enabled
      ? '（还是空的。上面点「生成台词」，几十秒就好。）'
      : '（没配大模型 —— 生成不了。先在上面填 chat.yaml。）';
    list.append(empty);
    return;
  }

  d.lines.forEach((text, i) => {
    const row = document.createElement('div');
    row.className = 'line-row';

    const input = document.createElement('input');
    input.type = 'text';
    input.value = text;
    input.title = '改完点别处即保存';

    const del = document.createElement('button');
    del.className = 'ghost line-del';
    del.textContent = '✕';
    del.title = '删掉这句';
    del.addEventListener('click', () => {
      const now = collectLines();
      now.splice(i, 1);
      putLines(now, true);
    });

    row.append(input, del);
    list.append(row);
  });
}

function collectLines() {
  const list = document.getElementById('linesList');
  if (!list) return [];
  return Array.from(list.querySelectorAll('input')).map((el) => el.value.trim()).filter(Boolean);
}

async function putLines(lines, rerender) {
  const st = document.getElementById('linesStatus');
  try {
    const res = await fetch('/api/lines', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lines }),
    });
    const d = await res.json();
    if (!res.ok) {
      if (st) st.textContent = '✗ 保存失败';
      return;
    }
    linesData = d;
    if (rerender) await loadLines();
    else if (st) st.textContent = '已保存 ✅（' + d.count + ' 条）';
  } catch (err) {
    if (st) st.textContent = '✗ ' + err;
  }
}

async function generateLines() {
  const btn = document.getElementById('linesGen');
  const st = document.getElementById('linesStatus');
  const count = parseInt(document.getElementById('linesCount').value, 10) || 30;
  const want = (document.getElementById('linesWant')?.value || '').trim();
  if (btn) btn.disabled = true;
  if (st) st.textContent = '生成中…（一次写 ' + count + ' 句，别关窗口）';
  try {
    const res = await fetch('/api/lines', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ count, want }),
    });
    const d = await res.json();
    if (!res.ok) {
      if (st) st.textContent = '✗ ' + (d?.detail?.message || ('HTTP ' + res.status));
      return;
    }
    linesData = d;
    await loadLines();
    if (st) st.textContent = '✅ 生成 ' + d.count + ' 条 —— 可以直接改 / 删';
    loadActivity();
  } catch (err) {
    if (st) st.textContent = '✗ ' + err;
  } finally {
    if (btn) btn.disabled = false;
  }
}

/** 一键打开那个**统一的台词池文件** ✗（`~/.config/nekopal/lines.yaml` ✗）。 */
async function openLinesFile() {
  const st = document.getElementById('linesStatus');
  try {
    await fetch('/api/lines/open', { method: 'POST' });
    if (st) st.textContent = '已打开台词文件 ✅';
  } catch (err) {
    if (st) st.textContent = '✗ 打不开：' + err;
  }
}

async function clearLines() {
  if (!window.confirm('清空台词池？点角色时会退回「每次现编」（慢）。')) return;
  const st = document.getElementById('linesStatus');
  try {
    await fetch('/api/lines', { method: 'DELETE' });
    await loadLines();
    if (st) st.textContent = '已清空';
  } catch (err) {
    if (st) st.textContent = '✗ ' + err;
  }
}

async function loadLineSource() {
  const sel = document.getElementById('lineSource');
  if (!sel) return 'pool';
  let value = 'pool';
  try {
    const ui = await (await fetch('/api/ui')).json();
    value = ui.line_source || 'pool';
    sel.value = value;
  } catch (err) { /* 读不到就用默认 */ }
  return value;
}

function initLinesPanel() {
  const list = document.getElementById('linesList');
  if (!list) return;

  // 改完点别处（失焦）→ 整份存回去。用事件委托，只挂一次。
  list.addEventListener('change', (event) => {
    if (event.target && event.target.tagName === 'INPUT') putLines(collectLines(), false);
  });

  document.getElementById('linesGen')?.addEventListener('click', generateLines);
  document.getElementById('linesOpen')?.addEventListener('click', openLinesFile);
  document.getElementById('linesClear')?.addEventListener('click', clearLines);

  // ⚠️ 「一句话需求」按回车 = 直接生成 ✗（省得去点按钮 ✗）
  document.getElementById('linesWant')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      generateLines();
    }
  });

  const sel = document.getElementById('lineSource');
  sel?.addEventListener('change', async () => {
    try {
      await fetch('/api/ui', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ line_source: sel.value }),
      });
      setStatus(sel.value === 'pool' ? '点击时从台词池抽（快）' : '点击时每次现编（慢，更应景）');
    } catch (err) { /* 忽略 */ }
  });

  // ⚠️ 快捷键 ⌘/Ctrl + Shift + L ✗ —— 一键打开台词文件 ✅
  document.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && event.shiftKey
        && (event.key === 'l' || event.key === 'L')) {
      event.preventDefault();
      openLinesFile();
    }
  });
}

initLinesPanel();

// ── ⚙️ 设置面板 ───────────────────────────────────────────────────────
// 以前这些开关**后端都有、界面一个都没有**（只能改 ~/.config/nekopal/*.json）。
// 现在统一收在这里。改动即时 PUT /api/settings，后端会写进 ui.json 持久化。
const SETTING_KEYS = ['auto_read', 'follow', 'auto_scroll', 'prefetch', 'cache_max_mb'];

let settingsLoaded = false;

async function saveSetting(patch) {
  try {
    await fetch('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    return true;
  } catch (err) {
    setStatus('设置没保存上（后端没应答）');
    return false;
  }
}

async function loadSettings(force = false) {
  const box = document.getElementById('setAutoRead');
  if (!box) return;
  if (settingsLoaded && !force) { refreshCacheInfo(); return; }

  let s;
  try {
    s = await (await fetch('/api/settings')).json();
  } catch (err) {
    return;
  }
  settingsLoaded = true;

  document.getElementById('setAutoRead').checked = s.auto_read !== false;
  document.getElementById('setFollow').checked = s.follow !== false;
  document.getElementById('setScroll').value = s.auto_scroll || 'center';
  document.getElementById('setPrefetch').value = s.prefetch ?? 3;
  document.getElementById('setCacheMb').value = s.cache_max_mb ?? 500;
  const pitchEl = document.getElementById('setPitch');
  if (pitchEl) pitchEl.value = s.pitch ?? 0;
  const dir = document.getElementById('expDir');
  if (dir && s.export_dir) dir.textContent = s.export_dir;

  // 🌟 这两个存的是 `ui.json` ✗（不是 `/api/settings` ✗）
  //    —— 角色窗每 3 秒轮询 `/api/ui`，改完那边自己就变了 ✅
  try {
    const ui = await (await fetch('/api/ui', { cache: 'no-store' })).json();
    const paw = document.getElementById('setPawCursor');
    const gaze = document.getElementById('setGaze');
    if (paw) paw.checked = ui.paw_cursor !== false;
    if (gaze) gaze.checked = ui.gaze_follow !== false;
  } catch (err) { /* 读不到就用默认 */ }

  refreshCacheInfo();
}

async function refreshCacheInfo() {
  const el = document.getElementById('cacheInfo');
  if (!el) return;
  try {
    const d = await (await fetch('/api/cache')).json();
    const mb = (d.bytes / 1048576).toFixed(1);
    el.textContent = mb + ' MB / 上限 ' + d.max_mb + ' MB（' + (d.files || 0) + ' 个片段）';
  } catch (err) {
    el.textContent = '读不到缓存信息';
  }
}

function initSettingsPanel() {
  const map = {
    setAutoRead: { key: 'auto_read', read: (el) => el.checked, ok: '自动朗读已' },
    setFollow: { key: 'follow', read: (el) => el.checked, ok: '边写边读已' },
    setScroll: { key: 'auto_scroll', read: (el) => el.value, ok: '自动滚动已' },
    setPrefetch: { key: 'prefetch', read: (el) => parseInt(el.value, 10) || 0, ok: '预取句数已' },
    setCacheMb: { key: 'cache_max_mb', read: (el) => parseInt(el.value, 10) || 500, ok: '缓存上限已' },
    setPitch: { key: 'pitch', read: (el) => parseFloat(el.value) || 0, ok: '音高已' },
  };

  for (const [id, cfg] of Object.entries(map)) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.addEventListener('change', async () => {
      const value = cfg.read(el);
      const ok = await saveSetting({ [cfg.key]: value });
      if (ok) setStatus(cfg.ok + (typeof value === 'boolean' ? (value ? '打开' : '关闭') : '设为 ' + value));
    });
  }

  document.getElementById('cacheClear')?.addEventListener('click', async () => {
    if (!window.confirm('清空音频缓存？下次朗读会重新合成（不影响你的设置）。')) return;
    try {
      const d = await (await fetch('/api/cache', { method: 'DELETE' })).json();
      setStatus('已清空缓存（' + d.files + ' 个片段）');
      refreshCacheInfo();
      loadActivity();
    } catch (err) {
      setStatus('清缓存失败');
    }
  });

  document.getElementById('settingsRefresh')?.addEventListener('click', () => loadSettings(true));

  // 🌟 存 `ui.json` 的那两个 ✗（猫爪光标 / 视线跟随 ✗）
  //    走 `/api/ui` 而不是 `/api/settings` —— 角色窗**每 3 秒轮询 `/api/ui`** ✅，
  //    所以改完不用重开窗口，那边自己就变了 ✅
  for (const [id, key, label] of [
    ['setPawCursor', 'paw_cursor', '🐾 猫爪光标'],
    ['setGaze', 'gaze_follow', '👀 视线跟随'],
  ]) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.addEventListener('change', async () => {
      try {
        await fetch('/api/ui', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ [key]: el.checked }),
        });
        setStatus(label + '已' + (el.checked ? '打开' : '关闭'));
      } catch (err) {
        setStatus('保存失败（后端没应答）');
      }
    });
  }
}

initSettingsPanel();

// ── 💾 导出面板 ───────────────────────────────────────────────────────
// 后端早就有 `POST /api/export`，但界面一直没有入口 —— 只能走命令行。
async function doExport() {
  const status = document.getElementById('expStatus');
  const what = document.getElementById('expWhat').value;
  const body = {
    format: document.getElementById('expFormat').value,
    gap: parseFloat(document.getElementById('expGap').value) || 0.15,
    filename: document.getElementById('expName').value.trim(),
  };

  if (what === 'task') {
    if (!currentTaskId) {
      if (status) status.textContent = '还没有正在朗读的任务 —— 先点一次「朗读」';
      return;
    }
    body.task_id = currentTaskId;
  } else {
    const text = els.input.value.trim();
    if (!text) {
      if (status) status.textContent = '输入框是空的';
      return;
    }
    body.text = text;
  }

  if (status) status.textContent = '导出中…（长文要一会儿）';
  try {
    const res = await fetch('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const d = await res.json();
    if (!res.ok) {
      if (status) status.textContent = '✗ ' + (d?.detail?.message || d?.message || res.status);
      return;
    }
    const mb = (d.bytes / 1048576).toFixed(1);
    if (status) status.textContent = '✅ ' + d.filename + '（' + d.segments + ' 段 · ' + mb + ' MB）';
    setStatus('已导出 ' + d.filename);
    loadActivity();
  } catch (err) {
    if (status) status.textContent = '✗ ' + err;
  }
}

function initExportPanel() {
  document.getElementById('expGo')?.addEventListener('click', doExport);
}

initExportPanel();

// 启动时把设置拉一次（顺便填上导出目录、缓存用量），省得第一次展开面板是空的
loadSettings();

// ── 启动体检 ───────────────────────────────────────────────────────────
// ⚠️ 为什么要有它：`chat.yaml` 一旦写坏（多个空格 / 漏个冒号 ✗），
//    后端会**静默**判定"没配 LLM" ✗ → 点击台词悄悄退回静态那 6 句 ✗。
//    2026-10-14 真实踩到，用户完全看不出原因 ✗。
//    → 所以启动时查一次，坏了就**当场写在状态栏上** ✗。
async function checkChatHealth() {
  try {
    const d = await (await fetch('/api/chat', { cache: 'no-store' })).json();
    if (d.config_error) {
      setStatus('⚠️ chat.yaml 有语法错误：' + d.config_error + ' —— AI 台词已停用');
      report('chat-config-error', { error: d.config_error });
    }
  } catch (err) { /* 读不到就算了 */ }
}

checkChatHealth();

