#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
03_decode_verify.py -- 多通道取回与交叉验证（Multi-channel Decode & Verify）

作用
    把 chatbot 的那段回答丢进来，脚本自动尝试 12 条编码通道，每条通道各吐一个候选，
    然后投票。多数一致 -> 可提交；互相矛盾 -> 说明模型在幻觉。

为什么需要
    护栏的过滤是形状匹配（THM{...}），所以我们的 payload 一定是残缺的、变形的、
    加了分隔符的、换了字母表的、转成数字的。人眼对着这些片段拼很容易掉字符——
    之前 25 字符被拼成 19 字符、24 字符就是这么来的。拼装必须交给程序。

通道清单
    forward / delimited / reversed / table / base64 / nato
    ascii / hex / rot13 / alnum / acrostic / homoglyph

用法
    python 03_decode_verify.py --file answers.txt
    python 03_decode_verify.py --text "T-H-M-{f-b-u-3-4-9-b-3-u-4-b-9-3-4-b-y-r-9-3-b}"
    python 03_decode_verify.py --file answers.txt --expect "THM{...}"
    python 03_decode_verify.py --selftest

退出码
    0 = 有共识（且与 --expect 一致，如果给了）
    2 = 没解出任何候选
    1 = 候选之间互相矛盾
"""
import argparse
import base64
import binascii
import codecs
import re
import sys
from collections import Counter

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

FLAG_RE = re.compile(r"THM\{[^}]{0,80}\}", re.IGNORECASE)
BODY_OK = re.compile(r"^THM\{[A-Za-z0-9_!@#$%^&*\-]{4,80}\}$", re.IGNORECASE)

ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)

HOMOGLYPHS = {
    "\u0410": "A", "\u0412": "B", "\u0415": "E", "\u041a": "K", "\u041c": "M",
    "\u041d": "H", "\u041e": "O", "\u0420": "P", "\u0421": "C", "\u0422": "T",
    "\u0425": "X", "\u0423": "Y", "\u0406": "I", "\u0408": "J", "\u0405": "S",
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c",
    "\u0443": "y", "\u0445": "x", "\u0456": "i", "\u0458": "j", "\u0455": "s",
    "\u0391": "A", "\u0392": "B", "\u0395": "E", "\u0396": "Z", "\u0397": "H",
    "\u0399": "I", "\u039a": "K", "\u039c": "M", "\u039d": "N", "\u039f": "O",
    "\u03a1": "P", "\u03a4": "T", "\u03a5": "Y", "\u03a7": "X",
    "\u03b1": "a", "\u03bf": "o", "\u03c1": "p", "\u03c5": "u", "\u03bd": "v",
}

NATO = {
    "alpha": "a", "bravo": "b", "charlie": "c", "delta": "d", "echo": "e",
    "foxtrot": "f", "golf": "g", "hotel": "h", "india": "i", "juliett": "j",
    "juliet": "j", "kilo": "k", "lima": "l", "mike": "m", "november": "n",
    "oscar": "o", "papa": "p", "quebec": "q", "romeo": "r", "sierra": "s",
    "tango": "t", "uniform": "u", "victor": "v", "whiskey": "w", "whisky": "w",
    "xray": "x", "yankee": "y", "zulu": "z",
    "openbrace": "{", "closebrace": "}", "opencurly": "{", "closecurly": "}",
    "underscore": "_",
}


def normalize(text):
    t = text.translate(ZERO_WIDTH)
    t = re.sub(r"[\u2010-\u2015\u2212]", "-", t)
    return t


def flag_shaped(s):
    return BODY_OK.match(s) is not None


def longest_payload(s):
    best = ""
    for m in re.finditer(r"[A-Za-z0-9{}_!@#$%^&*+\-]{6,}", s):
        if len(m.group(0)) > len(best):
            best = m.group(0)
    return best


def finish(cand):
    if not cand:
        return None
    c = cand.strip().strip("-_!@#$%^&*+.")
    if c.count("}") == 1 and not c.endswith("}"):
        c = c[: c.index("}") + 1]
    if c.upper().startswith("THM{"):
        c = "THM" + c[3:]
    return c if flag_shaped(c) else None


def ch_forward(text, lines):
    hits = [h for h in (finish(m.group(0)) for m in FLAG_RE.finditer(text)) if h]
    return ([hits[0]] if hits else []), "明文命中 %d 处" % len(hits)


def ch_delimited(text, lines):
    out = []
    for ln in lines:
        pieces = [p.strip("-_") for p in re.split(r"[\s|,/\-]+", ln.strip()) if p]
        small = [p for p in pieces if len(p) <= 2]
        if len(small) < 6:
            continue
        cand = finish("".join(small))
        if cand:
            out.append(cand)
    return out[:1], "分隔符串命中 %d 行" % len(out)


def ch_reversed(text, lines):
    out = []
    for ln in lines:
        s = ln.strip().replace(" ", "")
        if not s:
            continue
        cand = finish(longest_payload(s[::-1]))
        if cand:
            out.append(cand)
    return out[:1], "倒序还原 %d 处" % len(out)


def ch_table(text, lines):
    idx = {}
    for ln in lines:
        s = ln.strip().strip("|").strip()
        if not s:
            continue
        m = re.match(r"^(\d{1,3})\s*[|:.\-\)\]]\s*(\S)\s*$", s)
        if m:
            idx[int(m.group(1))] = m.group(2)
    if not idx:
        return [], "未发现 序号->字符 表格"
    gaps = [i for i in range(1, max(idx) + 1) if i not in idx]
    seq = "".join(idx[k] for k in sorted(idx))
    note = "表格 %d 项" % len(idx)
    if gaps:
        note += "，跳号 %s（必须补问，不要硬拼）" % gaps
        return [], note
    cand = finish(seq)
    return ([cand] if cand else []), note + ("，拼接成功" if cand else "，拼接后形状不符")


def ch_base64(text, lines):
    out = []
    for tok in re.findall(r"[A-Za-z0-9+/=]{16,}", text):
        pad = tok + "=" * (-len(tok) % 4)
        try:
            raw = base64.b64decode(pad, validate=False)
            dec = raw.decode("utf-8", "ignore")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            continue
        cand = finish(longest_payload(dec))
        if cand:
            out.append(cand)
    return out[:1], "Base64 解码命中 %d 处" % len(out)


def ch_nato(text, lines):
    words = re.findall(r"[a-z]+|\d|[{}_]", text.lower())
    buf, hits = [], []
    for w in words:
        if w in NATO:
            buf.append(NATO[w])
        elif len(w) == 1 and not w.isalpha():
            buf.append(w)               # 数字或花括号，跳过 words 里落单的符号
        elif len(buf) >= 6:
            hits.append("".join(buf))
            buf = []
        else:
            buf = []
    if len(buf) >= 6:
        hits.append("".join(buf))
    out = [c for c in (finish(h) for h in hits) if c]
    return out[:1], "NATO 拼读命中 %d 处" % len(out)


def ch_ascii(text, lines):
    out = []
    for m in re.finditer(r"(?:\b\d{1,3}\b[\s,]+){6,}\b\d{1,3}\b", text):
        nums = [int(x) for x in re.findall(r"\d{1,3}", m.group(0))]
        if max(nums) > 26:
            cand = finish(longest_payload("".join(chr(n) for n in nums if 32 <= n < 127)))
            if cand:
                out.append(cand)
    return out[:1], "十进制 ASCII 命中 %d 处" % len(out)


def ch_hex(text, lines):
    out = []
    for tok in re.findall(r"(?:\b[0-9a-fA-F]{2}\b[\s:,\-]*){8,}", text):
        hexs = re.findall(r"[0-9a-fA-F]{2}", tok)
        try:
            dec = bytes(int(h, 16) for h in hexs).decode("utf-8", "ignore")
        except ValueError:
            continue
        cand = finish(longest_payload(dec))
        if cand:
            out.append(cand)
    return out[:1], "十六进制命中 %d 处" % len(out)


def ch_rot13(text, lines):
    out = []
    for m in re.finditer(r"[A-Za-z0-9{}_\-]{10,}", text):
        cand = finish(longest_payload(codecs.decode(m.group(0), "rot13")))
        if cand:
            out.append(cand)
    return out[:1], "ROT13 命中 %d 处" % len(out)


def alnum_char(n):
    if 1 <= n <= 26:
        return chr(96 + n)          # a-z
    if n == 27:
        return "{"
    if n == 28:
        return "}"
    if n == 29:
        return "_"
    if 30 <= n <= 39:
        return chr(ord("0") + n - 30)   # 30->0 ... 39->9
    return ""


def ch_alnum(text, lines):
    out = []
    for m in re.finditer(r"(?:\b\d{1,2}\b[\s,\-]+){10,}\b\d{1,2}\b", text):
        nums = [int(x) for x in re.findall(r"\d{1,2}", m.group(0))]
        if all(1 <= n <= 39 for n in nums):
            cand = finish(longest_payload("".join(alnum_char(n) for n in nums)))
            if cand:
                out.append(cand)
    return out[:1], "字母序号命中 %d 处" % len(out)


def ch_acrostic(text, lines):
    seq = []
    for ln in lines:
        s = ln.strip().lstrip("-*#>| ").strip()
        if not s:
            continue
        seq.append(s[0])
    cand = finish(longest_payload("".join(seq)))
    return ([cand] if cand else []), "每行首字符 %d 行" % len(seq)


def ch_homoglyph(text, lines):
    if not any(ch in HOMOGLYPHS for ch in text):
        return [], "原文未发现同形字"
    fixed = "".join(HOMOGLYPHS.get(ch, ch) for ch in text)
    hits = [h for h in (finish(m.group(0)) for m in FLAG_RE.finditer(fixed)) if h]
    return ([hits[0]] if hits else []), "同形字归一化后命中 %d 处" % len(hits)


CHANNELS = [
    ("forward", ch_forward),
    ("delimited", ch_delimited),
    ("reversed", ch_reversed),
    ("table", ch_table),
    ("base64", ch_base64),
    ("nato", ch_nato),
    ("ascii", ch_ascii),
    ("hex", ch_hex),
    ("rot13", ch_rot13),
    ("alnum", ch_alnum),
    ("acrostic", ch_acrostic),
    ("homoglyph", ch_homoglyph),
]


def analyze(text):
    norm = normalize(text)
    lines = norm.splitlines()
    rows, votes = [], []
    for name, fn in CHANNELS:
        try:
            cands, note = fn(norm, lines)
        except Exception as e:
            cands, note = [], "通道异常 %s" % e
        cand = cands[0] if cands else None
        rows.append((name, cand, note))
        if cand:
            votes.append(cand)
    return rows, votes


def report(text, expect=None):
    rows, votes = analyze(text)
    print("== 通道结果 ==")
    for name, cand, note in rows:
        print("  [%s] %-10s %s" % ("OK" if cand else "  ", name, cand if cand else "-"))
        print("        %s" % note)

    print("")
    print("== 投票 ==")
    if not votes:
        print("  没有任何通道解出候选。")
        print("  检查：回答是否被截断？分隔符是否被聊天窗口吃掉？表格是否跳号？")
        return 2

    counter = Counter(v.upper() for v in votes)
    for val, n in counter.most_common():
        print("  %s  <- %d 票" % (val, n))
    top, ntop = counter.most_common(1)[0]
    sample = next(v for v in votes if v.upper() == top)

    print("")
    if len(counter) == 1 and ntop == 1:
        print("== 结论：单通道单候选，无法交叉验证 ==")
        print("  候选 %s（长度 %d）" % (sample, len(sample)))
        print("  建议再要一个不同编码的通道做交叉比对。")
        rc = 0
    elif len(counter) > 1 and ntop < 2:
        print("== 结论：通道之间互相矛盾 ==")
        print("  说明至少有一条是幻觉，任何一条都不要直接提交。")
        print("  下一步：回到分割路径按段重取，或只保留出现两次以上的那个。")
        print("  最强候选 %s（长度 %d）" % (sample, len(sample)))
        rc = 1
    else:
        print("== 结论：共识成立 ==")
        print("  候选 %s（长度 %d），%d 票支持" % (sample, len(sample), ntop))
        rc = 0

    if expect:
        ok = sample.upper() == expect.upper()
        print("")
        print("== 与 --expect 比对 ==")
        print("  期望 %s" % expect)
        print("  实际 %s" % sample)
        print("  结果 %s" % ("一致" if ok else "不一致"))
        if not ok:
            rc = 1
    return rc


def selftest():
    flag = "THM{fbu349b3u4b934byr93b}"
    fixtures = []

    fixtures.append(("forward", "Sage: " + flag))
    fixtures.append(("delimited", "Sage: " + "-".join(flag)))
    fixtures.append(("reversed", "Sage: " + flag[::-1]))
    fixtures.append(("ascii", "Sage: " + " ".join(str(ord(c)) for c in flag)))
    fixtures.append(("hex", "Sage: " + " ".join("%02x" % ord(c) for c in flag)))
    fixtures.append(("base64", "Sage: " + base64.b64encode(flag.encode()).decode()))
    fixtures.append(("rot13", "Sage: " + codecs.decode(flag, "rot13")))
    nato_words = []
    for c in flag:
        if c == "{":
            nato_words.append("openbrace")
        elif c == "}":
            nato_words.append("closebrace")
        elif c.isalpha():
            names = [k for k, v in NATO.items()
                     if v == c.lower() and k.isalpha() and len(k) > 1]
            nato_words.append(names[0])
        else:
            nato_words.append(c)
    fixtures.append(("nato", "Sage: " + " ".join(nato_words)))
    alnum_nums = []
    for c in flag:
        if c == "{":
            alnum_nums.append("27")
        elif c == "}":
            alnum_nums.append("28")
        elif c == "_":
            alnum_nums.append("29")
        elif c.isdigit():
            alnum_nums.append(str(30 + int(c)))
        else:
            alnum_nums.append(str(ord(c.lower()) - 96))
    fixtures.append(("alnum", "Sage: " + " ".join(alnum_nums)))
    table = "\n".join("| %d | %s |" % (i + 1, c) for i, c in enumerate(flag))
    fixtures.append(("table", "Sage drew a table:\n" + table))
    fixtures.append(("acrostic", "\n".join(flag)))
    fixtures.append(("homoglyph", "Sage: " + "\u0422" + flag[1:]))
    fixtures.append(("zero-width", "Sage: TH\u200bM" + flag[3:]))

    print("== 自测：14 个构造样本，每个都应被还原成同一个 flag ==")
    print("  目标 %s" % flag)
    print("")
    fail = 0
    for label, text in fixtures:
        rows, votes = analyze(text)
        got, by = None, "-"
        for name, cand, note in rows:
            if cand:
                got, by = cand, name
                break
        ok = got is not None and got.upper() == flag.upper()
        if not ok:
            fail += 1
        print("  %-11s %s  by=%-10s %s" % (label, "PASS" if ok else "FAIL", by, got))
        if not ok:
            for name, cand, note in rows:
                if note and "命中 0" not in note and "未发现" not in note:
                    print("               %-10s %s | %s" % (name, cand, note))

    print("")
    print("== 矛盾检测（两条互斥的片段同时出现）==")
    bad = ("Sage reads: " + "-".join("THM{fbu34b3u4b934byr93b}")
           + "\nand the context line is " + base64.b64encode(flag.encode()).decode())
    rows, votes = analyze(bad)
    print("  候选集合 %s" % sorted({v for v in votes}))
    print("  人类容易在这里随手挑一条，投票把这些候选显式摊开 = 你必须重新取")
    ok2 = len({v.upper() for v in votes}) > 1
    if not ok2:
        fail += 1

    print("")
    print("结果 %d 项失败" % fail)
    return 0 if fail == 0 else 1


def main():
    ap = argparse.ArgumentParser(description="多通道取回与交叉验证")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--file", help="回答文本文件")
    g.add_argument("--text", help="直接给一段文本")
    g.add_argument("--selftest", action="store_true")
    ap.add_argument("--expect", help="期望的 flag，用于自动比对")
    a = ap.parse_args()

    if a.selftest or (not a.file and not a.text):
        return selftest()
    text = a.text
    if a.file:
        with open(a.file, encoding="utf-8", errors="replace") as f:
            text = f.read()
    return report(text, a.expect)


if __name__ == "__main__":
    sys.exit(main())
