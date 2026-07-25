@echo off
REM Phase 4 (conifer) -- REAL Vivado logic synthesis for both stages.
REM The HLS utilization estimate is known-inflated (see lut_budget lesson from
REM the MLP flow); this produces the real LUT/FF numbers that gate Basys 3 fit.
REM Uses the C:\Temp copies left by run_synthesis.bat (which contain the
REM generated VHDL); copies vivado_synth.rpt back to WSL.

set WSL_BASE=\\wsl.localhost\Ubuntu\home\younix\nfl-fpga-accelerator\gbdt\phase4_hls
set LOCAL_BASE=C:\Temp\nfl_conifer
set VIVADO=C:\AMDDesignTools\2025.2\Vivado\bin\vivado.bat

for %%P in (win spread) do (
    echo ============================================================
    echo  conifer_%%P : Vivado logic synthesis
    echo ============================================================
    REM refresh the synth tcl from WSL (it is patched to the Verilog view there)
    copy /Y "%WSL_BASE%\hls_%%P\vivado_synth.tcl" "%LOCAL_BASE%_%%P\vivado_synth.tcl"
    cd /d "%LOCAL_BASE%_%%P"
    call "%VIVADO%" -mode batch -source vivado_synth.tcl -nojournal -log vivado_synth.log
    if errorlevel 1 (
        echo ERROR: vivado synth failed for conifer_%%P
        exit /b 1
    )
    copy "%LOCAL_BASE%_%%P\vivado_synth.rpt" "%WSL_BASE%\hls_%%P\vivado_synth.rpt"
)

echo Done. Reports: gbdt\phase4_hls\hls_win\vivado_synth.rpt / hls_spread\vivado_synth.rpt
