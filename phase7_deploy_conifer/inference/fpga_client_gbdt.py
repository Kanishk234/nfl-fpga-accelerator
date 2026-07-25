"""
Host UART driver for the two-stage GBDT accelerator (`top_gbdt` bitstream).

This is the GBDT analog of phase7_deploy/inference/fpga_client.py. The wire
protocol is DIFFERENT and the two are not interchangeable — the GBDT uses raw,
unscaled features (trees are scale-invariant; elo ~1500), so the MLP's
one-byte-per-feature INT8 packet cannot carry them. Every value on the wire is
the full `ap_fixed<24,12>` representation, i.e. round(x * 4096) as a 24-bit
two's-complement integer, LSB first.

  Laptop -> FPGA, 65 bytes:  0xAA | 63 feature bytes | XOR checksum
        feature i = bytes 3i (LSB), 3i+1, 3i+2 (MSB)
  FPGA -> laptop,  8 bytes:  0x55 | win_prob[3B, LSB first] | spread[3B] | status

Results come back full-width on purpose: the board's output is then bit-exact
comparable against phase6_sim_conifer/chain_golden/ — there is no
quantize-to-a-byte tolerance to hide behind like the MLP had.

Protocol source of truth: phase5_fpga_conifer/hdl/uart_framing_conifer.v
"""
import functools
import logging
import time

import serial

logger = logging.getLogger(__name__)

SOF_REQUEST    = 0xAA
SOF_RESPONSE   = 0x55
STATUS_OK      = 0x00
STATUS_NACK    = 0x01   # board detected a checksum error
STATUS_TIMEOUT = 0x02   # board's inference watchdog fired (gbdt_controller, 1 ms)

STATUS_NAMES = {STATUS_OK: "OK", STATUS_NACK: "NACK", STATUS_TIMEOUT: "TIMEOUT"}

N_FEATURES      = 21
N_FEATURE_BYTES = 63    # 21 x 3
RESPONSE_BYTES  = 8
FRAC_BITS       = 12
SCALE           = 1 << FRAC_BITS   # 4096

# At 115200 baud: 65 bytes out + 22 cycles compute + 8 bytes back ~ 6.4 ms of
# line time. 2.0 s is extremely generous — a timeout means hung or disconnected.
RESPONSE_TIMEOUT_S = 2.0


def to_fixed(value: float) -> int:
    """float -> 24-bit two's-complement ap_fixed<24,12> word.

    Must match make_chain_golden.py's quantization EXACTLY (round, not
    truncate). The golden pre-quantizes its inputs the same way, which is what
    makes board-vs-sim bit-exactness achievable — see PHASE6_CONIFER_COMPLETE.md
    "the round-vs-truncate 1-ulp bug".
    """
    q = int(round(value * SCALE))
    if not (-(1 << 23) <= q < (1 << 23)):
        raise ValueError(
            f"value {value} -> {q} does not fit ap_fixed<24,12> "
            f"(range [-2048, +2048))"
        )
    return q & 0xFFFFFF


def from_fixed(word: int, signed: bool = True) -> float:
    """24-bit ap_fixed<24,12> word -> float."""
    word &= 0xFFFFFF
    if signed and word >= (1 << 23):
        word -= (1 << 24)
    return word / SCALE


def pack_features(words: list[int]) -> list[int]:
    """21 fixed-point words -> the 63 wire bytes (LSB first per feature)."""
    if len(words) != N_FEATURES:
        raise ValueError(f"Expected {N_FEATURES} feature words, got {len(words)}")
    out = []
    for w in words:
        w &= 0xFFFFFF
        out += [w & 0xFF, (w >> 8) & 0xFF, (w >> 16) & 0xFF]
    return out


class GBDTClient:

    def __init__(self, port: str, baud: int = 115200):
        self.port = port
        self.baud = baud
        self.ser = None

    def connect(self):
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=RESPONSE_TIMEOUT_S,
            write_timeout=2.0,
        )
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        logger.info(f"Connected to GBDT accelerator on {self.port} at {self.baud} baud")

    def disconnect(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            logger.info("Disconnected")

    def is_connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    @staticmethod
    def compute_checksum(feature_bytes: list[int]) -> int:
        """XOR of the 63 feature bytes (matches uart_framing_conifer.v)."""
        return functools.reduce(lambda a, b: a ^ b, feature_bytes)

    def run_inference(self, feature_bytes: list[int]) -> dict:
        """
        Send 63 feature bytes and return the board's prediction.

        Returns dict with: win_prob, spread, win_pct_str, status,
                           raw_win, raw_spread (24-bit words), latency_ms

        Raises ConnectionError / ValueError / TimeoutError / RuntimeError.
        """
        if not self.is_connected():
            raise ConnectionError("Not connected. Call connect() first.")
        if len(feature_bytes) != N_FEATURE_BYTES:
            raise ValueError(
                f"Expected {N_FEATURE_BYTES} feature bytes, got {len(feature_bytes)}"
            )
        for b in feature_bytes:
            if not (0 <= b <= 255):
                raise ValueError(f"Feature byte out of range: {b}")

        checksum = self.compute_checksum(feature_bytes)
        packet = bytes([SOF_REQUEST] + list(feature_bytes) + [checksum])

        self.ser.reset_input_buffer()

        t_start = time.monotonic()
        self.ser.write(packet)
        response = self.ser.read(RESPONSE_BYTES)
        latency_ms = (time.monotonic() - t_start) * 1000

        if len(response) < RESPONSE_BYTES:
            raise TimeoutError(
                f"Response timeout: expected {RESPONSE_BYTES} bytes, got "
                f"{len(response)}. Board may be hung — press BTNC to reset."
            )

        if response[0] != SOF_RESPONSE:
            raise RuntimeError(
                f"Bad SOF in response: expected 0x55, got 0x{response[0]:02X}. "
                f"Framing desync — press BTNC to reset."
            )

        raw_win = response[1] | (response[2] << 8) | (response[3] << 16)
        raw_spread = response[4] | (response[5] << 8) | (response[6] << 16)
        status = response[7]

        status_str = STATUS_NAMES.get(status, f"UNKNOWN(0x{status:02X})")
        if status == STATUS_NACK:
            logger.warning("Board returned NACK — checksum error")
        elif status == STATUS_TIMEOUT:
            logger.warning("Board returned TIMEOUT — gbdt_controller watchdog fired")
        elif status != STATUS_OK:
            logger.warning(f"Board returned unknown status 0x{status:02X}")

        # win_prob is unsigned by construction (sigmoid ROM output is in [0,1));
        # spread is signed — it can favour either team.
        win_prob = from_fixed(raw_win, signed=False)
        spread = from_fixed(raw_spread, signed=True)

        result = {
            'win_prob': win_prob,
            'spread': spread,
            'win_pct_str': f"{win_prob:.1%}",
            'status': status_str,
            'raw_win': raw_win,
            'raw_spread': raw_spread,
            'latency_ms': latency_ms,
        }
        logger.info(
            f"Inference: win={win_prob:.1%} spread={spread:+.3f} "
            f"status={status_str} latency={latency_ms:.1f}ms"
        )
        return result

    def run_inference_floats(self, values: list[float]) -> dict:
        """Convenience: 21 raw float features -> quantize -> run_inference."""
        return self.run_inference(pack_features([to_fixed(v) for v in values]))

    def flush_host_buffers(self):
        """
        Flush host-side serial buffers. Does NOT reset the FPGA — only the
        physical BTNC does. (Sending a UART break injects 0x00 bytes that the
        framing FSM decodes as data mid-packet — AUDIT §7.1.)
        """
        if self.ser and self.ser.is_open:
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            logger.info("Flushed host serial buffers (board resets only via BTNC)")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()
