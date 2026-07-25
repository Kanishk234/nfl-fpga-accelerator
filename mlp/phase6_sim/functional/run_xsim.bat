@echo off
REM Phase 6 functional regression in Vivado XSIM: mlp_controller + the REAL io_stream
REM myproject IP, driven over all 50 games, results vs the Python golden.
REM
REM Why XSIM (not iverilog): iverilog runs this large HLS netlist at ~5 min/game and is
REM X-pessimistic on HLS RTL that doesn't reset every datapath reg. XSIM is compiled
REM (seconds), and is the simulator this IP was validated against in Phase 4 cosim.
REM
REM Run from Windows (double-click or: & "\\wsl.localhost\...\run_xsim.bat").
REM Produces sim_results.csv back in the WSL tree; then in WSL:
REM   python mlp/phase6_sim/functional/check_results.py

echo ============================================================
echo  Phase 6 Functional Regression -- XSIM (real IP + controller)
echo ============================================================
echo.

set REPO=\\wsl.localhost\Ubuntu\home\younix\nfl-fpga-accelerator
set IPV=%REPO%\artifacts\ip_repo\hdl\verilog
set HDL=%REPO%\mlp\phase5_fpga\hdl
set FUNC=%REPO%\mlp\phase6_sim\functional
set XVLOG=C:\AMDDesignTools\2025.2\Vivado\bin\xvlog.bat
set XELAB=C:\AMDDesignTools\2025.2\Vivado\bin\xelab.bat
set XSIM=C:\AMDDesignTools\2025.2\Vivado\bin\xsim.bat
set WORK=C:\Temp\nfl_xsim

set NGAMES=%1
if "%NGAMES%"=="" set NGAMES=50

REM --- Stage sources locally (XSIM needs the ROM .dat + tb_inputs.mem at run CWD) ---
echo Staging sources to %WORK% ...
if exist "%WORK%" rmdir /s /q "%WORK%"
mkdir "%WORK%"
xcopy /Q /Y "%IPV%\*.v"   "%WORK%\" >nul
xcopy /Q /Y "%IPV%\*.vh"  "%WORK%\" >nul
xcopy /Q /Y "%IPV%\*.dat" "%WORK%\" >nul
copy /Y "%HDL%\mlp_controller.v"          "%WORK%\" >nul
copy /Y "%FUNC%\tb_regression_real.v"     "%WORK%\" >nul
copy /Y "%FUNC%\tb_inputs.mem"            "%WORK%\" >nul

cd /d "%WORK%"

echo.
echo Compiling (xvlog)...
REM xvlog does not expand *.v on Windows; build an explicit file list and pass with -f.
if exist xvlog_files.f del xvlog_files.f
for %%f in (*.v) do @echo %%f>> xvlog_files.f
call "%XVLOG%" -i . -f xvlog_files.f
if errorlevel 1 ( echo ERROR: xvlog failed & pause & exit /b 1 )

echo.
echo Elaborating (xelab)...
call "%XELAB%" tb_regression_real -s tb_snapshot -relax
if errorlevel 1 ( echo ERROR: xelab failed & pause & exit /b 1 )

echo.
echo Running %NGAMES% games (xsim)...
call "%XSIM%" tb_snapshot -R -testplusarg "N=%NGAMES%" -testplusarg "IN=tb_inputs.mem" -testplusarg "OUT=sim_results.csv"
if errorlevel 1 ( echo ERROR: xsim failed & pause & exit /b 1 )

echo.
echo Copying results back to WSL...
copy /Y "%WORK%\sim_results.csv" "%FUNC%\sim_results.csv" >nul

echo.
echo ============================================================
echo  Done. Results: mlp\phase6_sim\functional\sim_results.csv
echo  Next, in WSL:  python mlp/phase6_sim/functional/check_results.py
echo ============================================================
pause
