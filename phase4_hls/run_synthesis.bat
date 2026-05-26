@echo off
REM Run Vitis HLS synthesis for the NFL FPGA accelerator.
REM
REM WHY we copy to a local Windows path first:
REM   Vitis HLS writes myproject.pp.0.cpp.tmp0 then renames it to myproject.pp.0.cpp.
REM   Windows-to-WSL filesystem (Y: mapped drive) does not support atomic renames,
REM   so the rename silently fails and all subsequent clang passes fail with
REM   "no such file or directory". Copying to C:\Temp first avoids this.

echo ============================================================
echo  NFL FPGA Accelerator -- Vitis HLS Synthesis
echo ============================================================
echo.

set WSL_HLS=\\wsl.localhost\Ubuntu\home\younix\nfl-fpga-accelerator\phase4_hls\hls_project
set LOCAL_HLS=C:\Temp\nfl_hls_project
set VITIS_BIN=C:\AMDDesignTools\2025.2\Vitis\bin
set XILINX_VIVADO=C:\AMDDesignTools\2025.2\Vivado

REM Copy project to local Windows path (avoids WSL rename-on-write failures)
echo Copying project to local Windows path...
if exist "%LOCAL_HLS%" rmdir /s /q "%LOCAL_HLS%"
xcopy /E /I /Q "%WSL_HLS%" "%LOCAL_HLS%"
if errorlevel 1 (
    echo ERROR: Could not copy project files to %LOCAL_HLS%
    pause
    exit /b 1
)

REM Also copy synth_only.tcl and project.tcl (synth_only.tcl is one level up in WSL)
copy "\\wsl.localhost\Ubuntu\home\younix\nfl-fpga-accelerator\phase4_hls\synth_only.tcl" "%LOCAL_HLS%\synth_only.tcl"

echo.
echo Working directory: %LOCAL_HLS%
echo.
echo Starting C Synthesis (5-15 minutes)...
echo.

cd /d "%LOCAL_HLS%"

REM vitis-run.bat sets XILINX_VIVADO from RDI_INSTALLROOT (required for set_part device libs)
call "%VITIS_BIN%\vitis-run.bat" --mode hls --tcl "synth_only.tcl"

if errorlevel 1 (
    echo.
    echo ERROR: Synthesis failed. Check output above.
    echo Logs: %LOCAL_HLS%\myproject_prj\solution1\solution1.log
    pause
    exit /b 1
)

REM Copy synthesis reports back to WSL project directory
echo.
echo Copying synthesis reports back to WSL...
set REPORT_SRC=%LOCAL_HLS%\myproject_prj
set REPORT_DST=%WSL_HLS%\myproject_prj
if exist "%REPORT_DST%" rmdir /s /q "%REPORT_DST%"
xcopy /E /I /Q "%REPORT_SRC%" "%REPORT_DST%"

echo.
echo ============================================================
echo  Synthesis complete!
echo  Reports: phase4_hls\hls_project\myproject_prj\solution1\syn\report\
echo.
echo  Next step -- run in WSL:
echo    python phase4_hls/resource_report.py
echo ============================================================
echo.
pause
