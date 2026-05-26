## Basys 3 pin constraints — NFL FPGA Accelerator
## Only pins actually used are constrained (avoids spurious DRC warnings).

## Clock — 100 MHz on-board oscillator
set_property PACKAGE_PIN W5       [get_ports clk]
set_property IOSTANDARD  LVCMOS33 [get_ports clk]
create_clock -add -name sys_clk_pin -period 10.00 -waveform {0 5} [get_ports clk]

## Reset — BTNC (center button), active high
set_property PACKAGE_PIN U18      [get_ports rst]
set_property IOSTANDARD  LVCMOS33 [get_ports rst]

## USB-UART RX (laptop → FPGA)
set_property PACKAGE_PIN B18      [get_ports uart_rxd]
set_property IOSTANDARD  LVCMOS33 [get_ports uart_rxd]

## USB-UART TX (FPGA → laptop)
set_property PACKAGE_PIN A18      [get_ports uart_txd]
set_property IOSTANDARD  LVCMOS33 [get_ports uart_txd]
