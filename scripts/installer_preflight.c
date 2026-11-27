/* Native Installer preflight: observe processes and the one application target.
 * Never terminate apps, modify preferences, or require a recipient toolchain. */
#include <CoreFoundation/CoreFoundation.h>
#include <libproc.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <sys/proc.h>
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef SOUNDBOX_INSTALL_LOCATION
#define SOUNDBOX_INSTALL_LOCATION "/Applications/Soundbox.app"
#endif
#ifndef SOUNDBOX_BUNDLE_ID
#define SOUNDBOX_BUNDLE_ID "com.lowfreqlabs.soundbox"
#endif

static int refuse(const char *message) {
    fprintf(stderr, "Soundbox: %s\n", message);
    return 1;
}

/* Return 1 only for the exact signed-bundle identifier, 0 for another bundle,
 * and -1 for an unreadable/invalid plist. Reading never opens the application. */
static int bundle_identity(const char *bundle) {
    char path[PROC_PIDPATHINFO_MAXSIZE];
    if (snprintf(path, sizeof(path), "%s/Contents/Info.plist", bundle) >= (int)sizeof(path)) return -1;
    struct stat value_stat;
    if (lstat(path, &value_stat) || !S_ISREG(value_stat.st_mode)
            || value_stat.st_size <= 0 || value_stat.st_size > 1024 * 1024) return -1;
    CFURLRef url = CFURLCreateFromFileSystemRepresentation(NULL, (const UInt8 *)path, strlen(path), false);
    if (!url) return -1;
    CFReadStreamRef stream = CFReadStreamCreateWithFile(NULL, url);
    CFRelease(url);
    if (!stream) return -1;
    if (!CFReadStreamOpen(stream)) { CFRelease(stream); return -1; }
    CFPropertyListRef value = CFPropertyListCreateWithStream(NULL, stream, 0, kCFPropertyListImmutable, NULL, NULL);
    CFReadStreamClose(stream); CFRelease(stream);
    if (!value) return -1;
    int result = -1;
    if (CFGetTypeID(value) == CFDictionaryGetTypeID()) {
        CFTypeRef identifier = CFDictionaryGetValue(value, CFSTR("CFBundleIdentifier"));
        if (identifier && CFGetTypeID(identifier) == CFStringGetTypeID()) {
            CFStringRef expected = CFStringCreateWithCString(NULL, SOUNDBOX_BUNDLE_ID, kCFStringEncodingUTF8);
            result = CFEqual(identifier, expected) ? 1 : 0;
            CFRelease(expected);
        }
    }
    CFRelease(value); return result;
}

static int app_path_running(const char *executable) {
    char path[PROC_PIDPATHINFO_MAXSIZE];
    if (strlcpy(path, executable, sizeof(path)) >= sizeof(path)) return -1;
    /* Inspect each ancestor app, including a native helper under Frameworks or
     * nested app bundles. A renamed Soundbox bundle keeps the same identifier. */
    char *cursor = path;
    const char *leaf = strrchr(executable, '/'); leaf = leaf ? leaf + 1 : executable;
    int known_helper = !strcmp(leaf, "Soundbox") || !strcmp(leaf, "soundbox-cdj")
                       || !strcmp(leaf, "soundbox-ipod-identity");
    while ((cursor = strstr(cursor, ".app/Contents/"))) {
        char *end = cursor + 4; char saved = *end; *end = '\0';
        int identity = bundle_identity(path);
        int named = !strcmp(strrchr(path, '/') ? strrchr(path, '/') + 1 : path, "Soundbox.app");
        *end = saved;
        if (identity == 1 || (identity < 0 && (named || known_helper))) return identity == 1 ? 1 : -1;
        cursor = end + 1;
    }
    return 0;
}

static int processes_closed(void) {
    int count = proc_listallpids(NULL, 0);
    if (count <= 0 || count > 1000000) return -1;
    int capacity = count + 256;
    pid_t *pids = calloc((size_t)capacity, sizeof(pid_t));
    if (!pids) return -1;
    count = proc_listallpids(pids, capacity * (int)sizeof(pid_t));
    if (count <= 0 || count >= capacity) { free(pids); return -1; }
    int result = 0;
    for (int index = 0; index < count; ++index) {
        pid_t pid = pids[index]; if (pid <= 0 || pid == getpid()) continue;
        char path[PROC_PIDPATHINFO_MAXSIZE] = {0};
        if (proc_pidpath(pid, path, sizeof(path)) <= 0) {
            struct proc_bsdinfo info = {0};
            int got = proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, sizeof(info));
            if (got == (int)sizeof(info) && info.pbi_status == SZOMB) continue;
            /* Some defunct processes reject libproc inspection even though
             * kill(pid, 0) still succeeds. Only an independent exact-PID kernel
             * zombie observation can exclude them; unknown live processes refuse. */
            struct kinfo_proc kernel_info = {0};
            size_t kernel_size = sizeof(kernel_info);
            int mib[] = { CTL_KERN, KERN_PROC, KERN_PROC_PID, pid };
            if (sysctl(mib, 4, &kernel_info, &kernel_size, NULL, 0) == 0
                    && kernel_size == sizeof(kernel_info)
                    && kernel_info.kp_proc.p_pid == pid
                    && kernel_info.kp_proc.p_stat == SZOMB) continue;
            if (kill(pid, 0) == -1 && errno == ESRCH) continue;
            result = -1; break;
        }
        int running = app_path_running(path);
        if (running) { result = running; break; }
    }
    free(pids); return result;
}

int main(void) {
    int arm64 = 0; size_t size = sizeof(arm64);
    if (sysctlbyname("hw.optional.arm64", &arm64, &size, NULL, 0) || arm64 != 1)
        return refuse("This installer requires an Apple Silicon Mac.");
    char release[64]; size = sizeof(release);
    if (sysctlbyname("kern.osrelease", release, &size, NULL, 0) || atoi(release) < 23)
        return refuse("This installer requires macOS 14 or later.");
    struct stat target;
    if (!lstat(SOUNDBOX_INSTALL_LOCATION, &target)) {
        if (!S_ISDIR(target.st_mode) || bundle_identity(SOUNDBOX_INSTALL_LOCATION) != 1)
            return refuse("The installation path contains an unexpected app or link. Move it aside before installing.");
    } else if (errno != ENOENT) return refuse("The installation path could not be checked.");
    int running = processes_closed();
    if (running == 1) return refuse("Quit Soundbox normally and wait for its background work to finish, then run the installer again.");
    if (running < 0) return refuse("Could not prove Soundbox and its helpers are closed. Quit Soundbox normally, then retry.");
    return 0;
}
