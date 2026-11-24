# CHANGELOG

## v0.1.0 · 2026-11-24

首次发布。六个模块：

### 新增

- `01-soul/` 灵魂模块：`soul.js` 状态机（心情 4 维 + 关系 4 维 + 13 种事件）+ `charter.md` 宪章 + `install.py`
- `02-framework/` 八层架构与判据、文件操作约束（防误删三道闸）
- `03-memory/` 六个记忆工具：note / mem-search / evt / consolidate / guard / done
- `04-im/` 群聊互证：server.py + chat-bridge.mjs + trace.mjs
- `05-tools/` 五个运维工具：安全删除 / 停摆告警 / 看门 / 质检 / 图库整理
- `06-docs/` 装机清单 + A 档 / B 档一键指令
- `scripts/check.py` 提交前检查（BOM / 硬路径 / Python 语法 / JS 语法）
- `.github/workflows/check.yml` 每次 push 自动跑检查

### 已知限制

- `soul.js` 从没给别人装过，只在原作者机器上验证
- `guard.py` 没有实战拦截记录
- `consolidate.py` 未验证幂等
- `done.py` 检查项不全
- 群聊模块默认只绑 127.0.0.1，局域网要自己改

### 来源

全部来自真事故。每一个工具对应一次实际问题，没有一个是纸上推演出来的。

---

## 怎么发版

1. 改完跑 `python scripts/check.py`（全过才能提交）
2. 更新本文件的「新增 / 修复 / 变更」
3. commit 信息格式：`类型: 一句话`（类型 = feat / fix / docs / chore）
4. 打 tag：`v0.1.x`
