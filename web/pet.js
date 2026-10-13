// 置顶小条：点一下就暂停 / 继续。
//
// 它**不直接控制声音** —— 声音在主跟读窗（浏览器）里。
// 这里只发一个控制命令，后端再广播给所有跟读窗（设计文档 6.x「远程控制」）。
//
// 好处：**不管当前焦点在哪个窗口、主窗是不是全屏**，它都能用 ——
// 这正是它存在的理由（切窗口很烦）。

const pet = document.querySelector('.pet');
const hint = document.querySelector('.hint');

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
document.getElementById('close').addEventListener('click', () => window.close());

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
