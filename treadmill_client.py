import asyncio
from bleak import BleakScanner, BleakClient
from bleak.exc import BleakError
import logging

logger = logging.getLogger(__name__)

TARGET_DEVICE_NAME = "LifeSpan"
CHARACTERISTIC_UUID = '0000fff1-0000-1000-8000-00805f9b34fb'

INIT_SEQUENCE = [
    b"\x02\x00\x00\x00\x00",
    b"\xC2\x00\x00\x00\x00",
    b"\xE9\xFF\x00\x00\x00",
    b"\xE4\x00\xF4\x00\x00"
]

CMDS = {
    "start": b"\xE1\x00\x00\x00\x00",
    "stop": b"\xE0\x00\x00\x00\x00",
}

INFO_QUERIES = {
    # 'steps' query according to discovered protocol.
    "steps": b"\xA1\x88\x00\x00\x00",
    "distance": b"\xA1\x85\x00\x00\x00",
    "time": b"\xA1\x89\x00\x00\x00",
    "speed": b"\xA1\x82\x00\x00\x00",
    "calories": b"\xA1\x87\x00\x00\x00",
    "unknown": b"\xA1\x81\x00\x00\x00",
    "state": b"\xA1\x91\x00\x00\x00",
}

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
        self.client = BleakClient(self.device_address, disconnected_callback=self._handle_disconnect)
        await self.client.connect()
        self.is_connected = True
        logger.info("Connected.")

        # send init sequence
        for cmd in INIT_SEQUENCE:
            await self.client.write_gatt_char(CHARACTERISTIC_UUID, cmd)
            await asyncio.sleep(0.5)

        logger.info("Initializing notifications...")
        await self.client.start_notify(CHARACTERISTIC_UUID, self._handle_rx)

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
        self._current_query = None # Reset
        
        if not query_type:
            return  # Received without querying or init callback

        val = None
        if query_type == "state":
            state_byte = data[2]
            if state_byte == 0x09:
                val = "Stopped"
            elif state_byte == 0x03:
                val = "Running"
            elif state_byte == 0x05:
                val = "Paused"
            else:
                val = f"Unknown (0x{state_byte:02x})"
        elif query_type == "speed":
            val = data[2] + (data[3] / 100.0)
        elif query_type == "distance":
            val = data[2] + (data[3] / 100.0)
        elif query_type == "time":
            h, m, s = list(data[2:5])
            val = f"{h:d}:{m:02d}:{s:02d}"
        elif query_type == "steps":
            # Just guessing step format based on others. Often 2-3 bytes.
            val = data[2] * 256 + data[3] # 16-bit int example, may need tweaking
        elif query_type == "calories":
            val = data[2] * 256 + data[3] # 16-bit int example

        if self.update_callback and val is not None:
             self.update_callback(query_type, val)

    async def start_polling(self, interval=2.0):
        while self.is_connected:
            for name, query in INFO_QUERIES.items():
                if not self.is_connected:
                    break
                self._current_query = name
                try:
                    await self.client.write_gatt_char(CHARACTERISTIC_UUID, query, response=False)
                except Exception as e:
                    logger.error(f"Error polling {name}: {e}")
                    break
                await asyncio.sleep(interval / len(INFO_QUERIES))

    # keep for reference, but resets the unit to EN
    # async def set_speed(self, speed_kmh):
    #     if not self.is_connected:
    #         return
    #     units = int(speed_kmh)
    #     hundredths = int(speed_kmh * 100) % 100
    #     cmd = b"\xD0" + units.to_bytes(1, 'little') + hundredths.to_bytes(1, 'little') + b"\x00\x00"
    #     await self.client.write_gatt_char(CHARACTERISTIC_UUID, cmd)

    async def start_treadmill(self):
         if self.is_connected:
             await self.client.write_gatt_char(CHARACTERISTIC_UUID, CMDS["start"])

    async def stop_treadmill(self):
         if self.is_connected:
             await self.client.write_gatt_char(CHARACTERISTIC_UUID, CMDS["stop"])
