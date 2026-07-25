@echo off
REM Phase 6 (conifer) chain regression in Vivado XSIM: gbdt_controller + sigmoid
REM ROM + BOTH real conifer netlists over 100 val games, results vs chain_golden/.
REM Mirrors mlp/phase6_sim/functional/run_xsim.bat (same toolchain + C:\Temp staging).
REM
REM Run from Windows. Produces sim_results.csv back in the WSL tree; then in WSL:
REM   python gbdt/phase6_sim/check_results.py

echo ============================================================
echo  Phase 6 (conifer) Chain Regression -- XSIM (real IPs)
echo ============================================================
echo.

set REPO=\\wsl.localhost\Ubuntu\home\younix\nfl-fpga-accelerator
set WINIP=%REPO%\gbdt\phase4_hls\hls_win\conifer_win\solution1\syn\verilog
set SPRIP=%REPO%\gbdt\phase4_hls\hls_spread\conifer_spread\solution1\syn\verilog
set HDL=%REPO%\gbdt\phase5_fpga\hdl
set FUNC=%REPO%\gbdt\phase6_sim
set XVLOG=C:\AMDDesignTools\2025.2\Vivado\bin\xvlog.bat
set XELAB=C:\AMDDesignTools\2025.2\Vivado\bin\xelab.bat
set XSIM=C:\AMDDesignTools\2025.2\Vivado\bin\xsim.bat
set WORK=C:\Temp\nfl_gbdt_xsim

set NGAMES=%1
if "%NGAMES%"=="" set NGAMES=100

echo Staging sources to %WORK% ...
if exist "%WORK%" rmdir /s /q "%WORK%"
mkdir "%WORK%"
xcopy /Q /Y "%WINIP%\*.v" "%WORK%\" >nul
xcopy /Q /Y "%SPRIP%\*.v" "%WORK%\" >nul
copy /Y "%HDL%\gbdt_controller.v"              "%WORK%\" >nul
copy /Y "%HDL%\sigmoid_rom.v"                  "%WORK%\" >nul
copy /Y "%FUNC%\tb_regression_gbdt.v"          "%WORK%\" >nul
copy /Y "%FUNC%\chain_golden\sigmoid_lut.mem"  "%WORK%\" >nul
copy /Y "%FUNC%\chain_golden\tb_inputs.mem"    "%WORK%\" >nul

cd /d "%WORK%"

echo.
echo Compiling (xvlog)...
REM xvlog does not expand *.v on Windows; build an explicit file list and pass with -f.
if exist xvlog_files.f del xvlog_files.f
for %%f in (*.v) do @echo %%f>> xvlog_files.f
call "%XVLOG%" -i . -f xvlog_files.f
if errorlevel 1 ( echo ERROR: xvlog failed & exit /b 1 )

echo.
echo Elaborating (xelab)...
call "%XELAB%" tb_regression_gbdt -s tb_gbdt_snapshot -relax
if errorlevel 1 ( echo ERROR: xelab failed & exit /b 1 )

echo.
echo Running %NGAMES% games (xsim)...
call "%XSIM%" tb_gbdt_snapshot -R -testplusarg "N=%NGAMES%" -testplusarg "IN=tb_inputs.mem" -testplusarg "OUT=sim_results.csv"
if errorlevel 1 ( echo ERROR: xsim failed & exit /b 1 )

echo.
echo Copying results back to WSL...
copy /Y "%WORK%\sim_results.csv" "%FUNC%\sim_results.csv" >nul

echo.
echo ============================================================
echo  Done. Results: gbdt\phase6_sim\sim_results.csv
echo  Next, in WSL:  python gbdt/phase6_sim/check_results.py
echo ============================================================
