#define _GNU_SOURCE
#include <security/pam_modules.h>
#include <security/pam_ext.h>
#include <security/pam_modutil.h>
#include <poll.h>
#include <pwd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

/* Only the explicit choice leaves the PAM conversation. */
PAM_EXTERN int pam_sm_authenticate(pam_handle_t *pam, int flags, int argc, const char **argv)
{
    (void)flags;
    (void)argc;
    (void)argv;
    const void *service, *remote;
    const char *user;
    if (pam_get_item(pam, PAM_SERVICE, &service) != PAM_SUCCESS ||
        !service || pam_get_item(pam, PAM_RHOST, &remote) != PAM_SUCCESS ||
        (remote && *(const char *)remote))
        return PAM_IGNORE;

    int agent = 0;
    if (!strcmp(service, "polkit-1")) {
        struct ucred peer;
        socklen_t size = sizeof(peer);
        if (getsockopt(STDIN_FILENO, SOL_SOCKET, SO_PEERCRED, &peer, &size) || peer.pid <= 0)
            return PAM_IGNORE;
        agent = peer.pid;
    } else if (strcmp(service, "sudo") && strcmp(service, "sudo-i")) {
        return PAM_IGNORE;
    }
    if (pam_get_user(pam, &user, NULL) != PAM_SUCCESS || !user)
        return PAM_IGNORE;
    struct passwd *account = pam_modutil_getpwnam(pam, user);
    if (!account || account->pw_uid < 1000)
        return PAM_IGNORE;

    struct sockaddr_un addr = { .sun_family = AF_UNIX,
        .sun_path = "/run/tb710fu-face-password/auth.sock" };
    int fd = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0);
    if (fd < 0)
        return PAM_IGNORE;
    int result = PAM_IGNORE;
    struct timeval timeout = { .tv_sec = 12 };
    char request[80], reply[16];
    int len = snprintf(request, sizeof(request), "%s %u %d", (const char *)service, account->pw_uid, agent);
    if (setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) ||
        connect(fd, (struct sockaddr *)&addr, sizeof(addr)) ||
        send(fd, request, len, MSG_NOSIGNAL) != len)
        goto done;
    ssize_t count = recv(fd, reply, sizeof(reply), 0);
    if (count == 3 && !memcmp(reply, "ASK", 3)) {
        char *answer = NULL;
        int rc = pam_prompt(pam, PAM_PROMPT_ECHO_OFF, &answer,
                            "选择验证方式（1 人脸识别，2 密码）：");
        int face = rc == PAM_SUCCESS && answer && !strcmp(answer, "1");
        if (answer) {
            explicit_bzero(answer, strlen(answer));
            free(answer);
        }
        if (!face)
            goto done;
    } else if (count != 4 || memcmp(reply, "AUTO", 4)) {
        goto done;
    }
    /* sudo also sets PAM_SILENT on the interactive desktop. Show the camera
       cue for a face attempt selected by this user, including automatic ones. */
    if (pam_info(pam, "正在识别人脸，请正视前置摄像头。") != PAM_SUCCESS ||
        send(fd, "FACE", 4, MSG_NOSIGNAL) != 4)
        goto done;
    count = recv(fd, reply, sizeof(reply), 0);
    struct pollfd agent_state = { .fd = STDIN_FILENO, .events = POLLRDHUP };
    if (count == 2 && !memcmp(reply, "OK", 2) &&
        (!agent || poll(&agent_state, 1, 0) == 0))
        result = PAM_SUCCESS;
done:
    close(fd);
    return result;
}

PAM_EXTERN int pam_sm_setcred(pam_handle_t *pam, int flags, int argc, const char **argv)
{
    (void)pam; (void)flags; (void)argc; (void)argv;
    return PAM_IGNORE;
}
