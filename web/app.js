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
  activityPanel: $('activityPanel'),
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
  } else {
    showSoundGate();
  }
  return ok;
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

  live.addEventListener('error', () => {
    // EventSource 自带重连；这里不打扰用户
  });
}

// ---- 事件绑定 --------------------------------------------------------
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
if (els.activityPanel) {
  els.activityPanel.addEventListener('toggle', () => {
    if (els.activityPanel.open) loadActivity();
  });
}
