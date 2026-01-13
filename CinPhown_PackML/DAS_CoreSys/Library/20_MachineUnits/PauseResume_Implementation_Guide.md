# FB 暫停/繼續功能實作指南

## 目的

本文件定義 TwinCAT PLC Function Block 實作暫停/繼續功能的標準規則，確保所有 FB 具有一致的行為模式。

---

## 設計原則

### 核心概念

1. **狀態保存**：暫停時必須保存所有「進行中」的狀態
2. **計時凍結**：所有 Timeout 計時器必須暫停計時，避免暫停期間觸發 Timeout
3. **安全停止**：暫停時停止所有運動（馬達、氣缸動作指令）
4. **無縫恢復**：繼續時從暫停點精確恢復，如同從未暫停過

### 適用範圍

- 繼承自 `FB_ObjectBase_V2` 的狀態機 FB
- 具有 `STATE_EXECUTING` 執行狀態的 FB
- 使用 TON 計時器進行 Timeout 監控的 FB

---

## 標準介面定義

### 輸入變數

| 變數名稱 | 類型 | 說明 |
|---------|------|------|
| Pause | BOOL | TRUE = 請求暫停，FALSE = 請求繼續 |

### 輸出變數

| 變數名稱 | 類型 | 說明 |
|---------|------|------|
| Paused | BOOL | TRUE = 目前處於暫停狀態 |

---

## 內部變數規範

### 暫停控制變數

| 變數名稱 | 類型 | 用途 |
|---------|------|------|
| _bPaused | BOOL | 內部暫停狀態旗標 |
| _bPauseEdge | BOOL | Pause 輸入的邊緣檢測 |

### 計時器保存變數命名規則

針對每個 TON 計時器，需建立兩個對應變數：

| 命名模式 | 類型 | 用途 |
|---------|------|------|
| tSaved_{TimerName} | TIME | 保存計時器經過時間 (ET) |
| bWasActive_{TimerName} | BOOL | 保存計時器是否正在計時 (IN) |

### 動作狀態保存變數

根據 FB 實際控制的設備，建立對應的保存變數：

| 設備類型 | 命名建議 | 類型 |
|---------|---------|------|
| 馬達方向 | iSaved_MoveDirection | INT |
| 數位輸出 | bSaved_{OutputName} | BOOL |
| 類比輸出 | rSaved_{OutputName} | REAL |

---

## 實作步驟

### 步驟 1：識別需要保存的狀態

盤點 FB 中所有需要在暫停時保存的項目：

1. **計時器清單**
   - 列出所有 TON 計時器
   - 記錄每個計時器的原始 PT 設定值

2. **輸出清單**
   - 列出所有馬達控制
   - 列出所有氣缸控制指令
   - 列出所有數位/類比輸出

3. **狀態變數清單**
   - 識別影響執行流程的關鍵變數

### 步驟 2：建立保存變數

在 VAR 區段新增：

1. 暫停控制變數（2 個）
2. 每個計時器的保存變數（每個計時器 2 個）
3. 每個輸出的保存變數

### 步驟 3：建立暫停控制 Action

建立 `A01_PauseControl` Action，包含以下邏輯區塊：

#### 區塊 A：狀態輸出與前置檢查

- 將 `_bPaused` 輸出到 `Paused`
- 檢查是否在 `STATE_EXECUTING`，若否則清除暫停狀態並返回

#### 區塊 B：進入暫停（上升邊緣）

觸發條件：`Pause AND NOT _bPauseEdge`

動作順序：
1. 設置 `_bPauseEdge := TRUE`
2. 設置 `_bPaused := TRUE`
3. 保存所有計時器的 IN 和 ET 值
4. 保存所有輸出狀態
5. 停止所有計時器（IN := FALSE）
6. 停止所有馬達
7. 關閉相關輸出

#### 區塊 C：離開暫停（下降邊緣）

觸發條件：`NOT Pause AND _bPauseEdge`

動作順序：
1. 設置 `_bPauseEdge := FALSE`
2. 設置 `_bPaused := FALSE`
3. 恢復所有輸出狀態
4. 恢復計時器（針對每個之前活動的計時器）：
   - 計算剩餘時間：`PT := 原始PT - 保存的ET`
   - 重新啟動：`IN := TRUE`

#### 區塊 D：邊緣重置

確保 Pause 為 FALSE 時重置邊緣檢測

### 步驟 4：整合到主程式

修改 FB 主程式（Implementation）：

1. 呼叫 `A00_BasicUnits()`
2. 呼叫 `A01_PauseControl()`
3. 用 `IF NOT _bPaused THEN` 包覆狀態機呼叫

### 步驟 5：修改執行狀態方法

在 `M_STATE_EXECUTING` 方法最開頭加入暫停檢查：

- 若 `_bPaused` 為 TRUE，立即 RETURN

### 步驟 6：修改休眠狀態方法

在 `M_STATE_DORMANT` 方法中重置所有暫停相關變數：

- `_bPaused := FALSE`
- `_bPauseEdge := FALSE`

---

## 計時器恢復計算規則

### 標準公式

```
剩餘時間 = 原始設定時間 - 暫停時已經過時間
新PT = Original_PT - Saved_ET
```

### 特殊情況處理

| 情況 | 處理方式 |
|------|---------|
| 計時器暫停時未啟動 | 不需恢復，維持原狀 |
| 計時器已完成（Q=TRUE） | 不需恢復 |
| 動態 PT 值 | 需額外保存暫停時的 PT 值 |

### 動態 PT 計時器處理

若計時器的 PT 值在執行過程中會改變，需額外建立：

| 變數 | 用途 |
|------|------|
| tSaved_{TimerName}_PT | 保存暫停時的 PT 設定值 |

恢復公式：`新PT = 保存的PT - 保存的ET`

---

## 馬達類型處理規範

### 直流馬達 (DC Motor)

直流馬達控制較為單純，無位置回授。

#### 需保存變數

| 變數名稱 | 類型 | 說明 |
|---------|------|------|
| `iSaved_MotorDirection` | INT | 馬達運轉方向 (1=正轉, -1=反轉, 0=停止) |
| `bSaved_MotorRunning` | BOOL | 馬達是否運轉中 |
| `rSaved_MotorSpeed` | REAL | 類比速度值（如有調速） |

#### 暫停處理

```
// 進入暫停
iSaved_MotorDirection := iMotorDirection;
bSaved_MotorRunning := bMotorRunning;
M_MotorStop();
```

#### 恢復處理

```
// 離開暫停
IF bSaved_MotorRunning THEN
    CASE iSaved_MotorDirection OF
        1:  M_MotorForward();
        -1: M_MotorBackward();
    END_CASE
END_IF
```

---

### 伺服馬達 (Servo Motor)

#### PLCopen 標準版本（通用參考）

若使用 PLCopen Motion Control 標準函數塊：

**需保存變數：**

| 變數名稱 | 類型 | 說明 |
|---------|------|------|
| `lrSaved_TargetPos` | LREAL | 目標位置 |
| `lrSaved_PausePos` | LREAL | 暫停當下的實際位置 |
| `rSaved_Velocity` | REAL | 運動速度設定 |
| `bSaved_MotionActive` | BOOL | 是否有未完成的移動指令 |

**暫停處理：**

```
// 進入暫停 - PLCopen 標準
IF Pause AND NOT _bPauseEdge THEN
    _bPauseEdge := TRUE;
    _bPaused := TRUE;

    // 1. 保存目標位置（最終要去的位置）
    lrSaved_TargetPos := fbAxis.TargetPosition;

    // 2. 記錄暫停當下位置
    lrSaved_PausePos := fbAxis.ActualPosition;

    // 3. 保存運動狀態
    bSaved_MotionActive := fbAxis.Busy;
    rSaved_Velocity := rCurrentVelocity;

    // 4. 執行停止（建議用 MC_Halt 或 MC_Stop）
    fbMC_Halt(Execute := TRUE);  // 減速停止，保持激磁
END_IF
```

**恢復處理：**

```
// 離開暫停 - PLCopen 標準
IF NOT Pause AND _bPauseEdge THEN
    _bPauseEdge := FALSE;
    _bPaused := FALSE;

    // 從當前實際位置移動到原本的目標位置
    IF bSaved_MotionActive THEN
        fbMC_MoveAbsolute(
            Position := lrSaved_TargetPos,
            Velocity := rSaved_Velocity,
            Execute := TRUE
        );
    END_IF
END_IF
```

---

#### 本專案實作版本

本專案使用 `arAxisCtrl_gb[AxisNo]` 和 `arAxisStatus_gb[AxisNo]` 進行軸控制。

#### 軸定義參考

```
AxisNo :
    TurnTable  := 1    // 轉盤軸
    RoundBelt  := 2    // 圓形輸送帶軸
    Allocate_X := 3    // 分配區 X 軸
    Allocate_Y := 4    // 分配區 Y 軸
    OutRobot_X := 5    // 外部機器人 X 軸
    OutRobot_Y := 6    // 外部機器人 Y 軸
    RackMotor  := 7    // 貨架電機
```

#### 需保存變數

| 變數名稱 | 類型 | 說明 |
|---------|------|------|
| `lrSaved_TargetPosition` | LREAL | 目標位置 (mm 或 deg) |
| `lrSaved_PausePosition` | LREAL | 暫停當下的實際位置 |
| `rSaved_Velocity` | REAL | 運動速度設定 |
| `rSaved_Acceleration` | REAL | 加速度設定 |
| `rSaved_Deceleration` | REAL | 減速度設定 |
| `bSaved_AxisMoving` | BOOL | 軸是否正在移動中 |
| `eSaved_AxisNo` | AxisNo | 正在控制的軸編號 |

#### 暫停處理

```
// 進入暫停 - 伺服馬達
IF arAxisStatus_gb[AxisNo].Admin.Moving THEN
    // 1. 保存目標位置（最終要去的位置）
    lrSaved_TargetPosition := arAxisCtrl_gb[AxisNo].PosMode.Position;

    // 2. 保存暫停當下實際位置
    lrSaved_PausePosition := arAxisStatus_gb[AxisNo].Admin.ActPos;

    // 3. 保存運動參數
    rSaved_Velocity := arAxisCtrl_gb[AxisNo].PosMode.Velocity;
    rSaved_Acceleration := arAxisCtrl_gb[AxisNo].PosMode.Acc;
    rSaved_Deceleration := arAxisCtrl_gb[AxisNo].PosMode.Dec;
    bSaved_AxisMoving := TRUE;
    eSaved_AxisNo := AxisNo;

    // 4. 執行停止（使用系統停止機制）
    arAxisCtrl_gb[AxisNo].Admin.Stop := TRUE;
END_IF
```

#### 恢復處理

```
// 離開暫停 - 伺服馬達
IF bSaved_AxisMoving THEN
    // 清除停止指令
    arAxisCtrl_gb[eSaved_AxisNo].Admin.Stop := FALSE;

    // 從當前位置繼續移動到原目標位置
    arAxisCtrl_gb[eSaved_AxisNo].PosMode.Position := lrSaved_TargetPosition;
    arAxisCtrl_gb[eSaved_AxisNo].PosMode.Velocity := rSaved_Velocity;
    arAxisCtrl_gb[eSaved_AxisNo].PosMode.Acc := rSaved_Acceleration;
    arAxisCtrl_gb[eSaved_AxisNo].PosMode.Dec := rSaved_Deceleration;

    // 重新啟動定位
    arAxisCtrl_gb[eSaved_AxisNo].Admin.Start := TRUE;

    bSaved_AxisMoving := FALSE;
END_IF
```

#### 完成判斷

恢復後需等待軸移動完成：

```
IF arAxisStatus_gb[AxisNo].Admin._OpModeAck = ModePosAbs AND
   arAxisStatus_gb[AxisNo].Admin.CmdDone THEN
    // 移動完成
END_IF
```

---

### 多軸同步移動處理

當使用 `FB_TableMove` 或 `FB_XY_Move` 進行多軸同步移動時：

#### 需保存變數

| 變數名稱 | 類型 | 說明 |
|---------|------|------|
| `stSaved_TargetTable` | ST_PositionTable | 目標位置表 |
| `bSaved_TableMoveActive` | BOOL | 位置表移動是否進行中 |

#### 暫停處理

```
// 進入暫停 - 多軸同步
IF fbTableMove.Busy THEN
    stSaved_TargetTable := stCurrentTarget;
    bSaved_TableMoveActive := TRUE;

    // 停止所有相關軸
    arAxisCtrl_gb[AxisNo.OutRobot_X].Admin.Stop := TRUE;
    arAxisCtrl_gb[AxisNo.OutRobot_Y].Admin.Stop := TRUE;
END_IF
```

#### 恢復處理

```
// 離開暫停 - 多軸同步
IF bSaved_TableMoveActive THEN
    // 清除停止指令
    arAxisCtrl_gb[AxisNo.OutRobot_X].Admin.Stop := FALSE;
    arAxisCtrl_gb[AxisNo.OutRobot_Y].Admin.Stop := FALSE;

    // 重新發送移動指令到目標位置
    fbTableMove.Execute := TRUE;
    fbTableMove.TargetTable := stSaved_TargetTable;

    bSaved_TableMoveActive := FALSE;
END_IF
```

---

### 直流馬達 vs 伺服馬達比較

| 項目 | 直流馬達 | 伺服馬達 |
|-----|---------|---------|
| **停止方式** | M_MotorStop() | arAxisCtrl_gb[].Admin.Stop |
| **位置保持** | 無法保持 | 激磁保持位置 |
| **恢復方式** | 重新啟動原方向 | 從當前位置移動到目標位置 |
| **需保存資訊** | 方向、速度 | 目標位置、速度、加減速參數 |
| **精確恢復** | 無法精確 | 可精確恢復到目標位置 |
| **完成判斷** | 依感測器 | CmdDone 信號 |

---

## 安全考量

### 暫停時的安全動作

1. **直流馬達**：必須停止（M_MotorStop）
2. **伺服馬達**：減速停止並保持激磁（arAxisCtrl_gb[].Admin.Stop）
3. **氣缸**：維持當前位置（不發送新指令）
4. **輸出**：根據安全需求決定是否關閉

### 禁止暫停的情況

某些關鍵動作可能不適合中途暫停，例如：

**通用情況：**
- 夾爪正在夾取物件途中
- 升降機構正在移動中
- 需要連續完成的安全相關動作

**伺服馬達特殊情況：**
- 正在進行 **歸原點 (Homing)** 動作時
- 正在進行 **電子凸輪同步** 時
- **垂直軸無煞車**，暫停可能導致重力下墜
- 正在進行 **齒輪比切換** 時
- **多軸插補移動** 中途（可能造成路徑偏離）

可透過額外的 `_bPauseAllowed` 旗標控制是否允許暫停：

```
// 判斷是否允許暫停
_bPauseAllowed := NOT bHomingActive
              AND NOT bCamActive
              AND NOT bVerticalAxisMoving;

// 暫停控制中加入檢查
IF Pause AND NOT _bPauseEdge AND _bPauseAllowed THEN
    // 執行暫停...
END_IF
```

### 暫停逾時保護

考慮加入暫停最長時間限制，超時後自動觸發警報或錯誤。

---

## 測試檢查清單

### 基本功能測試

- [ ] 在各個執行步驟按下暫停，確認動作停止
- [ ] 暫停後按下繼續，確認從正確位置恢復
- [ ] 確認暫停期間計時器不會 Timeout

### 邊界條件測試

- [ ] 在步驟切換瞬間按暫停
- [ ] 在計時器即將 Timeout 時暫停再繼續
- [ ] 快速連續按暫停/繼續
- [ ] 非執行狀態下按暫停（應無反應）

### 整合測試

- [ ] 與上層控制整合測試
- [ ] 與 HMI 整合測試
- [ ] 多個 FB 同時暫停測試

---

## 常見問題

### Q1：暫停後氣缸應該維持還是縮回？

**建議**：維持當前位置。若縮回可能造成物件掉落或位置偏移。但需根據實際應用場景評估安全性。

### Q2：需要保存步驟號碼嗎？

**不需要**。`uiExecutingStep` 在暫停時不會改變，恢復後會從相同步驟繼續。

### Q3：如何處理子 FB 的暫停？

**建議**：將 Pause 信號傳遞給子 FB，讓子 FB 各自處理暫停邏輯。或在父 FB 層級統一控制。

### Q4：恢復後計時器 PT 被改變，會影響下次執行嗎？

**會**。建議在 `A00_BasicUnits` 中每次都重新設定 PT 值，確保每次執行週期的 PT 值正確。

---

## 版本紀錄

| 版本 | 日期 | 修改內容 |
|-----|------|---------|
| 1.0 | 2025-12-27 | 初版建立 |
| 1.1 | 2025-12-27 | 新增馬達類型處理規範（直流馬達、伺服馬達、多軸同步）|
| 1.2 | 2025-12-27 | 補充 PLCopen 標準版本伺服馬達範例（MC_Halt、MC_MoveAbsolute）|

