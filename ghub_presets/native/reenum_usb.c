/*
 * reenum_usb.c — force macOS to re-enumerate a USB device or one of its
 * ancestor hubs (equivalent to unplug/replug of that node, or — at hub
 * level — the part of a reboot that clears wedged kernel USB state).
 *
 * Built for the G502 "Inactive in G HUB while mouse still works" wedge:
 * the device enumerates and basic HID input flows, but HID++ traffic on
 * interface 1 is silently dropped by stale kernel/hub state. Re-enumerating
 * the ancestor hub tears down and rebuilds that state without rebooting.
 *
 * Usage:
 *   reenum_usb --list                      # show device + ancestor hub chain
 *   reenum_usb --level 0                   # re-enumerate the device itself
 *   reenum_usb --level 1                   # re-enumerate its parent hub
 *   reenum_usb --vid 0x046d --pid 0xc332   # target override (default: G502)
 *
 * Requires root for the re-enumeration call (not for --list).
 * Build: clang -o reenum_usb reenum_usb.c -framework CoreFoundation -framework IOKit
 */

#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOCFPlugIn.h>
#include <IOKit/IOKitLib.h>
#include <IOKit/usb/IOUSBLib.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define DEFAULT_VID 0x046Du /* Logitech */
#define DEFAULT_PID 0xC332u /* G502 Proteus Spectrum (wired) */
#define MAX_CHAIN 8

static int prop_u32(io_service_t svc, CFStringRef key, uint32_t *out) {
    CFTypeRef ref = IORegistryEntryCreateCFProperty(svc, key, kCFAllocatorDefault, 0);
    if (!ref) return 0;
    int ok = 0;
    if (CFGetTypeID(ref) == CFNumberGetTypeID())
        ok = CFNumberGetValue((CFNumberRef)ref, kCFNumberSInt32Type, out);
    CFRelease(ref);
    return ok;
}

static void prop_str(io_service_t svc, CFStringRef key, char *buf, size_t n) {
    buf[0] = 0;
    CFTypeRef ref = IORegistryEntryCreateCFProperty(svc, key, kCFAllocatorDefault, 0);
    if (!ref) return;
    if (CFGetTypeID(ref) == CFStringGetTypeID())
        CFStringGetCString((CFStringRef)ref, buf, n, kCFStringEncodingUTF8);
    CFRelease(ref);
}

static io_service_t find_device(uint32_t vid, uint32_t pid) {
    CFMutableDictionaryRef match = IOServiceMatching("IOUSBHostDevice");
    io_iterator_t it = IO_OBJECT_NULL;
    if (IOServiceGetMatchingServices(MACH_PORT_NULL, match, &it) != KERN_SUCCESS)
        return IO_OBJECT_NULL;
    io_service_t svc;
    while ((svc = IOIteratorNext(it))) {
        uint32_t v = 0, p = 0;
        if (prop_u32(svc, CFSTR("idVendor"), &v) &&
            prop_u32(svc, CFSTR("idProduct"), &p) && v == vid && p == pid) {
            IOObjectRelease(it);
            return svc;
        }
        IOObjectRelease(svc);
    }
    IOObjectRelease(it);
    return IO_OBJECT_NULL;
}

/* chain[0] = device, chain[1] = parent hub, ... Returns count. */
static int build_chain(io_service_t device, io_service_t chain[MAX_CHAIN]) {
    int n = 0;
    chain[n++] = device;
    io_service_t cur = device;
    while (n < MAX_CHAIN) {
        io_registry_entry_t parent = IO_OBJECT_NULL;
        if (IORegistryEntryGetParentEntry(cur, kIOServicePlane, &parent) != KERN_SUCCESS)
            break;
        cur = parent;
        if (IOObjectConformsTo(parent, "IOUSBHostDevice"))
            chain[n++] = parent;
        /* Non-hub intermediates (ports, controllers) are walked through. */
        if (IOObjectConformsTo(parent, "IOUSBHostController") ||
            IOObjectConformsTo(parent, "AppleUSBXHCI"))
            break;
    }
    return n;
}

static kern_return_t reenumerate(io_service_t svc) {
    SInt32 score = 0;
    IOCFPlugInInterface **plug = NULL;
    kern_return_t kr = IOCreatePlugInInterfaceForService(
        svc, kIOUSBDeviceUserClientTypeID, kIOCFPlugInInterfaceID, &plug, &score);
    if (kr != KERN_SUCCESS || !plug)
        return kr ? kr : KERN_FAILURE;
    IOUSBDeviceInterface500 **dev = NULL;
    HRESULT hr = (*plug)->QueryInterface(
        plug, CFUUIDGetUUIDBytes(kIOUSBDeviceInterfaceID500), (LPVOID *)&dev);
    IODestroyPlugInInterface(plug);
    if (hr || !dev)
        return KERN_FAILURE;
    kern_return_t opened = (*dev)->USBDeviceOpenSeize(dev);
    kr = (*dev)->USBDeviceReEnumerate(dev, 0);
    if (kr == kIOReturnExclusiveAccess || kr == kIOReturnNotPermitted) {
        /* A kernel driver (e.g. AppleUSB20Hub on hubs) owns the device.
         * Capture: terminate its driver and re-enumerate, then release so
         * the OS reloads drivers on the fresh device object. Root only. */
        fprintf(stderr, "plain re-enumerate refused (0x%08x, driver owns device); "
                        "retrying with capture+release...\n", kr);
        kr = (*dev)->USBDeviceReEnumerate(dev, kUSBReEnumerateCaptureDeviceMask);
        if (kr == KERN_SUCCESS) {
            sleep(2);
            (*dev)->USBDeviceReEnumerate(dev, kUSBReEnumerateReleaseDeviceMask);
        }
    }
    if (opened == kIOReturnSuccess)
        (*dev)->USBDeviceClose(dev);
    (*dev)->Release(dev);
    return kr;
}

static void describe(io_service_t svc, int level) {
    char name[256];
    uint32_t loc = 0;
    prop_str(svc, CFSTR("USB Product Name"), name, sizeof(name));
    if (!name[0]) {
        io_name_t cls;
        IOObjectGetClass(svc, cls);
        strncpy(name, cls, sizeof(name) - 1);
        name[sizeof(name) - 1] = 0;
    }
    prop_u32(svc, CFSTR("locationID"), &loc);
    printf("level %d: %s @0x%08x\n", level, name, loc);
}

int main(int argc, char **argv) {
    uint32_t vid = DEFAULT_VID, pid = DEFAULT_PID;
    int level = -1, list = 0;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--vid") && i + 1 < argc)
            vid = (uint32_t)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--pid") && i + 1 < argc)
            pid = (uint32_t)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--level") && i + 1 < argc)
            level = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--list"))
            list = 1;
        else {
            fprintf(stderr,
                    "usage: %s [--vid 0xVVVV] [--pid 0xPPPP] (--list | --level N)\n",
                    argv[0]);
            return 2;
        }
    }
    if (!list && level < 0) {
        fprintf(stderr, "specify --list or --level N\n");
        return 2;
    }

    io_service_t device = find_device(vid, pid);
    if (device == IO_OBJECT_NULL) {
        fprintf(stderr, "device %04x:%04x not found on USB\n", vid, pid);
        return 3;
    }

    io_service_t chain[MAX_CHAIN];
    int n = build_chain(device, chain);

    if (list) {
        for (int i = 0; i < n; i++)
            describe(chain[i], i);
        return 0;
    }

    if (level >= n) {
        fprintf(stderr, "level %d out of range (chain has %d nodes; use --list)\n",
                level, n);
        return 4;
    }

    describe(chain[level], level);
    kern_return_t kr = reenumerate(chain[level]);
    if (kr != KERN_SUCCESS) {
        fprintf(stderr, "USBDeviceReEnumerate failed: 0x%08x (need root?)\n", kr);
        return 5;
    }
    printf("re-enumeration requested OK\n");
    return 0;
}
