# AutoMode FB Done / Busy / Resetting 時序檢查

## 檢查目的

針對 `GVL_ADS.AutoMode_*` 相關一行程 FB,檢查 `Done`、`Busy`、`Execute`、`STATE_RESETTING` 的時序是否符合以下語意:

> 當上位看到 `Done = FALSE` 時,該 FB 應已完成 reset / cleanup,並可安全接受下一筆 `Execute`。

若 FB 在 reset 還沒完成前就先把 `Done := FALSE` 或 `Busy := FALSE`,上位可能誤判為可下下一筆命令,造成命令重入、Robot 尚未 ready 就再次動作、或衍生 `Not in Home Pos` / disconnect 類錯誤。

## 建議統一規則 (reference)

所有 `GVL_ADS.AutoMode_*` 一行程 FB 應符合下列概念:

```st
M_STATE_DONE:
    Busy := FALSE;
    Done := TRUE;
    IF NOT Execute THEN
        eState := STATE_RESETTING;
    END_IF

M_STATE_RESETTING:
    Busy  := TRUE;        // reset cleanup 尚未完成,不能讓上位判斷可用
    Error := FALSE;
    // 不要在 reset 一開始 Done := FALSE

    CASE uiResettingStep OF
    0:
        // ... cleanup 動作 ...
        uiResettingStep := 9999;
    9999:
        Done := FALSE;    // 到這裡才代表下一筆可進
        Busy := FALSE;
        _ResetCompleted := TRUE;
    END_CASE

    IF _ResetCompleted THEN
        eState := STATE_DORMANT;
    END_IF
```

核心原則:

1. `STATE_DONE` 期間維持 `Done := TRUE`、`Busy := FALSE`。
2. `Execute` 關閉後進入 `STATE_RESETTING`。
3. `STATE_RESETTING` 期間 FB 尚未可用,應維持 `Busy := TRUE`。
4. reset / cleanup 全部完成後 (step 9999),才輸出 `Done := FALSE` 與 `Busy := FALSE`。
5. `Done = FALSE` 應代表 FB 已可接受下一筆 `Execute`。

## 已修正項目 (reference 實作)

### FB_ShippingRobotTransfer

檔案: `CinPhown_PackML\DAS_CoreSys\Library\20_MachineUnits\23_ShippingArea\RobotService\FB_ShippingRobotTransfer.TcPOU`

目前已調整為:

- `STATE_DONE` 維持 `Done := TRUE`。
- `STATE_RESETTING` 一開始 `Busy := TRUE; Error := FALSE`,**不會**立刻 `Done := FALSE`。
- step 200 檢查氣缸,step 9999 才一起:
  - `Done := FALSE`
  - `Busy := FALSE`
  - `_ResetCompleted := TRUE`
  - 回 `STATE_DORMANT`

此行為符合「`Done = FALSE` 後上位可立即下下一筆 `Execute`」的語意。後續所有 FB 均以此為對齊目標。

---

## Audit 範圍與發現

第二輪 audit (含 Fanuc 大手臂補檢) 共檢查 **27 支** FB(含 reference),除 `FB_ShippingRobotTransfer` 外 **26 支全部不符合 reference**,但問題輕重不一,可分四種 pattern:

| Pattern | 描述 | 嚴重度 |
|---|---|---|
| **A** | `RESETTING` 開頭 `IF _Executed THEN Done:=TRUE` 顯式保留 Done,但**沒設 `Busy:=TRUE`**,延續 DONE 的 FALSE | Busy 早關 |
| **B** | 用註解 `//Done := FALSE; //done 留到 dormant 關閉` 表達意圖,沒設 `Busy:=TRUE` | Busy 早關 |
| **C** | `RESETTING` 開頭直接 `Error:=FALSE; Done:=FALSE; Busy:=FALSE` 全清,後面才下 cleanup 動作 | Done+Busy 早關 |
| **D** | `RESETTING` 完全沒處理 Done/Busy,延續 DONE 的 `Busy=FALSE`、Done=TRUE | Busy 早關 (Done 偶然 OK) |
| **特殊** | `STATE_DONE` 直接 `eState:=STATE_DORMANT`,**完全跳過 RESETTING** | 結構性 |

---

## 全部 FB 總表

(reference 為 ShippingRobotTransfer,不列)

| 區域 | FB | Pattern | DONE | RESETTING line ref | 嚴重度 | 改動範圍 |
|---|---|---|---|---|---|---|
| **Shipping** ||||||
| Shipping | FB_ShippingRobot_Left | C | ✅ | L486 一進來 `Done:=FALSE; Busy:=FALSE` | 🔴 高 | small |
| Shipping | FB_ShippingRobot_Right | C | ✅ | L484 同上 | 🔴 高 | small |
| Shipping | FB_ShippingRobot_PhotoStandByPos | C | ✅ | L343 step 0 `Done:=FALSE` | 🔴 高 | small |
| Shipping | FB_ShippingRobot_TransmitCVEnd | C | ✅ | L462 step 0 `Done:=FALSE` | 🔴 高 | small |
| Shipping | FB_ShippingRobot_Buffer | C | ✅ | L422 `Done:=FALSE`,step 9999 沒清 Busy | 🟡 中 | small |
| **Allocate (Reverse)** ||||||
| Allocate | FB_SameSizeCV_ReverseBoxToWareHouse | C | ✅ | L807 step 0 `Done:=FALSE`,後面才 `StorageRobot.M_ResetAllCommand`、`WareHouse.M_ResetFeedInBox`、`NGRegionMove.Execute:=FALSE`、`Motor.MotorStop`,整段沒設 `Busy:=TRUE` | 🔴 高 | small |
| Allocate | FB_SameSizeCV_ReverseBatchPickAndPhoto | C | ✅ | L960 step 0 `Done:=FALSE`,後面才氣缸 retract + camera xExecute reset,整段沒設 `Busy:=TRUE` | 🔴 高 | small |
| **Allocate (Auto Batch — 結構性)** ||||||
| Allocate | FB_AllocateMoveAutoBatchMode | 特殊 | ⚠️ | L327 DONE 直接 `eState:=STATE_DORMANT`,**跳過 RESETTING**;RESETTING (L1297) `Busy:=FALSE` 一進來 | 🔴 嚴重 | medium |
| Allocate | FB_AllocateMoveAutoBatchMode_Reverse | 特殊 | ⚠️ | L316 同 BatchMode;RESETTING (L1242-1243) `Busy:=FALSE; Done:=FALSE` 一進來 | 🔴 嚴重 | medium |
| **Allocate (OutRobot)** ||||||
| Allocate | FB_OutRobot_RareBoxMove | C-變形 | ✅ | L1060-1062 `Done:=_Executed; Busy:=FALSE`,後面 `ConveyorMotor.M_MotorStop` + `M_RecoveryAllObjects` | 🟡 中 | small |
| Allocate | FB_OutRobot_EmptyBoxMove | C-變形 | ✅ | L1053-1055 同上,加 `PressCylinderOnI5.M_Retract` | 🟡 中 | small |
| Allocate | FB_OutRobot_BoxMoveReverse | C-變形 | ✅ | L1054-1056 同 RareBoxMove pattern | 🟡 中 | small |
| Allocate | FB_OutRobot_BoxMoveTurnTableToRoundBelt | C-變形 | ✅ | L557-559 同 RareBoxMove pattern (cleanup 較少) | 🟡 中 | small |
| **Storage / Fanuc 大手臂 — Service 動作 (Pattern A)** ||||||
| Storage | FB_FeedInEmptyBoxWithRobot_V4 | A | ✅ | L2527 `IF _Executed THEN Done:=TRUE`;step 0 一堆 cleanup (`Robot.M_ResetAllCommand`、`WareHouse.M_ResetFeedInBox`、`SameSizeCV.M_MotorStop` 等),step 100 `_ResetCompleted`;**整段沒設 `Busy:=TRUE`** | 🟡 中 | small |
| Storage | FB_FeedInRoundBeltBoxToWareHouse | A | ✅ | L1508 `IF _Executed THEN Done:=TRUE`;step 0: 釋放 RoundBelt + PullOut access、`WareHouse.M_ResetFeedInBox`、`Robot.M_ResetAllCommand`,step 100 `_ResetCompleted`;**整段沒設 `Busy:=TRUE`** | 🟡 中 | small |
| Storage | FB_WareHouseInnerPickToRoundBelt | A | ✅ | L2572 `IF _Executed THEN Done:=TRUE`;step 0 釋放 ReloadRoundBelt action + RoundBelt access、`WareHouse.M_ResetReloadBox`、`WareHouse.M_ResetFeedInBox`、`Robot.M_ResetAllCommand`;step 100 `_ResetCompleted`;**整段沒設 `Busy:=TRUE`** | 🟡 中 | small |
| Storage | FB_ReloadRoundBeltBoxWithRobot_V2 | A | ✅ | L1133 `IF _Executed THEN Done:=TRUE`;step 0 同上;**整段沒設 `Busy:=TRUE`** | 🟡 中 | small |
| **Storage / Fanuc 大手臂 — Batch (Pattern B)** ||||||
| Storage | FB_FeedInRoundBeltBoxBatch | B | ✅ | L486 註解 `//Done := FALSE; //done 留到dormant 關閉`;step 0 推進 fbSingleFlow,step 50 等 `fbSingleFlow.eState = STATE_DORMANT`,step 100 `_ResetCompleted`;**整段沒設 `Busy:=TRUE`** | 🟡 中 | small |
| Storage | FB_WareHouseInnerPickToRoundBeltBatch | B | ✅ | L648 同註解;step 0/50 同推進 fbSingleFlow,step 100 `_ResetCompleted`;**整段沒設 `Busy:=TRUE`** | 🟡 中 | small |
| **Storage / Fanuc 大手臂 — High-level Robot Move (Pattern C)** ||||||
| Storage | FB_WareHouseWithRobotMove_V3 | C | ✅ | L901-903 一進來 `Error:=FALSE; Done:=FALSE; Busy:=FALSE`;後面才 `Robot.M_ResetAllCommand`、`WareHouse.M_ResetFeedInBox/ReloadBox`、AClamp/BClamp 旗標 | 🔴 高 | small |
| **Storage / Fanuc 大手臂 — 底層 Region Move (Pattern C)** ||||||
| Storage | FB_RobotAbsMove_V2 | C | ✅ | L1099-1101 一進來全清;後面 `Robot.M_ResetAllCommand`、`Robot.M_ResetRobotAbsMove(TRUE,TRUE)`、step 100 `LowerConveyor.M_MotorStop` | 🔴 高 | small |
| Storage | FB_RobotRegionMove_FeedInConveyor_V2 | C | ✅ | L1034-1036 同 pattern;後面 Robot reset + step 100 `LowerConveyor.M_MotorStop` | 🔴 高 | small |
| Storage | FB_RobotRegionMove_AllocatedConveyor | C | ✅ | L1104-1106 同 pattern;加 `DifferenceCV.M_MotorStop`、`SamesizeCV.M_MotorStop`、`fbSameSizeCV_Press3/4.M_Retract` | 🔴 高 | small |
| Storage | FB_RobotRegionMove_PullOutConveyor | C | ✅ | L728-730 同 pattern;step 100 `M_ReleasePulloutAccess` + `M_StopStoragePulloutAction` | 🔴 高 | small |
| Storage | FB_RobotRegionMove_RoundBelt | C | ✅ | L749-751 同 pattern;cleanup 只有 Robot reset | 🔴 高 | small |
| Storage | FB_RobotRegionMove_BufferArea | C | ✅ | L559-561 同 pattern;cleanup 只有 Robot reset | 🔴 高 | small |
| **Storage — 自歸位 (Pattern D)** ||||||
| Storage | FB_RobotSelfHoming | D | ✅ | RESETTING 完全沒處理 Done/Busy → Done 維持 TRUE (OK),Busy 延續 DONE 的 FALSE (違規);step 9999 沒清旗標,進 DORMANT 才清 | 🟡 中 | small |

---

## Pattern 修法範本

### Pattern A / B / D 修法 (只需補 `Busy:=TRUE` + 在 step 9999 清旗標)

```st
M_STATE_RESETTING:
    Busy  := TRUE;                                // ← 新增 (保留 Done 不動)
    Error := FALSE;

    CASE uiResettingStep OF
    0:
        // ... 既有 cleanup ...
        uiResettingStep := 100;                   // (Pattern A/B 既有)
    100:                                          // 或 9999
        Done := FALSE;                            // ← 新增 (取代註解)
        Busy := FALSE;                            // ← 新增
        _ResetCompleted := TRUE;
    END_CASE

    IF _ResetCompleted THEN
        eState := STATE_DORMANT;
    END_IF
```

### Pattern C 修法 (把開頭三行搬到 step 9999)

```st
M_STATE_RESETTING:
    Busy  := TRUE;                                // ← 新增
    Error := FALSE;                               // ← 保留 (從原本三行抽出)
    // Done := FALSE;                             // ← 移除 (搬到 step 9999)
    // Busy := FALSE;                             // ← 移除 (搬到 step 9999)

    CASE uiResettingStep OF
    0:
        // ... 既有 cleanup ...
        uiResettingStep := 100;
    100:
        // ... step 100 既有動作 ...
        uiResettingStep := 9999;
    9999:
        Done := FALSE;                            // ← 新增
        Busy := FALSE;                            // ← 新增
        _ResetCompleted := TRUE;
    END_CASE

    IF _ResetCompleted THEN
        eState := STATE_DORMANT;
    END_IF
```

### 特殊 (AllocateMoveAutoBatchMode + _Reverse) 修法

DONE 直接跳 DORMANT,**整個 RESETTING 在 happy path 永遠不會被執行**。改法不是只調時序,而是要:

1. `STATE_DONE` 改成 reference 那種 `IF NOT Execute THEN eState := STATE_RESETTING`
2. 把現在塞在 DONE 裡的 `M_ReleaseRoundBeltAccess` / `RoundBeltMoveJob.HandShake.Execute:=FALSE` 等 cleanup 移到 RESETTING step
3. RESETTING step 9000 才 `Done:=FALSE; Busy:=FALSE; bResettingCompleted:=TRUE`

---

## 修改順序建議

依風險與工作量:

### Phase 1 — 結構性問題 (medium 改動)

1. `FB_AllocateMoveAutoBatchMode` (DONE 跳 DORMANT,需重寫 happy path)
2. `FB_AllocateMoveAutoBatchMode_Reverse` (同上)

### Phase 2 — Pattern C 高風險 (small 改動,共 13 支)

#### Shipping (4 支)
3. `FB_ShippingRobot_Left`
4. `FB_ShippingRobot_Right`
5. `FB_ShippingRobot_PhotoStandByPos`
6. `FB_ShippingRobot_TransmitCVEnd`

#### Allocate Reverse (2 支)
7. `FB_SameSizeCV_ReverseBoxToWareHouse`
8. `FB_SameSizeCV_ReverseBatchPickAndPhoto`

#### Storage 高層 + 底層 RegionMove (7 支)
9. `FB_WareHouseWithRobotMove_V3`
10. `FB_RobotAbsMove_V2`
11. `FB_RobotRegionMove_FeedInConveyor_V2`
12. `FB_RobotRegionMove_AllocatedConveyor`
13. `FB_RobotRegionMove_PullOutConveyor`
14. `FB_RobotRegionMove_RoundBelt`
15. `FB_RobotRegionMove_BufferArea`

### Phase 3 — Busy 早關 (small 改動,共 11 支,看上位是否依賴 Busy)

#### Shipping (1 支)
16. `FB_ShippingRobot_Buffer`

#### OutRobot (4 支)
17. `FB_OutRobot_RareBoxMove`
18. `FB_OutRobot_EmptyBoxMove`
19. `FB_OutRobot_BoxMoveReverse`
20. `FB_OutRobot_BoxMoveTurnTableToRoundBelt`

#### Storage Service / Batch / SelfHoming (6 支)
21. `FB_FeedInEmptyBoxWithRobot_V4`
22. `FB_FeedInRoundBeltBoxToWareHouse`
23. `FB_WareHouseInnerPickToRoundBelt`
24. `FB_ReloadRoundBeltBoxWithRobot_V2`
25. `FB_FeedInRoundBeltBoxBatch`
26. `FB_WareHouseInnerPickToRoundBeltBatch`
27. `FB_RobotSelfHoming`

---

## 注意事項

- 本文件為第二輪 audit 結果(已含 Fanuc 大手臂 12 支補檢),尚未逐支完整修改。
- 若上位只依 `Done` 判斷下一筆,Phase 1 + Phase 2 為必修。
- 若上位也依 `Busy` 判斷下一筆,Phase 3 也需修。
- 修改後應逐支確認:
  - `Done` 是否只在 reset 完成 (step 9999/100) 後才 FALSE。
  - `Busy` 是否在 reset cleanup 期間維持 TRUE。
  - 回 `STATE_DORMANT` 後,若 `Execute` 已為 TRUE,是否能下一輪直接進 `STATE_EXECUTING`。
- 每支 FB 修改時建議**只做狀態輸出時序調整,不更動原本 cleanup 動作流程**。
- Pattern C 底層 RegionMove (7 支) 是雙重風險:Service 動作 FB 通常呼叫底層 RegionMove,如果底層 race,Service FB 仍會踩雷。建議跟 Pattern C high-level (`FB_WareHouseWithRobotMove_V3`) 一併修。
