/**
 * NekoPal 的 Live2D 渲染胶水 —— 用 **Live2D 官方 Cubism 5 框架**把模型画到 canvas 上。
 *
 * ## 为什么不用 pixi-live2d-display
 *
 * 那个库（0.4.0）是按 Cubism **4** 的 Core API 写的，而我们要 Core **5**
 * （模型是 moc v5）。差分**不是补一个字段名能解决的**：
 *   - `moc.canvasWidth`          → Core 5 挪到了 `model.canvasinfo`
 *   - `drawables.vertexPositions` → Core 5 给「每个部件一个 Float32Array」，
 *                                   那个库当「一整块扁平数组」用
 *   - `drawables.renderOrders`    → Core 5 叫 `drawOrders`
 * 硬跑的后果是**一屏碎片**。官方框架与 Core 5 是配套的，所以走这条。
 *
 * ## 这个文件的性质
 *
 * **我们自己写的胶水**。用 esbuild 打成单个 `pet.js`，产物放
 * `~/.config/nekopal/l2d/` —— **不进仓库**（里面打包了 Live2D 官方框架）。
 *
 * 关键 API 都是照着官方示例（`Samples/TypeScript/Demo/src/lappmodel.ts`）来的：
 * - 着色器：`renderer.loadShaders(path)` + `renderer.drawModel(path)`
 * - 贴图：`renderer.bindTexture(i, glTexture)`
 * - 物理 / pose 的句柄是 `protected`，所以**继承** `CubismUserModel` 才拿得到
 */

import { CubismDefaultParameterId } from './cubismdefaultparameterid';
import { CubismModelSettingJson } from './cubismmodelsettingjson';
import { CubismEyeBlink } from './effect/cubismeyeblink';
import { CubismBreath, BreathParameterData } from './effect/cubismbreath';
import { CubismBreathUpdater } from './motion/cubismbreathupdater';
import { CubismEyeBlinkUpdater } from './motion/cubismeyeblinkupdater';
import { CubismMotion } from './motion/cubismmotion';
import { CubismMotionManager } from './motion/cubismmotionmanager';
import { CubismPhysicsUpdater } from './motion/cubismphysicsupdater';
import { CubismPoseUpdater } from './motion/cubismposeupdater';
import { CubismUpdateScheduler } from './motion/cubismupdatescheduler';
import { CubismMatrix44 } from './math/cubismmatrix44';
import { CubismModelMatrix } from './math/cubismmodelmatrix';
import { CubismViewMatrix } from './math/cubismviewmatrix';
import { CubismUserModel } from './model/cubismusermodel';
import { CubismFramework, LogLevel, Option } from './live2dcubismframework';

export interface MountOptions {
  canvas: HTMLCanvasElement;
  /** `xxx.model3.json` 的完整 URL */
  modelUrl: string;
  /** 着色器目录，**以 / 结尾** */
  shaderBase: string;
  /** 把消息送回页面（页面再转发到原生日志） */
  onLog?: (text: string) => void;
}

export interface PetHandle {
  width: number;
  height: number;
  /** 点一下角色时调它 —— 随机播一个非待机动作 */
  playReaction(): boolean;
  /** 眨一次眼 */
  blink(): boolean;
  destroy(): void;
}

/** 模型在「逻辑屏幕」里占的高度比例（1 = 恰好填满）。调这个就能改大小。 */
const FIT = 0.95;

/**
 * 继承一下 `CubismUserModel`。
 *
 * 唯一目的：`_physics` / `_pose` 在框架里是 **protected**，
 * 只有子类能拿到 —— 而挂物理和 pose 必须要它们。
 */
class PetUserModel extends CubismUserModel {
  physics(): any {
    return (this as any)._physics;
  }

  pose(): any {
    return (this as any)._pose;
  }
}

function joinUrl(dir: string, name: string): string {
  return name ? dir + name : '';
}

async function fetchBuffer(url: string): Promise<ArrayBuffer | null> {
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    return await res.arrayBuffer();
  } catch (err) {
    return null;
  }
}

export async function mountPet(opts: MountOptions): Promise<PetHandle> {
  const log = (s: string) => {
    if (opts.onLog) opts.onLog(s);
  };

  const { canvas } = opts;
  const gl = canvas.getContext('webgl', {
    alpha: true,
    premultipliedAlpha: true,
  });
  if (!gl) throw new Error('拿不到 WebGL 上下文');

  // ── ① 框架启动 ────────────────────────────────────────────────────────
  const option = new Option();
  option.logFunction = (m: string) => log('[cubism] ' + m);
  option.loggingLevel = LogLevel.LogLevel_Error;
  if (!CubismFramework.startUp(option)) throw new Error('startUp 失败');
  CubismFramework.initialize();

  // ── ② 读 model3.json ─────────────────────────────────────────────────
  const dir = opts.modelUrl.slice(0, opts.modelUrl.lastIndexOf('/') + 1);
  const settingBuf = await fetchBuffer(opts.modelUrl);
  if (!settingBuf) throw new Error('读不到 model3.json');
  const setting = new CubismModelSettingJson(settingBuf, settingBuf.byteLength);
  log(
    'model3.json: 模型=' + setting.getModelFileName() +
      ' · 贴图=' + setting.getTextureCount() +
      ' · 物理=' + (setting.getPhysicsFileName() || '无') +
      ' · pose=' + (setting.getPoseFileName() || '无'),
  );

  // ── ③ moc ───────────────────────────────────────────────────────────
  const mocBuf = await fetchBuffer(joinUrl(dir, setting.getModelFileName()));
  if (!mocBuf) throw new Error('读不到 moc3');

  const user = new PetUserModel();
  user.loadModel(mocBuf, true);
  const model = user.getModel();
  if (!model) throw new Error('loadModel 之后拿不到 model');

  // ── ④ 贴图 + renderer ───────────────────────────────────────────────
  // ⚠️ 顺序很讲究：createRenderer 内部已经 initialize 过，
  //    但 **gl 要另外用 startUp(gl) 交进去**（1596/1637 行的那个方法）。
  //    少了它，后面全是 'gl is null' ✗
  user.createRenderer(canvas.width, canvas.height);
  const renderer = user.getRenderer();
  if (!renderer) throw new Error('createRenderer 之后拿不到 renderer');
  renderer.startUp(gl);

  // ⚠️ `getTextureFileName(i)` **已经带目录**（如 `Textures/texture_00.png`），
  //    不要再拼 `getTextureDirectory()` —— 会变成 `TexturesTextures/...` ✗（踩过）
  for (let i = 0; i < setting.getTextureCount(); i++) {
    const name = setting.getTextureFileName(i);
    const url = joinUrl(dir, name);
    const image = await new Promise<HTMLImageElement>((resolve, reject) => {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error('贴图读不到：' + url));
      img.src = url;
    });

    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    // ⚠️ 官方用的是 bindTexture（不是 setTexture ✗）
    renderer.bindTexture(i, tex);
  }
  log('贴图已上传 ' + setting.getTextureCount() + ' 张');

  renderer.setIsPremultipliedAlpha(true);
  user.setRenderTargetSize(canvas.width, canvas.height);
  // ⚠️ 着色器是**运行时 fetch** 的，路径交给 renderer（不是那个单例 ✗）
  renderer.loadShaders(opts.shaderBase);
  log('着色器路径 ' + opts.shaderBase);

  // ── ⑤ 特效（呼吸 / 眨眼 / 物理 / pose）───────────────────────────────
  // 这一串是官方示例的标准做法：CubismUpdateScheduler 每帧按顺序跑 updater。
  // 没有它，模型是**拼好的但一动不动**（不呼吸、不眨眼、头发不摆）。
  //
  // ⚠️ updater **没有** initialize() 方法 ✗ —— 构造完直接塞给 scheduler 就行。
  //    而且眼眨的构造是 (motionUpdated 回调, eyeBlink)，不是 (eyeBlink, model) ✗
  const scheduler = new CubismUpdateScheduler();

  // 动作有没有在播 —— 眼眨要靠它决定"别在动的时候眨眼"（官方做法）
  let motionUpdated = false;

  // 眼眨：**自己实现**（见下面的 updateBlink），不用框架那个会炸的 ✗
  log('眨眼：自己实现 ✅（参数个数见下面的「特效 id」）');

  // ⚠️ 参数 id 必须是 CubismFramework.getIdManager().getId(...) 的产物，
  //    不能直接塞字符串 —— 否则呼吸完全不生效（而且不报错 ✗）
  const ids = CubismFramework.getIdManager();
  const breath = CubismBreath.create();
  breath.setParameters([
    new BreathParameterData(ids.getId(CubismDefaultParameterId.ParamAngleX), 0.0, 15.0, 6.5345, 0.5),
    new BreathParameterData(ids.getId(CubismDefaultParameterId.ParamAngleY), 0.0, 8.0, 3.5345, 0.5),
    new BreathParameterData(ids.getId(CubismDefaultParameterId.ParamAngleZ), 0.0, 10.0, 5.5345, 0.5),
    new BreathParameterData(ids.getId(CubismDefaultParameterId.ParamBodyAngleX), 0.0, 4.0, 15.5345, 0.5),
    new BreathParameterData(ids.getId(CubismDefaultParameterId.ParamBreath), 0.5, 0.5, 3.2345, 1),
  ]);
  scheduler.addUpdatableList(new CubismBreathUpdater(breath));

  const physicsName = setting.getPhysicsFileName();
  if (physicsName) {
    const buf = await fetchBuffer(joinUrl(dir, physicsName));
    if (buf) {
      user.loadPhysics(buf, buf.byteLength);
      const physics = user.physics();
      if (physics) {
        scheduler.addUpdatableList(new CubismPhysicsUpdater(physics));
        log('物理已挂上（头发/衣服会摆了）✅');
      }
    }
  }

  const poseName = setting.getPoseFileName();
  if (poseName) {
    const buf = await fetchBuffer(joinUrl(dir, poseName));
    if (buf) {
      user.loadPose(buf, buf.byteLength);
      const pose = user.pose();
      if (pose) {
        scheduler.addUpdatableList(new CubismPoseUpdater(pose));
        log('pose 已挂上 ✅');
      }
    }
  }

  // ── ⑥ 缩放：让 **view 直接框住角色的包围盒**（不碰模型矩阵）────────────
  //
  // 为什么这么绕：`CubismModelMatrix` 的构造函数里**藏了一次 setHeight(2.0)** ✗，
  // 而且 `setWidth/setHeight` 都是按它内部的 `_width/_height` 算缩放 ✗ ——
  // 叠起来很容易算出意料之外的结果。
  //
  // 而这个模型**没有 Layout** ✗（model3.json 里没这个字段），
  // 所以没有任何东西负责把它居中 ✗ —— 它就一直画在画布左下角。
  //
  // ⇒ 最干净的做法：**模型矩阵保持单位矩阵**（完全不动模型），
  //   让 view 的逻辑矩形**正好框住角色的包围盒**，并按窗口比例外扩。
  //   这样：等比 ✅ 自动居中 ✅ 尽量大 ✅ 一个 API 坑都不踩 ✅
  const canvasAspect = canvas.width / canvas.height;
  const view = new CubismViewMatrix();

  const b = modelBounds();
  const bw = b.x1 - b.x0;
  const bh = b.y1 - b.y0;
  const cx = (b.x0 + b.x1) / 2;
  const cy = (b.y0 + b.y1) / 2;

  let hw = bw / 2;
  let hh = bh / 2;
  // 按窗口比例把框外扩（保证逻辑矩形和 viewport 比例一致 → 绝不变形 ✗）
  if (bw / bh > canvasAspect) {
    hh = hw / canvasAspect;
  } else {
    hw = hh * canvasAspect;
  }
  hw /= FIT; // FIT = 留边距（0.95 → 角色占 95%）
  hh /= FIT;

  view.setScreenRect(cx - hw, cx + hw, cy - hh, cy + hh);

  // 模型矩阵：单位矩阵（不做任何变换）
  const modelMatrix = new CubismMatrix44();

  log(
    '缩放：角色包围盒 ' + bw.toFixed(3) + '×' + bh.toFixed(3) +
      '（比例 ' + (bw / bh).toFixed(3) + '）· 画布 ' +
      model.getCanvasWidth() + '×' + model.getCanvasHeight() +
      ' · 逻辑框 ' + (2 * hw).toFixed(3) + '×' + (2 * hh).toFixed(3) +
      '（' + (hw / hh).toFixed(3) + '）· 窗口 ' + canvasAspect.toFixed(3) +
      ' · 边距 ' + FIT,
  );

  /** 量出角色真正占了多大（画布往往比角色宽 ✗，按画布缩放会显得又小又细 ✗） */
  function modelBounds(): { x0: number; y0: number; x1: number; y1: number } {
    let x0 = Infinity;
    let y0 = Infinity;
    let x1 = -Infinity;
    let y1 = -Infinity;
    // ⚠️ 框架的 CubismModel 没有 `.drawables`（那是 Core 对象 ✗），
    //    它给的是 getDrawableVertexPositions(i) → Float32Array（x,y 交替 ✗）
    const n = model.getDrawableCount();
    for (let i = 0; i < n; i++) {
      const arr = model.getDrawableVertexPositions(i) as unknown as Float32Array;
      if (!arr || !arr.length) continue;
      for (let k = 0; k + 1 < arr.length; k += 2) {
        const x = arr[k];
        const y = arr[k + 1];
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
      }
    }
    if (!Number.isFinite(x0) || x1 <= x0 || y1 <= y0) {
      // 量不出来就退回画布（至少不会崩 ✗）
      return { x0: 0, y0: 0, x1: model.getCanvasWidth(), y1: model.getCanvasHeight() };
    }
    return { x0, y0, x1, y1 };
  }

  // ── ⑦ 动作（素材都在模型里：这个形象有 246 个）───────────────────────
  // ⚠️ 整段包 try/catch：动作加载失败**绝不能**影响画面 ✗
  //    （踩过：一句 setFadeIn 写错，整个模型就挂不上了）
  const motionManager = new CubismMotionManager();
  const motions = new Map<string, CubismMotion>();
  const groups: string[] = [];

  try {
    for (let g = 0; g < setting.getMotionGroupCount(); g++) {
      const group = setting.getMotionGroupName(g);
      groups.push(group);
      for (let i = 0; i < setting.getMotionCount(group); i++) {
        const file = setting.getMotionFileName(group, i);
        if (!file) continue;
        const buf = await fetchBuffer(joinUrl(dir, file));
        if (!buf) continue;
        // ⚠️ **不传 modelSetting**：模型设里那个 Groups/EyeBlink 解析不出 id，
        //    传进去会让 motion 内部抱着 null id，跑到 updateMotion 就炸 ✗
        //    （画面不受影响 —— 动作整段有 try/catch 兜着）
        const motion = user.loadMotion(
          buf,
          buf.byteLength,
        ) as unknown as CubismMotion;
        if (!motion) continue;
        // ⚠️ 是 setFadeInTime / setFadeOutTime（不是 setFadeIn ✗）
        motion.setFadeInTime(1.0);
        motion.setFadeOutTime(1.0);
        motions.set(group + '_' + i, motion);
      }
    }
    log('动作已加载 ' + motions.size + ' 个 · 分组 [' + groups.join(', ') + ']');
  } catch (err) {
    log('动作加载出错（不影响画面）✗ ' + (err && err.message ? err.message : err));
  }

  const idleGroup = groups.find((g) => /idle/i.test(g)) || groups[0] || '';

  // ── 眨眼（自己实现）─────────────────────────────────────────────────
  // 框架的 CubismEyeBlink 在这形象上内部 id 会是 null，一跑就炸 ✗。
  // 但眨眼本质就是「把 ParamEyeLOpen / ParamEyeROpen 压到 0 再抬回来」✗ ——
  // 直接写参数更简单、更可控，也不用跟框架那套较劲 ✅
  const eyeIds: unknown[] = [];
  const eyeCount = setting.getEyeBlinkParameterCount
    ? setting.getEyeBlinkParameterCount()
    : 0;
  for (let i = 0; i < eyeCount; i++) eyeIds.push(setting.getEyeBlinkParameterId(i));

  const lipIds: unknown[] = [];
  const lipCount = setting.getLipSyncParameterCount
    ? setting.getLipSyncParameterCount()
    : 0;
  for (let i = 0; i < lipCount; i++) lipIds.push(setting.getLipSyncParameterId(i));

  // ⚠️ 必须给每个 motion 调 setEffectIds：CubismMotion 的 _eyeBlinkParameterIds
  //    默认是 **null**，而 updateParameters 里直接 `.length` → 一播就抛错 ✗
  //    （官方示例没调，因为它在别处设过；我们这条路径得自己来）
  //    顺带把 lipSync 的 id 也带上 —— 口型同步将来就靠它 ✅
  for (const m of motions.values()) {
    const setter = (m as unknown as { setEffectIds?: (a: unknown[], b: unknown[]) => void })
      .setEffectIds;
    if (typeof setter === 'function') setter.call(m, eyeIds, lipIds);
  }
  log('特效 id：眨眼 ' + eyeIds.length + ' 个 · 口型 ' + lipIds.length + ' 个');

  let blinkLeft = 0;
  let blinkWait = 1.5 + Math.random() * 3;

  /** 眨一次眼 */
  function blink(): boolean {
    if (!eyeIds.length) return false;
    blinkLeft = 0.13; // 130ms，接近真人的眨眼时长
    return true;
  }

  /** 自己算眨眼：0.13s 内做 1 → 0 → 1 的三角波 */
  function updateBlink(dt: number) {
    if (!eyeIds.length) return;
    let open = 1;
    if (blinkLeft > 0) {
      blinkLeft -= dt;
      const t = Math.max(0, blinkLeft) / 0.13; // 1 → 0
      open = Math.abs(2 * t - 1); // 1 → 0 → 1
    } else {
      blinkWait -= dt;
      if (blinkWait <= 0) {
        blinkWait = 1.5 + Math.random() * 3;
        blinkLeft = 0.13;
        open = 0;
      }
    }
    for (const id of eyeIds) {
      model.setParameterValueById(id as never, open);
    }
  }

  /** 从某一组里随机挑一个播。`priority` 用 3(Force) 表示"点了立刻有反应"。 */
  function playRandomFrom(group: string, priority = 2): boolean {
    if (!group) return false;
    const n = setting.getMotionCount(group);
    if (!n) return false;
    const motion = motions.get(group + '_' + Math.floor(Math.random() * n));
    if (!motion) return false;
    motionManager.startMotionPriority(motion, false, priority);
    return true;
  }

  /** 点一下角色时用：优先挑**非待机**的动作（更有反应感 ✗） */
  function playReaction(): boolean {
    const others = groups.filter((g) => g !== idleGroup);
    const pool = others.length ? others : groups;
    if (!pool.length) return false;
    return playRandomFrom(pool[Math.floor(Math.random() * pool.length)], 3);
  }

  // ── ⑧ 每帧 ──────────────────────────────────────────────────────────
  let raf = 0;
  let last = performance.now();
  let reported = false;

  const tick = () => {
    // ⚠️ **先排下一帧再干活**：这样即使下面哪一步抛错，动画也不会整个停摆 ✗
    //    （踩过：一个坏掉的 updater 把帧循环带停，画面直接冻住）
    raf = requestAnimationFrame(tick);
    try {
      const now = performance.now();
      const dt = Math.min((now - last) / 1000, 0.1); // 卡顿时别让物理炸掉
      last = now;

      model.loadParameters();

      // 动作单独隔离：`updateMotion` 内部会碰眼眨那套，而这个形象的数据有坑 ✗
      // ⚠️ 底线是**画面绝不能因为它挂掉** —— 所以它出错只跳过动作，不跳过绘制
      try {
        if (motionManager.isFinished()) {
          playRandomFrom(idleGroup, 2);
          motionUpdated = false;
        } else {
          motionUpdated = motionManager.updateMotion(model, dt);
        }
      } catch (err) {
        if (!motionErrors) {
          motionErrors = true;
          log('动作更新出错（画面不受影响）✗ ' + (err && err.message ? err.message : err));
        }
      }
      model.saveParameters();

      scheduler.onLateUpdate(model, dt);

      // 眨眼放在最后：让它**盖过**呼吸/物理对这些参数的写入 ✗
      updateBlink(dt);

      model.update(); // Core 把顶点算成**变形后**的位置 —— 不做就会散架 ✗

      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);

      const mvp = new CubismMatrix44();
      mvp.setMatrix(view.getArray());
      mvp.multiplyByMatrix(modelMatrix);
      renderer.setMvpMatrix(mvp);
      renderer.drawModel(opts.shaderBase);
    } catch (err) {
      if (!loopErrors) {
        loopErrors = true;
        log('帧循环出错（已跳过这一帧，动画继续）✗ ' + (err && err.message ? err.message : err));
      }
    }
  };
  let loopErrors = false;
  let motionErrors = false;
  raf = requestAnimationFrame(tick);

  return {
    width: model.getCanvasWidth(),
    height: model.getCanvasHeight(),
    playReaction,
    blink,
    destroy() {
      cancelAnimationFrame(raf);
      CubismFramework.dispose();
    },
  };
}
