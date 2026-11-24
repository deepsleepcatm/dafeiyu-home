# 04-im · 接进 IM

## 它解决什么问题

**让 AI 不只活在那个窗口里**，手机能发、群里能说话。

## 里面有什么

`group-chat/` —— 群聊互证（局域网多会话）

| 文件 | 干什么 |
|---|---|
| `server.py` | 群聊 HTTP 服务（默认 :8765） |
| `chat-bridge.mjs` | 读消息库 → 叫醒对应的组 |
| `trace.mjs` | 结构化追踪，一句人话说清卡在哪 |
| `config.mjs` | 配置 |

**设计要点**：多个 AI 会话在一个群聊里互相 @、互相印证。不是"一个 AI 扮演多个角色"，是**真的开了多个会话**。

## 怎么用

```bash
python server.py --host 127.0.0.1 --port 8765
node chat-bridge.mjs
```

组名和数量自己定（`config.mjs` 里改）。

## 依赖

- Python 3.8+（server.py，纯标准库）
- Node.js 18+（bridge / trace）

## 需要注意

- **默认只绑 127.0.0.1**。要局域网访问得显式改 `--host 0.0.0.0`，并且自己加防火墙规则
- 这个模块**不是必装项**。只有需要多会话协同时才装

---

## 跑之前需要什么

**代码在这，但不是 clone 下来就能跑** —— 缺两样东西要自己装。

### ① Node.js 22.5 以上

桥用了 
ode:sqlite（Node 22.5 才开始有）。低了会报 Cannot find module 'node:sqlite'。

`ash
node --version    # 要显示 v22.5.0 或更高
`

### ② npm 依赖

`ash
cd B-自研部分/04-im/group-chat
npm install
`

会装 js-yaml。另外 @deepseek-ai/dsh-host-apiproxy 是 DSH 自带的，不用单独装。

### ③ Python 3.10+（只跑 server.py 才要）

server.py 只用标准库，不用 pip install 任何东西。

### ④ 配置

`ash
cp config.example.json config.json   # 在仓库根目录
# 改里面的 chat.dir / chat.port / chat.groups
`

**组名、端口、目录，全是你自己定。** 仓库里那份 GROUPS 数组是占位符。