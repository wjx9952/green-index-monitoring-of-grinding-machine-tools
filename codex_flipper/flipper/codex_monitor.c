#include <furi.h>
#include <gui/gui.h>
#include <input/input.h>
#include <notification/notification_messages.h>
#include <bt/bt_service/bt.h>
#include <furi_ble/profile_interface.h>
#include <services/serial_service.h>
#include <furi_hal_version.h>
#include <ble/core/ble_defs.h>

#define TAG "CodexMonitor"
#define RX_SIZE 512

typedef struct {
    Gui* gui;
    ViewPort* viewport;
    NotificationApp* notifications;
    Bt* bt;
    FuriHalBleProfileBase* ble_profile;
    FuriMessageQueue* queue;
    FuriMutex* lock;
    char rx[RX_SIZE];
    size_t rx_len;
    char status[16];
    char summary[72];
    int primary;
    int secondary;
    bool connected;
    bool approval;
} CodexMonitor;

typedef struct {
    FuriHalBleProfileBase base;
    BleServiceSerial* serial;
} CodexBleProfile;

static const FuriHalBleProfileTemplate codex_ble_template;

static FuriHalBleProfileBase* codex_ble_start(FuriHalBleProfileParams params) {
    UNUSED(params);
    CodexBleProfile* profile = malloc(sizeof(CodexBleProfile));
    profile->base.config = &codex_ble_template;
    profile->serial = ble_svc_serial_start();
    ble_svc_serial_set_rpc_active(profile->serial, false);
    return &profile->base;
}

static void codex_ble_stop(FuriHalBleProfileBase* base) {
    furi_check(base && base->config == &codex_ble_template);
    CodexBleProfile* profile = (CodexBleProfile*)base;
    ble_svc_serial_stop(profile->serial);
    free(profile);
}

static void codex_ble_get_config(GapConfig* config, FuriHalBleProfileParams params) {
    UNUSED(params);
    memset(config, 0, sizeof(GapConfig));
    config->adv_service.UUID_Type = UUID_TYPE_16;
    config->adv_service.Service_UUID_16 = 0x3080 | furi_hal_version_get_hw_color();
    config->appearance_char = 0x8600;
    /* This private local link carries no credentials and intentionally avoids
       PIN/bonding so a headless Raspberry Pi can connect reliably. */
    config->bonding_mode = false;
    config->pairing_method = GapPairingNone;
    config->conn_param.conn_int_min = 0x06;
    config->conn_param.conn_int_max = 0x24;
    config->conn_param.slave_latency = 0;
    config->conn_param.supervisor_timeout = 0;
    memcpy(config->mac_address, furi_hal_version_get_ble_mac(), sizeof(config->mac_address));
    strlcpy(
        config->adv_name,
        furi_hal_version_get_ble_local_device_name_ptr(),
        FURI_HAL_VERSION_DEVICE_NAME_LENGTH);
}

static const FuriHalBleProfileTemplate codex_ble_template = {
    .start = codex_ble_start,
    .stop = codex_ble_stop,
    .get_gap_config = codex_ble_get_config,
};

static BleServiceSerial* codex_serial(CodexMonitor* app) {
    return ((CodexBleProfile*)app->ble_profile)->serial;
}

static const NotificationSequence blink = {
    &message_red_255,
    &message_delay_100,
    &message_red_0,
    &message_delay_100,
    NULL,
};

static void copy_json_string(const char* json, const char* key, char* out, size_t out_size) {
    char needle[32];
    snprintf(needle, sizeof(needle), "\"%s\":\"", key);
    const char* start = strstr(json, needle);
    if(!start) return;
    start += strlen(needle);
    size_t i = 0;
    while(start[i] && start[i] != '"' && i + 1 < out_size) {
        out[i] = start[i];
        i++;
    }
    out[i] = '\0';
}

static int copy_json_int(const char* json, const char* key, int fallback) {
    char needle[32];
    snprintf(needle, sizeof(needle), "\"%s\":", key);
    const char* start = strstr(json, needle);
    if(!start || !strncmp(start + strlen(needle), "null", 4)) return fallback;
    return atoi(start + strlen(needle));
}

static void parse_line(CodexMonitor* app, const char* line) {
    furi_mutex_acquire(app->lock, FuriWaitForever);
    char op[16] = "";
    copy_json_string(line, "op", op, sizeof(op));
    if(!strcmp(op, "approval")) {
        app->approval = true;
        strlcpy(app->status, "APPROVAL", sizeof(app->status));
        copy_json_string(line, "summary", app->summary, sizeof(app->summary));
        notification_message(app->notifications, &blink);
    } else if(!strcmp(op, "state")) {
        copy_json_string(line, "status", app->status, sizeof(app->status));
        copy_json_string(line, "summary", app->summary, sizeof(app->summary));
        app->primary = copy_json_int(line, "primary", app->primary);
        app->secondary = copy_json_int(line, "secondary", app->secondary);
        app->approval = !strcmp(app->status, "approval");
    }
    furi_mutex_release(app->lock);
    view_port_update(app->viewport);
}

static uint16_t serial_callback(SerialServiceEvent event, void* context) {
    CodexMonitor* app = context;
    if(event.event != SerialServiceEventTypeDataReceived) return 0;
    for(size_t i = 0; i < event.data.size; i++) {
        char c = event.data.buffer[i];
        if(c == '\n') {
            app->rx[app->rx_len] = '\0';
            parse_line(app, app->rx);
            app->rx_len = 0;
        } else if(app->rx_len + 1 < sizeof(app->rx)) {
            app->rx[app->rx_len++] = c;
        } else {
            app->rx_len = 0;
        }
    }
    return event.data.size;
}

static void bt_status(BtStatus status, void* context) {
    CodexMonitor* app = context;
    app->connected = status == BtStatusConnected;
    view_port_update(app->viewport);
}

static void draw(Canvas* canvas, void* context) {
    CodexMonitor* app = context;
    furi_mutex_acquire(app->lock, FuriWaitForever);
    canvas_clear(canvas);
    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str(canvas, 2, 10, "Codex");
    canvas_set_font(canvas, FontSecondary);
    canvas_draw_str(canvas, 91, 9, app->connected ? "BLE OK" : "WAIT BLE");
    canvas_draw_line(canvas, 0, 13, 127, 13);
    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str(canvas, 2, 27, app->status);
    char quota[32];
    snprintf(quota, sizeof(quota), "5h %d%%  Week %d%%", app->primary, app->secondary);
    canvas_set_font(canvas, FontSecondary);
    canvas_draw_str(canvas, 2, 39, quota);
    canvas_draw_str(canvas, 2, 50, app->summary);
    canvas_draw_str(canvas, 2, 62, app->approval ? "OK approve | hold deny" : "Back exit");
    furi_mutex_release(app->lock);
}

static void input(InputEvent* event, void* context) {
    CodexMonitor* app = context;
    furi_message_queue_put(app->queue, event, 0);
}

static void send_decision(CodexMonitor* app, bool accept) {
    const char* msg = accept ? "{\"op\":\"approve\"}\n" : "{\"op\":\"decline\"}\n";
    ble_svc_serial_update_tx(codex_serial(app), (uint8_t*)msg, strlen(msg));
    app->approval = false;
    strlcpy(app->status, "working", sizeof(app->status));
    strlcpy(app->summary, accept ? "approved" : "declined", sizeof(app->summary));
    notification_message(app->notifications, &sequence_blink_green_100);
    view_port_update(app->viewport);
}

int32_t codex_monitor_app(void* p) {
    UNUSED(p);
    CodexMonitor* app = calloc(1, sizeof(CodexMonitor));
    app->primary = app->secondary = -1;
    strlcpy(app->status, "starting", sizeof(app->status));
    strlcpy(app->summary, "open Pi bridge", sizeof(app->summary));
    app->queue = furi_message_queue_alloc(8, sizeof(InputEvent));
    app->lock = furi_mutex_alloc(FuriMutexTypeNormal);
    app->viewport = view_port_alloc();
    view_port_draw_callback_set(app->viewport, draw, app);
    view_port_input_callback_set(app->viewport, input, app);
    app->gui = furi_record_open(RECORD_GUI);
    app->notifications = furi_record_open(RECORD_NOTIFICATION);
    app->bt = furi_record_open(RECORD_BT);
    gui_add_view_port(app->gui, app->viewport, GuiLayerFullscreen);

    app->ble_profile = bt_profile_start(app->bt, &codex_ble_template, NULL);
    if(!app->ble_profile) {
        strlcpy(app->status, "BLE ERROR", sizeof(app->status));
        strlcpy(app->summary, "profile start failed", sizeof(app->summary));
    }
    if(app->ble_profile) {
        ble_svc_serial_set_callbacks(codex_serial(app), RX_SIZE, serial_callback, app);
    }
    bt_set_status_changed_callback(app->bt, bt_status, app);

    bool running = true;
    while(running) {
        InputEvent event;
        if(furi_message_queue_get(app->queue, &event, 250) == FuriStatusOk) {
            if(event.key == InputKeyBack && event.type == InputTypeShort) running = false;
            if(event.key == InputKeyOk && app->approval) {
                if(event.type == InputTypeShort) send_decision(app, true);
                if(event.type == InputTypeLong) send_decision(app, false);
            }
        }
        if(app->approval) notification_message(app->notifications, &blink);
    }

    if(app->ble_profile) {
        ble_svc_serial_set_callbacks(codex_serial(app), 0, NULL, NULL);
    }
    bt_set_status_changed_callback(app->bt, NULL, NULL);
    bt_disconnect(app->bt);
    bt_profile_restore_default(app->bt);
    gui_remove_view_port(app->gui, app->viewport);
    view_port_free(app->viewport);
    furi_message_queue_free(app->queue);
    furi_mutex_free(app->lock);
    furi_record_close(RECORD_BT);
    furi_record_close(RECORD_NOTIFICATION);
    furi_record_close(RECORD_GUI);
    free(app);
    return 0;
}
