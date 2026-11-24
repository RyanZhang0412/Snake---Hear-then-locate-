"""Receive the existing XVF1/XVF2 UDP wire format and screen common windows."""
from collections import deque
import re
import socket
import struct
import sys
import time
from pathlib import Path

import numpy as np

from localize import SLOT_HOP, SLOT_WINDOW, unpack_window

HEADER = struct.Struct("<4sIQHHQI")
UDP_PORT = 54322


def packet_fields(packet):
    if len(packet) < HEADER.size:
        raise ValueError("short header")
    magic, sequence, start, count, size, delivered, overflows = HEADER.unpack_from(packet)
    compressed = bool(size & 0x8000)
    data = packet[HEADER.size:]
    if magic not in (b"XVF1", b"XVF2") or not 1 <= count <= 128 or len(data) != (size & 0x7fff):
        raise ValueError("invalid packet")
    if compressed:
        words, word, shift = [], 0, 0
        for byte in data:
            if shift == 28 and byte > 15:
                raise ValueError("invalid varint")
            word |= (byte & 127) << shift
            if byte & 128:
                shift += 7
            else:
                words.append((word >> 1) ^ -(word & 1))
                word, shift = 0, 0
        if shift or len(words) != count * 2:
            raise ValueError("wrong number of words")
        block = np.array(words, dtype="<i4")
    else:
        if len(data) != count * 8:
            raise ValueError("wrong packet size")
        block = np.frombuffer(data, dtype="<i4")
    return int(magic == b"XVF2"), (start, count, sequence, delivered, overflows, block.reshape(count, 2))


class CommonWindows:
    def __init__(self):
        self.pending = [{}, {}]
        self.chunks = deque()
        self.total = 0
        self.previous = None
        self.gaps = 0
        self.format_skips = 0

    def add(self, board, packet):
        start, count, _, _, _, _ = packet
        if self.previous and start < self.previous[0] + self.previous[1]:
            return None, False
        other = self.pending[1 - board].pop(start, None)
        if other is None:
            pending = self.pending[board]
            pending[start] = packet
            if len(pending) > 96:
                pending.pop(next(iter(pending)))
            return None, False
        if other[1] != count or abs(other[3] - packet[3]) > 2400:
            return None, False
        packets = (packet, other) if board == 0 else (other, packet)
        sequences = tuple(p[2] for p in packets)
        overflows = tuple(p[4] for p in packets)
        overflow = bool(self.previous and overflows != self.previous[3])
        if self.previous:
            old_start, old_count, old_seq, _ = self.previous
            if (start != old_start + old_count or overflow
                    or sequences != tuple((i + 1) & 0xffffffff for i in old_seq)):
                self.gaps += 1
                self.chunks.clear()
                self.total = 0
        self.previous = start, count, sequences, overflows
        self.chunks.append(np.column_stack((packets[0][-1], packets[1][-1])))
        self.total += count
        if self.total < SLOT_WINDOW:
            return None, overflow
        origin = (start + count - self.total) // 3
        window = np.concatenate(self.chunks)[:SLOT_WINDOW]
        remaining = SLOT_HOP
        while len(self.chunks[0]) <= remaining:
            remaining -= len(self.chunks.popleft())
        if remaining:
            self.chunks[0] = self.chunks[0][remaining:]
        self.total -= SLOT_HOP
        a, b = unpack_window(window[:, :2]), unpack_window(window[:, 2:])
        if a is None or b is None:
            self.format_skips += 1
            return None, overflow
        return (np.column_stack((a[0], b[0])), (b[1] - a[1]) / 3, origin), overflow


def prepare_esp(port, status, cancelled):
    """Run the same COM/Wi-Fi startup used in the Windows captures."""
    import serial
    from serial.tools import list_ports
    from windows_wifi import connected_credentials

    if port is None:
        ports = [p.device for p in list_ports.comports() if (p.vid, p.pid) == (0x1a86, 0x7523)]
        if len(ports) != 1:
            raise RuntimeError("请接上 ESP32 的 USB，或用 --serial COM14 指定端口")
        port = ports[0]
    status("正在检查 ESP32 和两块麦克风板")
    link = serial.Serial(port=None, baudrate=115200, timeout=.1)
    link.port, link.dtr, link.rts = port, False, False
    link.open()

    def command(line, seconds):
        link.write(("\n" + line + "\n").encode())
        end = time.monotonic() + seconds
        data = bytearray()
        while time.monotonic() < end and not cancelled.is_set():
            data.extend(link.read(4096))
        if cancelled.is_set():
            raise InterruptedError()
        return data.decode(errors="replace")

    try:
        link.rts = True
        time.sleep(.15)
        link.rts = False
        if cancelled.wait(2):
            raise InterruptedError()
        link.reset_input_buffer()
        diagnostic = command("DIAG", 1.3)
        if any(x not in diagnostic for x in ("Board 0 firmware 1.0.9", "Board 1 firmware 3.2.1")):
            raise RuntimeError("ESP32 未读到两块板的预期固件版本，请确认 USB 与 A/B 接线")
        control = command("CONTROL", 2)
        if not all(f"CONTROL board {i} result: ESP_OK" in control for i in range(2)):
            raise RuntimeError("麦克风路由设置失败")
        status("正在连接 Wi-Fi")
        ssid, key = connected_credentials()
        try:
            wifi = command("WIFI " + ssid.encode().hex() + " " + key.encode().hex(), 12)
        finally:
            del ssid, key
        match = re.search(r"Station IP: ([0-9.]+)", wifi)
        if match is None:
            raise RuntimeError("ESP32 未连接到 Windows 当前的 Wi-Fi")
        start = command("START", 1.5)
        state = command("STATUS", .5)
        counts = [int(v) for v in re.findall(r"Board [01] frames=(\d+)", state)]
        if "START result: ESP_OK" not in start or len(counts) != 2 or min(counts) < 10000:
            raise RuntimeError("I²S 没有持续收帧，请检查 BCLK/LRCK")
        return link, match[1]
    except BaseException:
        link.close()
        raise


def receive(host, cancelled, on_window, on_status, on_overflow):
    address = (socket.gethostbyname(host), UDP_PORT)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2 * 1024 * 1024)
        sock.settimeout(.5)
        sock.sendto(b"HELLO", address)
        windows = CommonWindows()
        last_packet = [0., 0.]
        last_counters = 0.
        last_hello = time.monotonic()
        try:
            while not cancelled.is_set():
                try:
                    data, peer = sock.recvfrom(2048)
                except socket.timeout:
                    data, peer = b"", None
                now = time.monotonic()
                if now - min(last_packet) > 1.5 and now - last_hello > 1:
                    sock.sendto(b"HELLO", address)
                    last_hello = now
                if peer and peer[0] == address[0]:
                    try:
                        board, packet = packet_fields(data)
                    except ValueError:
                        continue
                    last_packet[board] = now
                    result, overflow = windows.add(board, packet)
                    if overflow:
                        on_overflow()
                    if result is not None:
                        on_window(result)
                if now - last_counters > .25:
                    on_status([now - instant < 1.5 for instant in last_packet],
                              windows.gaps, windows.format_skips)
                    last_counters = now
        finally:
            sock.sendto(b"STOP", address)
