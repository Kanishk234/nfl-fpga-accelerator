@echo off
REM Phase 6 (conifer) full-UART test in Vivado XSIM: the ENTIRE top_gbdt (UART RX
REM -> framing -> controller -> both real IPs -> framing TX) driven at 115200
REM baud; responses checked bit-exactly against golden_fixed.mem inside the TB.
REM Mirrors phase6_sim/functional/run_xsim_uart.bat.
REM
REM Run from Windows. Self-checking — look for "FULL-UART TEST (GBDT): PASS".

echo ============================================================
echo  Phase 6 (conifer) Full-UART Test -- XSIM (real top_gbdt)
echo ============================================================
echo.

set REPO=\\wsl.localhost\Ubuntu\home\younix\nfl-fpga-accelerator
set WINIP=%REPO%\phase4_conifer\hls_win\conifer_win\solution1\syn\verilog
set SPRIP=%REPO%\phase4_conifer\hls_spread\conifer_spread\solution1\syn\verilog
set HDL=%REPO%\phase5_fpga_conifer\hdl
set MLPHDL=%REPO%\phase5_fpga\hdl
set FUNC=%REPO%\phase6_sim_conifer
set XVLOG=C:\AMDDesignTools\2025.2\Vivado\bin\xvlog.bat
set XELAB=C:\AMDDesignTools\2025.2\Vivado\bin\xelab.bat
set XSIM=C:\AMDDesignTools\2025.2\Vivado\bin\xsim.bat
set WORK=C:\Temp\nfl_gbdt_xsim_uart

echo Staging sources to %WORK% ...
if exist "%WORK%" rmdir /s /q "%WORK%"
mkdir "%WORK%"
xcopy /Q /Y "%WINIP%\*.v" "%WORK%\" >nul
xcopy /Q /Y "%SPRIP%\*.v" "%WORK%\" >nul
copy /Y "%MLPHDL%\uart_rx.v"                   "%WORK%\" >nul
copy /Y "%MLPHDL%\uart_tx.v"                   "%WORK%\" >nul
copy /Y "%HDL%\uart_framing_conifer.v"         "%WORK%\" >nul
copy /Y "%HDL%\gbdt_controller.v"              "%WORK%\" >nul
copy /Y "%HDL%\sigmoid_rom.v"                  "%WORK%\" >nul
copy /Y "%HDL%\top_gbdt.v"                     "%WORK%\" >nul
copy /Y "%FUNC%\tb_top_uart_gbdt.v"            "%WORK%\" >nul
copy /Y "%FUNC%\chain_golden\sigmoid_lut.mem"  "%WORK%\" >nul
copy /Y "%FUNC%\chain_golden\tb_inputs.mem"    "%WORK%\" >nul
copy /Y "%FUNC%\chain_golden\golden_fixed.mem" "%WORK%\" >nul

cd /d "%WORK%"

echo.
echo Compiling (xvlog)...
if exist xvlog_files.f del xvlog_files.f
for %%f in (*.v) do @echo %%f>> xvlog_files.f
call "%XVLOG%" -i . -f xvlog_files.f
if errorlevel 1 ( echo ERROR: xvlog failed & exit /b 1 )

echo.
echo Elaborating (xelab)...
call "%XELAB%" tb_top_uart_gbdt -s tb_uart_snapshot -relax
if errorlevel 1 ( echo ERROR: xelab failed & exit /b 1 )

echo.
echo Running (xsim)...
call "%XSIM%" tb_uart_snapshot -R
if errorlevel 1 ( echo ERROR: xsim failed & exit /b 1 )

echo.
echo ============================================================
echo  Done. Self-checking: see "FULL-UART TEST (GBDT)" line above.
echo ============================================================
