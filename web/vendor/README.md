# web/vendor —— 第三方前端库（**只有 MIT 的，没有 Live2D 的专有文件**）

这些文件是**原样下载**的，没有改动。为什么要放仓库里：
这个项目**零构建**（不用 Node / 不用打包器），直接打开就能跑。

| 文件 | 来源 | 版本 | 许可 |
| --- | --- | --- | --- |
| `pixi.min.js` | [pixi.js](https://github.com/pixijs/pixijs) | 6.5.10 | MIT |
| `cubism4.js` | [pixi-live2d-display](https://github.com/guansss/pixi-live2d-display) | 0.4.0 | MIT |

> 用**未压缩**的 `cubism4.js` 是故意的：出问题时调用栈能看出是哪个函数炸的
> （压缩版只有 `e[i]`，查过一次就知道了 ✗）。

## 🚫 这里**没有** `live2dcubismcore.min.js`

那是 Live2D 官方的**专有运行时**，**不进仓库**（见 `../../characters/README.md` 的红线）。
请自行从 Live2D 官网获取并同意条款，放到：

```
~/.config/nekopal/core/live2dcubismcore.min.js
```

后端会把它**本地中转**给页面（`/api/avatar-core/…`），项目本身不分发它。

## ⚠️ 一个已知的版本桥

Cubism **Core 5** 把 moc 的 `renderOrders` 改名成了 `drawOrders`，
而 `pixi-live2d-display@0.4.0` 是照 **Cubism 4** 写的、仍读旧名字 → 渲染会炸。

`web/character.js` 的 `bridgeCubismVersion()` 在 Core 外面补了一层别名解决了它。
将来如果有配 Cubism 5 的库，那个函数可以整段删掉。
