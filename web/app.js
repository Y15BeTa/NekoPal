// NekoPal 跟读窗前端的胶水层：音色加载 + 发起朗读 + 消费 SSE
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
};

const setStatus = (text) => { els.status.textContent = text; };

const reader = new Reader({
  container: els.reader,
  audio: els.audio,
  onStatus: setStatus,
});

let stream = null;

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

function subscribe(taskId, startIndex) {
  closeStream();
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
  setStatus('共 ' + data.sentences + ' 句，正在合成…');
  subscribe(data.task_id, data.start_index || 0);
}

// ---- 事件绑定 --------------------------------------------------------
els.readBtn.addEventListener('click', () => startRead(els.input.value));
els.clearBtn.addEventListener('click', () => {
  els.input.value = '';
  closeStream();
  reader.reset();
  els.reader.innerHTML = '<p class="empty">还没有内容。</p>';
  setStatus('就绪');
});
els.playPause.addEventListener('click', () => reader.toggle());
els.prev.addEventListener('click', () => reader.prev());
els.next.addEventListener('click', () => reader.next());
els.rate.addEventListener('change', () => reader.setRate(parseFloat(els.rate.value)));

// 粘贴后自动聚焦，方便直接点「朗读」
els.input.addEventListener('keydown', (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
    startRead(els.input.value);
  }
});

loadVoices();
