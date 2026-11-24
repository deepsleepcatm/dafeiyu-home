// config.mjs —— Node 端读 config.json（跟 Python 的 config.py 读同一份）
//
// 为什么要有它：
//   chat-bridge.mjs 里组清单、路径全是写死的。别人 clone 下来一个都跑不了。
//   现在全部从这里读，改 config.json 一处，Node 和 Python 一起生效。
import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
// 往上找仓库根（有 config.json 或 config.example.json 那层）
function findRoot(dir) {
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(dir, 'config.json')) || fs.existsSync(path.join(dir, 'config.example.json'))) return dir
    const up = path.dirname(dir)
    if (up === dir) break
    dir = up
  }
  return HERE
}
const ROOT = findRoot(HERE)

let cache = null
function load() {
  if (cache) return cache
  for (const f of ['config.json', 'config.example.json']) {
    try {
      cache = JSON.parse(fs.readFileSync(path.join(ROOT, f), 'utf8'))
      return cache
    } catch {}
  }
  cache = {}
  return cache
}

/** 读配置，支持点号路径：cfg('chat.port', 8765) */
export function cfg(key, dflt) {
  let node = load()
  for (const part of String(key).split('.')) {
    if (node && typeof node === 'object' && part in node) node = node[part]
    else return dflt
  }
  return node ?? dflt
}

/** 把 ~ 换成主目录 */
export function expand(p) {
  if (!p) return p
  return path.resolve(String(p).startsWith('~') ? String(p).replace(/^~/, os.homedir()) : String(p))
}

export { ROOT }
