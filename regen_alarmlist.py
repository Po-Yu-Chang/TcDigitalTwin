#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TCI HMI alarm 翻譯表重生腳本
============================
以 PLC GVL_ErrorCode.TcGVL 為權威,重生 alarmlist.new.sql + GC_MSG_Long.new.csv。

流程:
  1. 解析 GVL_ErrorCode.TcGVL,抽出每個 GC_ALARM_xxx (id, const_name, level, traditional_zh)
  2. base+offset 展開:
       MODBUSRTU=850 base   -> 851-854 (光電板1~4 Modbus 通訊)
       SAFE_DOOR_BASE=900 base -> 901-916 (安全門1~16 開啟)
     base ID 本身 (850/900) 不輸出
  3. 從舊 alarmlist.sql 建 (English↔Chinese↔Taiwanese) triple,做兩個查找表:
       taiwanese_to_chinese, english_to_chinese
  4. 對每個 PLC alarm 產三語:
       English   = traditional_zh 反查舊 Taiwanese→English,or 從 const_name 自動生
       Taiwanese = traditional_zh (PLC 註解)
       Chinese   = taiwanese_to_chinese[traditional_zh] 沿用,or 機械繁→簡轉
  5. 產 alarmlist.new.sql (50 row 一 batch) + GC_MSG_Long.new.csv (BOM, quote-all)

不動既有檔案 (GVL / 舊 SQL / 舊 CSV)。
"""

import csv
import datetime
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(r"C:\Users\qoose\Desktop\文件資料\客戶分類\T-泰興\TCI")
GVL_FILE = ROOT / "CinPhown_PackML" / "DAS_CoreSys" / "Library" / "10_Machine" / "13_ErrorHandling" / "GVL_ErrorCode.TcGVL"
OLD_SQL = ROOT / "alarmlist.sql"
OLD_CSV = ROOT / "GC_MSG_Long.csv"
NEW_SQL = ROOT / "alarmlist.new.sql"
NEW_CSV = ROOT / "GC_MSG_Long.new.csv"

PYTHON_EXE = r"C:/Users/qoose/AppData/Local/Programs/Python/Python312/python"


# =============================================================================
# Step 1: Parse GVL_ErrorCode.TcGVL
# =============================================================================
def parse_gvl(gvl_path: Path):
    """
    Returns list of dicts: {id, const_name, level, traditional_zh}
    Skips base IDs (MODBUSRTU=850, SAFE_DOOR_BASE=900) — those will be expanded.
    """
    text = gvl_path.read_text(encoding="utf-8")
    # Match: GC_ALARM_xxx : DINT := 100;   // [Tag] 描述...
    # Allow leading whitespace/tabs, allow GC_AlARM_ typo, allow trailing whitespace.
    pat = re.compile(
        r"^\s*(GC_(?:ALARM|AlARM)_\w+)\s*:\s*DINT\s*:=\s*(\d+)\s*;\s*//\s*(.*?)\s*$",
        re.MULTILINE,
    )
    tag_re = re.compile(r"^\[(\w+)\]\s*(.*)$")

    out = []
    for m in pat.finditer(text):
        const_name = m.group(1)
        eid = int(m.group(2))
        comment = m.group(3).strip()
        level = ""
        traditional_zh = comment
        tm = tag_re.match(comment)
        if tm:
            level = tm.group(1)  # Hold / Abort
            traditional_zh = tm.group(2).strip()
        out.append({
            "id": eid,
            "const_name": const_name,
            "level": level,
            "traditional_zh": traditional_zh,
        })
    return out


# =============================================================================
# Step 2: Expand base+offset (MODBUSRTU 850, SAFE_DOOR_BASE 900)
# =============================================================================
def expand_base_offsets(alarms):
    """
    Drop base IDs, generate offset entries.
      850 -> 851 光電板1 Modbus 通訊 ... 854 光電板4 Modbus 通訊
      900 -> 901 安全門1 開啟 ... 916 安全門16 開啟
    """
    base_ids = {850, 900}
    out = [a for a in alarms if a["id"] not in base_ids]

    for i in range(1, 5):
        out.append({
            "id": 850 + i,
            "const_name": f"GC_ALARM_MODBUSRTU+{i}",
            "level": "Hold",
            "traditional_zh": f"光電板{i} Modbus 通訊",
            "autogen_en": f"Photoelectric sensor {i} Modbus communication error",
        })
    for i in range(1, 17):
        out.append({
            "id": 900 + i,
            "const_name": f"GC_ALARM_SAFE_DOOR_BASE+{i}",
            "level": "Hold",
            "traditional_zh": f"安全門{i} 開啟",
            "autogen_en": f"Safe door {i} opened",
        })

    out.sort(key=lambda a: a["id"])
    return out


# =============================================================================
# Step 3: Parse old SQL -> build (english_to_chinese, taiwanese_to_chinese,
#                                  taiwanese_to_english) lookups
# =============================================================================
def parse_old_sql(sql_path: Path):
    text = sql_path.read_text(encoding="utf-8-sig", errors="replace")
    # rows: (idx, 'eid', 'lang', 'value')
    row_re = re.compile(
        r"\(\s*(\d+)\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'((?:[^'\\]|\\.)*)'\s*\)"
    )
    by_id = defaultdict(dict)
    for m in row_re.finditer(text):
        eid = m.group(2).strip()
        lang = m.group(3).strip()
        value = m.group(4)
        by_id[eid][lang] = value

    en_to_zh = {}
    tw_to_zh = {}
    tw_to_en = {}
    en_to_tw = {}
    for eid, langs in by_id.items():
        en = langs.get("English", "").strip()
        zh = langs.get("Chinese", "").strip()
        tw = langs.get("Taiwanese", "").strip()
        if en and zh:
            en_to_zh.setdefault(en, zh)
        if tw and zh:
            tw_to_zh.setdefault(tw, zh)
        if tw and en:
            tw_to_en.setdefault(tw, en)
        if en and tw:
            en_to_tw.setdefault(en, tw)
    return {
        "en_to_zh": en_to_zh,
        "tw_to_zh": tw_to_zh,
        "tw_to_en": tw_to_en,
        "en_to_tw": en_to_tw,
    }


# =============================================================================
# Step 4: Translation
# =============================================================================

# const_name 詞典 (用於英文 fallback 自動生)
CONST_DICT = {
    # area prefixes
    "STO": "Storage", "ALC": "Allocate", "SHP": "Shipping",
    # device categories
    "CYL": "Cylinder", "AXIS": "Axis",
    # mechanisms
    "RB": "RoundBelt", "ROUNDBELT": "RoundBelt",
    "CV": "Conveyor", "CONVEYOR": "Conveyor",
    "WH": "Warehouse", "WAREHOUSE": "Warehouse",
    "TT": "Turntable", "TURNTABLE": "Turntable",
    "OUTROBOT": "OutRobot",
    "UPCV": "UpperConveyor", "LOWCV": "LowerConveyor",
    "ROBOT": "Robot",
    # actions
    "RELOAD": "reload",
    "FEEDIN": "feed-in", "FEEDOUT": "feed-out",
    "PULLOUT": "pull-out",
    "ABS": "absolute", "MOVE": "move",
    "REGION": "region", "BUFFER": "buffer", "SUPPLY": "supply",
    "EMPTY": "empty", "RARE": "rare",
    "BATCH": "batch", "MODE": "mode", "REVERSE": "reverse",
    "DIFF": "different-diameter", "SAME": "same-diameter",
    "CAMERA": "camera", "CAM": "camera",
    "CAM1": "camera1", "CAM2": "camera2", "CAM3": "camera3", "CAM4": "camera4",
    "BLOCK": "block", "BLOCK1": "block1", "BLOCK2": "block2", "BLOCK3": "block3",
    "PRESS": "press", "PRESS1": "press1", "PRESS2": "press2",
    "PRESS3": "press3", "PRESS4": "press4",
    "FEEDIN1": "feed-in1", "FEEDIN2": "feed-in2",
    "DIAMETER": "diameter",
    "DIA": "diameter",
    "HORIZ": "horizontal", "VERT": "vertical", "CLAMP": "clamp",
    "STACK": "stack", "PUSH": "push",
    "SHIP": "ship",
    "LCLAMP1": "left-clamp1", "LCLAMP2": "left-clamp2",
    "RCLAMP1": "right-clamp1", "RCLAMP2": "right-clamp2",
    "LVERT1": "left-lift1", "LVERT2": "left-lift2",
    "RVERT1": "right-lift1", "RVERT2": "right-lift2",
    "AVERT": "A-side-lift", "BVERT": "B-side-lift",
    "ACLAMP": "A-side-clamp", "BCLAMP": "B-side-clamp",
    "APRESS1": "A-side-press1", "APRESS2": "A-side-press2",
    "BPRESS1": "B-side-press1", "BPRESS2": "B-side-press2",
    "ABLOCK": "A-side-block", "BBLOCK": "B-side-block",
    "WORK": "work-zone", "PLUG": "plug", "CHARGE": "charge",
    "TRANS": "transmit", "TRANSMIT": "transmit",
    "TRANS_PLUG": "transmit-plug",
    "TRANS_CV": "transmit-conveyor",
    "TRANS_CAMHORIZ": "transmit-camera-horizontal",
    "CAMHORIZ": "camera-horizontal",
    "WORK_PRESS": "work-zone-press",
    "WORK_PLUG": "work-zone-plug",
    "CHARGE_PLUG": "charge-zone-plug",
    "TT_TO_RB": "turntable-to-roundbelt",
    "RB_TO_WH": "roundbelt-to-warehouse",
    "REVERSE_TO_WAREHOUSE": "reverse-to-warehouse",
    "ROBOT_LEFT": "robot-left", "ROBOT_RIGHT": "robot-right",
    "ROBOT_BUFFER": "robot-buffer",
    "ROBOT_TRANSFER": "robot-transfer",
    "ROBOT_TRANSMIT_END": "robot-transmit-end",
    "ROBOT_PHOTOSTANDBYPOS": "robot-photo-standby-pos",
    "ROBOT_ABS_MOVE": "robot-absolute-move",
    "ROBOTSELFHOMING": "robot-self-homing",
    "ROBOTINNERPICK": "robot-inner-pick",
    "ROBOTINNERPICK_BATCH": "robot-inner-pick-batch",
    "ROUNDBELTTOWAREHOUSE_BATCH": "roundbelt-to-warehouse-batch",
    "REGION_ALLOC_CV": "region-move-allocate-conveyor",
    "REGION_BUFFER": "region-move-buffer-area",
    "REGION_FEEDIN_CV": "region-move-feed-in-conveyor",
    "REGION_PULLOUT_CV": "region-move-pull-out-conveyor",
    "REGION_ROUNDBELT": "region-move-round-belt",
    "UPPER_FEEDIN_CV": "upper-feed-in-conveyor",
    "WAREHOUSE_ROBOT": "warehouse-robot-move",
    "CABINET_FEEDIN": "cabinet-feed-in",
    "CABINET_RELOAD": "cabinet-reload",
    "FEEDIN_RB_TO_WH": "feed-in-roundbelt-to-warehouse",
    "DIAMETER_MOVEMENT": "diameter-movement",
    "RELOAD_BOX": "reload-box",
    "FEEDIN_BOX": "feed-in-box",
    "ADD_BOXES": "add-boxes",
    "BOX_SUPPLY": "box-supply",
    "FEEDIN_EMPTY": "empty-box-feed-in",
    "RELOAD_ROUNDBELT": "round-belt-reload",
    "BATCH_MODE": "batch-mode",
    "BATCH_REVERSE": "batch-reverse",
    "DIFF_CV_CAMERA": "diff-conveyor-camera",
    "OUTROBOT_REVERSE": "outrobot-reverse-box",
    "OUTROBOT_EMPTY": "outrobot-empty-box",
    "OUTROBOT_RARE": "outrobot-rare-box",
    "PULLOUT_CV": "pull-out-conveyor",
    "SAME_CV_CAMERA": "same-conveyor-camera",
    "SAME_CV_CAMERA_REVERSE": "same-conveyor-camera-reverse",
    "OUTROBOT_TT_TO_RB": "outrobot-turntable-to-roundbelt",
    "NEEDLING_MOVE": "needling-move",
    "REVERSE_TO_WAREHOUSE_BATCH": "reverse-to-warehouse-batch",
    "LEFT_TRANS_CV": "left-transmit-conveyor",
    "RIGHT_TRANS_CV": "right-transmit-conveyor",
    # generic helpers
    "MACHINE": "machine", "HOMING": "homing",
    "STORAGE": "storage", "ALLOCATE": "allocate", "SHIPPING": "shipping",
    "TABLE": "table",
    "MODBUSRTU": "Modbus-RTU", "MODBUS_RTU": "Modbus-RTU",
    "MACHINEMODE": "machine-mode", "SWITCH": "switch",
    "SAFE": "safe", "DOOR": "door", "BASE": "base",
    "RACK": "rack", "RACKMOTOR": "rack-motor",
    "ALLOCATE_X": "allocate-X-axis", "ALLOCATE_Y": "allocate-Y-axis",
    "OUTROBOT_X": "outrobot-X-axis", "OUTROBOT_Y": "outrobot-Y-axis",
}


def autogen_english(const_name: str, level: str, traditional_zh: str) -> str:
    """
    從 const_name 自動產 English 描述。
    e.g. GC_ALARM_STO_RELOAD_BOX -> "Storage reload-box error"
         GC_ALARM_CYL_STO_UPCV_FEEDIN1 -> "Storage upper-conveyor feed-in1 cylinder error"
    """
    name = const_name
    # strip GC_ALARM_ / GC_AlARM_ prefix
    if name.startswith("GC_ALARM_"):
        name = name[len("GC_ALARM_"):]
    elif name.startswith("GC_AlARM_"):
        name = name[len("GC_AlARM_"):]

    # strip trailing +N (offset)
    name = re.sub(r"\+\d+$", "", name)

    parts = name.split("_")

    # CYL_xxx -> 標記為 cylinder,單字尾加 cylinder
    is_cyl = parts[0] == "CYL" if parts else False
    is_axis = parts[0] == "AXIS" if parts else False

    if is_cyl or is_axis:
        parts = parts[1:]

    words = []
    for p in parts:
        if not p:
            continue
        # 試完整 token,再試逐段
        if p.upper() in CONST_DICT:
            words.append(CONST_DICT[p.upper()])
        else:
            # 沒查到 — 保留原樣 (camelize 友善)
            words.append(p.lower())

    body = " ".join(words)
    if is_cyl:
        body = body + " cylinder"
    if is_axis:
        body = body + " axis"

    # 結尾標記
    if level == "Abort":
        suffix = " error (Abort)"
    else:
        suffix = " error"
    return (body + suffix).strip()


def _fuzzy_lookup_old(tw: str, table: dict):
    """
    舊 SQL 的 Taiwanese 字串通常比 PLC 註解多前後綴 (區名+「錯誤」),
    例如:
      PLC: '補盒服務'        舊 SQL: '倉儲補盒服務錯誤'
      PLC: '機台回原點'      舊 SQL: '機台回原點錯誤'
      PLC: 'TableMove 錯誤'  舊 SQL: 'TableMove 錯誤'
    策略:
      1. 直接 lookup
      2. 後綴試「錯誤」/「服務錯誤」/「 Error」
      3. 包含 tw 的 entry 中,字數差最小的那筆
    Returns matched value or None.
    """
    if not tw:
        return None
    if tw in table:
        return table[tw]
    # 試後綴
    for suffix in ("錯誤", "服務錯誤", " 錯誤", " Error", " error"):
        if (tw + suffix) in table:
            return table[tw + suffix]
    # substring fuzzy:找包含 tw 的所有 entries,挑「字數差距最小」(避免抓到不相關長字串)
    candidates = [(k, v) for k, v in table.items() if tw in k]
    if not candidates:
        return None
    # 限制長度差不超過 8 字 (避免「圓帶」誤抓「圓帶 XXXX 服務錯誤」過長)
    candidates = [(k, v) for k, v in candidates if len(k) - len(tw) <= 8]
    if not candidates:
        return None
    candidates.sort(key=lambda kv: len(kv[0]) - len(tw))
    return candidates[0][1]


def build_english(alarm, lookups):
    """優先查舊翻譯;若 alarm 有 autogen_en 預設 (base+offset 用),用它;否則 autogen"""
    tw = alarm["traditional_zh"]
    found = _fuzzy_lookup_old(tw, lookups["tw_to_en"])
    if found is not None:
        return found
    if "autogen_en" in alarm:
        return alarm["autogen_en"]
    return autogen_english(alarm["const_name"], alarm["level"], tw)


# -----------------------------------------------------------------------------
# 繁→簡 fallback dict (專案常見字)
# -----------------------------------------------------------------------------
T2S_FALLBACK = {
    # 基礎
    "倉": "仓", "儲": "储", "補": "补", "機": "机", "針": "针", "輸": "输",
    "緩": "缓", "衝": "冲", "區": "区", "軸": "轴", "處": "处", "間": "间",
    "運": "运", "錯": "错", "誤": "误", "開": "开", "關": "关", "門": "门",
    "電": "电", "設": "设", "備": "备", "續": "续", "變": "变", "發": "发",
    "隊": "队", "頭": "头", "轉": "转", "單": "单", "號": "号", "線": "线",
    "徑": "径", "層": "层", "員": "员", "辦": "办", "參": "参", "義": "义",
    "舉": "举", "態": "态", "樣": "样", "網": "网", "樞": "枢", "調": "调",
    "訊": "讯", "認": "认", "識": "识", "車": "车", "貨": "货", "過": "过",
    "進": "进", "達": "达", "寶": "宝", "價": "价", "構": "构", "應": "应",
    "實": "实", "團": "团", "為": "为", "會": "会", "後": "后", "來": "来",
    "學": "学", "體": "体", "業": "业", "長": "长", "與": "与", "產": "产",
    "報": "报", "視": "视", "壓": "压", "檢": "检", "確": "确", "無": "无",
    "繞": "绕", "給": "给", "貫": "贯", "總": "总", "養": "养", "覽": "览",
    "見": "见", "靈": "灵", "辭": "辞", "職": "职", "聲": "声",
    "東": "东", "雙": "双", "頻": "频", "戰": "战", "衛": "卫", "響": "响",
    "議": "议", "夢": "梦", "藥": "药", "書": "书", "習": "习",
    "強": "强", "農": "农", "權": "权", "馬": "马", "駛": "驶", "龍": "龙",
    "龜": "龟", "圍": "围", "儀": "仪", "優": "优", "稱": "称", "爭": "争",
    "廠": "厂", "塵": "尘",
    # 動作/狀態
    "動": "动", "從": "从", "點": "点", "啟": "启", "緒": "绪", "並": "并",
    "結": "结", "離": "离", "傳": "传", "達": "达", "送": "送", "啟": "启",
    "啓": "启", "復": "复", "歸": "归", "極": "极", "舊": "旧", "棧": "栈",
    "託": "托", "夾": "夹", "選": "选", "擇": "择", "處": "处",
    "連": "连", "斷": "断", "標": "标", "誌": "志", "畢": "毕",
    "稀": "稀", "盤": "盘", "筆": "笔", "畫": "画", "預": "预",
    "緊": "紧", "鬆": "松", "鎖": "锁", "閉": "闭", "終": "终", "稱": "称",
    "順": "顺", "鬥": "斗", "資": "资", "據": "据", "驟": "骤", "驅": "驱",
    "繪": "绘", "圖": "图", "層": "层", "陣": "阵", "證": "证", "億": "亿",
    "範": "范", "圍": "围", "級": "级", "編": "编", "誰": "谁", "誰": "谁",
    "誰": "谁", "雙": "双", "獲": "获", "盡": "尽", "莊": "庄", "華": "华",
    "個": "个", "麼": "么", "對": "对", "離": "离", "讀": "读", "請": "请",
    "聯": "联", "屬": "属", "蓋": "盖", "節": "节", "節": "节",
    "歷": "历", "績": "绩", "贈": "赠", "藏": "藏", "養": "养", "藝": "艺",
    "塊": "块", "縮": "缩", "擴": "扩", "壞": "坏", "髮": "发", "瀟": "潇",
    "畫": "画", "簡": "简", "繁": "繁", "顆": "颗", "顛": "颠", "顧": "顾",
    # 額外 (專案內出現)
    "盒": "盒",  # same
    "供": "供",  # same
    "拍": "拍",
    "照": "照",
    "緩衝": "缓冲",
    "圓": "圆", "鏈": "链", "鋸": "锯",
    "幣": "币", "範": "范", "績": "绩", "頓": "顿",
    "騎": "骑", "驕": "骄", "驗": "验", "驚": "惊", "鬱": "郁",
    "靜": "静", "雜": "杂", "雙": "双", "離": "离", "難": "难",
    "霧": "雾", "靈": "灵", "靠": "靠", "韌": "韧", "頁": "页",
    "頂": "顶", "順": "顺", "頭": "头", "頸": "颈", "頻": "频",
    "顆": "颗", "願": "愿", "顛": "颠", "類": "类", "風": "风",
    "飛": "飞", "餐": "餐", "飯": "饭", "飼": "饲", "飾": "饰",
    "餘": "余", "館": "馆", "首": "首",
    "馴": "驯", "驕": "骄", "驅": "驱", "驟": "骤",
    "黨": "党", "鴻": "鸿", "齊": "齐", "齒": "齿",
    "纖": "纤", "繼": "继", "績": "绩", "繭": "茧", "繳": "缴",
    "脹": "胀", "腦": "脑", "腳": "脚", "膚": "肤", "舊": "旧", "舉": "举",
    "船": "船", "艦": "舰", "膽": "胆", "腎": "肾",
    "搶": "抢", "擊": "击", "擾": "扰", "攔": "拦", "攝": "摄", "攢": "攒",
    "敗": "败", "齡": "龄",
    # 特定字 (從專案註解)
    "繞圈": "绕圈",
    "佔": "占",
}


def try_install_opencc():
    """嘗試 pip install opencc-python-reimplemented;成功 return module else None"""
    try:
        import opencc  # noqa: F401
        from opencc import OpenCC
        return OpenCC("t2s")
    except ImportError:
        pass
    print("[opencc] not installed, attempting pip install...", file=sys.stderr)
    try:
        result = subprocess.run(
            [PYTHON_EXE, "-m", "pip", "install", "--quiet", "opencc-python-reimplemented"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            print(f"[opencc] pip install failed: {result.stderr[:500]}", file=sys.stderr)
            return None
    except Exception as e:
        print(f"[opencc] pip install exception: {e}", file=sys.stderr)
        return None
    try:
        from opencc import OpenCC
        return OpenCC("t2s")
    except ImportError:
        print("[opencc] still cannot import after install", file=sys.stderr)
        return None


def t2s_fallback_convert(text: str):
    """逐字查 fallback dict;回傳 (converted, untranslated_chars_set)"""
    out_chars = []
    untrans = set()
    for ch in text:
        if ch in T2S_FALLBACK:
            out_chars.append(T2S_FALLBACK[ch])
        else:
            # 是繁體 CJK 但沒在表 — 保留原字並記錄
            if "一" <= ch <= "鿿":
                # 簡單判斷:若該字不在 fallback dict 也不在 simplified 常用集,可能是繁體未轉
                # 保守起見,不做更複雜判斷,僅 chars 沒 mapping 的視為 untranslated
                pass
            out_chars.append(ch)
    # 計算未轉繁體字 (heuristic):字本身在 dict values 不存在,且是繁體常見字符範圍
    # 這個 set 由呼叫端關心,這邊只回傳 converted text
    return "".join(out_chars)


def build_chinese(alarm, lookups, opencc_converter, fallback_used_log):
    """
    1. 先試 tw_to_zh 直接 / fuzzy lookup 沿用舊翻譯
    2. 沒找到:用 OpenCC (若有) or fallback dict 轉
    Returns (chinese_value, source_tag)
      source_tag in {'reused_exact', 'reused_fuzzy', 'opencc', 'fallback'}
    """
    tw = alarm["traditional_zh"]
    if not tw:
        return "", "empty"
    if tw in lookups["tw_to_zh"]:
        return lookups["tw_to_zh"][tw], "reused_exact"
    # fuzzy
    fuzzy = _fuzzy_lookup_old(tw, lookups["tw_to_zh"])
    if fuzzy is not None:
        return fuzzy, "reused_fuzzy"
    # 機械轉
    if opencc_converter is not None:
        return opencc_converter.convert(tw), "opencc"
    converted = t2s_fallback_convert(tw)
    fallback_used_log.append((alarm["id"], alarm["const_name"], tw, converted))
    return converted, "fallback"


# =============================================================================
# Step 5: Generate SQL + CSV
# =============================================================================
def sql_escape(s: str) -> str:
    return s.replace("'", "''")


def generate_sql(rows, out_path: Path, total_alarms: int):
    """
    rows = list of (idx, eid, lang, value)
    輸出 INSERT INTO alarmlist VALUES (...),(...),(...) 50 row 一個 batch,以 $$ 結尾,delimiter $$。
    """
    today = datetime.date.today().isoformat()
    header = f"""-- ================================================
-- dds.alarmlist  —  PLC RegisterAlarm ID 對照表
-- Generated: {today}
-- Source: regenerated from GVL_ErrorCode.TcGVL (PLC 為權威)
-- Rows: {len(rows)}  |  Unique AlarmID: {total_alarms}
-- 三語對照:English / Chinese (簡) / Taiwanese (繁)
-- ================================================

delimiter $$

DROP TABLE IF EXISTS `alarmlist`$$

CREATE TABLE `alarmlist` (
  `index`   int(11)      NOT NULL,
  `ErrorID` varchar(200) DEFAULT NULL,
  `Type`    varchar(200) DEFAULT NULL,
  `Value`   varchar(200) DEFAULT NULL,
  PRIMARY KEY (`index`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8$$

"""
    BATCH = 50
    out_lines = [header]
    for batch_start in range(0, len(rows), BATCH):
        chunk = rows[batch_start:batch_start + BATCH]
        out_lines.append("INSERT INTO `alarmlist` (`index`, `ErrorID`, `Type`, `Value`) VALUES\n")
        line_strs = []
        for idx, eid, lang, value in chunk:
            line_strs.append(
                f"  ({idx}, '{eid}', '{lang}', '{sql_escape(value)}')"
            )
        out_lines.append(",\n".join(line_strs))
        out_lines.append("$$\n\n")
    out_lines.append("delimiter ;\n")
    out_path.write_text("".join(out_lines), encoding="utf-8")


def generate_csv(rows, out_path: Path):
    """
    rows = list of (idx, eid, lang, value)
    UTF-8 with BOM, all fields quoted, header: index,ErrorID,Type,Value
    """
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(["index", "ErrorID", "Type", "Value"])
        for idx, eid, lang, value in rows:
            writer.writerow([str(idx), str(eid), lang, value])


# =============================================================================
# Main
# =============================================================================
def main():
    print(f"GVL : {GVL_FILE}")
    print(f"舊 SQL : {OLD_SQL}")
    print(f"舊 CSV : {OLD_CSV}")
    print(f"輸出 SQL : {NEW_SQL}")
    print(f"輸出 CSV : {NEW_CSV}")
    print()

    # Step 1
    raw = parse_gvl(GVL_FILE)
    print(f"[1] 從 GVL 解析到 {len(raw)} 個 GC_ALARM_* 常數")

    # Step 2
    alarms = expand_base_offsets(raw)
    print(f"[2] base+offset 展開後,共 {len(alarms)} 個 alarm")

    # Step 3
    lookups = parse_old_sql(OLD_SQL)
    print(f"[3] 舊 SQL 翻譯查找表:tw_to_zh={len(lookups['tw_to_zh'])}, "
          f"tw_to_en={len(lookups['tw_to_en'])}, en_to_zh={len(lookups['en_to_zh'])}")

    # Step 4 prep
    opencc_converter = try_install_opencc()
    if opencc_converter is None:
        print("[4] OpenCC 不可用,使用內建 fallback dict")
    else:
        print("[4] 使用 OpenCC 機械繁→簡轉換")
    print()

    # Step 4 translate
    rows = []
    idx = 1
    en_reused_exact = en_reused_fuzzy = en_autogen = 0
    zh_reused_exact = zh_reused_fuzzy = zh_opencc = zh_fallback = 0
    fallback_log = []
    autogen_en_log = []  # (id, const, tw, en) 自動生 EN 列表
    for alarm in alarms:
        tw = alarm["traditional_zh"]
        # English
        if tw in lookups["tw_to_en"]:
            en = lookups["tw_to_en"][tw]
            en_reused_exact += 1
        else:
            fuzzy_en = _fuzzy_lookup_old(tw, lookups["tw_to_en"])
            if fuzzy_en is not None:
                en = fuzzy_en
                en_reused_fuzzy += 1
            else:
                if "autogen_en" in alarm:
                    en = alarm["autogen_en"]
                else:
                    en = autogen_english(alarm["const_name"], alarm["level"], tw)
                en_autogen += 1
                autogen_en_log.append((alarm["id"], alarm["const_name"], tw, en))
        # Chinese
        zh, zh_src = build_chinese(alarm, lookups, opencc_converter, fallback_log)
        if zh_src == "reused_exact":
            zh_reused_exact += 1
        elif zh_src == "reused_fuzzy":
            zh_reused_fuzzy += 1
        elif zh_src == "opencc":
            zh_opencc += 1
        elif zh_src == "fallback":
            zh_fallback += 1
        rows.append((idx,     alarm["id"], "English",   en)); idx += 1
        rows.append((idx,     alarm["id"], "Chinese",   zh)); idx += 1
        rows.append((idx,     alarm["id"], "Taiwanese", tw)); idx += 1

    # Step 5 generate
    generate_sql(rows, NEW_SQL, len(alarms))
    generate_csv(rows, NEW_CSV)

    # Report — write to file (avoid Windows console encoding issues)
    report_lines = []
    report_lines.append("=" * 64)
    report_lines.append("產生報告")
    report_lines.append("=" * 64)
    report_lines.append(f"總 alarm 數:{len(alarms)}")
    report_lines.append(f"總 row 數:{len(rows)} (每 alarm 三語)")
    report_lines.append("")
    report_lines.append(f"English 沿用舊翻譯 (exact):{en_reused_exact}")
    report_lines.append(f"English 沿用舊翻譯 (fuzzy):{en_reused_fuzzy}")
    report_lines.append(f"English 自動生:{en_autogen}")
    report_lines.append("")
    report_lines.append(f"Chinese 沿用舊翻譯 (exact):{zh_reused_exact}")
    report_lines.append(f"Chinese 沿用舊翻譯 (fuzzy):{zh_reused_fuzzy}")
    if opencc_converter:
        report_lines.append(f"Chinese OpenCC 機械轉換:{zh_opencc}")
    else:
        report_lines.append(f"Chinese fallback dict 轉換:{zh_fallback}")
    report_lines.append("")
    if autogen_en_log:
        report_lines.append(f"自動生 English 列表 (全部 {len(autogen_en_log)} 筆,前 30):")
        for eid, cn, tw, en in autogen_en_log[:30]:
            report_lines.append(f"  id={eid:4d}  {cn}")
            report_lines.append(f"     tw: {tw}")
            report_lines.append(f"     en: {en}")
    report_lines.append("")
    if fallback_log:
        report_lines.append(f"用 fallback dict 轉的 alarm 列表 (前 10):")
        for eid, cn, tw, conv in fallback_log[:10]:
            report_lines.append(f"  {eid:4d} {cn}: {tw} -> {conv}")
        if len(fallback_log) > 10:
            report_lines.append(f"  ...(共 {len(fallback_log)} 筆)")
    report_lines.append("")
    report_lines.append("輸出檔:")
    report_lines.append(f"  {NEW_SQL}")
    report_lines.append(f"  {NEW_CSV}")

    report_path = ROOT / "_regen_report.txt"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"報告寫到 {report_path}")
    print(f"輸出檔:")
    print(f"  {NEW_SQL}")
    print(f"  {NEW_CSV}")


if __name__ == "__main__":
    main()
