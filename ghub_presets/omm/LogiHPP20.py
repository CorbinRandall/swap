import sys, os, struct
from .HidppFeatures import Feature
from .utils import pretty_list, pretty_list2
if sys.platform == 'win32':
    _omm_dir = os.path.dirname(os.path.abspath(__file__))
    _dll_dir = os.path.join(_omm_dir, 'x64' if struct.calcsize("P") * 8 == 64 else 'x86')
    # Vendored hidapi.dll folder is optional — pip's hidapi package embeds its own.
    if os.path.isdir(_dll_dir):
        os.add_dll_directory(_dll_dir)

#hid.dll binary from
#https://github.com/libusb/hidapi

#py hid binding:
#https://github.com/apmorton/pyhidapi
import hid
from . import hid_compat

class LogiHPP20:
    def __init__(self, pid = 0, name = '', serial = '', index_list = []):
        """init hidpp device

        Args:
            pid (int): usb pid
            name (str): partial name string
            serial (str): serial number optional.
            index_list (list, optional): a list of possible connection id. 
                    0 for bluetooth, 0xFF for wired, 1-6 for receiver. Defaults to [0xFF].
        """
        assert pid > 0 or name, 'error: pid or name muse be set'
        self.debug = False
        self.wireless_receiver = False
        vid = 0x046D
        self.swid = 0xF
        self.port_short = None
        self.port_long = None
        self.port_very_long = None
        self.functions = []
        self.SHORT_REGS = [0x80, 0x81]  #RAP registers: 80 set 81 get
        self.LONG_REGS = [0x82, 0x83]   #82 set 83 get
        self.product_name = ''
        self.feature_index = {0:0}
        # HID read tuning — discovery uses short timeouts so a sleeping
        # Lightspeed mouse cannot block the UI for minutes (6 indices × 15s).
        self.read_timeout_ms = 5000
        self.read_attempts = 3
        list_short = []
        list_long = []
        list_very_long = []
        devs = hid_compat.enumerate_devices(vid, pid)
        #print(devs)
        for dev in devs:
            if serial and dev['serial_number'] != serial:
                continue
            if dev['usage_page'] >= 0xFF00:
                h = hid_compat.open_device(dev['path'])
                data = hid_compat.get_report_descriptor(h)
                if len(data) > 10 and 0x85 in data:
                    if b"\x85\x10" in data:
                        list_short.append((dev["path"], dev["product_id"]))
                    if b"\x85\x11" in data:
                        list_long.append((dev["path"], dev["product_id"]))
                    if b"\x85\x12" in data:
                        list_very_long.append((dev["path"], dev["product_id"]))
                h.close()
        list_long = list(set(list_long))
        #print(list_long)
        path_long, dev_name_hidpp, product_id = self.detect_device(list_long, name, index_list)
        if not list_long:
            raise AssertionError(
                "error while opening device! No HID++ long-report interface for this PID."
            )
        if not path_long:
            raise AssertionError(
                "error while opening device! Receiver is present but no awake mouse "
                "answered HID++ (power on / wiggle the mouse). If Logitech G HUB HID "
                "Driver shows 'activated disabled' under Login Items → Driver "
                "Extensions, reboot once so it fully unloads."
            )
        self.port_long = hid_compat.open_device(path_long)
        product = (getattr(self.port_long, "product", None) or "").lower()
        self.wireless_receiver = "receiver" in product
        print(f'{dev_name_hidpp} pid 0x{product_id:04X} at 0x{self.device_index:02X}')
        #print('device info', self.device_index, dev_name_hidpp,'\n')

    def close(self):
        for p in [self.port_short, self.port_long, self.port_very_long]:
            if p is not None:
                p.close()

    def detect_device(self, long_path_list, name, _dev_index_list):
        path_long = None
        dev_name_hidpp = ''
        product_id = ''
        prev_timeout = self.read_timeout_ms
        prev_attempts = self.read_attempts
        # Probe fast: one short read per index. Full 5s×3 retries stay for
        # post-open feature calls once the device is known alive.
        self.read_timeout_ms = 400
        self.read_attempts = 1
        try:
            for path, pid in long_path_list:
                #print(path, pid)
                if path_long:
                    break
                dev = hid_compat.open_device(path)
                self.port_long = dev
                is_receiver = 'receiver' in dev.product.lower()
                if not _dev_index_list:
                    dev_index_list = [1,2,3,4,5,6] if is_receiver else [255,0]
                else:
                    dev_index_list = _dev_index_list
                for i in dev_index_list:
                    if is_receiver:
                        print(f'checking receiver 046D:{pid:04X} sub-id {i}')
                    self.device_index = i
                    try:
                        dev_name = self.get_device_name()
                    except Exception as probe_exc:
                        # TCC / SetReport denial is definitive — don't keep probing.
                        from .hid_compat import HidWriteDenied

                        if isinstance(probe_exc, HidWriteDenied):
                            dev.close()
                            raise
                        continue
                    if not dev_name:
                        continue
                    #print(f'{dev_name} at {i}')
                    if (name and name in dev_name) or not name:
                        path_long = path
                        dev_name_hidpp = dev_name
                        product_id = pid
                        break
                dev.close()
        finally:
            self.read_timeout_ms = prev_timeout
            self.read_attempts = prev_attempts
        return path_long, dev_name_hidpp, product_id
        
    @staticmethod
    def list_devices(pid = 0):
        try:
            devs = hid.enumerate()
        except TypeError:
            devs = hid_compat.enumerate_devices(0x046D, pid or 0)
        devs = [d for d in devs if d.get("vendor_id") == 0x046D]
        if pid:
            devs = [d for d in devs if d.get("product_id") == pid]
        sn = set()
        for dev in devs:
            if dev['serial_number'] not in sn:
                sn.add(dev['serial_number'])
                print(dev['manufacturer_string'], dev['product_string'])
                print('SN: ', dev['serial_number'])
                print(f"PID  0x{dev['product_id']:04X}\n")

    @staticmethod
    def is_receiver(pid):
        """detect if a usb dev is receiver by checking product name string.

        Args:
            pid (int): usb pic

        Returns:
            bool: True if "receiver" in product name
        """
        devs = hid_compat.enumerate_devices(0x046D, pid)
        for dev in devs:
            product = (dev.get("product_string") or "").lower()
            if "receiver" in product:
                return True
        return False

    def get_feature_list(self):
        data = list(self.call_feature(1, 0))
        features = []
        for function_index in range(0, data[4]+1):
            out = list(self.call_feature(1, 1, [function_index]))
            features.append(out[4]<< 8 | out[5])
        return features

    @staticmethod
    def _normalize_hidpp_response(sent, out):
        """Normalize HID++ readbacks; Lightspeed wireless can shift the header by one byte."""
        if not out:
            return None
        sent_hdr = list(sent[:4])
        out_list = list(out)
        # 0xFF sub_id marks an HID++ error packet — return as-is for error parsing.
        if len(out_list) >= 3 and out_list[2] == 0xFF:
            return out
        if out_list[:4] == sent_hdr:
            return out
        if (
            len(out_list) >= 5
            and out_list[0] == 0x11
            and out_list[1] == sent_hdr[1]
            and out_list[3] == sent_hdr[2]
            and out_list[4] == sent_hdr[3]
        ):
            normalized = [out_list[0], out_list[1], out_list[3], out_list[4]] + out_list[5:]
            return bytes((normalized + [0] * 20)[:20])
        return None

    def ping_device(self, data, read_back = False):
        if self.port_short is not None and data[0] == 0x10 and len(data) <= 7:
            data = (data + [0]*7)[:7]
            self.port_short.write(bytes(data))
        else:
            data = (data + [0]*20)[:20]
            data[0] = 0x11
            self.port_long.write(bytes(data))

        #RAP w/short register, read from short
        #everything else from long
        data[0] = 0x11
        if not read_back:
            return []

        sent = bytes(data)
        out = None
        for _attempt in range(max(1, int(self.read_attempts))):
            out = self.port_long.read(size=255, timeout=int(self.read_timeout_ms))
            normalized = self._normalize_hidpp_response(sent, out)
            if normalized is not None:
                if self.debug:
                    print('fap ping:')
                    print(pretty_list2(data))
                    print(pretty_list2(normalized))
                return normalized
        if self.debug:
            print('fap ping:')
            print(pretty_list2(data))
            print(pretty_list2(out) if out else 'no readback')
        return None
    
    def find_feature_index(self, val):
        if val in self.feature_index:
            return self.feature_index.get(val)
        out = self.call_feature(0, 0, list(struct.pack(">H", val)))
        if out and out[4] > 0:
            self.feature_index[val]  = out[4]
            return out[4]
        else:
            return 0xFF

    def has_feature(self, val):
        return self.find_feature_index(val) != 0xFF

    def call_feature(self, feature_val, func_id, params = [0], read_back = True):
        feature_idx = self.find_feature_index(feature_val)
        if feature_idx == 0xFF:
            return None
        if isinstance(params, bytes) or isinstance(params, bytearray):
            params_arr = list(params)
        elif isinstance(params, list):
            params_arr = params
        else:
            raise Exception('wrong params')
        
        data = [0x10, self.device_index, feature_idx, func_id << 4 | self.swid] + params_arr
        return self.ping_device(data, read_back)

    def hidpp20_info(self, prop=''):
        dev = self.port_long
        if prop == 'product':
            return dev.product
        elif prop == 'serial':
            return dev.serial
        elif prop == 'protocol':
            return self.protocol()
        else:
            return f'{dev.product}\nSN: {dev.serial}\nProtocol {self.protocol()}\n'
    
    def protocol(self):
        data = list(self.call_feature(0, 1))
        if data[4] == 4:
            desc = 'hid++ 2.0'
        elif data[4] == 2:
            desc = 'hid++ 2.0 - legacy'
        elif data[4] == 0x8f:
            desc = 'hid++ 1.0'
        else:
            desc = 'unknown'
        return f'{str(data[4])}.{str(data[5])} {desc}'


    def get_device_name(self):
        """use feature 0005 to get device name
        """
        if not self.has_feature(Feature.device_name):
            return None
        out = self.call_feature(Feature.device_name, 0, [0])
        name_length = out[4]
        name = b''
        while len(name) < name_length:
            frag = self.call_feature(Feature.device_name, 1, [len(name)])
            if frag:
                name += frag[4:4+name_length - len(name)]
            else:
                print('error while reading name')
                return None
        return name.decode('utf-8')