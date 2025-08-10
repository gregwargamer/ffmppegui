#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <unistd.h>
#include <signal.h>
#include <time.h>
#include <errno.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <fcntl.h>

#include <libwebsockets.h>
#include <jansson.h>
#include <curl/curl.h>
#include <pthread.h>

//this part do that
// constantes et configuration par variables d'environnement
static const char *getenv_default(const char *key, const char *defv) {
    const char *v = getenv(key);
    return (v && *v) ? v : defv;
}

//this other part do that
// état d'exécution global minimal
typedef struct AgentState {
    char controller_url[1024];
    char controller_ws[1024];
    char agent_token[256];
    char ffmpeg_path[512];
    char agent_id[512];
    int concurrency;
    int active_jobs;
    struct lws_context *lws_ctx;
    struct lws *wsi;
    int should_exit;
    pthread_mutex_t msg_mutex;
    struct MsgNode *msg_head;
    struct MsgNode *msg_tail;
} AgentState;

//this other part do that
// file de messages sortants (thread-safe)
typedef struct MsgNode {
    char *text;
    struct MsgNode *next;
} MsgNode;

static void enqueue_ws_text(AgentState *st, const char *text) {
    if (!text) return;
    MsgNode *n = (MsgNode*)calloc(1, sizeof(MsgNode));
    if (!n) return;
    n->text = strdup(text);
    if (!n->text) { free(n); return; }
    pthread_mutex_lock(&st->msg_mutex);
    if (st->msg_tail) { st->msg_tail->next = n; st->msg_tail = n; }
    else { st->msg_head = st->msg_tail = n; }
    struct lws *wsi = st->wsi;
    pthread_mutex_unlock(&st->msg_mutex);
    if (wsi) lws_callback_on_writable(wsi);
}

static void enqueue_ws_json(AgentState *st, json_t *obj) {
    char *text = json_dumps(obj, JSON_COMPACT);
    if (!text) return;
    enqueue_ws_text(st, text);
    free(text);
}

static char *dequeue_ws_text(AgentState *st) {
    pthread_mutex_lock(&st->msg_mutex);
    MsgNode *n = st->msg_head;
    if (!n) { pthread_mutex_unlock(&st->msg_mutex); return NULL; }
    st->msg_head = n->next;
    if (!st->msg_head) st->msg_tail = NULL;
    pthread_mutex_unlock(&st->msg_mutex);
    char *t = n->text;
    free(n);
    return t;
}

//this other part do that
// utilitaire de concaténation sûre
static void safe_snprintf(char *dst, size_t dstsz, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(dst, dstsz, fmt, ap);
    dst[dstsz - 1] = '\0';
    va_end(ap);
}

//this other part do that
// conversion http->ws
static void http_to_ws(const char *http, char *out, size_t outsz) {
    if (strncmp(http, "https://", 8) == 0) {
        safe_snprintf(out, outsz, "wss://%s", http + 8);
    } else if (strncmp(http, "http://", 7) == 0) {
        safe_snprintf(out, outsz, "ws://%s", http + 7);
    } else {
        safe_snprintf(out, outsz, "ws://%s", http);
    }
}

//this other part do that
// exécution de commande et capture stdout (utilisé pour -encoders)
static char *exec_capture(const char *cmd, char *const argv[]) {
    int pipefd[2];
    if (pipe(pipefd) != 0) return NULL;
    pid_t pid = fork();
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); return NULL; }
    if (pid == 0) {
        // enfant
        dup2(pipefd[1], STDOUT_FILENO);
        close(pipefd[0]);
        close(pipefd[1]);
        execvp(cmd, argv);
        _exit(127);
    }
    close(pipefd[1]);
    size_t cap = 8192; size_t len = 0;
    char *buf = (char*)malloc(cap);
    if (!buf) { close(pipefd[0]); return NULL; }
    for (;;) {
        if (len + 4096 > cap) { cap *= 2; char *nb = (char*)realloc(buf, cap); if (!nb) { free(buf); close(pipefd[0]); return NULL; } buf = nb; }
        ssize_t r = read(pipefd[0], buf + len, 4096);
        if (r < 0) { if (errno == EINTR) continue; break; }
        if (r == 0) break;
        len += (size_t)r;
    }
    close(pipefd[0]);
    int status = 0; waitpid(pid, &status, 0);
    buf[len] = '\0';
    return buf;
}

//this other part do that
// détection des encodeurs ffmpeg
static json_t *detect_encoders(const char *ffmpeg_path) {
    char *argv[] = {(char*)ffmpeg_path, "-hide_banner", "-encoders", NULL};
    char *out = exec_capture(ffmpeg_path, argv);
    json_t *arr = json_array();
    if (!out) return arr;
    char *line = out;
    while (*line) {
        char *nl = strchr(line, '\n');
        if (nl) *nl = '\0';
        // motif simple: colonnes où le 2e champ est le code encoder
        // on accepte alnum,_,-
        const char *p = line;
        // sauter préfixe indicateurs
        while (*p && (*p == ' ' || *p == '\t' || *p == '.' || (*p >= 'A' && *p <= 'Z'))) p++;
        // lire mot
        char name[128]; size_t ni = 0;
        while (*p && *p != ' ' && *p != '\t' && ni + 1 < sizeof(name)) {
            char c = *p;
            if ((c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_' || c == '-') {
                name[ni++] = c;
            } else {
                break;
            }
            p++;
        }
        name[ni] = '\0';
        if (ni > 0) {
            json_array_append_new(arr, json_string(name));
        }
        if (!nl) break; else line = nl + 1;
    }
    free(out);
    return arr;
}

//this other part do that
// envoi d'un objet JSON via WS
static int ws_send_json(struct lws *wsi, json_t *obj) {
    char *text = json_dumps(obj, JSON_COMPACT);
    if (!text) return -1;
    size_t len = strlen(text);
    size_t bufsz = LWS_PRE + len;
    unsigned char *buf = (unsigned char*)malloc(bufsz);
    if (!buf) { free(text); return -1; }
    memcpy(buf + LWS_PRE, text, len);
    int rc = lws_write(wsi, buf + LWS_PRE, len, LWS_WRITE_TEXT);
    free(buf);
    free(text);
    return rc < 0 ? -1 : 0;
}

//this other part do that
// création répertoire
static void mkdir_p(const char *path) {
    char tmp[2048];
    snprintf(tmp, sizeof(tmp), "%s", path);
    for (char *p = tmp + 1; *p; p++) {
        if (*p == '/') { *p = '\0'; mkdir(tmp, 0755); *p = '/'; }
    }
    mkdir(tmp, 0755);
}

//this other part do that
// upload via HTTP PUT avec libcurl
static int upload_file_put(const char *url, const char *file_path, long timeout_secs) {
    CURL *curl = curl_easy_init();
    if (!curl) return -1;
    FILE *fp = fopen(file_path, "rb");
    if (!fp) { curl_easy_cleanup(curl); return -1; }
    struct stat st; if (stat(file_path, &st) != 0) { fclose(fp); curl_easy_cleanup(curl); return -1; }
    curl_easy_setopt(curl, CURLOPT_URL, url);
    curl_easy_setopt(curl, CURLOPT_UPLOAD, 1L);
    curl_easy_setopt(curl, CURLOPT_READDATA, fp);
    curl_easy_setopt(curl, CURLOPT_INFILESIZE_LARGE, (curl_off_t)st.st_size);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, timeout_secs);
    curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 10L);
    curl_easy_setopt(curl, CURLOPT_HTTP_VERSION, (long)CURL_HTTP_VERSION_2TLS);
    CURLcode res = curl_easy_perform(curl);
    long status = 0; curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);
    fclose(fp);
    curl_easy_cleanup(curl);
    if (res != CURLE_OK) return -1;
    if (status < 200 || status >= 300) return -1;
    return 0;
}

//this other part do that
// lecture ligne par ligne depuis fd
static ssize_t read_line_fd(int fd, char *buf, size_t bufsz) {
    size_t i = 0;
    while (i + 1 < bufsz) {
        char c; ssize_t r = read(fd, &c, 1);
        if (r == 0) break;
        if (r < 0) { if (errno == EINTR) continue; return -1; }
        if (c == '\n') break;
        buf[i++] = c;
    }
    buf[i] = '\0';
    return (ssize_t)i;
}

//this other part do that
// exécuter ffmpeg et parser la progression
static int run_ffmpeg_and_upload(AgentState *st, const char *job_id, const char *input_url, const char *output_url, json_t *ffmpeg_args, const char *output_ext) {
    char tmpdir[2048];
    snprintf(tmpdir, sizeof(tmpdir), "%s/%s", getenv_default("TMPDIR", "/tmp"), "ffmpegeasy");
    mkdir_p(tmpdir);
    char tmpout[2048];
    snprintf(tmpout, sizeof(tmpout), "%s/%s%s", tmpdir, job_id, output_ext);

    size_t argc = 0, cap = 16;
    char **argv = (char**)calloc(cap, sizeof(char*));
    if (!argv) return -1;
    argv[argc++] = (char*)st->ffmpeg_path;
    argv[argc++] = "-i";
    argv[argc++] = (char*)input_url;
    size_t arrsz = json_array_size(ffmpeg_args);
    for (size_t i = 0; i < arrsz; i++) {
        const char *s = json_string_value(json_array_get(ffmpeg_args, i));
        if (!s) continue;
        if (argc + 2 >= cap) { cap *= 2; argv = (char**)realloc(argv, cap * sizeof(char*)); }
        argv[argc++] = strdup(s);
    }
    argv[argc++] = tmpout;
    argv[argc] = NULL;

    int pipefd[2];
    if (pipe(pipefd) != 0) { free(argv); return -1; }
    pid_t pid = fork();
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); free(argv); return -1; }
    if (pid == 0) {
        // enfant: rediriger stdout vers pipe, ignorer stderr
        dup2(pipefd[1], STDOUT_FILENO);
        int devnull = open("/dev/null", O_WRONLY);
        if (devnull >= 0) dup2(devnull, STDERR_FILENO);
        close(pipefd[0]); close(pipefd[1]);
        execvp(st->ffmpeg_path, argv);
        _exit(127);
    }
    close(pipefd[1]);

    char line[4096];
    json_t *payload = json_object();
    int rc = 0;
    for (;;) {
        ssize_t n = read_line_fd(pipefd[0], line, sizeof(line));
        if (n <= 0) break;
        char *eq = strchr(line, '=');
        if (eq) {
            *eq = '\0';
            const char *k = line; const char *v = eq + 1;
            json_object_set_new(payload, k, json_string(v));
            if (strcmp(k, "progress") == 0) {
                json_t *msg = json_object();
                json_object_set_new(msg, "type", json_string("progress"));
                json_t *pay = json_object();
                json_object_set_new(pay, "jobId", json_string(job_id));
                json_object_set(pay, "data", payload);
                json_object_set_new(msg, "payload", pay);
                enqueue_ws_json(st, msg);
                json_decref(msg);
                json_decref(payload);
                payload = json_object();
            }
        }
    }
    close(pipefd[0]);
    int status = 0; waitpid(pid, &status, 0);
    if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
        rc = -1;
    }

    if (rc == 0) {
        if (upload_file_put(output_url, tmpout, 900L) != 0) rc = -1;
    }

    json_t *cmp = json_object();
    json_object_set_new(cmp, "type", json_string("complete"));
    json_t *pl = json_object();
    json_object_set_new(pl, "jobId", json_string(job_id));
    json_object_set_new(pl, "agentId", json_string(st->agent_id));
    json_object_set_new(pl, "success", rc == 0 ? json_true() : json_false());
    json_object_set_new(cmp, "payload", pl);
    enqueue_ws_json(st, cmp);
    json_decref(cmp);

    unlink(tmpout);
    for (size_t i = 0; i < arrsz; i++) {
        free(argv[3 + i]);
    }
    free(argv);
    return rc;
}

//this other part do that
// gestion des messages du serveur
typedef struct LeaseTask {
    AgentState *st;
    char *jobId;
    char *inputUrl;
    char *outputUrl;
    char *outputExt;
    json_t *args;
} LeaseTask;

static void *lease_thread(void *arg) {
    LeaseTask *t = (LeaseTask*)arg;
    run_ffmpeg_and_upload(t->st, t->jobId, t->inputUrl, t->outputUrl, t->args, t->outputExt);
    t->st->active_jobs -= 1;
    json_decref(t->args);
    free(t->jobId); free(t->inputUrl); free(t->outputUrl); free(t->outputExt); free(t);
    return NULL;
}

static void handle_message(AgentState *st, const char *txt) {
    json_error_t jerr; json_t *root = json_loads(txt, 0, &jerr);
    if (!root) return;
    const char *type = json_string_value(json_object_get(root, "type"));
    if (type && strcmp(type, "lease") == 0) {
        json_t *p = json_object_get(root, "payload");
        const char *jobId = json_string_value(json_object_get(p, "jobId"));
        const char *inputUrl = json_string_value(json_object_get(p, "inputUrl"));
        const char *outputUrl = json_string_value(json_object_get(p, "outputUrl"));
        const char *outputExt = json_string_value(json_object_get(p, "outputExt"));
        json_t *args = json_object_get(p, "ffmpegArgs");
        if (!jobId || !inputUrl || !outputUrl || !json_is_array(args)) { json_decref(root); return; }
        if (!outputExt) outputExt = ".out";
        if (st->active_jobs >= st->concurrency) { json_decref(root); return; }
        st->active_jobs += 1;
        json_t *acc = json_object();
        json_t *pl = json_object();
        json_object_set_new(acc, "type", json_string("lease-accepted"));
        json_object_set_new(pl, "agentId", json_string(st->agent_id));
        json_object_set_new(pl, "jobId", json_string(jobId));
        json_object_set_new(acc, "payload", pl);
        enqueue_ws_json(st, acc);
        json_decref(acc);
        LeaseTask *t = (LeaseTask*)calloc(1, sizeof(LeaseTask));
        t->st = st;
        t->jobId = strdup(jobId);
        t->inputUrl = strdup(inputUrl);
        t->outputUrl = strdup(outputUrl);
        t->outputExt = strdup(outputExt);
        t->args = json_deep_copy(args);
        pthread_t th; pthread_create(&th, NULL, lease_thread, t); pthread_detach(th);
    }
    json_decref(root);
}

//this other part do that
// callback libwebsockets
static int ws_callback(struct lws *wsi, enum lws_callback_reasons reason, void *user, void *in, size_t len) {
    AgentState *st = (AgentState*)lws_context_user(lws_get_context(wsi));
    switch (reason) {
        case LWS_CALLBACK_CLIENT_ESTABLISHED: {
            st->wsi = wsi;
            json_t *msg = json_object();
            json_object_set_new(msg, "type", json_string("register"));
            json_t *pl = json_object();
            json_object_set_new(pl, "id", json_string(st->agent_id));
            json_object_set_new(pl, "name", json_string(st->agent_id));
            json_object_set_new(pl, "concurrency", json_integer(st->concurrency));
            json_t *enc = detect_encoders(st->ffmpeg_path);
            json_object_set_new(pl, "encoders", enc);
            json_object_set_new(pl, "token", json_string(st->agent_token));
            json_object_set_new(msg, "payload", pl);
            enqueue_ws_json(st, msg);
            json_decref(msg);
            break;
        }
        case LWS_CALLBACK_CLIENT_RECEIVE: {
            char *txt = (char*)malloc(len + 1);
            if (!txt) break;
            memcpy(txt, in, len); txt[len] = '\0';
            handle_message(st, txt);
            free(txt);
            break;
        }
        case LWS_CALLBACK_CLIENT_WRITEABLE: {
            char *text = dequeue_ws_text(st);
            if (text) {
                size_t slen = strlen(text);
                unsigned char *buf = (unsigned char*)malloc(LWS_PRE + slen);
                if (buf) {
                    memcpy(buf + LWS_PRE, text, slen);
                    lws_write(wsi, buf + LWS_PRE, slen, LWS_WRITE_TEXT);
                    free(buf);
                }
                free(text);
                if (st->msg_head) lws_callback_on_writable(wsi);
            }
            break;
        }
        case LWS_CALLBACK_CLIENT_CONNECTION_ERROR:
        case LWS_CALLBACK_CLOSED:
            st->should_exit = 1;
            st->wsi = NULL;
            break;
        default:
            break;
    }
    return 0;
}

//this other part do that
// boucle de heartbeats
static void send_heartbeat_periodic(AgentState *st) {
    static time_t last = 0;
    time_t now = time(NULL);
    if (now - last >= 10) {
        last = now;
        json_t *msg = json_object();
        json_object_set_new(msg, "type", json_string("heartbeat"));
        json_t *pl = json_object();
        json_object_set_new(pl, "id", json_string(st->agent_id));
        json_object_set_new(pl, "activeJobs", json_integer(st->active_jobs));
        json_object_set_new(msg, "payload", pl);
        if (st->wsi) enqueue_ws_json(st, msg);
        json_decref(msg);
    }
}

//this other part do that
// création du contexte WS et connexion
static int ws_callback(struct lws *wsi, enum lws_callback_reasons reason, void *user, void *in, size_t len);
static struct lws *connect_ws(AgentState *st) {
    struct lws_context_creation_info info;
    memset(&info, 0, sizeof(info));
    info.port = CONTEXT_PORT_NO_LISTEN;
    info.options = LWS_SERVER_OPTION_DO_SSL_GLOBAL_INIT;
    info.user = st;
    static struct lws_protocols protocols[] = {
        { "ws", ws_callback, 0, 4096, 0, NULL, 0 },
        { NULL, NULL, 0, 0, 0, NULL, 0 }
    };
    info.protocols = protocols;
    st->lws_ctx = lws_create_context(&info);
    if (!st->lws_ctx) return NULL;

    struct lws_client_connect_info ccinfo;
    memset(&ccinfo, 0, sizeof(ccinfo));

    char url[2048];
    char esc[512];
    size_t ti = 0; for (const char *p = st->agent_token; *p && ti + 3 < sizeof(esc); p++) {
        unsigned char c = (unsigned char)*p;
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.') esc[ti++] = c; else ti += snprintf(esc + ti, sizeof(esc) - ti, "%%%02X", c);
    }
    esc[ti] = '\0';
    snprintf(url, sizeof(url), "%s/agent?token=%s", st->controller_ws, esc);

    const char *proto = (strncmp(url, "wss://", 6) == 0) ? "wss" : "ws";
    const char *hostbegin = strstr(url, "://");
    hostbegin = hostbegin ? hostbegin + 3 : url;
    const char *path = strchr(hostbegin, '/');
    char hostport[512];
    if (path) {
        size_t hl = (size_t)(path - hostbegin);
        if (hl >= sizeof(hostport)) hl = sizeof(hostport) - 1;
        memcpy(hostport, hostbegin, hl); hostport[hl] = '\0';
    } else {
        snprintf(hostport, sizeof(hostport), "%s", hostbegin);
        path = "/";
    }
    char host[512]; int port = (strcmp(proto, "wss") == 0) ? 443 : 80;
    const char *colon = strchr(hostport, ':');
    if (colon) {
        size_t hl = (size_t)(colon - hostport);
        if (hl >= sizeof(host)) hl = sizeof(host) - 1;
        memcpy(host, hostport, hl); host[hl] = '\0';
        port = atoi(colon + 1);
        if (port <= 0) port = (strcmp(proto, "wss") == 0) ? 443 : 80;
    } else {
        snprintf(host, sizeof(host), "%s", hostport);
    }
    int ssl = (strcmp(proto, "wss") == 0) ? 1 : 0;

    ccinfo.context = st->lws_ctx;
    ccinfo.address = host;
    ccinfo.path = path;
    ccinfo.host = host;
    ccinfo.origin = host;
    ccinfo.protocol = "ws";
    ccinfo.pwsi = &st->wsi;
    ccinfo.port = port;
    ccinfo.ssl_connection = ssl ? LCCSCF_USE_SSL : 0;
    ccinfo.method = "GET";
    ccinfo.userdata = NULL;

    return lws_client_connect_via_info(&ccinfo);
}

//this other part do that
// point d'entrée
int main(void) {
    curl_global_init(CURL_GLOBAL_DEFAULT);

    AgentState st; memset(&st, 0, sizeof(st));
    pthread_mutex_init(&st.msg_mutex, NULL);
    snprintf(st.controller_url, sizeof(st.controller_url), "%s", getenv_default("CONTROLLER_URL", "http://localhost:4000"));
    http_to_ws(st.controller_url, st.controller_ws, sizeof(st.controller_ws));
    snprintf(st.agent_token, sizeof(st.agent_token), "%s", getenv_default("AGENT_TOKEN", "dev-token"));
    snprintf(st.ffmpeg_path, sizeof(st.ffmpeg_path), "%s", getenv_default("FFMPEG_PATH", "ffmpeg"));
    st.concurrency = (int)sysconf(_SC_NPROCESSORS_ONLN);
    if (st.concurrency <= 0) st.concurrency = 1;
    snprintf(st.agent_id, sizeof(st.agent_id), "%s-%d", getenv_default("HOSTNAME", "agent"), getpid());

    if (!connect_ws(&st)) {
        fprintf(stderr, "websocket connection failed\n");
        return 1;
    }

    while (!st.should_exit) {
        lws_service(st.lws_ctx, 50);
        send_heartbeat_periodic(&st);
    }

    if (st.lws_ctx) lws_context_destroy(st.lws_ctx);
    pthread_mutex_destroy(&st.msg_mutex);
    curl_global_cleanup();
    return 0;
}