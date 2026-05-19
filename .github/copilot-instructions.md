# GitHub Copilot Instructions

## Project

TwinCAT 3 PLC project for the **CinPhown_PackML** machine — a third-generation pin-packing / storage / allocation / shipping machine. Built on a PackML-inspired state-machine framework.

- **Solution**: `CinPhown_PackML.sln` (open in Visual Studio + TwinCAT XAE)
- **Main PLC project**: `CinPhown_PackML/DAS_CoreSys/DAS_CoreSys.plcproj`
- **Platform**: Beckhoff TwinCAT 3 — Structured Text (`.TcPOU` / `.TcGVL` / `.TcDUT`)
- **There is no command-line build.** Build and deploy via TwinCAT XAE IDE only.

## Git Commit Conventions

Format: `<Type> (<Scope>) : <中文說明>`

Examples: `Fix (RobotService) : InnerPick 一行程FB，修正續做後的狀態機切換。`

Types: `Fix`, `Refactor`, `Feature`, `Merge`

Do **not** add AI branding, co-author footers, or emoji lines to commits.

## Library Architecture

`DAS_CoreSys/Library/` is layered by numeric prefix — lower = more foundational:

| Prefix | Layer | Key contents |
|--------|-------|--------------|
| `00_PackML/` | Framework | `01_ObjectBase/FB_ObjectBase_V2` — base FB every stateful FB mirrors |
| `10_Machine/` | Machine-wide | IO mapping, `FB_MachineControl`, `FB_ErrorHandler_V2`, `GVL_ErrorCode`, HMI handshake GVLs, device wrappers (EL1889/EL2889, `FB_FanucRobot_Basic_V2`) |
| `20_MachineUnits/` | Sub-units | `21_StorageArea/`, `22_AllocateArea/`, `23_ShippingArea/` — each has `FB_<Area>`, `Service/`, and sometimes `RobotService/` sub-folders |
| `99_Utilities/` | Helpers | Generic utilities |

## State Machine Pattern (FB_ObjectBase_V2)

Every service/action FB follows this pattern:

1. `Implementation` body calls `A00_BasicUnits()` then `A10_StateControl()`.
2. If Pause/Resume is needed, also call `A01_PauseControl()` between them and wrap the state-machine dispatch in `IF NOT _bPaused THEN`.
3. Business logic lives in `M_STATE_EXECUTING` using `uiExecutingStep : UDINT` (step counter; `9999` = done sentinel).
4. Signal completion by setting `_Executed := TRUE`; signal error via `_Error := TRUE` with `ErrorID`, `UniqueErrorCode : STRING`, and `iMsgID : UINT` (from `GVL_ErrorCode` GC_MSG_* constants).

### RESETTING State Timing Rule

`Done = FALSE` must only be output **after** all cleanup is finished (never at the top of `M_STATE_RESETTING`). The reference pattern:

```iecst
M_STATE_RESETTING:
    Busy  := TRUE;       // hold Busy during cleanup
    Error := FALSE;

    CASE uiResettingStep OF
    0:
        // ... cleanup actions ...
        uiResettingStep := 9999;
    9999:
        Done := FALSE;           // only here, after cleanup
        Busy := FALSE;
        _ResetCompleted := TRUE;
    END_CASE

    IF _ResetCompleted THEN
        eState := STATE_DORMANT;
    END_IF
```

Patterns to avoid: setting `Done := FALSE; Busy := FALSE` at the top of `RESETTING` before cleanup, or having `STATE_DONE` jump directly to `STATE_DORMANT` (skipping RESETTING entirely).

## Pause / Resume Framework

See `Library/20_MachineUnits/PauseResume_Implementation_Guide.md` for full detail. Required in every executing FB:

- Internal flags: `_bPaused : BOOL`, `_bPauseEdge : BOOL`
- Action `A01_PauseControl` called after `A00_BasicUnits`
- TON timers saved as `tSaved_<Name> : TIME` + `bWasActive_<Name> : BOOL`; restored with `PT := original_PT - Saved_ET`
- On pause: stop DC motors with `M_MotorStop`; stop servos with `arAxisCtrl_gb[AxisNo].Admin.Stop := TRUE`
- On resume: restart from saved target position/direction

**禁止暫停 situations** (must not pause mid-action): homing, CAM sync, gearing transitions, vertical axes without brake, interpolated multi-axis moves.

## Axis Indexing

`arAxisCtrl_gb[]` / `arAxisStatus_gb[]` use fixed indices:

```
1=TurnTable  2=RoundBelt  3=Allocate_X  4=Allocate_Y
5=OutRobot_X  6=OutRobot_Y  7=RackMotor
```

## Alarm ID Tooling (Python)

Two Python scripts maintain the alarm translation tables:

- **`audit_alarm_ids.py`** — cross-checks `GVL_ErrorCode.TcGVL`, all `RegisterAlarm()` call sites, `alarmlist.sql`, and `GC_MSG_Long.csv`; outputs a mismatch punch list.
- **`regen_alarmlist.py`** — regenerates `alarmlist.new.sql` and `GC_MSG_Long.new.csv` from `GVL_ErrorCode.TcGVL` as the authority. Does not overwrite the originals.

Run from the repo root: `python audit_alarm_ids.py` / `python regen_alarmlist.py`

Required languages per alarm ID: **English**, **Chinese**, **Taiwanese**.

## Key Conventions

### File format
`.TcPOU` / `.TcGVL` / `.TcDUT` are XML with ST code in CDATA blocks. When editing with text tools, **always preserve the XML structure and `<LineIds>` sections** — TwinCAT uses these for breakpoints and diff.

### Legacy code
Superseded second-gen logic lives in `Old_version_Service/` or `Old_Version_Service/` folders — **do not edit these**. They exist for reference/diff only.

### Versioning
`_V2`, `_V3` suffixes on FB names mark active revisions. Prefer the highest version when extending. Earlier unsuffixed versions may coexist.

### Variants
Round-belt vs XPlanar and robot vs non-robot variants coexist by design. Check which top-level `FB_<Area>` the machine configuration uses before assuming which Service FBs are live.

### Artifacts
`_CompileInfo/`, `_Boot/`, `.tmc`, `.tclrq`, `.tclrs` are generated — do not hand-edit.
