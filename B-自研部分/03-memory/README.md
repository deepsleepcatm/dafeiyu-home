# 03-memory · 记忆工具

## 它解决什么问题

**AI 每次新对话都从零开始，怎么让它接得上？**

答案是四套数据分工，不是一个大记忆库：

| 数据 | 管什么 |
|---|---|
| 记忆库（`.md` + `MEMORY.md` 索引） | 长期事实：她是谁、什么话别说、约定、教训 |
| 事件流（`events.jsonl`） | 做过什么：决策/动作/失败/交接 |
| 状态（`state.json`） | 我现在什么样：心情、关系 |
| 时间线（`timeline.jsonl`） | 怎么变成这样的：可重放 |

## 里面有什么

| 文件 | 干什么 |
|---|---|
| `note.py` | 写记忆。**强制标来源**：她亲口 / 我实测 / 外部资料 / 我推的。没有来源写不进去 |
| `mem-search.py` | 跨对话检索。本地 bge-small-zh 向量 + 关键词混合 |
| `evt.py` | 事件流。记 decision / action / failure / handoff |
| `consolidate.py` | 巩固。补字段、算权重、标过期、出权重表 |
| `guard.py` | 动作风险分级 |
| `done.py` | 收尾机器判定（"我觉得做完了"不算数） |

## 怎么用

```bash
cp config.example.json config.json    # 先改里面的路径
python config.py                       # 自检：告诉你读到了什么、缺什么

python note.py --title "..." --type feedback --importance 9 \
               --source 她亲口 --ref "原话" --body "..."
python mem-search.py query "关键词" 5
python evt.py action "修好了 X" --target X
python consolidate.py
```

## 依赖

- Python 3.12（`mem-search.py` 需要 numpy / onnxruntime / tokenizers，都是 cp312 编的）
- 模型：bge-small-zh（首次用会下载，约 90 MB）

## 需要注意

**不跑 `consolidate.py`，记忆库会越攒越乱**，检索越来越不准。这是维护成本，不是一次性的。
