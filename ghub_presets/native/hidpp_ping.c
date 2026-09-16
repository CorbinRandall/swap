/*
 * hidpp_ping.c — minimal HID++ liveness probe for a Logitech mouse.
 *
 * Opens the vendor HID collection (usage page 0xFF00) NON-exclusively via
 * IOHIDManager, sends a HID++ 2.0 short ping (IRoot ping, sw id 0x0a,
 * payload 0x55), and waits for the echo. Distinguishes "the device answers
 * HID++" (G Hub's problem) from "HID++ is dead on the link" (USB/hub/KVM
 * problem) without needing G Hub at all.
 *
 * Exit codes: 0 = ping answered, 1 = no answer, 2 = usage/open error,
 *             3 = device not found.
 * Build: clang -o hidpp_ping hidpp_ping.c -framework CoreFoundation -framework IOKit
 */

#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/hid/IOHIDManager.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define DEFAULT_VID 0x046D
#define DEFAULT_PID 0xC332
#define VENDOR_PAGE 0xFF00

static volatile int g_got_reply = 0;

static void input_cb(void *ctx, IOReturn result, void *sender,
                     IOHIDReportType type, uint32_t reportID,
                     uint8_t *report, CFIndex len) {
    (void)ctx; (void)result; (void)sender; (void)type;
    printf("reply: id=0x%02x len=%ld:", reportID, (long)len);
    for (CFIndex i = 0; i < len && i < 20; i++) printf(" %02x", report[i]);
    printf("\n");
    /* HID++ 2.0 ping echo: feature 0x00, fn/sw 0x1a, data ends 0x55.
     * Any well-formed reply proves the channel is alive. */
    g_got_reply = 1;
    CFRunLoopStop(CFRunLoopGetCurrent());
}

static void dict_set_int(CFMutableDictionaryRef d, CFStringRef key, int val) {
    CFNumberRef n = CFNumberCreate(kCFAllocatorDefault, kCFNumberIntType, &val);
    CFDictionarySetValue(d, key, n);
    CFRelease(n);
}

int main(int argc, char **argv) {
    int vid = DEFAULT_VID, pid = DEFAULT_PID;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--vid") && i + 1 < argc)
            vid = (int)strtol(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--pid") && i + 1 < argc)
            pid = (int)strtol(argv[++i], NULL, 0);
    }

    IOHIDManagerRef mgr =
        IOHIDManagerCreate(kCFAllocatorDefault, kIOHIDOptionsTypeNone);
    CFMutableDictionaryRef match = CFDictionaryCreateMutable(
        kCFAllocatorDefault, 0, &kCFTypeDictionaryKeyCallBacks,
        &kCFTypeDictionaryValueCallBacks);
    dict_set_int(match, CFSTR(kIOHIDVendorIDKey), vid);
    dict_set_int(match, CFSTR(kIOHIDProductIDKey), pid);
    dict_set_int(match, CFSTR(kIOHIDDeviceUsagePageKey), VENDOR_PAGE);
    IOHIDManagerSetDeviceMatching(mgr, match);
    CFRelease(match);

    if (IOHIDManagerOpen(mgr, kIOHIDOptionsTypeNone) != kIOReturnSuccess) {
        fprintf(stderr, "IOHIDManagerOpen failed\n");
        return 2;
    }
    CFSetRef set = IOHIDManagerCopyDevices(mgr);
    if (!set || CFSetGetCount(set) == 0) {
        fprintf(stderr, "no vendor-page HID device for %04x:%04x\n", vid, pid);
        return 3;
    }
    CFIndex count = CFSetGetCount(set);
    IOHIDDeviceRef devs[8];
    CFSetGetValues(set, (const void **)devs);
    printf("vendor collections found: %ld\n", (long)count);

    static uint8_t buf[64];
    int any_sent = 0;
    for (CFIndex i = 0; i < count && i < 8; i++) {
        IOHIDDeviceRef dev = devs[i];
        if (IOHIDDeviceOpen(dev, kIOHIDOptionsTypeNone) != kIOReturnSuccess) {
            fprintf(stderr, "device %ld: open failed (non-exclusive)\n", (long)i);
            continue;
        }
        IOHIDDeviceRegisterInputReportCallback(dev, buf, sizeof(buf), input_cb, NULL);
        IOHIDDeviceScheduleWithRunLoop(dev, CFRunLoopGetCurrent(),
                                       kCFRunLoopDefaultMode);
        /* HID++ short: report 0x10, dev idx 0xff, feat 0x00, fn|sw 0x1a, 00 00 55 */
        uint8_t ping[] = {0xff, 0x00, 0x1a, 0x00, 0x00, 0x55};
        IOReturn r = IOHIDDeviceSetReport(dev, kIOHIDReportTypeOutput, 0x10,
                                          ping, sizeof(ping));
        printf("device %ld: ping sent (rc=0x%08x)\n", (long)i, r);
        if (r == kIOReturnSuccess)
            any_sent = 1;
    }
    CFRelease(set);
    if (!any_sent) {
        fprintf(stderr, "could not send ping on any collection\n");
        return 2;
    }
    CFRunLoopRunInMode(kCFRunLoopDefaultMode, 2.5, false);
    if (g_got_reply) {
        printf("HID++ ALIVE — device answers; problem is on the G Hub side.\n");
        return 0;
    }
    printf("HID++ SILENT — no reply; link/hub/KVM is dropping vendor traffic.\n");
    return 1;
}
