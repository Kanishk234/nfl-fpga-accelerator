@echo off
REM Phase 4 (conifer) -- Vitis HLS synthesis for BOTH GBDT stages.
REM Mirrors mlp\phase4_hls\run_synthesis.bat: copy to a local Windows path first
REM (WSL filesystem breaks Vitis HLS's atomic renames), run vitis-run, copy
REM reports back. Non-interactive: no pause, exit codes propagate.

set WSL_BASE=\\wsl.localhost\Ubuntu\home\younix\nfl-fpga-accelerator\gbdt\phase4_hls
set LOCAL_BASE=C:\Temp\nfl_conifer
set VITIS_BIN=C:\AMDDesignTools\2025.2\Vitis\bin
set XILINX_VIVADO=C:\AMDDesignTools\2025.2\Vivado

for %%P in (win spread) do (
    echo ============================================================
    echo  conifer_%%P : copy + synthesize
    echo ============================================================
    if exist "%LOCAL_BASE%_%%P" rmdir /s /q "%LOCAL_BASE%_%%P"
    xcopy /E /I /Q "%WSL_BASE%\hls_%%P" "%LOCAL_BASE%_%%P"
    if errorlevel 1 exit /b 1
    copy "%WSL_BASE%\synth_gbdt.tcl" "%LOCAL_BASE%_%%P\synth_gbdt.tcl"

    cd /d "%LOCAL_BASE%_%%P"
    call "%VITIS_BIN%\vitis-run.bat" --mode hls --tcl "synth_gbdt.tcl"
    if errorlevel 1 (
        echo ERROR: synthesis failed for conifer_%%P
        exit /b 1
    )

    echo Copying reports back to WSL...
    if exist "%WSL_BASE%\hls_%%P\conifer_%%P" rmdir /s /q "%WSL_BASE%\hls_%%P\conifer_%%P"
    xcopy /E /I /Q "%LOCAL_BASE%_%%P\conifer_%%P" "%WSL_BASE%\hls_%%P\conifer_%%P"
)

echo ============================================================
echo  Both stages synthesized. Reports:
echo    gbdt\phase4_hls\hls_win\conifer_win\solution1\syn\report\
echo    gbdt\phase4_hls\hls_spread\conifer_spread\solution1\syn\report\
echo ============================================================
