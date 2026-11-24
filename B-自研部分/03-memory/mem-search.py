"""记忆检索：本地 bge-small-zh 向量 + 关键词混合，给 engramory 记忆库用。

用法:
  python mem-search.py index            建索引（扫描所有 .md，存 npz）
  python mem-search.py query "问题" [k]  检索 top-k
"""
import os, sys, json, re, math, glob, time
import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from config import cfg, expand   # noqa: E402


BASE = expand(cfg("memory.dir", "~/.dsh/.engramory-memory"))
MODEL_DIR = expand(cfg("memory.model_dir", "~/.dsh/mem-tools/models/bge-small-zh"))
INDEX = expand(cfg("memory.index", "~/.dsh/mem-tools/index.npz"))

tok = None
sess = None


def load():
    global tok, sess
    if tok is None:
        tok = Tokenizer.from_file(os.path.join(MODEL_DIR, "tokenizer.json"))
        tok.enable_truncation(max_length=512)
        tok.enable_padding(length=512)
    if sess is None:
        sess = ort.InferenceSession(
            os.path.join(MODEL_DIR, "model_quantized.onnx"),
            providers=["CPUExecutionProvider"],
        )
    return tok, sess


def encode(texts):
    t, s = load()
    names = [i.name for i in s.get_inputs()]
    out = []
    for i in range(0, len(texts), 8):
        batch = texts[i:i + 8]
        encs = t.encode_batch(batch)
        feeds = {
            "input_ids": np.array([e.ids for e in encs], dtype=np.int64),
            "attention_mask": np.array([e.attention_mask for e in encs], dtype=np.int64),
        }
        if "token_type_ids" in names:
            feeds["token_type_ids"] = np.array([e.type_ids for e in encs], dtype=np.int64)
        res = s.run(None, feeds)
        v = res[0][:, 0, :]  # bge 用 [CLS] pooling
        v = v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)
        out.append(v)
    return np.vstack(out)


def chunks_of(path):
    """把一篇笔记按 ## 小节切块，太短的不切。"""
    txt = open(path, encoding="utf-8", errors="replace").read()
    title = os.path.basename(path)[:-3]
    body = re.sub(r"^---.*?---\s*", "", txt, flags=re.S)  # 去掉 frontmatter
    parts = re.split(r"\n(?=## )", body)
    out = []
    for p in parts:
        p = p.strip()
        if len(p) < 20:
            continue
        out.append({"file": path, "title": title, "text": p})
    return out


def build():
    files = [p for p in glob.glob(os.path.join(BASE, "**", "*.md"), recursive=True)
             if "archive" not in p.lower()]
    rows = []
    for f in files:
        rows.extend(chunks_of(f))
    if not rows:
        print("没有任何笔记")
        return
    vecs = encode([r["title"] + "。" + r["text"] for r in rows])
    np.savez(INDEX, vecs=vecs, rows=json.dumps(rows, ensure_ascii=False))
    print(f"索引完成：{len(rows)} 块，{len(files)} 篇，向量 {vecs.shape}")


def links_of(text):
    return re.findall(r"\]\(([^)]+\.md)\)", text)


_INDEX_CACHE = {}


def load_index():
    """按 mtime 缓存的索引加载（索引 143MB，每次 np.load 很贵）"""
    st = os.stat(INDEX)
    key = (st.st_mtime, st.st_size)
    if _INDEX_CACHE.get("k") == key:
        return _INDEX_CACHE["v"]
    data = np.load(INDEX, allow_pickle=False)
    v = (data["vecs"], json.loads(str(data["rows"])))
    _INDEX_CACHE.clear()
    _INDEX_CACHE["k"] = key
    _INDEX_CACHE["v"] = v
    return v


STOP_WORDS = ["怎么样", "怎么", "如何", "什么", "哪些", "是不是", "能不能", "可以吗",
              "一下", "帮我", "请问", "那个", "这个", "然后", "还有"]
PRONOUNS = {"刚才": None, "上次": None, "之前": None, "那次": None, "那个事": None, "这事": None, "那事": None}


EV_PATH = expand(cfg("runtime.events", "~/.dsh/runtime/events.jsonl"))


def _recent_topic():
    """从事件流取最近一条的 target，用于补全指代"""
    try:
        with open(EV_PATH, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()[-40:]
    except OSError:
        return ""
    for ln in reversed(lines):
        try:
            e = json.loads(ln)
        except Exception:
            continue
        if e.get("target"):
            return str(e["target"])
    return ""


def expand_query(q):
    """查询改写 + 分解。返回子查询列表（含原句）。

    三条规则：
      1. 去停用词得到语义核
      2. 按分隔符拆成子句
      3. 指代词补全成最近话题
    """
    out = [q.strip()]
    # 1. 语义词核
    core = q
    for w in STOP_WORDS:
        core = core.replace(w, "")
    core = core.strip("，。？！、 的了吗呢")
    if len(core) >= 2 and core not in out:
        out.append(core)
    # 2. 按分隔符拆
    for part in re.split(r"[，,、；;和以及]", q):
        part = part.strip().strip("的了吗呢")
        if len(part) >= 3 and part not in out:
            out.append(part)
    # 3. 指代补全
    if any(pr in q for pr in PRONOUNS):
        topic = _recent_topic()
        if topic:
            # 一次替换，长词优先（避免"刚才"和"那个事"重复替换）
            pat = "|".join(sorted(PRONOUNS, key=len, reverse=True))
            restored = re.sub(pat, topic, q, count=1)
            if restored not in out:
                out.append(restored)
    return out[:4]          # 最多 4 个子查询


def minmax(vals, eps=1e-9):
    v = np.asarray(vals, dtype=float)
    lo, hi = v.min(), v.max()
    rng = hi - lo if hi - lo > 0 else eps
    return (v - lo) / rng


def query(q, k=5, spread=2, task_kind="rule"):
    """三因子归一化检索（书 p217 / 命题 4.1 修正版）：
    先硬过滤 → 三项各自 min-max 归一 → 加权 → top-k → 沿 links 扩散。
    """
    if not os.path.exists(INDEX):
        print("还没建索引，先跑 index")
        return
    vecs, rows = load_index()
    subs = expand_query(q)
    qvs = encode(subs)              # 原句 + 改写 + 分解，一起编码

    # 硬过滤：active 状态（weights.json 里的条目默认 active）
    W = {}
    wp = os.path.join(os.path.dirname(__file__), "weights.json")
    if os.path.exists(wp):
        W = json.load(open(wp, encoding="utf-8"))

    n = len(rows)
    s_imp = np.zeros(n)
    s_rec = np.zeros(n)
    s_sim = (vecs @ qvs.T).max(axis=1)      # 任一子查询命中即算像
    now = time.time()
    half_life = {"episode": 7, "fact": 90, "procedural": 365, "relationship": 1e9,
                 "feedback": 365, "user": 365, "self": 365, "moment": 90,
                 "upcoming": 60, "project": 45, "reference": 120}
    for i, r in enumerate(rows):
        w = W.get(r["file"], {})
        if w.get("status") == "archived":
            s_imp[i] = -1e9  # 归档条目不参与
            continue
        s_imp[i] = float(w.get("importance", 5))
        age_days = (now - os.path.getmtime(r["file"])) / 86400
        typ = w.get("type", "reference")
        s_rec[i] = float(np.exp(-age_days / half_life.get(typ, 90)))
        refs = w.get("refs", 0)
        # 无实测命中记录 → 权重打折（E4 修正：不给默认满分）
        if w.get("hits", 0) == 0 and refs == 0:
            s_imp[i] *= 0.6
            s_rec[i] *= 0.6

    wmap = {"rule": (0.5, 0.1, 0.4), "material": (0.2, 0.3, 0.5),
            "procedure": (0.3, 0.2, 0.5)}
    w1, w2, w3 = wmap.get(task_kind, wmap["rule"])
    a = (w1 * minmax(s_imp) + w2 * minmax(s_rec) + w3 * minmax(s_sim))

    # 三层加成：core 最该被想起，archival 降权但不排除
    TIER_BOOST = {"core": 1.25, "recall": 1.0, "archival": 0.75}
    for i, r in enumerate(rows):
        t_i = (W.get(r["file"], {}) or {}).get("tier", "recall")
        a[i] *= TIER_BOOST.get(t_i, 1.0)
    # 无解题阈值：语义相似度全部过低 → 报"未找到"，不拿最相似的凑数
    if float(s_sim.max()) < 0.45:
        print(f"\n问题：{q}\n未找到相关记忆（最高语义相似度 {s_sim.max():.3f} < 0.45）。不凑数。")
        return
    order = np.argsort(-a)[:k]
    hits = []
    for i in order:
        r = rows[i]
        hits.append({
            "score": float(a[i]),
            "sim": float(s_sim[i]),
            "title": r["title"],
            "file": os.path.basename(r["file"]),
            "text": r["text"][:220].replace("\n", " "),
        })
    # 图扩散：从命中的文件里捞它引用到的其它 md
    have = {h["file"] for h in hits}
    extra = []
    for i in order:
        src = rows[i]["file"]
        try:
            raw = open(src, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for ln in links_of(raw):
            base = os.path.basename(ln)
            if base in have:
                continue
            have.add(base)
            extra.append({"from": os.path.basename(src), "to": base})
            if len(extra) >= spread:
                break
        if len(extra) >= spread:
            break
    print(f"\n问题：{q}\n")
    for n, h in enumerate(hits, 1):
        print(f"[{n}] 综合{h['score']:.3f} / 语义{h['sim']:.3f}  {h['title']}  ({h['file']})")
        print(f"    {h['text']}\n")
    if extra:
        print("沿关联扩散：")
        for e in extra:
            print(f"  {e['from']} → {e['to']}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "query"
    if cmd == "index":
        build()
    else:
        q = sys.argv[2] if len(sys.argv) > 2 else ""
        k = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        query(q, k)
