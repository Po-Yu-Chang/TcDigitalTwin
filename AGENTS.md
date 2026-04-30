# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

TwinCAT 3 PLC project for the **CinPhown_PackML** machine — a third-generation pin-packing / storage / allocation / shipping machine for TCI (泰興). Controls storage area, allocation area, and shipping area (with a Fanuc small robot replacing the second-generation tray-based shipping). Built on a PackML-inspired state-machine framework to fix architectural pain points from the previous generation (lack of state machine, bolted-on error handling, complex IO mapping).

- Solution file: `CinPhown_PackML.sln` (Visual Studio + TwinCAT XAE).
- Main PLC project: `CinPhown_PackML/DAS_CoreSys/DAS_CoreSys.plcproj`.
- Platform: Beckhoff TwinCAT 3 (TcPOU / TcGVL / TcDUT / Structured Text). Edit/build/deploy via TwinCAT XAE in Visual Studio — there is no command-line build.
- Hardware: Beckhoff EtherCAT IO (EL1889 / EL2889), multiple servo axes, Fanuc robots, Modbus RTU devices, HMI via handshake GVLs.

## Git Commit Conventions

From `.AGENTS.md` and the user's global instructions:
- **Do NOT** add `Co-Authored-By: Codex`, "Generated with Codex" footer, or emoji branding lines.
- Commit message style in this repo (see recent log): `<Type> (<Scope>) : <中文說明>` — e.g. `Fix (RobotService) : InnerPick 一行程FB，修正續做後的狀態機切換。`. Types seen: `Fix`, `Refactor`, `Feature`, `Merge`.

## Architecture — The Big Picture

### Layered library structure (`DAS_CoreSys/Library/`)

Code is organized by numeric prefix indicating layer, **lower numbers = more foundational**:

- `00_PackML/` — framework primitives
  - `01_ObjectBase/FB_ObjectBase_V2` — **the base FB every stateful FB inherits/mirrors.** Implements the PackML-style state machine (`STATE_DORMANT / EXECUTING / ABORTING / ABORTED / DONE / ERROR / RESETTING / Holding / Held / UnHold`) via `A10_StateControl` dispatching to `M_STATE_*` methods. Exposes `Execute / Abort / Reset / Pause` inputs and `Busy / Done / Aborted / Error / Paused / ErrorID / UniqueErrorCode / iMsgID` outputs. New concrete FBs typically copy the commented-out template bodies in each `M_STATE_*` method.
  - `02_Mode/` — PackML mode management.
- `10_Machine/` — machine-wide services
  - `00_BaseFBs/` — shared base FBs.
  - `11_IOData/` — IO mapping.
  - `12_MachineControl/FB_MachineControl` + `Service/FB_MachineHoming` — top-level orchestration.
  - `13_ErrorHandling/` — `FB_ErrorHandler_V2` plus `GVL_ErrorCode` (unique error codes + `GC_MSG_*` numeric message IDs consumed by HMI for translation via `iMsgID` output on every state FB).
  - `18_HMI_Handshake/` — HMI command/state exchange GVLs.
  - `19_Device/` — device wrappers: `EL1889` / `EL2889` (Beckhoff EtherCAT terminals), `FanucRobot/FB_FanucRobot_Basic_V2`.
- `20_MachineUnits/` — machine sub-units, each a self-contained area with its own `FB_<Area>`, `Service/` (action FBs), and sometimes `Service_Robot/` or `RobotService/` (robot-driven variants):
  - `21_StorageArea/` — storage / box supply / warehouse with round-belt and robot variants.
  - `22_AllocateArea/` — allocation; supports both round-belt and XPlanar variants (`FB_AllocateAreaWithRoundbelt` vs `FB_AllocateAreaWithXPlanar`).
  - `23_ShippingArea/` — shipping (3rd-gen uses Fanuc small robot, not a tray).
- `99_Utilities/` — generic helpers.

### State machine conventions

Every action/service FB follows the `FB_ObjectBase_V2` pattern:
1. `Implementation` calls `A00_BasicUnits()` then `A10_StateControl()`.
2. Business logic lives in `M_STATE_EXECUTING` using a `uiExecutingStep : UDINT` step counter (with `9999` = done sentinel).
3. Set `_Executed`, `_Error`, or `_Abort` internal flags; the base machinery transitions state accordingly.
4. Errors are assigned `ErrorID`, `UniqueErrorCode : STRING`, and `iMsgID : UINT` (GC_MSG_* from `GVL_ErrorCode`) so HMI can translate.

### Pause / Resume framework

See `Library/20_MachineUnits/PauseResume_Implementation_Guide.md` for the canonical rules. Every executing FB must:
- Own `_bPaused` + `_bPauseEdge` internal flags and an `A01_PauseControl` action called after `A00_BasicUnits`.
- Save/restore TON timers as `tSaved_<Name>` + `bWasActive_<Name>` with recovery `PT := original_PT - Saved_ET`.
- Stop motion on pause (DC motors: `M_MotorStop`; servos: `arAxisCtrl_gb[AxisNo].Admin.Stop := TRUE`) and restart from saved target on resume.
- Wrap the state-machine dispatch in `IF NOT _bPaused THEN ...`.
- The guide also lists **禁止暫停** situations (homing, CAM sync, gearing, vertical axes without brake, interpolated multi-axis moves).

### Axis indexing convention

`arAxisCtrl_gb[] / arAxisStatus_gb[]` use a fixed `AxisNo` enum:
`1=TurnTable, 2=RoundBelt, 3=Allocate_X, 4=Allocate_Y, 5=OutRobot_X, 6=OutRobot_Y, 7=RackMotor`.

### Variants and legacy code

- Second-gen logic that was superseded is kept under `Old_version_Service/` or `Old_Version_Service/` folders — **do not edit these**, they exist for reference/diff.
- `_V2`, `_v2`, `_V3` suffixes on FB names mark the active revision; earlier non-suffixed versions may still live alongside. Prefer the highest version when extending.
- Round-belt vs XPlanar and robot vs non-robot variants coexist by design — check which top-level `FB_<Area>` a machine config uses before assuming which Service FBs are live.

## Workflow Notes

- Generated build artifacts (`_CompileInfo/`, `_Boot/`, `.tmc`, `.tclrq`, `.tclrs`) should not be hand-edited.
- `.TcPOU` / `.TcGVL` / `.TcDUT` are XML with ST code in CDATA — always preserve the XML structure and `<LineIds>` sections when editing with raw text tools.
- Handover docs live in `交接文件/` (Chinese). `專案描述.md` summarizes how this generation differs from gen-2 and why PackML was adopted.
