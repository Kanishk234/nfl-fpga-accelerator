import serial
import functools
import time
import logging

logger = logging.getLogger(__name__)

SOF_REQUEST   = 0xAA
SOF_RESPONSE  = 0x55
STATUS_OK     = 0x00
STATUS_NACK   = 0x01   # board detected a checksum error
STATUS_TIMEOUT = 0x02  # board's MLP watchdog fired — inference never completed (AUDIT §5.2)

STATUS_NAMES = {STATUS_OK: "OK", STATUS_NACK: "NACK", STATUS_TIMEOUT: "TIMEOUT"}

# At 115200 baud: 23 bytes out + 1,759 cycle MLP + 4 bytes back ≈ 2.4ms.
# 2.0s is extremely generous — if we timeout, the board is hung or disconnected.
RESPONSE_TIMEOUT_S = 2.0


class FPGAClient:

    def __init__(self, port: str, baud: int = 115200):
        self.port = port
        self.baud = baud
        self.ser  = None

    def connect(self):
        self.ser = serial.Serial(
            port          = self.port,
            baudrate      = self.baud,
            bytesize      = serial.EIGHTBITS,
            parity        = serial.PARITY_NONE,
            stopbits      = serial.STOPBITS_ONE,
            timeout       = RESPONSE_TIMEOUT_S,
            write_timeout = 1.0,
        )
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        logger.info(f"Connected to FPGA on {self.port} at {self.baud} baud")

    def disconnect(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            logger.info("Disconnected from FPGA")

    def is_connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    @staticmethod
    def compute_checksum(feature_bytes: list[int]) -> int:
        """XOR checksum of 21 feature bytes."""
        return functools.reduce(lambda a, b: a ^ b, feature_bytes)

    def run_inference(self, feature_bytes: list[int]) -> dict:
        """
        Send 21 feature bytes to the FPGA and return the prediction.

        Returns dict with keys:
            win_prob, spread, win_pct_str, status,
            raw_win, raw_spread, latency_ms

        Raises:
            ConnectionError: if not connected
            ValueError: if feature_bytes is not length 21 or any byte out of range
            TimeoutError: if no response within RESPONSE_TIMEOUT_S
            RuntimeError: if response SOF is wrong
        """
        if not self.is_connected():
            raise ConnectionError("Not connected to FPGA. Call connect() first.")
        if len(feature_bytes) != 21:
            raise ValueError(f"Expected 21 feature bytes, got {len(feature_bytes)}")
        for b in feature_bytes:
            if not (0 <= b <= 255):
                raise ValueError(f"Feature byte out of range: {b}")

        checksum = self.compute_checksum(feature_bytes)
        packet   = bytes([SOF_REQUEST] + feature_bytes + [checksum])

        self.ser.reset_input_buffer()

        t_start = time.monotonic()
        self.ser.write(packet)

        response   = self.ser.read(4)
        t_end      = time.monotonic()
        latency_ms = (t_end - t_start) * 1000

        if len(response) < 4:
            raise TimeoutError(
                f"FPGA response timeout: expected 4 bytes, got {len(response)}. "
                f"Board may be hung — press BTNC to reset."
            )

        sof, win_raw, spread_raw, status = response

        if sof != SOF_RESPONSE:
            raise RuntimeError(
                f"Bad SOF in response: expected 0x55, got 0x{sof:02X}. "
                f"Possible framing error — press BTNC to reset."
            )

        status_str = STATUS_NAMES.get(status, f"UNKNOWN(0x{status:02X})")
        if status == STATUS_NACK:
            logger.warning("FPGA returned NACK — checksum error detected by board")
        elif status == STATUS_TIMEOUT:
            logger.warning("FPGA returned TIMEOUT — MLP did not complete (board watchdog, AUDIT §5.2)")
        elif status != STATUS_OK:
            logger.warning(f"FPGA returned unknown status 0x{status:02X}")

        win_prob = win_raw / 256.0
        spread   = spread_raw if spread_raw < 128 else spread_raw - 256

        result = {
            'win_prob':    win_prob,
            'spread':      spread,
            'win_pct_str': f"{win_prob:.1%}",
            'status':      status_str,
            'raw_win':     win_raw,
            'raw_spread':  spread_raw,
            'latency_ms':  latency_ms,
        }

        logger.info(
            f"Inference: win={win_prob:.1%} spread={spread:+d} "
            f"status={status_str} latency={latency_ms:.1f}ms"
        )
        return result

    def flush_host_buffers(self):
        """
        Flush the host-side serial RX/TX buffers. This does NOT reset the FPGA — the
        Basys 3 has no UART reset path; only the physical BTNC button resets the design.
        (The old reset_board() sent a UART break, which the board's uart_rx decodes as
        0x00 byte(s) and can advance the framing FSM mid-packet with garbage — AUDIT §7.1.)
        """
        if self.ser and self.ser.is_open:
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            logger.info("Flushed host serial buffers (FPGA itself is reset only by BTNC)")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()
