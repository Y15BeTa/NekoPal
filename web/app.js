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
};

//: 当前正在读的任务；用于区分「自己点的」与「外部推来的」
let currentTaskId = null;
//: 浏览器是否已解锁自动播放
let audioUnlocked = false;

function setStatus(text) {
  els.status.textContent = text;
  // 播放被浏览器拦下时，把解锁提示亮出来
  if (typeof text === 'string' && text.includes('需要先与页面交互')) showSoundGate();
}

function showSoundGate() {
  if (!audioUnlocked && els.soundGate) els.soundGate.hidden = false;
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
    // 播放一段 0 字节静音 WAV：满足"在用户手势中播放过"的条件，又不出声。
    // 用**独立的 Audio 对象**，避免污染主播放器的状态（曾导致误报"已读完"）。
    const primer = new Audio(
      'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA='
    );
    primer.muted = true;
    await primer.play();
    primer.pause();
    setStatus('声音已启用 · opencode 回答完就会自动朗读');
  } catch (err) {
    setStatus('解锁失败，请直接点一次「朗读」');
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
    const preferred = all.find((v) => v.id === data.default)
      || all.find((v) => v.backend === 'say' && (v.lang || []).includes('zh'))
      || all.find((v) => v.backend === 'say')
      || all[0];
    if (preferred) els.voice.value = preferred.id;

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
    reader.setPlan(data.sentences.map((s) => s.text), startIndex);
    setStatus('文本已就绪（' + data.sentences.length + ' 句），正在合成…');
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
        setStatus('错误：' + (detail.message || detail.code));
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
els.rate.addEventListener('change', () => reader.setRate(parseFloat(els.rate.value)));

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
showSoundGate();
