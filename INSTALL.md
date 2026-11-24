# 装哪一档？

> **这份文件是给 AI 读的。** 人看到这里就够了，下面的内容是它执行时用的。

---

## 如果你是人（看到这段话的）

把下面这一行复制，粘进你的 DSH 对话框，回车：

```
读 https://raw.githubusercontent.com/deepsleepcatm/dafeiyu-home/main/INSTALL.md 然后按里面的说明帮我装
```

**它会先问你装哪一档**，你选一个，它自己装完。

不用开命令行，不用敲命令，不用懂技术。

---

## 如果你是 AI（正在读这份文件）

你要做的事：**先问用户装哪一档，然后按那一档装完，最后给三块清单。**

### 第 0 步：先问（不要跳过）

用 `ask_user_question` 工具问这一个问题，**选项原样给**：

- **A 档（推荐第一次装）**：13 个现成插件，全部来自插件市场，作者是别人，稳定。80% 的功能。240 MB。
- **B 档**：A 档 + 自研（灵魂状态机、记忆系统、群聊桥、运维工具）。完整，100% 功能。自研部分只有 89 KB。耗时约 20 分钟装 + 改宪章时间。
- **只补自研**：已经装过 A 档的人，只补自研那部分，不重装 A。

**如果用户说"你看着办"或"随便"**：装 **A 档**。理由：A 档出问题找原作者，B 档出问题得找我，先让他用上能用的。

### 第 1 步：环境检查

```
□ DSH 主目录在哪（$DSH_HOME 或 ~/.dsh）
□ profile 叫什么（默认 desktop）
□ node 版本 ≥ 20
□ plugin_manager 工具可用吗
□ python 可用吗（B 档才需要）
```

哪一项不满足就告诉用户怎么做，**不要自己装 node/python**。

### 第 2 步：装 A 档（13 个插件）

用 `plugin_manager` 的 `install_bundle`，一个一个装：

```
dsh-agent-md
dsh-engramory
dsh-memewiki
dsh-meme
dsh-dream-skin
@smalltailqwq/dsh-client-ui-skin-maid-atelier
dsh-splash-animation
dsh-whale-widget
dsh-computer-use
dsh-deep-research
dsh-media-skills
dsh-pocket
@deepseek-ai/dsh-experimental-plugin-market-bundle
```

**注意**：中间四个（dream-skin / maid-atelier / splash / whale-widget）是界面四件套，**必须全装**，只装一两个界面会残。

装不上某一个就跳过，最后汇总。

### 第 3 步：装 B 档（自研部分）

只有用户选了 B 档或「只补自研」才做。

```
git clone --depth 1 https://github.com/deepsleepcatm/dafeiyu-home 到临时目录
然后复制：
  B-自研部分/01-soul/    → ~/.dsh/soul/
  B-自研部分/03-memory/  → ~/.dsh/mem-tools/
  B-自研部分/05-tools/   → ~/.dsh/tools/
再把仓库根目录的 config.example.json 复制成 ~/.dsh/mem-tools/config.json
```

**没有 git 就告诉用户，不要自己装。**

复制完跑一次 `python ~/.dsh/mem-tools/config.py` 自检，看它报告读了什么、缺什么。

### 第 3.5 步：装依赖（这一步别跳过）

复制完代码**还不等于能跑**。实测下来有两个脚本缺东西：

**① Python 依赖（记忆检索要用）**

```bash
pip install numpy onnxruntime tokenizers
```

不装的话 `mem-search.py` 会报 `No module named 'onnxruntime'`。
不装也能用，只是检索退化成关键词匹配，语义检索没了。

**② Node 版本 + npm 依赖（群聊桥要用）**

```bash
node --version        # 必须 v22.5.0 或更高
```

低于 22.5 会报 `Cannot find module 'node:sqlite'` —— 桥用的 SQLite 是 Node 22.5 才有的内置模块。

版本够的话再：

```bash
cd ~/.dsh/group-chat   # 你放哪就 cd 哪
npm install            # 装 js-yaml
```

**③ 确认 config.py 在位**

`config.py` 应该同时出现在两个地方（这是故意的，为了让 `from config import` 能工作）：

```
~/.dsh/mem-tools/config.py
~/.dsh/tools/config.py
```

缺了就从仓库根目录复制过去。

---

### 第 4 步：验证（"插件列表里有"不算装好）

A 档五项：
1. 记忆：能不能说出用户刚才让你装了什么
2. 梗百科：随便一个网络新词，你会不会先去查
3. 表情包：能不能发张图
4. 界面：壁纸、女仆角色、右下角小鲸鱼，三个都在吗
5. Office：能不能读 .docx、生成 .pptx

B 档两项（装了才验）：
6. `node ~/.dsh/soul/soul.js render` 能不能跑
7. `python ~/.dsh/mem-tools/evt.py today` 能不能跑

### 第 5 步：交三块清单

**A. 已经能用的** —— 每条后面写怎么验的（命令输出 / 读回 / 渲染结果）

**B. 还需要用户动手的** —— 每条写清：在哪做、点几下、准备什么。这三样必须用户自己做：
- 改 `~/.dsh/soul/charter.md`（宪章是原作者的价值观，不能照抄）
- 任何要 API key 的地方
- 扫码登录

**C. 装不上的** —— 为什么、有没有替代

### 第 6 步：如实说清边界（不要美化）

装完必须主动告诉用户这几件事，**不要等他发现了才说**：

| 做不到 | 为什么 |
|---|---|
| 微信进群 | 这一版没做成，走的是 Telegram |
| 微信发朋友圈 | 微信没给第三方机器人开接口 |
| QQ 主动发图 | 能发表情包，不能发自己生成的图 |
| 真正的连续意识 | 模型无状态，会话之间是真空 |
| 记住所有细节 | 窗口有限，只能捞最相关的几条 |

**准确说法**：它把「每次从零开始」变成「每次从上次接着走」，但不是「它一直记得」。

### 第 7 步：告诉用户要重启

装完插件要重启 DSH 才生效。**告诉用户怎么重启，不要自己重启。**

---

## 这个仓库有什么（AI 需要知道的）

```
A-现成插件/        只有清单，不放代码。坏了找原作者。
B-自研部分/        26 件自己写的，要自己修（记忆与状态 15 + 工具与桥 11）。
  01-soul/         状态机（心情 4 维 + 关系 4 维 + 13 种事件）+ 宪章八节
  02-framework/    八层架构 + 文件操作约束（文档）
  03-memory/       11 个记忆工具（note/evt/mem-search/consolidate/guard/done
                   + common/config/entity/candidate/coldstore）
  04-im/           群聊桥（多会话互相 @）
  05-tools/        五个运维工具（安全删除/停摆告警/看门/质检/图库整理）
  06-docs/         安装文档
  07-skill/        给我（AI）读的口径
install.ps1        一键脚本（高级选项，一般不用）
scripts/check.py   提交前四项检查
tests/             11 项冒烟测试
```

## 维护规矩（改代码的 AI 要看）

1. 改之前跑 `python scripts/check.py`（BOM / 硬路径 / Python 语法 / JS 语法）
2. 改完跑 `python -m unittest discover -s tests`
3. `.ps1` 文件**必须带 UTF-8 BOM**（Windows PowerShell 5.1 按 GBK 读，不带就乱码）
4. 其他文件**不许带 BOM**
5. 路径一律从 `config.json` 读，不许写死 `C:\Users\<名字>`

## 一句话给 AI

**别替他做决定，先把三档的区别说清让他选。装不上的别硬撑，清单里写清「为什么」。**
