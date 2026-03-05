from typing import Tuple, Callable
from bleak import BleakClient, BleakScanner, BLEDevice, BleakGATTCharacteristic, BleakError
import traceback
import asyncio

from homeassistant.components.light import ColorMode

from .const import LOGGER

WRITE_CHARACTERISTIC_UUIDS = ["8b00ace7-eb0b-49b0-bbe9-9aee0a26e1a3"]
READ_CHARACTERISTIC_UUIDS  = ["0734594a-a8e7-4b1a-a6b1-cd5243059a57"]

async def discover():
    """Discover Bluetooth LE devices."""
    devices = await BleakScanner.discover()
    LOGGER.debug("Discovered devices: %s", [{"address": device.address, "name": device.name} for device in devices])
    return [device for device in devices if device.name and device.name.lower().startswith("tl100")]

def create_status_callback(future: asyncio.Future):
    def callback(sender: int, data: bytearray):
        if not future.done():
            future.set_result(data)
    return callback

async def get_device(mac: str) -> BLEDevice:
    devices = await BleakScanner.discover()
    LOGGER.debug(f"Discovered devices: {devices}")
    return next((device for device in devices if device.address.lower()==mac.lower()),None)

class BeurerInstance:
    def __init__(self, device: BLEDevice) -> None:
        self._mac = device.address
        #device = get_device(self._mac)
        if device == None:
            LOGGER.error(f"Was not able to find device with mac {self._mac}")
        self._device = BleakClient(device,  disconnected_callback=self.disconnected_callback)
        self._trigger_update = None
        self._is_on = False
        self._light_on = None
        self._color_on = None
        self._rgb_color = (0,0,0)
        self._brightness = None
        self._color_brightness = None
        self._effect = None
        self._write_uuid = None
        self._read_uuid = None
        self._mode = None
        self._supported_effects = ["Off", "Random", "Rainbow", "Rainbow Slow", "Fusion", "Pulse", "Wave", "Chill", "Action", "Forest", "Summer"]
        asyncio.create_task(self.connect())

    def disconnected_callback(self, client):
        LOGGER.debug("Disconnected callback called!")
        self._is_on = False
        self._light_on = False
        self._color_on = False
        self._write_uuid = None
        self._read_uuid = None
        asyncio.create_task(self.trigger_entity_update())

    def set_update_callback(self, trigger_update: Callable):
        LOGGER.debug(f"Setting update callback to {trigger_update}")
        self._trigger_update = trigger_update

    async def _write(self, data: bytearray):
        LOGGER.debug("Sending in write: " + ''.join(format(x, ' 03x') for x in data)+f" to characteristic {self._write_uuid}, device is {self._device.is_connected}")
        try:
            if (not self._device.is_connected) or (self._write_uuid == None):
                await self._device.connect(timeout=20)
            await self._device.write_gatt_char(self._write_uuid, data)
        except (BleakError) as error:
            track = traceback.format_exc()
            LOGGER.debug(track)
            LOGGER.warn(f"Error while trying to write to device: {error}")
            await self.disconnect()

    @property
    def mac(self):
        return self._mac

    @property
    def is_on(self):
        return self._is_on

    @property
    def rgb_color(self):
        return self._rgb_color

    @property
    def color_brightness(self):
        return self._color_brightness

    @property
    def white_brightness(self):
        return self._brightness

    @property
    def effect(self):
        return self._effect

    @property
    def color_mode(self):
        return self._mode

    @property
    def supported_effects(self):
        return self._supported_effects

    def find_effect_position(self, effect) -> int:
        try:
            return self._supported_effects.index(effect)
        except ValueError:
            #return Off if not found
            return 0

    def makeChecksum(self, b: int, bArr: list[int]) -> int:
        for b2 in bArr:
            b = b ^ b2
        return b

    async def sendPacket(self, message: list[int]):
          #LOGGER.debug(f"Sending packet with length {message.length}: {message}")
        if not self._device.is_connected:
            await self.connect()
        length=len(message)
        checksum = self.makeChecksum(length+2,message) #Plus two bytes
        packet=[0xFE,0xEF,0x0A,length+7,0xAB,0xAA,length+2]+message+[checksum,0x55,0x0D,0x0A]
        print("Sending message"+''.join(format(x, ' 03x') for x in packet))
        await self._write(packet)

    async def set_color(self, rgb: Tuple[int, int, int]):
        r, g, b = rgb
        LOGGER.debug(f"Setting to color: %s, %s, %s", r, g, b)
        self._mode = ColorMode.RGB
        self._rgb_color = (r,g,b)
        if not self._color_on:
            await self.turn_on()
        #Send color
        await self.sendPacket([0x32,r,g,b])
        await asyncio.sleep(0.1)
        await self.triggerStatus()

    async def set_color_brightness(self, brightness: int):
        LOGGER.debug(f"Setting to brightness {brightness}")
        self._mode = ColorMode.RGB
        if not self._color_on:
            await self.turn_on()
        #Send brightness
        await self.sendPacket([0x31,0x02,int(brightness/255*100)])
        await asyncio.sleep(0.1)
        await self.triggerStatus()

    async def set_white(self, intensity: int):
        LOGGER.debug(f"Setting white to intensity: %s", intensity)
        #self._brightness = intensity
        self._mode = ColorMode.WHITE
        if not self._light_on:
            await self.turn_on()
        await self.sendPacket([0x31,0x01,int(intensity/255*100)])
        await asyncio.sleep(0.2)
        self.set_effect("Off")
        await self.triggerStatus()

    async def set_effect(self, effect: str):
        LOGGER.debug(f"Setting effect {effect}")
        self._mode = ColorMode.RGB
        if not self._color_on:
            await self.turn_on()
        await self.sendPacket([0x34,self.find_effect_position(effect)])
        await self.triggerStatus()

    async def turn_on(self):
        LOGGER.debug("Turning on")
        if not self._device.is_connected:
            await self.connect()
        #WHITE mode
        if self._mode == ColorMode.WHITE:
            await self.sendPacket([0x37,0x01])
        #COLOR mode
        else:
            await self.sendPacket([0x37,0x02])
            LOGGER.debug(f"Current color state: {self._color_on}, {self._rgb_color}, {self._color_brightness}, {self._effect}")
            #Lamp wants to turn on on rainbow mode when enabling mood light, so send last status
            if not self._color_on:
                LOGGER.debug(f"Restoring last known color state")
                self._color_on = True
                await asyncio.sleep(0.2)
                await self.set_effect(self._effect)
                await asyncio.sleep(0.2)
                await self.set_color(self._rgb_color)
                await self.set_color_brightness(self._color_brightness)
        await asyncio.sleep(0.2)
        await self.triggerStatus()

    async def turn_off(self):
        LOGGER.debug("Turning off")
        #turn off white
        await self.sendPacket([0x35,0x01])
        #turn off color
        await self.sendPacket([0x35,0x02])
        await asyncio.sleep(0.1)
        await self.triggerStatus()

    async def triggerStatus(self):
        #Trigger notification with current values
        await self.sendPacket([0x30,0x01])
        await asyncio.sleep(0.2)
        await self.sendPacket([0x30,0x02])
        LOGGER.info(f"Triggered update")

    async def trigger_entity_update(self):
        if self._trigger_update:
            LOGGER.debug(f"Triggering async update")
            self._trigger_update()
        else:
            LOGGER.warn(f"No async update function provided: {self._trigger_update}")

    #We receive status version 1 then version 2.
    # So changes to the light status shall only be done in version 2 handler
    async def notification_handler(self, characteristic: BleakGATTCharacteristic, res: bytearray):
        """Simple notification handler which prints the data received."""
        #LOGGER.info("Received notification %s: %r", characteristic.description, res)
        LOGGER.debug("Received notification: " + ''.join(format(x, ' 03x') for x in res))
        if len(res) < 9:
            return
        reply_version = res[8]
        LOGGER.debug(f"Reply version is {reply_version}")
        #Short version with only _brightness
        if reply_version == 1:
            self._light_on = True if res[9] == 1 else False
            if res[9] == 1:
                self._brightness = int(res[10]*255/100) if res[10] > 0 else None
                self._mode = ColorMode.WHITE
            #self._is_on = self._light_on or self._color_on
            LOGGER.debug(f"Short version, on: {self._is_on}, brightness: {self._brightness}")
        #Long version with color information
        elif reply_version == 2:
                self._color_on = True if res[9] == 1 else False
                if res[9] == 1:
                    self._mode = ColorMode.RGB
                    #effect will be turned off if light off, update only if light on
                    self._effect = self._supported_effects[res[16]]
                self._color_brightness = int(res[10]*255/100) if res[10] > 0 else None
                self._rgb_color = (res[13], res[14], res[15])
                self._is_on = self._light_on or self._color_on
                LOGGER.debug(f"Long version, on: {self._is_on}, brightness: {self._color_brightness}, rgb color: {self._rgb_color}, effect: {self._effect}")
                LOGGER.debug(f"res: {res[9]}, light_on {self._light_on}, color_on {self._color_on}")
                await self.trigger_entity_update()
        #Device turned off
        elif reply_version == 255:
                self._is_on = False
                self._light_on = False
                self._color_on = False
                LOGGER.debug(f"Device off")
                await self.trigger_entity_update()
        #Device is going to shutdown
        elif reply_version == 0:
            LOGGER.debug(f"Device is going to shut down")
            await self.disconnect()
            return
        else:
            LOGGER.debug(f"Received unknown notification")
            return

    async def connect(self) -> bool:
        LOGGER.debug(f"Going to connect to device")
        try:
            if not self._device.is_connected:
                await self._device.connect(timeout=20)
                await asyncio.sleep(0.1)

                for char in self._device.services.characteristics.values():
                    if char.uuid in WRITE_CHARACTERISTIC_UUIDS:
                        self._write_uuid = char.uuid
                    if char.uuid in READ_CHARACTERISTIC_UUIDS:
                        self._read_uuid = char.uuid

                if not self._read_uuid or not self._write_uuid:
                    LOGGER.error("No supported read/write UUIDs found")
                    return False

                LOGGER.info(f"Read UUID: {self._read_uuid}, Write UUID: {self._write_uuid}")

            await asyncio.sleep(0.1)
            LOGGER.info(f"Starting notifications")

            await self._device.start_notify(self._read_uuid, self.notification_handler)

            await self.triggerStatus()
            await asyncio.sleep(0.1)
        except (Exception) as error:
            track = traceback.format_exc()
            LOGGER.debug(track)
            LOGGER.error(f"Error connecting: {error}")
            await self.disconnect()
            return False
        await asyncio.sleep(0.1)
        return True

    async def update(self):
        try:
            if not self._device.is_connected:
                if not await self.connect():
                    LOGGER.info("Was not able to connect to device for updates")
                    await self.disconnect()
                    return
            await self._device.start_notify(self._read_uuid, self.notification_handler)

            LOGGER.info(f"Triggering update")

            await self.triggerStatus()
            #await asyncio.sleep(0.1)

        except (Exception) as error:
            track = traceback.format_exc()
            LOGGER.debug(track)
            LOGGER.error(f"Error getting status: {error}")
            await self.disconnect()

    async def disconnect(self):
        LOGGER.debug("Disconnecting")
        if self._device.is_connected:
            await self._device.disconnect()
        self._is_on = False
        self._light_on = False
        self._color_on = False
        await self.trigger_entity_update()
