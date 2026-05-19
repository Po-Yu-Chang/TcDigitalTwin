#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TCI PLC Alarm ID Audit
======================
四份來源交叉比對:
  1. GVL_ErrorCode.TcGVL  -> GC_ALARM_xxx 常數宣告
  2. *.TcPOU/*.TcGVL/*.TcDUT 內 RegisterAlarm( 呼叫實際使用的 ID
     (忽略 Old_version_Service / Old_Version_Service)
  3. alarmlist.sql        -> SQL alarm 翻譯表
  4. GC_MSG_Long.csv      -> CSV alarm 翻譯表

輸出 mismatch punch list (A~E 五段)
"""

import csv
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(r"C:\Users\qoose\Desktop\文件資料\客戶分類\T-泰興\TCI")
PLC_ROOT = ROOT / "CinPhown_PackML" / "DAS_CoreSys"
GVL_FILE = PLC_ROOT / "Library" / "10_Machine" / "13_ErrorHandling" / "GVL_ErrorCode.TcGVL"
SQL_FILE = ROOT / "alarmlist.sql"
CSV_FILE = ROOT / "GC_MSG_Long.csv"

IGNORE_DIR_NAMES = {"old_version_service", "old_Version_Service", "Old_version_Service", "Old_Version_Service"}


# -----------------------------------------------------------------------------
# 1) Parse GC_ALARM_* constants from GVL_ErrorCode.TcGVL
# -----------------------------------------------------------------------------
def parse_gvl_constants(gvl_path: Path):
    """
    Returns:
      const_to_id     : dict[str, int]    constant_name -> base id
      const_to_line   : dict[str, int]    constant_name -> line number
      base_with_offsets : dict[str, list[int]]  constant_name -> [實際註冊到的 IDs]
    """
    text = gvl_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    const_to_id = {}
    const_to_line = {}
    # match e.g. "GC_ALARM_FOO  : DINT := 100;   // comment"
    pat = re.compile(r"\b(GC_ALARM_\w+|GC_AlARM_\w+)\s*:\s*DINT\s*:=\s*(\d+)\s*;")
    for ln_idx, ln in enumerate(lines, start=1):
        m = pat.search(ln)
        if not m:
            continue
        name = m.group(1)
        val = int(m.group(2))
        const_to_id[name] = val
        const_to_line[name] = ln_idx

    # Hard-coded base+offset expansions per CLAUDE-given rules and inline comments:
    base_with_offsets = {}
    # GC_ALARM_MODBUSRTU = 850, +1..+4 -> 851-854
    if "GC_ALARM_MODBUSRTU" in const_to_id:
        base = const_to_id["GC_ALARM_MODBUSRTU"]
        base_with_offsets["GC_ALARM_MODBUSRTU"] = list(range(base + 1, base + 5))
    # GC_ALARM_SAFE_DOOR_BASE = 900, +1..+16 -> 901-916
    if "GC_ALARM_SAFE_DOOR_BASE" in const_to_id:
        base = const_to_id["GC_ALARM_SAFE_DOOR_BASE"]
        base_with_offsets["GC_ALARM_SAFE_DOOR_BASE"] = list(range(base + 1, base + 17))

    return const_to_id, const_to_line, base_with_offsets


# -----------------------------------------------------------------------------
# 2) Walk PLC source tree and grep RegisterAlarm( calls
# -----------------------------------------------------------------------------
def walk_plc_files(plc_root: Path):
    exts = {".TcPOU", ".TcGVL", ".TcDUT"}
    for root, dirs, files in os.walk(plc_root):
        # prune ignored dirs (case-insensitive match)
        pruned = []
        for d in list(dirs):
            if d.lower() in {x.lower() for x in IGNORE_DIR_NAMES}:
                pruned.append(d)
                dirs.remove(d)
        for f in files:
            ext = os.path.splitext(f)[1]
            if ext in exts:
                yield Path(root) / f


def _split_args_top_level(args_text: str):
    """Split a CSV-like comma list at depth 0 (parentheses-aware)."""
    parts, buf, depth = [], [], 0
    for ch in args_text:
        if ch in "([{":
            depth += 1
            buf.append(ch)
        elif ch in ")]}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return parts


def _balance_parens(text: str, start_idx: int):
    """Given text and index of '(', return content inside up to matching ')'.
    Handles multi-line. Returns (inner_text, end_idx) or (None, None) if unbalanced."""
    assert text[start_idx] == "("
    depth = 0
    for i in range(start_idx, len(text)):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[start_idx + 1:i], i
    return None, None


def parse_register_alarm_calls(plc_root: Path, const_to_id, base_with_offsets):
    """
    Returns:
      static_calls : list of (id_int, file, line, snippet)
      dynamic_calls: list of (file, line, snippet)
      const_used   : set[str] of GC_ALARM_* names referenced (with or without offset)

    PLC RegisterAlarm uses named-arg syntax:
      RegisterAlarm(Trigger := ..., ID := <const-or-expr>, value := ..., Message := ..., Category := ...)
    so we need to find the parameter assigned to ID, not the first positional.
    """
    call_finder = re.compile(r"\bRegisterAlarm\s*\(", re.IGNORECASE)

    # Patterns for static ID-arg analysis:
    plain_const_re = re.compile(r"GVL_ErrorCode\.(GC_(?:ALARM|AlARM)_\w+)\s*$")
    const_plus_int_re = re.compile(r"GVL_ErrorCode\.(GC_(?:ALARM|AlARM)_\w+)\s*\+\s*(\d+)\s*$")
    int_plus_const_re = re.compile(r"(\d+)\s*\+\s*GVL_ErrorCode\.(GC_(?:ALARM|AlARM)_\w+)\s*$")
    bare_int_re = re.compile(r"^\s*(\d+)\s*$")
    id_named_re = re.compile(r"^\s*ID\s*:=\s*(.*\S)\s*$", re.IGNORECASE | re.DOTALL)

    static_calls = []
    dynamic_calls = []
    const_used = set()

    for fp in walk_plc_files(plc_root):
        try:
            text = fp.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = fp.read_text(encoding="utf-8-sig")
            except Exception:
                text = fp.read_text(encoding="latin-1")
        # build line index for line-number lookup
        line_starts = [0]
        for i, c in enumerate(text):
            if c == "\n":
                line_starts.append(i + 1)

        for m in call_finder.finditer(text):
            paren_idx = m.end() - 1  # position of '('
            inner, end_idx = _balance_parens(text, paren_idx)
            if inner is None:
                continue
            ln_idx = next(i for i in range(len(line_starts) - 1, -1, -1) if line_starts[i] <= m.start()) + 1
            # find snippet: just the line containing the call start
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.start())
            if line_end < 0:
                line_end = len(text)
            snippet = text[line_start:line_end].strip()[:240]

            args = _split_args_top_level(inner)
            id_expr = None
            uses_named = any(":=" in a for a in args)
            if uses_named:
                for a in args:
                    mm = id_named_re.match(a)
                    if mm:
                        id_expr = mm.group(1).strip()
                        break
            else:
                # positional — first arg is ID
                if args:
                    id_expr = args[0].strip()

            if id_expr is None:
                dynamic_calls.append((str(fp), ln_idx, snippet + "  [no ID arg found]"))
                continue

            # Try static parses
            m1 = plain_const_re.match(id_expr)
            if m1:
                name = m1.group(1)
                const_used.add(name)
                if name in const_to_id:
                    static_calls.append((const_to_id[name], str(fp), ln_idx, snippet))
                else:
                    dynamic_calls.append((str(fp), ln_idx, snippet + f"  [unknown const {name}]"))
                continue
            m2 = const_plus_int_re.match(id_expr)
            if m2:
                name = m2.group(1)
                off = int(m2.group(2))
                const_used.add(name)
                if name in const_to_id:
                    static_calls.append((const_to_id[name] + off, str(fp), ln_idx, snippet))
                else:
                    dynamic_calls.append((str(fp), ln_idx, snippet + f"  [unknown const {name}]"))
                continue
            m3 = int_plus_const_re.match(id_expr)
            if m3:
                off = int(m3.group(1))
                name = m3.group(2)
                const_used.add(name)
                if name in const_to_id:
                    static_calls.append((const_to_id[name] + off, str(fp), ln_idx, snippet))
                else:
                    dynamic_calls.append((str(fp), ln_idx, snippet + f"  [unknown const {name}]"))
                continue
            m4 = bare_int_re.match(id_expr)
            if m4:
                static_calls.append((int(m4.group(1)), str(fp), ln_idx, snippet))
                continue
            # dynamic
            dynamic_calls.append((str(fp), ln_idx, snippet + f"  [ID={id_expr[:80]}]"))

    return static_calls, dynamic_calls, const_used


# -----------------------------------------------------------------------------
# 3) Parse alarmlist.sql -> {id: {lang: value}}
# -----------------------------------------------------------------------------
def parse_sql(sql_path: Path):
    text = sql_path.read_text(encoding="utf-8-sig", errors="replace")
    # find INSERT INTO alarmlist VALUES (..),(..),(..);
    # rows look like:  (1,'1','English','Allocate Estop has trig'),
    row_re = re.compile(
        r"\(\s*(\d+)\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'((?:[^'\\]|\\.)*)'\s*\)"
    )
    by_id = defaultdict(dict)
    for m in row_re.finditer(text):
        # idx = m.group(1)  # row index, ignore
        eid_raw = m.group(2).strip()
        lang = m.group(3).strip()
        value = m.group(4)
        try:
            eid = int(eid_raw)
        except ValueError:
            continue
        by_id[eid][lang] = value
    return by_id


# -----------------------------------------------------------------------------
# 4) Parse GC_MSG_Long.csv -> {id: {lang: value}}
# -----------------------------------------------------------------------------
def parse_csv(csv_path: Path):
    by_id = defaultdict(dict)
    # Read with utf-8-sig to drop BOM
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        for row in reader:
            if not row or len(row) < 4:
                continue
            try:
                eid = int(row[1].strip())
            except ValueError:
                continue
            lang = row[2].strip()
            value = row[3]
            by_id[eid][lang] = value
    return by_id


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def main():
    print(f"GVL : {GVL_FILE}")
    print(f"PLC : {PLC_ROOT}")
    print(f"SQL : {SQL_FILE}")
    print(f"CSV : {CSV_FILE}")
    print()

    const_to_id, const_to_line, base_with_offsets = parse_gvl_constants(GVL_FILE)
    print(f"[1] GVL: {len(const_to_id)} GC_ALARM_* constants declared")

    static_calls, dynamic_calls, const_used = parse_register_alarm_calls(
        PLC_ROOT, const_to_id, base_with_offsets
    )

    plc_registered_ids = set()
    plc_id_origin = defaultdict(list)  # id -> list of (file, line, snippet)
    for (id_, fp, ln, snip) in static_calls:
        plc_registered_ids.add(id_)
        plc_id_origin[id_].append((fp, ln, snip))

    # Add the documented base+offset expansions (MODBUSRTU/SAFE_DOOR_BASE).
    # These constants are "used" in dynamic ways in code (loops over indices), but per
    # the GVL comments they DEFINITELY occupy 851-854 / 901-916.
    for cname, ids in base_with_offsets.items():
        for i in ids:
            plc_registered_ids.add(i)
            plc_id_origin[i].append((str(GVL_FILE), const_to_line[cname], f"(documented base+offset of {cname})"))
        # Mark the base const itself as "used" so it doesn't show up as orphan.
        if cname in const_to_id:
            const_used.add(cname)

    print(f"[2] PLC RegisterAlarm: {len(static_calls)} static call sites, "
          f"{len(dynamic_calls)} dynamic call sites; {len(plc_registered_ids)} unique IDs")

    sql_by_id = parse_sql(SQL_FILE)
    csv_by_id = parse_csv(CSV_FILE)
    print(f"[3] SQL : {len(sql_by_id)} unique IDs")
    print(f"[4] CSV : {len(csv_by_id)} unique IDs")
    print()

    sql_ids = set(sql_by_id.keys())
    csv_ids = set(csv_by_id.keys())

    # ---------------- Section A: PLC has, SQL/CSV missing ----------------
    print("=" * 72)
    print("A. PLC 有註冊但 SQL/CSV 查不到的 ID (HMI 看到 alarm 但顯示不出訊息):")
    print("=" * 72)
    section_a = []
    for id_ in sorted(plc_registered_ids):
        in_sql = id_ in sql_ids
        in_csv = id_ in csv_ids
        if in_sql and in_csv:
            continue
        # find a label for "where" — prefer constant whose base id == id_ or whose
        # base+offset range covers id_.
        label = None
        for cname, base in const_to_id.items():
            if base == id_:
                label = cname
                break
        if not label:
            for cname, ids in base_with_offsets.items():
                if id_ in ids:
                    base = const_to_id[cname]
                    label = f"{cname}+{id_ - base}"
                    break
        if not label:
            origin = plc_id_origin[id_][0]
            label = f"in {Path(origin[0]).name}:{origin[1]}"

        if in_sql and not in_csv:
            tag = "CSV 缺"
        elif in_csv and not in_sql:
            tag = "SQL 缺"
        else:
            tag = "都缺"
        section_a.append((id_, label, tag))

    if not section_a:
        print("無問題")
    else:
        for id_, label, tag in section_a[:30]:
            print(f"   - {id_}: {label} [{tag}]")
        if len(section_a) > 30:
            print(f"   ...(共 {len(section_a)} 筆,只顯示前 30)")

    # ---------------- Section B: SQL/CSV has, PLC unused ----------------
    print()
    print("=" * 72)
    print("B. SQL/CSV 有翻譯但 PLC 沒在用的 ID (孤兒翻譯):")
    print("=" * 72)
    union_translation_ids = sql_ids | csv_ids
    section_b = []
    for id_ in sorted(union_translation_ids):
        if id_ in plc_registered_ids:
            continue
        # try get English value
        en = ""
        if id_ in sql_by_id and "English" in sql_by_id[id_]:
            en = sql_by_id[id_]["English"]
        elif id_ in csv_by_id and "English" in csv_by_id[id_]:
            en = csv_by_id[id_]["English"]
        elif id_ in sql_by_id:
            en = next(iter(sql_by_id[id_].values()), "")
        elif id_ in csv_by_id:
            en = next(iter(csv_by_id[id_].values()), "")
        # truncate
        en = en[:80]
        in_sql = id_ in sql_ids
        in_csv = id_ in csv_ids
        if in_sql and in_csv:
            tag = "SQL+CSV 都有"
        elif in_sql:
            tag = "SQL 有"
        else:
            tag = "CSV 有"
        section_b.append((id_, en, tag))

    if not section_b:
        print("無問題")
    else:
        for id_, en, tag in section_b[:30]:
            print(f"   - {id_}: {en!r} [{tag}]")
        if len(section_b) > 30:
            print(f"   ...(共 {len(section_b)} 筆,只顯示前 30)")

    # ---------------- Section C: SQL vs CSV diff ----------------
    print()
    print("=" * 72)
    print("C. SQL 跟 CSV 兩邊 ID set 不一致 (應該一致才對):")
    print("=" * 72)
    only_sql = sorted(sql_ids - csv_ids)
    only_csv = sorted(csv_ids - sql_ids)
    if not only_sql and not only_csv:
        print("無問題")
    else:
        print(f"   - 只在 SQL ({len(only_sql)} 筆): {only_sql[:60]}{' ...' if len(only_sql) > 60 else ''}")
        print(f"   - 只在 CSV ({len(only_csv)} 筆): {only_csv[:60]}{' ...' if len(only_csv) > 60 else ''}")

    # ---------------- Section D: Per-ID language coverage ----------------
    print()
    print("=" * 72)
    print("D. 同一 ID 在 SQL/CSV 缺某語言 (應 English+Chinese+Taiwanese 齊全):")
    print("=" * 72)
    REQUIRED_LANGS = {"English", "Chinese", "Taiwanese"}
    section_d = []
    for source_name, by_id in [("SQL", sql_by_id), ("CSV", csv_by_id)]:
        for id_ in sorted(by_id.keys()):
            langs = set(by_id[id_].keys())
            missing = REQUIRED_LANGS - langs
            if missing:
                section_d.append((id_, source_name, sorted(missing)))
    if not section_d:
        print("無問題")
    else:
        for id_, src, miss in section_d[:30]:
            print(f"   - {id_} in {src}: 缺 {miss}")
        if len(section_d) > 30:
            print(f"   ...(共 {len(section_d)} 筆,只顯示前 30)")

    # ---------------- Section E: Orphan GVL constants ----------------
    print()
    print("=" * 72)
    print("E. PLC 在 GVL 宣告但沒在任何地方 RegisterAlarm 用到的常數 (孤兒常數):")
    print("=" * 72)
    section_e = []
    for cname, base in const_to_id.items():
        if cname in const_used:
            continue
        # also accept base+offset usage already registered as cname being in const_used
        section_e.append((cname, base, const_to_line.get(cname, 0)))
    section_e.sort(key=lambda x: x[1])
    if not section_e:
        print("無問題")
    else:
        for cname, base, ln in section_e[:30]:
            print(f"   - {cname} (id={base}): 宣告於 GVL_ErrorCode L{ln}")
        if len(section_e) > 30:
            print(f"   ...(共 {len(section_e)} 筆,只顯示前 30)")

    # ---------------- Dynamic calls notice ----------------
    print()
    print("=" * 72)
    print("[備註] 動態 ID 呼叫 site (第一參數為 loop var / array index / 表達式),需人工確認:")
    print("=" * 72)
    if not dynamic_calls:
        print("無")
    else:
        # de-dup by (file, line)
        seen = set()
        uniq = []
        for fp, ln, snip in dynamic_calls:
            key = (fp, ln)
            if key in seen:
                continue
            seen.add(key)
            uniq.append((fp, ln, snip))
        for fp, ln, snip in uniq[:30]:
            rel = os.path.relpath(fp, ROOT)
            print(f"   - {rel}:{ln}  {snip}")
        if len(uniq) > 30:
            print(f"   ...(共 {len(uniq)} 筆,只顯示前 30)")


if __name__ == "__main__":
    main()
