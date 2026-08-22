import struct
import time
from typing import List, Optional
from .HidppConstants import *
from .utils import crc16_ccitt, pretty_list
from .HidppProfile import Profile
from .HidppFeatures import *


class OnboardWriteError(RuntimeError):
    """HID++ onboard memory write failed (blocked or rejected by device)."""


ROM_PROFILE_SECTOR_FLAG = 0x0100


def _slot_to_user_sector(slot: int) -> int:
    return int(slot)


def _slot_to_rom_sector(slot: int) -> int:
    return ROM_PROFILE_SECTOR_FLAG | int(slot)


def _sector_to_slot(sector: int) -> int:
    sector = int(sector)
    if sector <= 0:
        return 0
    if sector >= ROM_PROFILE_SECTOR_FLAG:
        return sector & 0xFF
    return sector


def _profile_switch_params(sector: int) -> list[int]:
    sector = int(sector)
    return [(sector >> 8) & 0xFF, sector & 0xFF, 0]


_HIDPP_ERROR_NAMES = {
    0x01: "Unknown",
    0x02: "InvalidArgument",
    0x03: "OutOfRange",
    0x04: "HWError",
    0x05: "NotAllowed",
    0x06: "InvalidFeatureIndex",
    0x07: "InvalidFunctionIndex",
    0x08: "Busy",
    0x09: "Unsupported",
    0x0B: "WrongEntity",
    0x0C: "ResourceError",
    0x0D: "RequestUnavailable",
    0x0E: "InvalidParamValue",
    0x0F: "WrongPingCode",
}


def _hidpp_error_code(out) -> Optional[int]:
    if out is None:
        return None
    if len(out) > 2 and out[2] == 0xFF and len(out) > 5:
        return int(out[5])
    return None


def _check_hidpp_response(out, *, context: str) -> None:
    if out is None:
        raise OnboardWriteError(
            f"{context}: no HID++ response (macOS may be blocking SetReport — "
            "disable Logitech G HUB HID Driver under Login Items → Driver Extensions, reboot)"
        )
    code = _hidpp_error_code(out)
    if code:
        label = _HIDPP_ERROR_NAMES.get(code, f"0x{code:02X}")
        raise OnboardWriteError(f"{context}: HID++ {label}")


#https://github.com/libratbag/libratbag/blob/master/src/hidpp20.c
class FeatureOnboardProfile:
    """interface to feature 0x8100, onboard profile
    """
    def __init__(self, dev):
        self.dev = dev
        assert self.dev.has_feature(Feature.onboard_profile), 'unsupported device: no onboard profiles!'
        data = self.dev.call_feature(Feature.onboard_profile, 0, [0])
        #sample output on G502
        #0x11 0xff 0x0c 0x0f 0x01 0x02 0x01 0x05 0x05 0x0b 0x10 0x01 0x00 0x0a 0x01 0x00 0x00 0x00 0x00 0x00
        memory_layout, self.profile_format, macro_format, self.num_profiles, \
            num_profiles_oob, self.num_buttons, self.num_pages, reported_page_size, gshift = \
            struct.unpack('>BBBBBBBHB', data[4:14])
        assert memory_layout == 1, f'unsupported device! {memory_layout}'
        assert self.profile_format <= 5, f'unsupported profile format {self.profile_format}'
        assert macro_format == 1, f'unsupported macro format {macro_format}'
        assert self.num_buttons <= 16, f'too many buttons! {self.num_buttons}'
        # Devices often report sector_size 255 but store a 256-byte sector with
        # CRC in the last two bytes (libratbag hidpp20.c). Keep both:
        #   sector_size = HID++ write/read count (255)
        #   page_size   = in-memory buffer (256)
        self.sector_size = int(reported_page_size)
        self.page_size = 256 if self.sector_size in (255, 256) else int(reported_page_size)
        assert self.page_size in [256, 1024], f'unsupported page size, should be 256 or 1024: {self.page_size}'
        if self.sector_size == 0:
            self.sector_size = self.page_size
        self.num_gbuttons = self.num_buttons if gshift & 0x3 == 0x2 else 0
        self.extended_report_rate = self.dev.has_feature(Feature.extended_report_rate)
        self.profile_list = [{}]
        data = self.read_memory_page(0)
        for i in range(self.num_profiles):
            rom, page, vis = struct.unpack('BBB', data[i*4:i*4+3])
            if rom == 0xFF:
                print(f'profile {i+1} is disabled, run "omm.py -p {i+1} --enable"')
                page = -1
            elif rom == 0x01:
                print(f'profile {i+1} is on ROM')
                page = -1
            else:
                assert rom == 0 and page == i + 1 , f'error memory layout at profile {i+1} {hex(page)}'
            self.profile_list.append({'page':page, 'vis': vis == 1})
        self.page_layout = self.calc_page_layout()
        self.dest = 1
        self._overlay_switch_target: Optional[int] = None

    def close(self):
        self.dev.close()

    def info_display(self):
        print(self.dev.hidpp20_info())
        if not self.onboard_mode:
            print('onboard mode disabled! run "omm.py --onboard on" first!')
            return False
        print('number of buttons:  ', self.num_buttons)
        print('number of pages:    ', self.num_pages)
        print('page size:          ', self.page_size)
        print('profile format:     ', self.profile_format)

        current_profile = self.current_profile
        status = []
        for idx, p in enumerate(self.profile_list[1:]):
            status.append(f"{idx+1}{'*' if idx+1 == current_profile else ''}{'x' if p['page'] > 0xFF else ''}{'-' if not p['vis'] else ''}")
            #print(f'profile {idx+1}:', 'enabled' if p['page'] > 0 else 'disabled', 'visible' if p['vis'] else 'hidden')
        print('profile status:     ', '  '.join(status), '\n')
        return True

    def read_memory_page(self, page, verify = True):
        """read memory page from device.

        Args:
            page (int): page index
            verify (bool, optional): check checksum for page 0 and all profile pages. don't verify for macro pages.

        Returns:
            bytearray: content out
        """
        # libratbag hidpp20_onboard_profiles_read_sector: reading past
        # sector_size-16 fails (ERR_INVALID_ARGUMENT); the final window starts
        # at sector_size-16 (239 when the device reports 255).
        sector = int(getattr(self, "sector_size", self.page_size) or self.page_size)
        buf_len = max(self.page_size, sector)
        ret = bytearray(b"\xff" * buf_len)
        offset = 0
        while offset < sector:
            read_at = (sector - 16) if (sector - offset < 16) else offset
            out = self.dev.call_feature(
                Feature.onboard_profile,
                5,
                list(struct.pack(">HH", page, read_at)),
            )
            if not out or (len(out) > 2 and out[2] == 0xFF):
                chunk = b"\xff" * 16
            else:
                chunk = bytes(out[4:20]) if len(out) >= 20 else bytes(out[4:])
                chunk = (chunk + b"\xff" * 16)[:16]
            ret[read_at : read_at + 16] = chunk
            offset = read_at + 16
        ret = ret[: self.page_size]
        if verify:
            if sector == 255:
                expected = struct.unpack(">H", ret[253:255])[0]
                actual = crc16_ccitt(ret[:253])
            else:
                expected = struct.unpack(">H", ret[-2:])[0]
                actual = crc16_ccitt(ret[:-2])
            if actual != expected:
                if getattr(self.dev, "wireless_receiver", False):
                    pass
                else:
                    raise AssertionError(
                        f"checksum error while reading memory page: {page}"
                    )
        return bytearray(ret)

    def write_memory_page(self, page, data, verify = True):
        """write memory page with data

        Args:
            page (int): page index
            data (bytesarray): data to write.
            verify (bool, optional): auto calculate checksum and update last 2 bytes. for page 0 and all profile pages. don't verify for macro pages.
        """
        assert len(data) == self.page_size, 'wrong data size!'
        data = bytearray(data)
        sector = int(getattr(self, "sector_size", self.page_size) or self.page_size)
        if verify:
            if sector == 255:
                # Device-reported sector is 255 bytes: CRC over [0:253] at [253:255].
                # (Old code used page_size=256 CRC at [254:256] and write_len=254,
                # which dropped the CRC — firmware then ignored button remaps.)
                checksum = crc16_ccitt(data[:253])
                data[253:255] = struct.pack(">H", checksum)
                data[255] = 0xFF
            else:
                checksum = crc16_ccitt(data[:-2])
                data = data[:-2] + struct.pack(">H", checksum)
        write_len = sector if sector in (255, 256, self.page_size) else self.page_size
        if getattr(self.dev, "wireless_receiver", False) and write_len == 256:
            write_len = 255
        #call 06 to start, then 07 writing in loop, 08 to finish
        _check_hidpp_response(
            self.dev.call_feature(
                Feature.onboard_profile, 6, list(struct.pack('>HHH', page, 0, write_len))
            ),
            context=f"write page {page} start",
        )
        # Transfer full page_size buffer (16×16). With sector_size=255 this matches
        # libratbag: start(255) + 256 bytes of write_data.
        for i in range(int(self.page_size / 16)):
            _check_hidpp_response(
                self.dev.call_feature(
                    Feature.onboard_profile, 7, list(data[i*16:i*16+16])
                ),
                context=f"write page {page} chunk {i}",
            )
        commit_out = self.dev.call_feature(Feature.onboard_profile, 8)
        if commit_out is None:
            raise OnboardWriteError(
                f"write page {page} commit: no HID++ response (macOS may be blocking SetReport)"
            )
        commit_err = _hidpp_error_code(commit_out)
        if commit_err:
            # Lightspeed receiver often returns HWError on commit even when flash updated.
            if getattr(self.dev, "wireless_receiver", False) and commit_err == 0x04:
                return
            _check_hidpp_response(commit_out, context=f"write page {page} commit")
        if len(commit_out) > 4 and commit_out[4] != 0:
            if getattr(self.dev, "wireless_receiver", False) and commit_out[4] == 0x04:
                return
            _check_hidpp_response(commit_out, context=f"write page {page} commit")
        return

    def onboard_profile_to_bin(self):
        page = self.profile_list[self.dest]["page"]
        assert page == self.dest, f"error profile {self.dest} at page {page}"
        return self.read_memory_page(self.page_layout[self.dest][0])

    def onboard_profile_save(self, data):
        page = self.profile_list[self.dest]["page"]
        assert page == self.dest, f"error profile {self.dest} at page {page}"
        print('save profile', self.dest)
        #write to profile page and macro page
        self.write_memory_page(self.page_layout[self.dest][0], data[0])
        for i, macro in enumerate(data[1:], 1):
            self.write_memory_page(self.page_layout[self.dest][i] , macro, False)

    def profile_bin_from_json(self, j):
        return Profile(self).profile_bytes_from_json(j, self.dest)

    def profile_bin_to_json(self, data):
        p = Profile(self)
        p.load_profile_bin(data)
        return p.profile_to_json()

    def calc_page_layout(self):
        # irregular page layout override
        # return [[], [1,6,7],[2,10,11,12,15],[3,10,11],[4,12,13],[5,14,15]]

        # default: 2 pages per profile for macro (16-6)/5
        ret = [[]]
        pages = int((self.num_pages - self.num_profiles - 1) / self.num_profiles)
        for i in range(self.num_profiles):
            arr = [self.profile_list[i+1]['page']]
            arr += list(range(self.num_profiles + i*pages+1, self.num_profiles + i*pages+pages+1))
            ret.append(arr)
        return ret

    def _read_active_profile_index(self) -> int:
        data = self.dev.call_feature(Feature.onboard_profile, 4, [0, 0, 0])
        if not data or len(data) < 6:
            raise OnboardWriteError("read active profile: no HID++ response")
        code = _hidpp_error_code(data)
        if code:
            label = _HIDPP_ERROR_NAMES.get(code, f"0x{code:02X}")
            raise OnboardWriteError(f"read active profile: HID++ {label}")
        # HID++ func 4 returns 1-based active profile index in byte 5 (libratbag/OpenLogi).
        return int(data[5])

    def _slot_content_fingerprint(self, slot: int) -> tuple:
        self.dest_profile = slot
        meta = self.profile_bin_to_json(self.onboard_profile_to_bin())
        return (tuple(meta.get("dpi_list") or []), tuple(meta.get("buttons") or []))

    def _read_slot_pages(self, slot: int) -> list:
        pages = []
        for page_index, page_number in enumerate(self.page_layout[slot]):
            pages.append(self.read_memory_page(page_number, verify=(page_index == 0)))
        return pages

    def _write_slot_pages(self, slot: int, pages: list) -> None:
        self.dest_profile = slot
        self.onboard_profile_save(pages)

    def _profile_switch_candidates(self, profile_index: int) -> List[int]:
        """Sectors to try for func 3, derived from onboard directory page 0."""
        data = self.read_memory_page(0, verify=False)
        offset = (profile_index - 1) * 4
        rom, page, _vis = struct.unpack("BBB", data[offset : offset + 3])
        candidates: List[int] = []
        if rom == 0x01:
            candidates.append(_slot_to_rom_sector(profile_index))
        elif rom == 0 and page == profile_index:
            candidates.append(_slot_to_user_sector(profile_index))
        else:
            candidates.append(_slot_to_user_sector(profile_index))
        rom_sector = _slot_to_rom_sector(profile_index)
        if rom_sector not in candidates:
            candidates.append(rom_sector)
        return candidates

    @property
    def current_profile(self):
        return self._read_active_profile_index()

    def _set_active_sector(self, sector: int) -> Optional[int]:
        """Issue func 3 profile switch. Returns HID++ error code, or None on success/no reply."""
        out = self.dev.call_feature(
            Feature.onboard_profile, 3, _profile_switch_params(sector)
        )
        if out is None:
            return None
        code = _hidpp_error_code(out)
        if code and not getattr(self.dev, "wireless_receiver", False):
            label = _HIDPP_ERROR_NAMES.get(code, f"0x{code:02X}")
            raise OnboardWriteError(f"switch profile sector 0x{sector:04X}: HID++ {label}")
        return code

    def _switch_profile_index(self, profile_index: int) -> None:
        assert profile_index in range(1, self.num_profiles + 1), (
            f"wrong profile index! {profile_index}"
        )
        self.dest = profile_index
        if not self.profile_enabled:
            self.profile_enabled = True
        if not self.profile_visibility:
            self.profile_visibility = True

        curr = self.current_profile
        if profile_index == curr:
            return

        candidates = self._profile_switch_candidates(profile_index)

        last_err: Optional[Exception] = None
        for sector in candidates:
            try:
                err_code = self._set_active_sector(sector)
            except OnboardWriteError as exc:
                last_err = exc
                continue
            # Lightspeed receivers can lag or NAK func 3 before the sector readback updates.
            for delay in (0.0, 0.12, 0.25, 0.5):
                if delay:
                    time.sleep(delay)
                if self.current_profile == profile_index:
                    print(f"switch profile: {curr}=>{profile_index} (sector 0x{sector:04X})")
                    return
            if err_code:
                label = _HIDPP_ERROR_NAMES.get(err_code, f"0x{err_code:02X}")
                last_err = OnboardWriteError(
                    f"switch profile sector 0x{sector:04X}: HID++ {label}"
                )
            else:
                last_err = OnboardWriteError(
                    f"switch profile sector 0x{sector:04X}: device still on slot {self.current_profile}"
                )

        # Lightspeed receivers reject func 3 (InvalidArgument). Caller should
        # overlay the target profile onto the hardware-active slot instead of
        # swapping flash (swap scrambled slots and lied about the active index).
        if getattr(self.dev, "wireless_receiver", False):
            raise OnboardWriteError(
                f"wireless receiver cannot activate slot {profile_index} via HID++ "
                f"(still on slot {curr}); use overlay"
            )

        if last_err is not None:
            raise last_err
        raise OnboardWriteError(f"switch profile: slot {profile_index} was not activated")

    @current_profile.setter
    def current_profile(self, profile_index):
        self._switch_profile_index(profile_index)

    @property
    def onboard_mode(self):
        data = self.dev.call_feature(Feature.onboard_profile, 2)
        return data[4] == 1

    @onboard_mode.setter
    def onboard_mode(self, mode = True):
        want = bool(mode)
        _check_hidpp_response(
            self.dev.call_feature(
                Feature.onboard_profile, 1, [1 if want else 2, 0, 0]
            ),
            context="set onboard mode",
        )
        if bool(self.onboard_mode) != want:
            raise OnboardWriteError(
                "onboard mode did not stick (Logitech HID driver may be blocking writes)"
            )

    @property
    def dest_profile(self):
        """return current working profile index

        Returns:
            int: self.dest
        """
        return self.dest

    @dest_profile.setter
    def dest_profile(self, profile_index):
        """set working profile index for all future function

        Args:
            profile_index (int): profile index
        """
        assert profile_index in range(1, self.num_profiles+1), f'error wrong profile index! {profile_index}'
        #assert self.profile_list[profile_index]['page'] > 0, f'profile {profile_index} is disabled!'
        self.dest = profile_index

    @property
    def profile_enabled(self):
        return self.profile_list[self.dest]['page'] > 0

    @profile_enabled.setter
    def profile_enabled(self, enabled):
        """ enable or disable profile
            when enabled, also make it visible
            when disable, set all 4 bytes to 0xFF
            Note: this assumes profile 1 in memory page 1, 2 in page 2, etc..

        Args:
            enabled (bool): enable/disable a profile
        """
        data = self.read_memory_page(0, False)
        val = 1 if enabled else 0
        if self.current_profile == self.dest:
            if val:
                print(f'current profile: {self.dest} is already enabled')
            else:
                print(f'error: can\'t disable current profile: {self.dest}')
            return

        print(f"{'enable' if val == 1 else 'disable'} profile {self.dest}")
        if val == 1:
            data[(self.dest-1)*4:self.dest*4] = bytearray([0, self.dest, 1, 0])
            self.profile_list[self.dest]['page'] = self.dest
        else:
            data[(self.dest-1)*4:self.dest*4] = bytearray([0xff, 0xff, 0xff, 0xff])
            self.profile_list[self.dest]['page'] = -1
        self.write_memory_page(0, data)


    @property
    def profile_visibility(self):
        """check profile visible?

        Returns:
            bool: profile visible?
        """
        return self.profile_list[self.dest]['vis']

    @profile_visibility.setter
    def profile_visibility(self, visibility):
        """set profile visibility for self.dest

        Args:
            visibility (bool): visibility
        """
        if not self.profile_enabled:
            print(f'profile: {self.dest} is disabled, enable it first!')
            return
        val = 1 if visibility else 0
        if self.current_profile == self.dest and val == 0:
            print(f'can\'t hide current profile: {self.dest}')
            return
        if self.profile_list[self.dest]['vis'] == visibility:
            state = "visible" if val == 1 else "hidden"
            print(f"profile {self.dest} is already {state}, no need to change")
            return
        print(f"set profile {self.dest}: {'visible' if val == 1 else 'hidden'}")
        self.profile_list[self.dest]['vis'] = val
        data = self.read_memory_page(0, False)
        data[(self.dest-1)*4+2] = val
        self.write_memory_page(0, data)