// 跟读窗播放器：音频队列 + 高亮同步 + 自动滚动
//
// 设计文档 5.3 的关键不变式：时间戳永远相对于「当前播放的音频时间轴」，
// 因此 audio.currentTime 可以直接和 words[] 的 t0/t1 比较 ——
// 无论语速是通过 playbackRate（前端）还是合成参数（后端）实现，都不需要换算。

const SCROLL_BLOCK = 'center';
// 词级匹配覆盖率低于此值时，退化为句级高亮
const WORD_COVERAGE_THRESHOLD = 0.5;

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

/**
 * 把后端的词级时间戳映射回原文的字符区间（贪心顺序匹配）。
 * 中文 WordBoundary 切分粒度较碎，匹配不上的片段直接跳过；
 * 覆盖率太低时返回空数组，由调用方退化为句级高亮。
 */
function buildSpans(text, words) {
  const spans = [];
  let cursor = 0;
  let matched = 0;

  for (const word of words || []) {
    const token = String(word.text || '').trim();
    if (!token) continue;
    const index = text.indexOf(token, cursor);
    if (index < 0) continue;
    const end = index + token.length;
    spans.push({ start: index, end, t0: word.t0, t1: word.t1 });
    matched += token.length;
    cursor = end;
  }

  const coverage = text.length ? matched / text.length : 0;
  return coverage >= WORD_COVERAGE_THRESHOLD ? spans : [];
}

export class Reader {
  constructor({ container, audio, onStatus }) {
    this.container = container;
    this.audio = audio;
    this.onStatus = onStatus || (() => {});

    this.sentences = [];
    this.sentenceEls = [];
    // index -> { audio, duration, words, spans, wordEls }
    this.segments = new Map();
    this.current = -1;
    this.activeWord = -1;
    this.startIndex = 0;
    this.started = false;
    this.pendingIndex = null;
    this.rate = 1.0;
    this.playing = false;
    this.done = false;

    this.audio.addEventListener('ended', () => this._onEnded());
    this.audio.addEventListener('timeupdate', () => this._onTime());
    this.audio.addEventListener('error', () => this.onStatus('音频播放失败'));

    this.container.addEventListener('click', (event) => {
      const el = event.target.closest('.sentence');
      if (el) this.playFrom(Number(el.dataset.i));
    });
  }

  // ---- 生命周期 ------------------------------------------------------
  reset() {
    this.audio.pause();
    this.audio.removeAttribute('src');
    this.sentences = [];
    this.sentenceEls = [];
    this.segments.clear();
    this.current = -1;
    this.activeWord = -1;
    this.started = false;
    this.pendingIndex = null;
    this.playing = false;
    this.done = false;
    this.container.innerHTML = '';
  }

  setPlan(sentences, startIndex = 0) {
    this.container.innerHTML = '';
    this.sentences = sentences.slice();
    this.sentenceEls = [];
    this.startIndex = Math.max(0, Math.min(startIndex, Math.max(0, sentences.length - 1)));
    this.done = false;
    this.started = false;

    const frag = document.createDocumentFragment();
    sentences.forEach((text, index) => {
      const el = document.createElement('p');
      el.className = 'sentence';
      el.dataset.i = String(index);
      el.textContent = text;
      frag.appendChild(el);
      this.sentenceEls.push(el);
    });
    this.container.appendChild(frag);
  }

  addSegment(segment) {
    const index = segment.i;
    const record = {
      audio: segment.audio,
      duration: segment.duration,
      words: segment.words || [],
      spans: [],
      wordEls: [],
    };
    this.segments.set(index, record);

    const el = this.sentenceEls[index];
    if (el && segment.words && segment.words.length) {
      const spans = buildSpans(this.sentences[index], segment.words);
      if (spans.length) {
        record.spans = spans;
        record.wordEls = this._renderSpans(el, this.sentences[index], spans);
      }
    }

    if (!this.started && index >= this.startIndex) {
      this.started = true;
      this.playFrom(index);
    } else if (this.pendingIndex === index) {
      this.playFrom(index);
    }
  }

  markDone() {
    this.done = true;
    if (!this.playing) this.onStatus('已读完');
  }

  // ---- 控制 ----------------------------------------------------------
  async playFrom(index) {
    if (index < 0 || index >= this.sentences.length) return;

    const segment = this.segments.get(index);
    if (!segment) {
      this.pendingIndex = index;
      this.onStatus('合成中…（第 ' + (index + 1) + ' 句）');
      return;
    }
    this.pendingIndex = null;

    if (this.current !== index) this._clearWords();
    this.current = index;
    this.activeWord = -1;
    this._markSentence(index);

    this.audio.src = segment.audio;
    this.audio.playbackRate = this.rate;
    try {
      await this.audio.play();
      this.playing = true;
      this._updateButton();
      this.onStatus(
        this.done
          ? '播放中'
          : '播放中 · 第 ' + (index + 1) + ' / ' + this.sentences.length + ' 句'
      );
    } catch (err) {
      this.playing = false;
      this._updateButton();
      this.onStatus('需要先与页面交互才能播放（点一下页面任意处）');
    }
  }

  toggle() {
    if (this.playing) {
      this.audio.pause();
      this.playing = false;
      this._updateButton();
      this.onStatus('已暂停');
      return;
    }
    if (this.current < 0) {
      this.playFrom(this.startIndex);
      return;
    }
    this.audio.play().then(() => {
      this.playing = true;
      this._updateButton();
      this.onStatus('播放中');
    }).catch(() => this.onStatus('播放失败'));
  }

  next() { this.playFrom(Math.min(this.current + 1, this.sentences.length - 1)); }

  prev() { this.playFrom(Math.max(this.current - 1, 0)); }

  setRate(rate) {
    this.rate = rate;
    // 关键：playbackRate 不改变音频字节，也不需要任何时间戳换算
    this.audio.playbackRate = rate;
  }

  // ---- 内部 ----------------------------------------------------------
  _onEnded() {
    this.sentenceEls[this.current]?.classList.add('done');
    const next = this.current + 1;
    if (next < this.sentences.length) {
      this.playFrom(next);
    } else {
      this.playing = false;
      this._updateButton();
      this.onStatus('已读完');
    }
  }

  _onTime() {
    const record = this.segments.get(this.current);
    if (!record || !record.spans.length) return;

    const t = this.audio.currentTime;
    let lo = 0;
    let hi = record.spans.length - 1;
    let found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const span = record.spans[mid];
      if (t < span.t0) hi = mid - 1;
      else if (t >= span.t1) lo = mid + 1;
      else { found = mid; break; }
    }
    if (found !== this.activeWord) this._markWord(found);
  }

  _markSentence(index) {
    this.sentenceEls.forEach((el, i) => el.classList.toggle('active', i === index));
    this.sentenceEls[index]?.scrollIntoView({ behavior: 'smooth', block: SCROLL_BLOCK });
  }

  /** 只在**当前段**的词元素里切换高亮，绝不越界到别的句子。 */
  _markWord(wordIndex) {
    const record = this.segments.get(this.current);
    this.activeWord = wordIndex;
    if (!record) return;
    record.wordEls.forEach((el, i) => el.classList.toggle('active', i === wordIndex));
  }

  _clearWords() {
    for (const record of this.segments.values()) {
      record.wordEls.forEach((el) => el.classList.remove('active'));
    }
    this.activeWord = -1;
  }

  _renderSpans(el, text, spans) {
    let html = '';
    let cursor = 0;
    spans.forEach((span, k) => {
      if (span.start > cursor) html += escapeHtml(text.slice(cursor, span.start));
      html += '<span class="w" data-k="' + k + '">' +
        escapeHtml(text.slice(span.start, span.end)) + '</span>';
      cursor = span.end;
    });
    html += escapeHtml(text.slice(cursor));
    el.innerHTML = html;
    return Array.from(el.querySelectorAll('.w'));
  }

  _updateButton() {
    const button = document.getElementById('playPause');
    if (button) button.textContent = this.playing ? '⏸' : '▶';
  }
}
