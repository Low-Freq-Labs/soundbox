/* Fresh process names from one complete kernel snapshot. No retained state. */
#include <sys/types.h>
#include <sys/sysctl.h>
#include <sys/user.h>
#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <libproc.h>

/* 1 = a requested name exists; 0 = proved absent; -1 = unknown. */
__attribute__((visibility("default")))
int sb_process_names_running(const char *const *names, size_t count) {
    if (names == NULL || count == 0 || count > 16) return -1;
    for (size_t i = 0; i < count; ++i) {
        if (names[i] == NULL) return -1;
        size_t length = strnlen(names[i], MAXCOMLEN + 1);
        if (length == 0 || length > MAXCOMLEN) return -1;
    }
    int query[] = {CTL_KERN, KERN_PROC, KERN_PROC_ALL, 0};
    const size_t maximum = 64 * 1024 * 1024;
    pid_t self = getpid();
    char self_name[2 * MAXCOMLEN + 1] = {0};
    if (proc_name(self, self_name, sizeof(self_name)) <= 0 ||
        strnlen(self_name, sizeof(self_name)) > MAXCOMLEN) return -1;
    for (unsigned attempt = 0; attempt < 6; ++attempt) {
        size_t needed = 0;
        if (sysctl(query, 4, NULL, &needed, NULL, 0) != 0 ||
            needed == 0 || needed > maximum / 2) return -1;
        size_t capacity = needed + needed / 4 + 64 * sizeof(struct kinfo_proc);
        if (capacity > maximum) return -1;
        struct kinfo_proc *rows = malloc(capacity);
        if (rows == NULL) return -1;
        size_t used = capacity;
        int status = sysctl(query, 4, rows, &used, NULL, 0);
        int error = errno;
        if (status != 0) {
            free(rows);
            if (error == ENOMEM) continue;
            return -1;
        }
        if (used == 0 || used > capacity || used % sizeof(*rows) != 0) {
            free(rows);
            return -1;
        }
        int found = 0, self_seen = 0, invalid = 0;
        for (size_t i = 0; i < used / sizeof(*rows); ++i) {
            const char *name = rows[i].kp_proc.p_comm;
            if (rows[i].kp_proc.p_pid == self &&
                strncmp(name, self_name, sizeof(rows[i].kp_proc.p_comm)) == 0)
                self_seen = 1;
            if (memchr(name, '\0', sizeof(rows[i].kp_proc.p_comm)) == NULL) {
                invalid = 1;
                break;
            }
            for (size_t j = 0; j < count; ++j) {
                if (strcmp(name, names[j]) == 0) found = 1;
            }
        }
        free(rows);
        if (invalid) return -1;
        if (found) return 1;
        return self_seen ? 0 : -1;
    }
    return -1;
}
