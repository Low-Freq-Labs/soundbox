/* Read-only endpoint-zero iPod identity helper. Never a publisher.
 * Protocol facts: original gtkpod/libgpod tools/ipod-usb.c.
 * macOS API: installed Apple IOUSBLib.h DeviceRequestTO contract; no open needed.
 * NO device open/seize, interface claim, configuration, reset, detach or writes.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <DiskArbitration/DiskArbitration.h>
#include <IOKit/IOKitLib.h>
#include <IOKit/IOCFPlugIn.h>
#include <IOKit/usb/IOUSBLib.h>
#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <time.h>

#define CHUNK_BYTES 4096u
#define MAX_BYTES (1024u * 1024u)
#define TOTAL_SECONDS 8.0

struct expected {
    char root[PATH_MAX];
    const char *bsd;
    const char *serial;
    const char *uuid;
    uint64_t device;
    uint64_t inode;
    uint64_t registry;
    uint16_t product;
};

static bool number(const char *text, uint64_t *out) {
    if (!text[0] || text[0] == '-') return false;
    char *end = NULL;
    errno = 0;
    unsigned long long value = strtoull(text, &end, 0);
    if (errno || !end || *end) return false;
    *out = (uint64_t)value;
    return true;
}

static bool bsd_name(const char *text) {
    if (strncmp(text, "disk", 4)) return false;
    const char *p = text + 4;
    if (*p < '0' || *p > '9') return false;
    while (*p >= '0' && *p <= '9') p++;
    while (*p == 's') {
        p++;
        if (*p < '0' || *p > '9') return false;
        while (*p >= '0' && *p <= '9') p++;
    }
    return !*p;
}

static double monotonic_seconds(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now)) return -1;
    return (double)now.tv_sec + (double)now.tv_nsec / 1e9;
}

static bool property_number(io_registry_entry_t entry, CFStringRef key, int *out) {
    CFTypeRef value = IORegistryEntryCreateCFProperty(entry, key, kCFAllocatorDefault, 0);
    bool ok = value && CFGetTypeID(value) == CFNumberGetTypeID()
        && CFNumberGetValue((CFNumberRef)value, kCFNumberIntType, out);
    if (value) CFRelease(value);
    return ok;
}

static bool serial_matches(io_registry_entry_t entry, const char *expected) {
    CFTypeRef value = IORegistryEntryCreateCFProperty(entry, CFSTR("USB Serial Number"), kCFAllocatorDefault, 0);
    if (!value) value = IORegistryEntryCreateCFProperty(entry, CFSTR("kUSBSerialNumberString"), kCFAllocatorDefault, 0);
    char actual[129] = {0};
    bool ok = value && CFGetTypeID(value) == CFStringGetTypeID()
        && CFStringGetCString((CFStringRef)value, actual, sizeof actual, kCFStringEncodingUTF8)
        && !strcasecmp(actual, expected);
    if (value) CFRelease(value);
    return ok;
}

/* Caller owns the returned exact USB ancestor. No device/interface is opened. */
static io_registry_entry_t usb_for_bsd(const struct expected *e) {
    io_registry_entry_t current = IOServiceGetMatchingService(kIOMainPortDefault,
        IOBSDNameMatching(kIOMainPortDefault, 0, e->bsd));
    for (unsigned depth = 0; current && depth < 64; depth++) {
        if (IOObjectConformsTo(current, "IOUSBHostDevice") || IOObjectConformsTo(current, "IOUSBDevice")) {
            uint64_t registry = 0;
            int vendor = 0, product = 0;
            bool matches = IORegistryEntryGetRegistryEntryID(current, &registry) == KERN_SUCCESS
                && registry == e->registry
                && property_number(current, CFSTR("idVendor"), &vendor) && vendor == 0x05ac
                && property_number(current, CFSTR("idProduct"), &product) && product == e->product
                && serial_matches(current, e->serial);
            if (matches) return current;
            IOObjectRelease(current);
            return IO_OBJECT_NULL;
        }
        io_registry_entry_t parent = IO_OBJECT_NULL;
        kern_return_t status = IORegistryEntryGetParentEntry(current, kIOServicePlane, &parent);
        IOObjectRelease(current);
        current = status == KERN_SUCCESS ? parent : IO_OBJECT_NULL;
    }
    if (current) IOObjectRelease(current);
    return IO_OBJECT_NULL;
}

static bool current_mount(const struct expected *e) {
    struct stat info;
    if (stat(e->root, &info) || !S_ISDIR(info.st_mode)
        || (uint64_t)info.st_dev != e->device || (uint64_t)info.st_ino != e->inode) return false;
    DASessionRef session = DASessionCreate(kCFAllocatorDefault);
    if (!session) return false;
    CFURLRef url = CFURLCreateFromFileSystemRepresentation(kCFAllocatorDefault,
        (const UInt8 *)e->root, (CFIndex)strlen(e->root), true);
    DADiskRef disk = url ? DADiskCreateFromVolumePath(kCFAllocatorDefault, session, url) : NULL;
    const char *bsd = disk ? DADiskGetBSDName(disk) : NULL;
    bool ok = bsd && !strcmp(bsd, e->bsd);
    CFDictionaryRef description = disk ? DADiskCopyDescription(disk) : NULL;
    CFTypeRef volume_uuid = description ? CFDictionaryGetValue(description, kDADiskDescriptionVolumeUUIDKey) : NULL;
    CFStringRef uuid_string = volume_uuid && CFGetTypeID(volume_uuid) == CFUUIDGetTypeID()
        ? CFUUIDCreateString(kCFAllocatorDefault, (CFUUIDRef)volume_uuid) : NULL;
    char actual_uuid[64] = {0};
    ok = ok && uuid_string && CFStringGetCString(uuid_string, actual_uuid,
        sizeof actual_uuid, kCFStringEncodingUTF8) && !strcasecmp(actual_uuid, e->uuid);
    if (uuid_string) CFRelease(uuid_string);
    if (description) CFRelease(description);
    if (disk) CFRelease(disk);
    if (url) CFRelease(url);
    CFRelease(session);
    if (!ok) return false;
    io_registry_entry_t usb = usb_for_bsd(e);
    if (!usb) return false;
    IOObjectRelease(usb);
    return true;
}

/* Apple returns XML with <key> entries inside arrays. CoreFoundation accepts
 * its own device format; serialize that validated object rather than relaxing
 * the engine's strict plist parser or editing guessed XML field sequences. */
static CFDataRef canonical_reply(const unsigned char *payload, size_t used) {
    if (!used || used > MAX_BYTES) return NULL;
    CFDataRef data = CFDataCreate(kCFAllocatorDefault, payload, (CFIndex)used);
    CFErrorRef error = NULL;
    CFPropertyListRef plist = data ? CFPropertyListCreateWithData(kCFAllocatorDefault,
        data, kCFPropertyListImmutable, NULL, &error) : NULL;
    if (error) { CFRelease(error); error = NULL; }
    CFDataRef result = NULL;
    if (plist && CFGetTypeID(plist) == CFDictionaryGetTypeID())
        result = CFPropertyListCreateData(kCFAllocatorDefault, plist,
            kCFPropertyListBinaryFormat_v1_0, 0, &error);
    if (error) CFRelease(error);
    if (plist) CFRelease(plist);
    if (data) CFRelease(data);
    if (result && CFDataGetLength(result) > MAX_BYTES) {
        CFRelease(result);
        result = NULL;
    }
    return result;
}

int main(int argc, const char **argv) {
    if (argc != 9) {
        fprintf(stderr, "usage: reader mount bsd mount_dev mount_ino usb_registry_id usb_pid usb_serial volume_uuid\n");
        return 2;
    }
    struct expected e = {0};
    uint64_t product = 0;
    if (!realpath(argv[1], e.root) || !bsd_name(argv[2])
        || !number(argv[3], &e.device) || !number(argv[4], &e.inode)
        || !number(argv[5], &e.registry) || !e.registry
        || !number(argv[6], &product) || product > UINT16_MAX || !product
        || !argv[7][0] || strlen(argv[7]) > 128 || strlen(argv[8]) != 36) {
        fprintf(stderr, "Invalid exact-device arguments\n");
        return 2;
    }
    e.bsd = argv[2]; e.product = (uint16_t)product; e.serial = argv[7]; e.uuid = argv[8];
    if (!current_mount(&e)) {
        fprintf(stderr, "Current mount/BSD/USB ownership mismatch before query\n");
        return 3;
    }
    io_registry_entry_t service = usb_for_bsd(&e);
    if (!service) return 3;
    IOCFPlugInInterface **plugin = NULL;
    SInt32 score = 0;
    IOReturn status = IOCreatePlugInInterfaceForService(service,
        kIOUSBDeviceUserClientTypeID, kIOCFPlugInInterfaceID, &plugin, &score);
    IOObjectRelease(service);
    if (status != kIOReturnSuccess || !plugin) {
        fprintf(stderr, "No device user-client interface: 0x%08x\n", status);
        return 4;
    }
    IOUSBDeviceInterface650 **device = NULL;
    HRESULT queried = (*plugin)->QueryInterface(plugin,
        CFUUIDGetUUIDBytes(kIOUSBDeviceInterfaceID650), (LPVOID *)&device);
    (*plugin)->Release(plugin);
    if (queried || !device) {
        fprintf(stderr, "No endpoint-zero request interface: 0x%08x\n", (unsigned)queried);
        return 4;
    }
    /* Intentionally never call USBDeviceOpen, USBDeviceOpenSeize or any
     * interface/configuration/reset method. DeviceRequestTO is allowed unopened. */
    unsigned char *payload = calloc(1, MAX_BYTES);
    if (!payload) { (*device)->Release(device); return 5; }
    size_t used = 0;
    bool complete = false;
    double started = monotonic_seconds();
    unsigned requests = 0;
    if (started < 0) status = kIOReturnError;
    for (unsigned page = 0; started >= 0 && page < MAX_BYTES / CHUNK_BYTES; page++) {
        double now = monotonic_seconds();
        if (now < 0 || now - started >= TOTAL_SECONDS || !current_mount(&e)) {
            status = kIOReturnAborted;
            break;
        }
        IOUSBDevRequestTO request = {0};
        request.bmRequestType = 0xc0; /* IN / vendor / device, default pipe only */
        request.bRequest = 0x40;
        request.wValue = 2;
        request.wIndex = (UInt16)page;
        request.wLength = CHUNK_BYTES;
        request.pData = payload + used;
        request.noDataTimeout = 1000;
        request.completionTimeout = 1000;
        requests++;
        status = (*device)->DeviceRequestTO(device, &request);
        if (status != kIOReturnSuccess || request.wLenDone > CHUNK_BYTES) break;
        used += request.wLenDone;
        if (request.wLenDone < CHUNK_BYTES) { complete = true; break; }
    }
    (*device)->Release(device);
    if (status != kIOReturnSuccess || !complete || !used || !current_mount(&e)) {
        fprintf(stderr, "Identity read refused/incomplete: status0x%08x requests%u bytes%zu\n", status, requests, used);
        free(payload);
        return 6;
    }
    CFDataRef canonical = canonical_reply(payload, used);
    free(payload);
    if (!canonical) {
        fprintf(stderr, "Identity reply was not a bounded dictionary plist (%zu bytes)\n", used);
        return 7;
    }
    size_t length = (size_t)CFDataGetLength(canonical);
    fprintf(stderr, "Read-only identity reply: requests%u bytes%zu canonical%zu\n", requests, used, length);
    bool written = fwrite(CFDataGetBytePtr(canonical), 1, length, stdout) == length && fflush(stdout) == 0;
    CFRelease(canonical);
    return written ? 0 : 8;
}
