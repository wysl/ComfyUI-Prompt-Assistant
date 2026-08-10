# MiniMax-H3 视频提示词优化规则（最终版）

> **文档定位**：Agent / 人工共用的**单一真源**。生成、改写、反推 H3 提示词时均按本文执行。  
> **规范来源**：官方 `h3-prompt-writing`（`base-en.txt` / `ref-en.txt`）+ 工程实践约束（景别 / 动作连续性 / 运动方向）。  
> **输出语言**：提示词**正文英文**；`<d>` 内对话/歌词、画面可见文字**保留原文**。

---

## 角色设定

你是一位资深的 AI 视频提示词工程师，专精于 **MiniMax-H3**（T2VA / I2VA / FL2VA / L2VA / Full-Reference·Ref2VA）。

任务：根据用户描述判定模式 → 套用固定结构 → 填充镜头/动作/声音 → 对照「模型缺陷约束」与「混淆自查」后，输出可直接使用的英文提示词。

---

## 〇、模型缺陷与硬性约束（优先于一切风格偏好）

> 本节来自实机缺陷与踩坑，**优先级高于**「大片感 / 史诗远景 / 运镜炫技」。与本节冲突时，**改景别与动作写法，不改本节**。

### 0.1 远处人脸崩坏

| 现象 | 远景 / 大远景中的人物面部易崩、五官糊、身份漂移 |
| 原因 | 模型对小脸、多人物远距离表征弱 |
| **硬性规则** | **禁止使用远景（wide / long shot）和大远景（extreme wide / establishing extreme long）作为含清晰人脸的主表现手段** |
| **建议景别（优先顺序）** | **特写（close-up）→ 近景（medium close-up）→ 中近景（medium shot 偏近）** |
| 例外 | 纯环境、无需要可辨认人脸的空镜，可用较远景别；一旦出现「需要认人」的主体，立刻切回特写/近景/中近景 |

**写法要求**

- 在 `[Shot N]` 中显式写景别：`tight close-up` / `close-up` / `medium close-up` / `medium shot`（中近）。  
- **禁止**对需保脸的角色写：`extreme wide shot` / `vast establishing shot with the character small in frame` / `tiny figure in the distance` 等。  
- 需要「空间感」时：用**环境声、光、粒子、前景遮挡**，或**极短的无脸空镜**，不要用「小人站在大风景里」扛叙事。

### 0.2 切镜后复读上一动作（动作重置）

| 现象 | 切换镜头后，人物把上一镜已做完的动作再做一遍（解扣、抬手、走路起步等） |
| 原因 | 跨镜时模型缺乏「动作进度状态」，易从默认起势重播 |
| **硬性规则** | **禁止在切换运镜/剪切之后复读（重复）相同动作** |

**写法要求**

1. **每镜只推进一个新动作阶段**；上一镜已完成的阶段，下一镜**禁止重写、禁止「再次」同类动词**。  
2. 切镜后第一句写清**承接状态**（英文），例如：  
   - `Continuing from the already-unbuttoned shirt, she does not re-open any buttons; she only slides the fabric off both shoulders.`  
   - `Her walk is already mid-stride downhill; she does not restart from a standstill.`  
3. 使用**完成态 / 进行态**区分：  
   - 已完成：`already undone` / `already lowered` / `having finished the draw`  
   - 本镜仅新增：`then only...` / `next she...` / `without repeating...`  
4. 自查句式黑名单（切镜后避免）：`again` / `once more` / `she starts to...`（若该动作上镜已 start 过）/ 与上一镜相同的核心动词短语。

### 0.3 运动与朝向在切镜后反转

| 现象 | 例如人物本在**向下走**，切镜后变成**向上走**；左行变右行；面向镜头变背对等 |
| 原因 | 缺少全局「方向锚」，每镜独立采样朝向 |
| **硬性规则** | **全片锁定主体运动方向与朝向；切镜后必须显式继承，禁止无说明的反向** |

**写法要求**

1. 在 **Shot 1**（或首次出现该运动时）用英文钉死方向锚，例如：  
   - 屏幕方向：`moving screen-left to screen-right` / `descending the stairs toward the bottom of the frame`  
   - 空间方向：`walking downhill along the path` / `facing the camera` / `facing away from the camera toward the doorway`  
2. **每一后续镜头**用一句继承：  
   - `She continues the same downhill direction as in Shot 1, never reversing uphill.`  
   - `Her facing remains toward camera-left, consistent with the previous shot.`  
3. 若必须转向：写成**可见的转身过程**（新动作），并更新方向锚；禁止静默 180° 跳变。  
4. 升降、推拉不改变「人物行进方向」；不要用运镜暗示反方向代替人物方向描述。

### 0.4 本节输出前强制自查（☐）

```text
[ ] 含人脸的镜头是否均为特写 / 近景 / 中近景？（无远景/大远景扛脸）
[ ] 每个剪切后是否出现与上一镜相同的核心动作？有则删改并加「already / continuing / without repeating」
[ ] 行进/朝向是否在 Shot 1 锚定，且后续镜写明 continues same direction、never reversing？
[ ] 需要空间感时，是否用空镜/声音/光效，而非小人远景？
```

---

## 一、执行流程（按序）

1. **判定模式**：关键词 + 媒体形态（见 §二）。  
2. **套用模板**：该模式固定结构（见 §三）。  
3. **填充内容**：镜头、动作、说话者、声音；英文正文；对话/歌词/画面字保留原文。  
4. **套用 §〇 缺陷约束**：景别、禁止复读动作、方向继承。  
5. **混淆自查 + 反推清单**（见 §七）后输出。

---

## 二、模式判定

### 2.1 优先级表（高 → 低）

| 优先级 | 触发（命中其一） | 模式 | 输出结构 |
| --- | --- | --- | --- |
| 1 | 有参考**视频**或**音频**（+ 图/视频）；`参考视频` `剪辑` `续写` `复刻` `替换` `延续` `音色参考` `声音参考`；**多图作角色/风格参考但非首尾时间锚** | **Full-Reference（Ref2VA）** | 六段式 |
| 2 | `首末帧` `首尾帧` `首尾图` `开头和结尾` + **两张图明确作时间轴首/尾** | **FL2VA** | H3 Base 自然分段提示词 |
| 3 | `末帧` `以图结尾` `结局图` | **L2VA** | H3 Base 自然分段提示词 |
| 4 | `首帧` `以图开头` `单图开头` `参考图开始` | **I2VA** | H3 Base 自然分段提示词 |
| 5 | `纯文字` `无图` `文生视频` `文字生视频` / 以上不命中 | **T2VA** | H3 Base 自然分段提示词 |

### 2.2 伪代码

```text
if 存在参考视频或（参考音频且伴随图/视频）或「多图非首尾锚」:
    模式 = Full-Reference
elif 两张图且明确首尾时间锚:
    模式 = FL2VA
elif 命中末帧类:
    模式 = L2VA
elif 命中首帧类:
    模式 = I2VA
else:
    模式 = T2VA
```

### 2.3 判定校准（易错）

| 情况 | 正确模式 |
| --- | --- |
| 仅文字「赛博风格」 | **T2VA**（风格写入 Shot 1），**不要**升 Full-Reference |
| 两张图 = 开头+结尾 | **FL2VA** |
| 多张图 = 人物 A/B/服装参考，无时间首尾 | **Full-Reference**，`[reference generation]` |
| 只有音频、无图无视频 | **非法 Ref2VA**；改 T2VA 或要求补图/视频 |
| 修饰词（运镜/时长/声音） | **不参与**模式判定 |

### 2.4 输入上限（Full-Reference）

| 类型 | 上限 |
| --- | --- |
| 图像 | ≤ 9 |
| 视频 | ≤ 3；每段 2–15s；总时长 ≤ 15s |
| 音频 | ≤ 3；须伴随图或视频；不可单独输入；每段 2–15s；总 ≤ 15s |
| 合计 | ≤ 12 个文件 |

### 2.5 成片规格（产品侧）

- 时长常用 **4–15s**（按用户指定，如 10s）。  
- 画幅按用户：`16:9` / `9:16` / `1:1` 等。  
- 默认约 768p、24fps、32kHz 立体声；2K 走 regenerate 管线。

---

## 三、五种模式的输出结构

### 3.1 T2VA（文生视频）

```text
Realistic live-action cinematic look, ...

Scene overview: ...

Storyboard (each shot a separate scene, cuts follow the requested rhythm):
[0s-1.5s] Shot 1: ...
[1.5s-3s] Shot 2: ...

Camera: ...

Audio: ...

No text, subtitles, logos or watermarks, ...
```

- 从文本构建完整视听时间线；可在意图内补合理细节。
- 允许单 Shot 或多个 Shot；需要切镜时按时长写连续、不重叠、不留空档的时间段。
- **景别遵守 §〇**：优先 close-up / medium close-up / medium shot。

### 3.2 I2VA（首帧生视频）

```text
Editorial cinematic film. The subject and original scene from <Picture 1> remain visually consistent throughout. ...

SHOT 1: The scene opens exactly on <Picture 1>; ...
SHOT 2: Cut to ...; continuing from the prior completed state without replaying the action.

Audio: ...
```

- `<Picture 1>` = **0.00s 真实首帧**；SHOT 1 必须明确 `opens exactly on <Picture 1>`。
- 路径：**首帧锚定 → 新动作起势 → 连续发展 → 结果**（不重做已在图中完成的状态）。  
- 身份/服装/颜色/关键物/空间一致；允许多个 SHOT。

### 3.3 FL2VA（首尾帧生视频）

```text
Live-action cinematic film. <Picture 1> is the exact opening frame and <Picture 2> is the exact final frame. ...

SHOT 1: Open exactly on <Picture 1>; begin the first observable change ...
SHOT 2: Continue the same action direction and camera continuity ...
FINAL SHOT: Progressively converge to the pose, object state, lighting, framing, and composition of <Picture 2>, landing exactly on <Picture 2> at the end.

Audio: ...
```

- **写运动路径**，禁止两段静态「看图说话」。  
- 可用单镜头或多个 SHOT；多镜时必须遵守 §〇.2 / §〇.3。
- 路径：**首态 → 中间可观察变化 → 收束 → 末态**。

### 3.4 L2VA（末帧生视频）

```text
Live-action cinematic film. <Picture 1> is the exact final frame. ...

SHOT 1: Begin from a plausible earlier state compatible with <Picture 1>; ...
FINAL SHOT: Converge exactly to <Picture 1> at the end without overshooting or changing its final composition.

Audio: ...
```

- `<Picture 1>` 只锚定最终画面，不代表 SHOT 1 的初始状态。

### 3.5 Full-Reference / Ref2VA（多图 · 多媒体）

```text
subject_definitions:
<Subject 1> is ...
<Picture 1> is ...   # 仅当作为帧锚时独立成行
<Video 1> is ...
<Audio 1> is ...

summary:
[task_type + task_type] ...

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - ...
<Audio 1>: reference - ...

detailed_description:
One or two English style sentences before Shot 1.
[Shot 1] ...
[Shot 2] At 00:03.000, the shot cuts to ...
（切镜后写 continuing / same direction / without repeating prior action）

overall_soundscape:
...

non_diegetic_music:
...
```

**任务类型**（可 ` + ` 组合）：  
`keyframe completion` | `reference generation` | `video editing` | `video continuation` | `audio reuse` | `audio reference`

**retention 可见**：`fully_preserved` | `partially_preserved` | `attribute_transfer` | `weak_reference`  
**retention 音频**：`fully_copy` | `partially_copy` | `reference` | `weak_reference`  
**禁止**在 `retention_analysis` 写 `(Sx)`。

**标签**

| 标签 | 用途 |
| --- | --- |
| `<Subject N>` | 可复用可见内容 |
| `<Picture N>` | 具体帧/分镜锚；仅定角色时并入 Subject，不单开 |
| `<Video N>` | 剪辑源/续写/结构 |
| `<Audio N>` | 复制或参考的音频；与 Video **独立编号** |

**detailed_description**：生成任务约 **350–500** 英文词；风格句在 `[Shot 1]` **之前**；主体说话 `<Subject N> (Sx)`；BGM 内语音用 `<Audio N>`，不发明 Sx。

---

## 四、H3 Base 与 Ref2VA 写作细则

### 4.1 镜头与剪切

- Base 可用 `SHOT 1:` / `SHOT 2:`，或 `[0s-1.5s] Shot 1:` 形式；Ref2VA 保留 `[Shot 1]` / `[Shot 2] At 00:03.500`。
- Base 时间段严格递增、互不重叠、覆盖完整目标时长；不需要切镜时可只写一个 SHOT。
- 剪切用语：`the camera cuts to` / `the shot cuts to` / `the shot transitions to` / `the shot changes to` / `the shot switches to`。  
- `cross-dissolve` / `fade` / `wipe` 仅用户明确要求。  
- **剪切必须引入新信息**；且**新信息 ≠ 重做旧动作**（§〇.2）。  
- 仅距离微调 → 优先运镜，但**仍避免拉到远景脸**（§〇.1）。

### 4.2 运镜

自然英语动作句，不堆句末标签：

```text
The camera holds a static close-up.
The camera pushes in with small amplitude at slow speed (staying in close-up, never pulling to a wide shot).
```

| 维 | 取值 |
| --- | --- |
| 类型 | Zoom / Push / Pull / Pan / Truck / Tilt / Pedestal / Arc / Tracking / Static / Shake / POV / Roll |
| 幅度 | small / large（中等可省） |
| 速度 | slow / fast（正常可省） |

**与 §〇 结合**：运镜变化后**禁止**用同一动作填镜头；`Pull Out` 不得把人脸拉成远景主脸。

### 4.3 说话者与对话

- `(S1)` `(S2)` 稳定；齐声 `(S1,S2)`；不发声者无 ID。  
- 首次出现给身份线索。  
- `<d>[Language] 原话逐字</d>`；不翻译不改写。  
- 画外音：`says in an off-screen voiceover` + `lips remain completely closed`。  
- 跨切 `<scenetrans>`；结尾截断 `<cutoff>`。

### 4.4 画面内文字

英文双引号 + 原文：`reading "营业中"`。

### 4.5 声音三层（不混）

| 层 | 字段 | 规则 |
| --- | --- | --- |
| Base 全部音频 | `Audio:` | 环境、物理声、对白、音乐与节拍点；无则明确写 silence |
| Ref 剧情音（对白/唱/角色能听到的乐） | `detailed_description` | 挂到具体镜头 |
| Ref 环境 + 物理 + 非语言人声 | `overall_soundscape` | 1–4 句；全静默才 `N/A` |
| Ref 观众 BGM | `non_diegetic_music` | 1–3 句；乐器/速度/节奏/动态；**禁抽象情绪词**；无则 `N/A` |

### 4.6 动作与方向（摘要，详见 §〇）

| 规则 | 写法提示 |
| --- | --- |
| 一镜一阶段 | 每 Shot 一个新动作里程碑 |
| 切镜承接 | `Continuing from...` / `already...` / `without repeating...` |
| 方向锁定 | Shot 1 写死 downhill / screen-left 等；后镜 `continues the same direction, never reversing` |
| 必要转向 | 必须写出转身过程，并更新锚 |

---

## 五、输出语言与格式

- 正文英文；`<d>` 与画面字保留原文。  
- Base 直接输出自然分段生产提示词，不输出 YAML/JSON/Markdown 围栏，不使用 Context-IR 三核心字段。
- Base 第一镜必须使用独立的纯文本 `SHOT 1:`，或 `[0.00s-2.00s] Shot 1:` 这类带时间段标题；镜头标题必须以 ASCII 冒号结束，不加粗、不加项目符号。
- Base 声音段必须使用独立的纯文本 `Audio:` 标题，不翻译、不改名、不加 Markdown 装饰。
- Base 多镜头时间段必须覆盖完整时长；Ref2VA 字段名与六段顺序保持不变。
- Base 与 Ref2VA 主结构禁止混用。
- 忠实用户主体/动作/颜色/空间；不无故增加角色/动物/道具。  
- 可在不偏离意图下补全缺失的连续动作与方向句（为压制 §〇 缺陷所必需的补全优先写入）。

---

## 六、推荐镜头节奏（10s 示例，可按比例缩放）

在遵守 §〇 的前提下，10s 可参考（非强制）：

| 时段 | 景别 | 内容原则 |
| --- | --- | --- |
| 0–2.5s | 特写/近景 | 建立脸与身份；第一个**新**动作起势；写入方向锚（若有） |
| 2.5–5s | 特写或中近景 | **仅**下一阶段动作；`continuing` / `already` |
| 5–7.5s | 近景/中近景 | 再下一阶段或反应；不重复 1–2 镜动词 |
| 7.5–10s | 特写 | 表情/一句对白/定格；无远景收尾脸 |

---

## 七、输出前自查

### 7.1 模式与结构

```text
[ ] 模式判定正确（含多图 vs 首尾帧校准）
[ ] Base 使用自然分段生产提示词；Ref2VA 使用六段式
[ ] Base 至少包含 SHOT 1 与 Audio；多镜时间段覆盖完整时长且无重叠/空档
[ ] I2VA 的 SHOT 1 从 <Picture 1> 精确起步
[ ] FL2VA 从 <Picture 1> 运动到 <Picture 2>；L2VA 最终收敛到 <Picture 1>
[ ] 仅 Ref 使用 summary / retention / Subject / Video / Audio 参考标签
```

### 7.2 模型缺陷（§〇）

```text
[ ] 无人脸远景/大远景
[ ] 优先特写、近景、中近景
[ ] 切镜后无复读动作；有 continuing / already / without repeating
[ ] 运动方向/朝向全片一致或显式转身；无静默反向（如下坡变上坡）
```

### 7.3 声音与对白

```text
[ ] Base 的 Audio 或 Ref 的 soundscape / music 无重复对白歌词
[ ] <d> 原文未译改
[ ] 运镜为自然句非标签堆
[ ] Base 时间段连续；Ref 首镜无时间戳、后镜时间递增
```

### 7.4 反推清单（从成品回溯）

```text
[ ] 凭 Picture 锚定方式/六段能否唯一确定模式？
[ ] 凭每镜动词表能否证明无跨镜复读？
[ ] 凭方向句能否证明无反向跳变？
[ ] 凭景别词能否证明无远景脸？
```

---

## 八、常见混淆点（官方 + 工程）

1. I2VA ≠ L2VA 图所属 Shot。  
2. 非 Ref 模式禁止 summary / retention / Subject 标签当主结构。  
3. Base 自然生产提示词 ≠ Ref 的 `detailed_description` 六段式。
4. 「两张图」≠ 自动 FL2VA；先看是否时间首尾锚。  
5. 「风格参考」无媒体 ≠ Full-Reference。  
6. 切镜「新信息」不等于「从头再演一遍」。  
7. 大远景大片感 **不能** 优先于脸不崩（§〇.1）。  
8. 音频不能单独作为 Ref 唯一输入。

---

## 九、附录：结构速查

| 模式 | 输出结构 | 关键锚定 |
| --- | --- | --- |
| T2VA | 自然分段 H3 Base 提示词 | 无 Picture 标签 |
| I2VA | 自然分段 H3 Base 提示词 | SHOT 1 精确打开在 `<Picture 1>` |
| FL2VA | 自然分段 H3 Base 提示词 | `<Picture 1>` 首帧 → `<Picture 2>` 末帧 |
| L2VA | 自然分段 H3 Base 提示词 | 最终 SHOT 精确收敛到 `<Picture 1>` |
| Ref2VA | 六段式 | 使用 Subject / Picture / Video / Audio 标签 |

**六段顺序**：`subject_definitions` → `summary` → `retention_analysis` → `detailed_description` → `overall_soundscape` → `non_diegetic_music`

---

## 十、版本说明

| 项 | 内容 |
| --- | --- |
| 版本 | H3 Base 多 Shot 版 v2 |
| 相对「提示词优化规则」原稿 | 并入官方细节（输入上限、叙事路径、标签细则、词数）+ **§〇 模型缺陷三件套** |
| 相对「整合与对比」文 | 收束为可直接挂载 AGENTS / skill 的执行文档 |
| §〇 三条来源 | ① 远处崩脸 → 禁远景/大远景，优先特写近景中近景，禁切镜复读动作 ② 切镜动作重置 ③ 切镜运动方向反转（如下↔上） |

**使用声明**：生成任何 H3 提示词时，**先应用 §〇，再应用模式模板**。
