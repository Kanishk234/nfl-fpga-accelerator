"""
Programs the Basys 3 via Vivado's hardware manager.
Run from WSL2 — invokes Vivado batch mode on Windows via cmd.exe.
"""

import subprocess
import sys
from pathlib import Path

# Windows paths — used inside the Vivado TCL script and for cmd.exe invocation.
BITSTREAM_WIN  = 'C:/nfl_fpga_build/nfl_fpga_accelerator.runs/impl_1/top.bit'
VIVADO_WIN     = 'C:/Xilinx/Vivado/2025.2/bin/vivado.bat'

# WSL2 mirror of the bitstream path — used for the exists() check from Linux.
BITSTREAM_WSL  = '/mnt/c/nfl_fpga_build/nfl_fpga_accelerator.runs/impl_1/top.bit'

# Write TCL to a Windows-accessible temp location so Vivado can read it.
TCL_WIN_PATH   = 'C:/Windows/Temp/program_board.tcl'
TCL_WSL_PATH   = '/mnt/c/Windows/Temp/program_board.tcl'

PROGRAM_TCL = """\
open_hw_manager
connect_hw_server
open_hw_target
set device [lindex [get_hw_devices] 0]
current_hw_device $device
refresh_hw_device $device
set_property PROGRAM.FILE {BITSTREAM} $device
program_hw_devices $device
close_hw_target
disconnect_hw_server
close_hw_manager
puts "Board programmed successfully"
"""


def program_board(bitstream_win: str = BITSTREAM_WIN):
    if not Path(BITSTREAM_WSL).exists():
        print(f"ERROR: Bitstream not found at WSL path: {BITSTREAM_WSL}")
        print("Run Vivado synthesis+implementation first.")
        sys.exit(1)

    tcl = PROGRAM_TCL.replace('BITSTREAM', bitstream_win)

    with open(TCL_WSL_PATH, 'w') as f:
        f.write(tcl)
    print(f"TCL script written to {TCL_WIN_PATH}")

    print(f"Programming board with: {bitstream_win}")
    # .bat files require cmd.exe to execute from WSL2
    result = subprocess.run(
        ['cmd.exe', '/c', VIVADO_WIN,
         '-mode', 'batch', '-source', TCL_WIN_PATH],
        capture_output=False,
    )
    if result.returncode != 0:
        print("ERROR: Programming failed — check Vivado output above")
        sys.exit(1)
    print("Done.")


if __name__ == '__main__':
    program_board()
