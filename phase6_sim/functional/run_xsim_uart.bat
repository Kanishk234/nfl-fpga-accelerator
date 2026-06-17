@echo off
REM Phase 6 full-UART-chain functional test in XSIM: drives the entire `top`
REM (uart_rx -> framing -> mlp_controller -> real myproject IP -> framing tx) over
REM serialized UART packets, a few games + a corrupted-checksum NACK case.
REM
REM Run from Windows (double-click or & "\\wsl.localhost\...\run_xsim_uart.bat").

echo ============================================================
echo  Phase 6 Full-UART-Chain Test -- XSIM (top + real IP)
echo ============================================================
echo.

set REPO=\\wsl.localhost\Ubuntu\home\younix\nfl-fpga-accelerator
set IPV=%REPO%\artifacts\ip_repo\hdl\verilog
set HDL=%REPO%\phase5_fpga\hdl
set FUNC=%REPO%\phase6_sim\functional
set XVLOG=C:\AMDDesignTools\2025.2\Vivado\bin\xvlog.bat
set XELAB=C:\AMDDesignTools\2025.2\Vivado\bin\xelab.bat
set XSIM=C:\AMDDesignTools\2025.2\Vivado\bin\xsim.bat
set WORK=C:\Temp\nfl_xsim_uart

echo Staging sources to %WORK% ...
if exist "%WORK%" rmdir /s /q "%WORK%"
mkdir "%WORK%"
xcopy /Q /Y "%IPV%\*.v"   "%WORK%\" >nul
xcopy /Q /Y "%IPV%\*.vh"  "%WORK%\" >nul
xcopy /Q /Y "%IPV%\*.dat" "%WORK%\" >nul
REM full HDL chain (NOT myproject_stub.v — the real IP above provides module myproject)
copy /Y "%HDL%\uart_rx.v"        "%WORK%\" >nul
copy /Y "%HDL%\uart_tx.v"        "%WORK%\" >nul
copy /Y "%HDL%\uart_framing.v"   "%WORK%\" >nul
copy /Y "%HDL%\mlp_controller.v" "%WORK%\" >nul
copy /Y "%HDL%\top.v"            "%WORK%\" >nul
copy /Y "%FUNC%\tb_top_uart.v"   "%WORK%\" >nul
copy /Y "%FUNC%\tb_inputs.mem"   "%WORK%\" >nul

cd /d "%WORK%"

echo.
echo Compiling (xvlog)...
if exist xvlog_files.f del xvlog_files.f
for %%f in (*.v) do @echo %%f>> xvlog_files.f
call "%XVLOG%" -i . -f xvlog_files.f
if errorlevel 1 ( echo ERROR: xvlog failed & pause & exit /b 1 )

echo.
echo Elaborating (xelab)...
call "%XELAB%" tb_top_uart -s tb_uart_snap -relax
if errorlevel 1 ( echo ERROR: xelab failed & pause & exit /b 1 )

echo.
echo Running full-UART test (~10 s)...
call "%XSIM%" tb_uart_snap -R -testplusarg "IN=tb_inputs.mem"
if errorlevel 1 ( echo ERROR: xsim failed & pause & exit /b 1 )

echo.
echo ============================================================
echo  Done. See the PASS/FAIL line above.
echo ============================================================
pause
