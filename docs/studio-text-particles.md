# GuraNovel 文本粒子消散：从文字位置到逐帧动画

这份文档对应 2026-09-07 的 `refactor/frontend-dashboard-ui` 工作区实现，讲解创作区文字替换时的消散效果。它是一份源码导读，不代表本次重新完成了浏览器视觉验收。

核心过程是：**记录旧文字的字形位置 → 把旧文字画到 Canvas → 将有颜色的区域切成小块 → 逐帧移动、缩小并淡出这些小块 → 用遮罩揭示新的真实文字。**

整个效果使用浏览器原生能力：DOM Range、Canvas 2D、requestAnimationFrame、CSS mask，以及控制容器高度的 Web Animations API，没有引入粒子动画库。

## 1. 先分清三个同时配合的部分

| 部分 | 实际承载方式 | 负责什么 |
| --- | --- | --- |
| 旧文字 | 临时 Canvas | 显示正在飞散的字形碎片 |
| 新文字 | 原本的 DOM / textarea | 保持真实排版，通过渐变遮罩逐行显露 |
| 边框、按钮、容器 | 原本的 DOM | 调整真实高度，让底部按钮跟随底边 |

例如输入区从“通用写作要求”切换到某条评论时，React 可以先把新内容放进 DOM，但暂时隐藏它。用户先看到覆盖在原位置的旧文字碎片；碎片消失以后，才看到新内容。

这里需要区分“数据已经更新”和“新内容已经可见”。把这两件事分开，才能安排完整的离场和入场。

主要源码入口：

| 文件 | 阅读重点 |
| --- | --- |
| [studioTextParticles.ts](D:/Document/Repositories/GuraNovel/.hermes/worktrees/frontend-dashboard-ui/frontend/src/studioTextParticles.ts) | 字形采样、Canvas 重画、粒子运动、逐行遮罩 |
| [StudioMotion.tsx](D:/Document/Repositories/GuraNovel/.hermes/worktrees/frontend-dashboard-ui/frontend/src/StudioMotion.tsx) | 保存旧快照、触发切换、调整容器高度 |
| [studio.css](D:/Document/Repositories/GuraNovel/.hermes/worktrees/frontend-dashboard-ui/frontend/src/studio.css) | 隐藏新文字、定位粒子层、固定按钮位置 |
| [Studio.tsx](D:/Document/Repositories/GuraNovel/.hermes/worktrees/frontend-dashboard-ui/frontend/src/Studio.tsx) | 将评论、要求、审阅等状态传给动画组件 |

## 2. 浏览器已经排好了字，先把位置记下来

入口函数是 `snapshotText(box)`。这里的“快照”起初只是一组数据，还没有生成图片。

它查找带有 `data-sweep-text` 的元素，通过 `document.createTreeWalker(..., NodeFilter.SHOW_TEXT)` 遍历其中的文本节点，然后逐个记录字形：

```ts
type Glyph = {
  text: string
  x: number
  y: number
  width: number
  height: number
  font: string
  color: string
}
```

`Intl.Segmenter` 使用 `granularity: 'grapheme'` 拆分文本。字素可以理解为用户看到的一个完整文字单位；某些 emoji 或带组合附加符号的字母由多个 Unicode 单位组成，不能简单按一个 JavaScript 字符处理。

对每个字素，代码用 `Range.setStart()` 和 `Range.setEnd()` 框出它，再调用 `range.getBoundingClientRect()` 获取浏览器已经排好的位置。这样，自动换行、中英文混排和实际字体宽度都参与了测量。

位置转换为相对于动画容器的坐标：

```ts
x = 字素矩形.left - 容器矩形.left
y = 字素矩形.top  - 容器矩形.top
```

假设容器左上角在屏幕 `(300, 200)`，某个字在 `(340, 260)`，快照里记录的就是 `(40, 60)`。Canvas 覆盖容器时，只需在 `(40, 60)` 重画这个字。

空白字素不需要绘制，不过浏览器测出的后续字素位置已经包含了空格带来的间距。被 `data-text-inactive="true"` 标记的内容也不会被采样。

## 3. 把记录下来的字形重画成一张透明图片

`paint(snapshot)` 创建一个未插入页面的 Canvas，并逐个调用 `fillText()`：

```ts
ctx.font = glyph.font
ctx.fillStyle = glyph.color
ctx.fillText(glyph.text, glyph.x, baselineY)
```

Canvas 绘制文字默认使用基线定位，而 DOM 矩形给的是顶部和高度，所以代码通过字体测量值估算基线：

```ts
baselineY = glyph.y + glyph.height
  - (metrics.fontBoundingBoxDescent ?? glyph.height * 0.2)
```

可以把基线想成写英文时字母大多站着的那条线，`g`、`p` 等字母的一部分会落在线下。这里减去的 descent 就是基线以下的字体度量；缺少该值时，使用高度的 20% 作为近似。

生成的图片只有文字笔画有颜色，其余地方保持透明。**这是按字形数据重新绘制，不是对网页截图。** 因此它很适合普通文字，但不能自动复制文字周围的图标、边框、复杂文字阴影等外观。

接下来会用到两个 Canvas：一个保存这张不变的字形图片，另一个插入页面，负责每一帧绘制移动中的碎片。前者是素材，后者是动画画面。

## 4. 粒子其实是图片里的小方块

`animateTextChange()` 通过 `getImageData()` 读取字形图片的像素。返回的数据按 RGBA 排列，每四个数代表一个像素；A 是透明度，0 为透明，255 为不透明。

代码按 `step × step` 网格检查图片：只要小格子里有非透明像素，就把它记录为一颗粒子。空白背景不生成粒子。

```text
一张透明的文字图片
        ↓ 网格切分
空白格：跳过
笔画格：保留原图坐标、随机延迟、水平位移、垂直位移
```

每颗粒子保存的数据很少：

```ts
{ x, y, delay, dx, dy }
```

`x、y` 表示从原图哪里取这一块；`dx、dy` 表示它最终要向哪里移动。粒子自身保留了原来笔画的颜色和形状，并没有被统一替换成圆点。

网格尺寸的计算是：

```ts
step = Math.max(2, Math.ceil(Math.sqrt(ink / 6000)))
```

`ink` 是 alpha 大于 12 的像素数量。文字越多，网格就越粗，以减少绘制次数；文字较少时，最小使用 2 像素方块。

这里的 6000 是估算目标，**不是严格的粒子数量上限**。笔画像素可能稀疏地分散到很多格子里，而且统计 ink 和判断格子是否可见使用的 alpha 阈值不同，实际数量会偏离估算。

## 5. 一颗粒子是怎样动起来的

动画开始时，每颗粒子各自随机取得：

| 参数 | 当前范围 | 视觉影响 |
| --- | --- | --- |
| `delay` | 0 至不足 160 ms | 碎片错开出发，避免整齐地一起移动 |
| `dx` | 12 至不足 58 px | 向右散开 |
| `dy` | -57 至 -12 px 之间 | 向上散开，屏幕坐标向下为正 |

随机数只在创建粒子时生成，每一帧沿用同一组数值。若每帧重新随机位置，轨迹就会变成抖动。

代码使用 `requestAnimationFrame(tick)` 在浏览器的动画帧中更新画面。位置根据经过的毫秒数计算，不按“每帧移动几像素”累加，因此帧率变化不会按比例改变动画总时长。

总消散窗口 `DISSOLVE_MS` 为 820 ms。粒子的进度为：

```ts
p = clamp((elapsed - tile.delay) / (820 - 160), 0, 1)
```

这里 `clamp` 是解释公式用的写法，源码使用 `Math.max(0, Math.min(1, ...))`，把结果限制在 0 到 1 之间。

之后用同一个进度驱动四种变化：

```ts
位置 X = x + dx * p
位置 Y = y + dy * p * p
透明度 = 1 - p
方块边长 = step * (1 - p * 0.4)
```

水平位移随进度匀速增长；向上的位移采用 `p²`，早期较小，后期增大，形成向上弯的轨迹。与此同时，粒子逐渐变透明，边长缩到原来的 60%。它最终不可见主要是因为透明度归零，并非尺寸缩到零。

以一颗粒子为例：起点 `(40, 60)`，方块边长 `2 px`，延迟 `100 ms`，`dx = 30`，`dy = -40`。

| 消散阶段已过时间 | 进度 p | 当前位置 | 透明度 | 边长 |
| --- | --- | --- | --- | --- |
| 100 ms | 0 | `(40, 60)` | 1 | 2 px |
| 430 ms | 0.5 | `(55, 50)` | 0.5 | 1.6 px |
| 760 ms | 1 | `(70, 20)` | 0 | 1.2 px |

每帧先 `clearRect()` 清空动画 Canvas，再逐颗 `drawImage()`。这个调用既指定从原图裁哪一小块，也指定把它画到哪里、画多大：

```ts
ctx.globalAlpha = 1 - p
ctx.drawImage(
  source,
  tile.x, tile.y, step, step,       // 原图中的取样矩形
  tile.x + tile.dx * p,            // 新位置 X
  tile.y + tile.dy * p * p,        // 新位置 Y
  step * (1 - p * 0.4),            // 新宽度
  step * (1 - p * 0.4),            // 新高度
)
```

没有质量、力、碰撞或真实风场计算。当前轨迹由这几条插值公式直接决定，已经能形成轻微向右上方飞散的视觉效果。

## 6. 为什么新文字不会先闪一下

`useTextMotion()` 用 ref 保存上一次渲染的文字快照。当 `textKey` 或 `changeKey` 变化时，把旧快照交给 `animateTextChange()`。

动画准备在 `useLayoutEffect()` 中执行，即 React 更新 DOM 之后、浏览器绘制这一帧之前。函数同步设置 `data-text-phase="dissolve"`，CSS 随即隐藏新内容：

```css
[data-text-phase='dissolve'] [data-sweep-text],
[data-text-phase='waiting'] [data-sweep-text] {
  opacity: 0;
}
```

上方输入区里的 textarea 也有对应隐藏规则。否则只隐藏测量文字的镜像，真正的输入框仍可能提前显示。

使用 `opacity: 0` 时，文字仍参与布局，可以继续测量新内容的行和高度。此时用户看到的是临时 Canvas 上的旧字形。

阶段切换由统一的时间边界控制：

```ts
return hasOld && elapsed < DISSOLVE_MS ? 'dissolve' : 'reveal'
```

每颗粒子的运动持续 660 ms，最晚出发时间不足 160 ms，因此到 820 ms 时所有粒子都已结束。新文字等到这个边界后才进入揭示阶段。

进入揭示阶段时，代码先移除旧 Canvas，通知容器更新高度，再测量新文字的实际排版并设置遮罩。这些操作在同一个动画帧的同步回调中完成，中间不会绘制一帧完整的新文字。测量放在布局更新后，可以避免用滚动条出现前的行坐标裁切更新后的文字。

## 7. 新文字如何柔和地逐行出现

`revealRows()` 再次使用 DOM Range。这次获取 `getClientRects()`，收集文本实际换行后的矩形；同一显示元素中，垂直范围重叠的片段合并到同一行。这样，小号上标注号与周围正文即使顶部坐标不同，也不会被误当成两行。

这比只按字符串中的 `\n` 分行更适合响应式界面，因为浏览器还会根据可用宽度自动换行。

每一行有一个独立的渐变遮罩：左侧不透明、右侧透明，中间有 24 px 的柔和过渡。遮罩限定在该行的局部矩形内，从行首开始计算进度，避免尚未入场的上标或缩进行露出左侧文字的中间一段。对于这里使用的 CSS 渐变 mask，黑色的不透明部分让内容可见，透明部分把内容遮住。

```text
已经可见的文字       24 px 渐变边缘       尚未显露
████████████████████▓▒░                 
                          → 向右推进
```

代码并不逐字修改字符串，而是持续移动遮罩边缘，露出原本就存在的 DOM 文字。

单行持续 300 ms，边缘位置使用 `1 - (1 - p)²` 的缓出进度：开始移动较快，接近终点时减速。各行之间错开：

```ts
stagger = Math.min(90, 900 / Math.max(1, rows.length))
```

例如 3 行文字依次在 0、90、180 ms 开始，最后一行约在 480 ms 完成。各行会重叠播放，不必等待上一行完整结束才开始下一行。行数很多时，间隔会缩短，避免入场时间随行数无限增长。

输入区有一个特殊处理：用布局镜像测量行位置，但把遮罩应用到真实 textarea。最终移除遮罩后，留下的仍然是可以输入、选择和复制的文本。

## 8. 容器高度与底部按钮怎么配合

`MotionFrame` 将自然排版的内容放在 `.studio-motion-content` 中，按钮等控件作为 `chrome` 放在内容测量区域之外。

文字消散阶段会暂停启动新的高度调整。到揭示阶段开始时，文字层发出 `studio:text-ready` 事件，外框再从当前可见高度过渡到新内容所需高度：

```ts
box.animate(
  [{ height: `${from}px` }, { height: `${next}px` }],
  { duration: 440, easing: 'cubic-bezier(0, 0, .58, 1)' },
)
```

这里调整真实 `height`，使容器边界实际移动。底部控件通过 `position: absolute; bottom: 16px` 贴着容器底部，因此会自然跟随，无需再给每个按钮计算一条轨迹。

一个没有额外等待、包含 3 行新文字的典型时间线为：

```text
相对时间        0                 820             1260  1300 ms
旧字形          [---- 粒子消散 ----]
新文字                            [--- 逐行揭示 ------]
外框高度                          [--- 440 ms ----]
```

新文字揭示和容器变高可以同时进行。`studio:text-ready` 表示文字层已允许重新测量和调整布局，不单指“所有新文字已经出现”；正常清理结束时也会发送一次该事件。

## 9. 触发、打断与清理

是否播放替换动画，由传入的 key 决定。文本内容和宽度组成的 `signature` 主要用于决定是否重新采样快照，**不意味着每次输入一个字都会播放消散**。

父级 `MotionFrame` 如果设置了 `data-sweep-group`，内部文字组件会把动画交给父级统一处理，避免父子两层分别播放相同的效果。

新的切换发生时，先调用上一次保存的停止函数：取消 requestAnimationFrame、移除临时 Canvas、清除 mask 和阶段属性、恢复 busy 状态。组件卸载时也执行清理。

需要区分两种打断能力：容器高度动画会读取当前可见高度作为新的起点；文字粒子会清理旧动画并使用保存的文字快照重新开始，**不会保留每颗旧粒子的实时位置和速度**。当前实现可打断、可清理，但不是完整的粒子物理续接。

用户开启 `prefers-reduced-motion` 时，React 动画组件会跳过这些文字动效；容器也直接采用最终高度。临时 Canvas 设置 `aria-hidden` 且不拦截指针，避免把视觉副本当成可操作内容。

## 10. 发送评论时整框消散是另一种实现

[studioPanelParticles.ts](D:/Document/Repositories/GuraNovel/.hermes/worktrees/frontend-dashboard-ui/frontend/src/studioPanelParticles.ts) 的 `dissolvePanel()` 用于整个评论框。它需要同时带走输入框、SVG 图标、边框和阴影，因此没有使用上面的逐字 Canvas 重画方式。

它克隆真实面板，为 textarea 显式复制当前值，把副本划分为 12 列、最多 20 行。每一个片段内部都是完整面板副本，通过 `clip-path: inset(...)` 仅露出所属格子，再用 `element.animate()` 对片段进行位移和淡出。

原面板暂时隐藏；全部片段的 `animation.finished` 完成后，才执行发送回调。取消时停止动画并恢复原面板，不执行完成回调。

| 对比 | 文本消散 | 整框消散 |
| --- | --- | --- |
| 视觉来源 | Canvas 重画的字形图片 | 实际面板 DOM 的克隆 |
| 碎片内容 | 有笔画的像素小块 | 包含背景和控件的矩形片段 |
| 动画驱动 | requestAnimationFrame + drawImage | Web Animations API |
| 粒度控制 | 自适应网格，约 6000 的估算目标 | 最多 240 个片段，每片包含面板副本 |
| 完成后 | 揭示新文字 | 执行评论发送回调 |

二者都借助“保留一份视觉副本，再拆散它”来完成离场，但使用的渲染手段和开销不同。

## 11. 调参数时先理解影响范围

| 想调整什么 | 对应代码 | 注意事项 |
| --- | --- | --- |
| 旧文字消散速度 | `DISSOLVE_MS` | 同时被整框消散复用；文本粒子的分母还减去 160，不能随意减到 160 或以下 |
| 粒子细腻程度 | `step` 和 `ink / 6000` | 格子越细，逐帧绘制调用通常越多 |
| 飞散方向、幅度 | `dx`、`dy` 的生成范围 | 当前整体向右上移动；改幅度需同时检查 Canvas 边界裁剪 |
| 碎片松散程度 | 随机 `delay` | 若改最大延迟，应同步修改进度分母中的 160，保持最后一颗在阶段边界前结束 |
| 缩小程度 | `1 - p * 0.4` | 当前末态尺寸为 60%，透明度负责彻底消失 |
| 新文字入场速度 | 300 ms、90 ms、900 ms | 分别控制单行时间和行间错开节奏 |
| 遮罩边缘柔和程度 | 24 px | 既用于边缘位置计算，也用于渐变宽度；调整时应保持一致 |
| 容器伸缩速度 | 440 ms 和 easing | 与粒子轨迹独立，控制真实布局边界 |

学习时可以先对照第 5 节的数值计算，再读 `tick()`。先理解“一颗粒子”的变化，就能理解循环里几千颗粒子组成的效果；不必先引入新的渲染框架。

## 12. 当前实现的边界与验证方法

字形位置来自浏览器，但 Canvas 重绘仍有近似：字体基线用了估算，逐字绘制也不等同于浏览器对整段文本的全部排版与绘制处理。源码未按 `devicePixelRatio` 放大 Canvas 后备分辨率，在高像素密度屏幕上，临时粒子层可能比真实文字软一些。

动画 Canvas 使用旧文字快照的尺寸，不再向右、向下额外扩展 72 px，避免把父容器撑出临时滚动条。飞散到画布边界外的碎片会被裁剪。Setting 正文区还保留稳定的纵向滚动条空间，避免长短条目切换时可用宽度变化，造成揭示中的文字重新换行。

性能开销包括按字素测量布局、读取像素和逐帧绘制多个图块。当前实现面向短文字面板；扩大到整章正文时，应先在真实浏览器中测量开销，再决定是否需要更粗的网格或其他渲染方式。

已有 [StudioMotion.test.tsx](D:/Document/Repositories/GuraNovel/.hermes/worktrees/frontend-dashboard-ui/frontend/src/StudioMotion.test.tsx) 检查 820 ms 边界、逐行 mask、打断清理、控件保留和高度动画等逻辑；[studioPanelParticles.test.ts](D:/Document/Repositories/GuraNovel/.hermes/worktrees/frontend-dashboard-ui/frontend/src/studioPanelParticles.test.ts) 检查整框完成后回调与取消恢复。

这些测试通过模拟 Canvas 和几何测量验证行为，不能证明字体像素对齐、帧率或视觉柔和程度。修改效果时，除相关测试外，还应在预览里观察：旧文字是否完整离场，新文字是否闪现，快速切换是否遗留遮罩，以及外框变化时按钮是否持续贴住底边。
