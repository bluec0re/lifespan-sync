import asyncio
from urllib import response
from bleak import BleakScanner, BleakClient
from bleak.exc import BleakError
import logging

logger = logging.getLogger(__name__)

TARGET_DEVICE_NAME = "LifeSpan"
CHARACTERISTIC_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"

INIT_SEQUENCE = [
    b"\x02\x00\x00\x00\x00",
]

CMDS = {
    "start": b"\xe1\x00\x00\x00\x00",
    "stop": b"\xe0\x00\x00\x00\x00",
}

INFO_QUERIES = {
    # 'steps' query according to discovered protocol.
    "steps": b"\xa1\x88\x00\x00\x00",
    "distance": b"\xa1\x85\x00\x00\x00",
    "time": b"\xa1\x89\x00\x00\x00",
    "speed": b"\xa1\x82\x00\x00\x00",
    "calories": b"\xa1\x87\x00\x00\x00",
    "unknown": b"\xa1\x81\x00\x00\x00",
    "state": b"\xa1\x91\x00\x00\x00",
}

INIT_QUERIES = {
    "profile": b"\x52\x00\x00\x00\x00",
    "unit": b"\xa1\x81\x00\x00\x00",
}

SET_SPEED_CMD = b"\xd0"
SET_WEIGHT_CMD = b"\xe4"

UNIT_METRIC = 0xAA
UNIT_IMPERIAL = 0xFF

STATE_MAPPING = {
    1: "Idling",
    2: "Summary",
    3: "Run",
    5: "Pause",
    9: "Edit",
    10: "Safe Key Loss",
    15: "Error",
    16: "Test or Engerineer",
}

VALUE_MULTIPLIER = 256
SPEED_DIVISOR = 100.0
DISTANCE_DIVISOR = 100.0


class TreadmillClient:
    def __init__(self, update_callback=None):
        """
        update_callback is called with (key, value) pairs
        e.g., update_callback("speed", 2.5)
        """
        self.client = None
        self.device_address = None
        self.update_callback = update_callback
        self.is_connected = False
        self._disconnect_event = asyncio.Event()
        self._query_queue = asyncio.Queue()
        self._current_query = None

    async def connect(self):
        logger.info("Discovering device...")
        devices = await BleakScanner.discover()
        for d in devices:
            if d.name and TARGET_DEVICE_NAME in d.name:
                self.device_address = d.address
                break

        if not self.device_address:
            raise BleakError("LifeSpan treadmill not found")

        logger.info(f"Connecting to {self.device_address}...")
        self.client = BleakClient(
            self.device_address, disconnected_callback=self._handle_disconnect
        )
        await self.client.connect()
        self.is_connected = True
        logger.info("Connected.")

        # send init sequence
        for cmd in INIT_SEQUENCE:
            await self.client.write_gatt_char(CHARACTERISTIC_UUID, cmd)
            await asyncio.sleep(0.5)

        logger.info("Initializing notifications...")
        await self.client.start_notify(CHARACTERISTIC_UUID, self._handle_rx)

        for name, query in INIT_QUERIES.items():
            self._current_query = name
            await self.client.write_gatt_char(
                CHARACTERISTIC_UUID, query, response=False
            )
            await asyncio.sleep(0.5)

    def _handle_disconnect(self, client):
        logger.warning(f"Disconnected from {client.address}!")
        self.is_connected = False
        self.client = None
        self._disconnect_event.set()
        if self.update_callback:
            self.update_callback("status", "disconnected")

    async def disconnect(self):
        if self.client and self.client.is_connected:
            await self.client.disconnect()

    def _handle_rx(self, sender, data):
        query_type = self._current_query
        self._current_query = None  # Reset

        if not query_type:
            return  # Received without querying or init callback

        val = None
        if query_type == "state":
            state_byte = data[2]
            val = STATE_MAPPING.get(state_byte, f"Unknown (0x{state_byte:02x})")
        elif query_type == "speed":
            val = data[2] + (data[3] / SPEED_DIVISOR)
        elif query_type == "distance":
            val = data[2] + (data[3] / DISTANCE_DIVISOR)
        elif query_type == "time":
            h, m, s = list(data[2:5])
            val = f"{h:d}:{m:02d}:{s:02d}"
        elif query_type == "steps":
            # Just guessing step format based on others. Often 2-3 bytes.
            val = data[2] * VALUE_MULTIPLIER + data[3]  # 16-bit int example, may need tweaking
        elif query_type == "calories":
            val = data[2] * VALUE_MULTIPLIER + data[3]  # 16-bit int example
        elif query_type == "unit":
            unit_byte = data[2]
            if unit_byte == UNIT_METRIC:
                val = "metric"
            elif unit_byte == UNIT_IMPERIAL:
                val = "imperial"
            else:
                val = f"unknown (0x{unit_byte:02x})"

        if self.update_callback and val is not None:
            self.update_callback(query_type, val)

    async def start_polling(self, interval=2.0):
        while self.is_connected:
            for name, query in INFO_QUERIES.items():
                if not self.is_connected:
                    break
                self._current_query = name
                try:
                    await self.client.write_gatt_char(
                        CHARACTERISTIC_UUID, query, response=False
                    )
                except Exception as e:
                    logger.error(f"Error polling {name}: {e}")
                    break
                await asyncio.sleep(interval / len(INFO_QUERIES))

    async def set_speed(self, speed_val):
        if not self.is_connected:
            return
        units = int(speed_val)
        hundredths = int(speed_val * 100) % 100
        cmd = (
            SET_SPEED_CMD
            + units.to_bytes(1, "little")
            + hundredths.to_bytes(1, "little")
            + b"\x00\x00"
        )
        await self.client.write_gatt_char(CHARACTERISTIC_UUID, cmd)

    async def set_weight(self, weight):
        if not self.is_connected:
            return
        cmd = SET_WEIGHT_CMD + weight.to_bytes(2, "little") + b"\x00\x00"
        await self.client.write_gatt_char(CHARACTERISTIC_UUID, cmd)

    async def start_treadmill(self):
        if self.is_connected:
            await self.client.write_gatt_char(CHARACTERISTIC_UUID, CMDS["start"])

    async def stop_treadmill(self):
        if self.is_connected:
            await self.client.write_gatt_char(CHARACTERISTIC_UUID, CMDS["stop"])
