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
import { CubismExpressionUpdater } from './motion/cubismexpressionupdater';
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
  /** 有哪些动作分组（右键菜单用） */
  listMotionGroups(): string[];
  /** 播某一组的随机一个动作 */
  playMotionGroup(group: string): boolean;
  /** 有哪些表情（右键菜单用；可能为空 ✗） */
  listExpressions(): string[];
  /** 切表情 */
  setExpression(name: string): boolean;
  /** 设定嘴张开程度 0..1（口型同步用） */
  setMouth(open: number): void;
  /**
   * 视线跟随：归一化光标位置（-1..1，+x 右、+y 下）。
   * 透明区域是"点穿"的 —— 网页收不到那里的 mousemove，所以由原生面板推过来。
   */
  setGaze(nx: number, ny: number): void;
  /** 窗口尺寸变了 → 重新量包围盒 + 重算矩阵 */
  refit(): void;
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

  /** 表情管理器（protected ✗ 只能从子类拿） */
  expressions(): any {
    return (this as any)._expressionManager;
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
  // ⚠️ **换装**要能重新挂载 ✗ —— 所以只在没启动时 startUp（框架是单例 ✗）
  if (CubismFramework.isStarted && !CubismFramework.isStarted()) {
    if (!CubismFramework.startUp(option)) throw new Error('startUp 失败');
    CubismFramework.initialize();
  }

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
  // ⚠️ **必须先 update 一次再量包围盒** ✗
  //    `getDrawableVertexPositions` 给的是「上一帧算好的顶点」——
  //    挂载时还没跑过任何一帧，量到的是空/过期数据 → 框出一个错的框 ✗
  //    （症状：角色缩成一小团、还偏在角落 —— 用户实测截图确认过）
  model.update();

  // 把**所有尺寸数字**都打出来：变形问题只能靠数字定位 ✗
  log(
    '尺寸：canvas attr ' + canvas.width + '×' + canvas.height +
      ' · canvas CSS ' + canvas.clientWidth + '×' + canvas.clientHeight +
      ' · dpr ' + (window.devicePixelRatio || 1) +
      ' · window ' + window.innerWidth + '×' + window.innerHeight,
  );

  /**
   * 量包围盒 + 算 MVP 矩阵。
   *
   * 抽成函数是因为**窗口可以缩放** ✗ —— 尺寸一变就要重算（见 refit ✗）。
   * 矩阵是**手写**的：scale(2/visW, 2/visH) + translate(-cx,-cy)。
   * 用 CubismViewMatrix 会被它的 maxScreenRect 夹取平移 ✗（默认全 0 ✗），
   * 把视图挤成一团、脸就成锥子了 ✗（真踩过）。
   */
  let mvpMatrix = new CubismMatrix44();

  function fit(): void {
    // ⚠️ 必须先 update 一次再量 ✗：getDrawableVertexPositions 给的是"上一帧的顶点"，
    //    挂载时还没跑过任何一帧，量到的是空数据 ✗
    model.update();

    const canvasAspect = canvas.width / canvas.height;
    const b = modelBounds();
    const bw = b.x1 - b.x0;
    const bh = b.y1 - b.y0;
    const cx = (b.x0 + b.x1) / 2;
    const cy = (b.y0 + b.y1) / 2;

    // 可视范围（**模型坐标系**里的宽高）
    // ⚠️ 关键：visW/visH 的比值**必须等于 canvas 的宽高比** → 否则就是横向拉扁 ✗
    let visW = (bh / FIT) * canvasAspect;
    let visH = bh / FIT;
    if (visW < bw / FIT) {
      // 她比窗口"胖" → 改成按宽度填满（宁可上下留白，也不裁人 ✗）
      visW = bw / FIT;
      visH = visW / canvasAspect;
    }

    const matrix = new CubismMatrix44();
    matrix.scale(2 / visW, 2 / visH);
    matrix.translate(-cx, -cy);
    mvpMatrix = matrix;

    // 验算：模型 x 单位与 y 单位最终落在屏幕上的**像素数**应当相等（相等=不变形 ✗）
    const pxPerUnitX = (2 / visW) * canvas.width * 0.5;
    const pxPerUnitY = (2 / visH) * canvas.height * 0.5;

    log(
      '缩放：角色 ' + bw.toFixed(3) + '×' + bh.toFixed(3) +
        '（' + (bw / bh).toFixed(3) + '）· 可视 ' + visW.toFixed(3) + '×' +
        visH.toFixed(3) + '（' + (visW / visH).toFixed(3) + '）· canvas ' +
        canvas.width + '×' + canvas.height +
        '（' + canvasAspect.toFixed(3) + '）· 每单位像素 x=' +
        pxPerUnitX.toFixed(1) + ' y=' + pxPerUnitY.toFixed(1) +
        (Math.abs(pxPerUnitX - pxPerUnitY) < 1 ? ' ✅不变形' : ' ❌变形!'),
    );
  }

  fit();

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

  // ── 表情（这个形象可能是 0 个 ✗；adv_ 那几个有 19 个 ✅）────────────────
  const expressionMotions = new Map<string, unknown>();
  const exprManager = user.expressions();
  try {
    const exprCount = setting.getExpressionCount ? setting.getExpressionCount() : 0;
    for (let i = 0; i < exprCount; i++) {
      const name = setting.getExpressionName(i);
      const file = setting.getExpressionFileName(i);
      if (!file) continue;
      const buf = await fetchBuffer(joinUrl(dir, file));
      if (!buf) continue;
      const m = user.loadExpression(buf, buf.byteLength, name);
      if (m) expressionMotions.set(name, m);
    }
    if (exprManager) {
      scheduler.addUpdatableList(new CubismExpressionUpdater(exprManager));
    }
    log(
      '表情已加载 ' + expressionMotions.size + ' 个' +
        (exprCount ? '' : '（⚠️ 这个形象本身没带表情 ✗）'),
    );
  } catch (err) {
    log('表情加载出错（不影响画面）✗ ' + (err && err.message ? err.message : err));
  }

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

  // 口型要动的就是 LipSync 组（= ParamMouthOpenY ✗），数值由外部喂进来 ✗
  const mouthIds = lipIds;
  let mouthOpen = 0;    // 实际喂给模型的张嘴量（带包络平滑）
  let mouthTarget = 0;  // 外部（口型同步）刚送来的瞬时值

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
  // 🔎 口型的**量程**：如果这个范围很小（比如 0~0.1），那"看不出张嘴"就是必然的 ——
  //    喂 1.0 进去也只动一丁点。没有这个数字，根本分不清是"没喂到"还是"动不了"。
  for (const id of lipIds) {
    try {
      const idx = model.getParameterIndex(id as never);
      log(
        '口型参数 ' + String(id) +
        ' 范围 ' + Number(model.getParameterMinimumValue(idx)).toFixed(3) +
        ' ~ ' + Number(model.getParameterMaximumValue(idx)).toFixed(3) +
        ' · 默认 ' + Number(model.getParameterDefaultValue(idx)).toFixed(3)
      );
    } catch (err) { /* 忽略 */ }
  }

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

  // ── ⑦.5 视线跟随：眼睛和头**看向光标** ────────────────────────────────
  // ⚠️ 光标位置由**原生面板**推过来（`window.__nekopalCursor` ✗）——
  //    因为透明区域是"点穿"的 ✗，网页在那些地方**收不到 mousemove** ✗。
  //
  // 做法：**混进**动作/呼吸算完的值里 ✗，而不是整个覆盖 ✗ ——
  // 覆盖的话，头就完全不跟着动作走了（像机器人 ✗）。
  let gazeTx = 0;  // 目标（-1..1：+x 右、+y 下 ✗）
  let gazeTy = 0;
  let gazeX = 0;   // 平滑后的实际值 ✗
  let gazeY = 0;
  let gazeW = 0;   // 「正在看她」的权重（0 = 没在看 ✗）
  let glanceLeft = 0;              // 这一眼还要看多久（秒 ✗）
  let nextGlance = 1.2 + Math.random() * 2.5;
  let gazeLogged = false;

  // 只对模型**确实有**的参数动手 ✗（不同形象参数表不一样 ✗）
  const gazeIds: string[] = [];
  for (const id of ['ParamEyeBallX', 'ParamEyeBallY', 'ParamAngleX', 'ParamAngleY', 'ParamAngleZ']) {
    try {
      if (model.getParameterIndex(id as never) >= 0) gazeIds.push(id);
    } catch (err) { /* 没有这个参数 —— 跳过 */ }
  }
  log('视线参数：找到 ' + gazeIds.length + '/5 → ' + (gazeIds.join(' / ') || '（没有 ✗ 视线跟随会静默失效 ✗）'));

  const updateGaze = (dt: number) => {
    if (!gazeIds.length) return;

    // ── 「**偶尔**看她一眼」的节奏 ✗ ────────────────────────────────────
    // 用户原话是"偶尔看向我光标的地方"✗ —— 一直死盯着不像猫 ✗，
    // 所以做成：休息一会儿 → 瞟一眼（约 1～2.5 秒）→ 再休息 ✗。
    if (glanceLeft > 0) {
      glanceLeft -= dt;
      gazeW += (1 - gazeW) * Math.min(1, dt * 4);
    } else {
      nextGlance -= dt;
      gazeW += (0 - gazeW) * Math.min(1, dt * 2.5);
      if (nextGlance <= 0) {
        glanceLeft = 0.9 + Math.random() * 1.7;
        nextGlance = 2.4 + Math.random() * 4.6;
        gazeLogged = false;
      }
    }

    // 平滑：跟太紧会像抽搐 ✗
    const k = Math.min(1, dt * 7);
    gazeX += (gazeTx - gazeX) * k;
    gazeY += (gazeTy - gazeY) * k;

    const mix = (id: string, target: number, weight: number) => {
      const w = weight * gazeW;
      if (w <= 0.001) return; // 没在看 → 完全不干扰动作 ✗
      try {
        const cur = Number(model.getParameterValueById(id as never) ?? 0);
        model.setParameterValueById(id as never, cur * (1 - w) + target * w);
      } catch (err) { /* 忽略 */ }
    };
    // 眼珠：几乎完全听光标的（-1..1 ✗）
    mix('ParamEyeBallX', gazeX, 0.9);
    mix('ParamEyeBallY', -gazeY, 0.9);
    // 头：只轻轻偏一点，别把动作带跑 ✗（-30..30 ✗）
    mix('ParamAngleX', gazeX * 26, 0.34);
    mix('ParamAngleY', -gazeY * 20, 0.28);
    mix('ParamAngleZ', -gazeX * 6, 0.2);
  };

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
      // 视线：也放最后 —— 同样要盖过呼吸/物理，但只**混一点**进去 ✗
      updateGaze(dt);
      // 口型：嘴的张合程度由外部（口型同步）驱动
      // ⚠️ 不能把瞬时音量直接喂进去：原始 RMS 是一串尖刺，看起来只是"嘴在抖"，
      //    根本不像在说话。真人口型是**快起、慢落**——张嘴跟得快、闭嘴慢慢合。
      if (mouthTarget > mouthOpen) {
        mouthOpen += (mouthTarget - mouthOpen) * Math.min(1, dt * 45); // 快起
      } else {
        mouthOpen += (mouthTarget - mouthOpen) * Math.min(1, dt * 11); // 慢落
      }
      if (mouthIds.length) {
        for (const id of mouthIds) model.setParameterValueById(id as never, mouthOpen);
      }

      model.update(); // Core 把顶点算成**变形后**的位置 —— 不做就会散架 ✗

      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);

      // 手写矩阵：**直接就是它**（不再有 view 那一层 ✗）
      const mvp = new CubismMatrix44();
      mvp.setMatrix(mvpMatrix.getArray());
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
    listMotionGroups: () => groups.slice(),
    playMotionGroup: (group: string) => playRandomFrom(group, 3),
    listExpressions: () => Array.from(expressionMotions.keys()),
    setExpression: (name: string) => {
      const m = expressionMotions.get(name);
      if (!m || !exprManager) return false;
      exprManager.startMotion(m, false);
      return true;
    },
    setMouth: (open: number) => {
      mouthTarget = Math.max(0, Math.min(1, open));
    },
    /**
     * 视线跟随：传**归一化**的光标位置 ✗（-1..1，+x 右、+y 下 ✗）。
     * 原生面板每 80ms 推一次光标位置过来（透明区域点穿，网页自己收不到 mousemove ✗）。
     */
    setGaze: (nx: number, ny: number) => {
      gazeTx = Math.max(-1, Math.min(1, nx));
      gazeTy = Math.max(-1, Math.min(1, ny));
    },
    refit: () => fit(),
    destroy() {
      cancelAnimationFrame(raf);
      // ⚠️ **故意不 dispose 框架**：换装时要能重新挂载 ✗
      //    （dispose 之后再 startUp 很容易出幺蛾子；框架本来就是单例 ✗）
    },
  };
}
