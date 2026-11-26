#include "driver.h"

/* Track the selected card's blue border in pixels read back from the native
 * window. The adjustment is a separate check for intent and final position. */
typedef struct {
  GtkWidget *window, *layout, *scroll;
  GtkAdjustment *horizontal;
  guint phase, input, missing_phase;
  gint64 since;
  GString *trace;
} Motion;

enum MotionPhase {
  PAN_SINGLE, PAN_REVERSE, PAN_REDUCED, BUTTON_ZOOM, WHEEL_ZOOM,
  RESET_ZOOM, REDUCED_ZOOM, ZOOM_AND_SELECT
};

typedef struct { guint at, key; } MotionKey;
static const MotionKey single[] = {{0, GDK_KEY_Right}};
static const MotionKey rapid[] = {{0, GDK_KEY_Left}, {60, GDK_KEY_Left},
                                  {170, GDK_KEY_Right}, {230, GDK_KEY_Right}};
static const MotionKey reduced[] = {{0, GDK_KEY_Left}};
enum { ZOOM_OUT = 1, WHEEL_DOWN, WHEEL_UP, RESET, ARROW_DOWN };
static const MotionKey zoom[] = {{0, ZOOM_OUT}};
static const MotionKey gesture[] = {{0, WHEEL_DOWN}, {65, WHEEL_DOWN}, {130, WHEEL_UP}};
static const MotionKey reset[] = {{0, RESET}};
static const MotionKey follow[] = {{0, RESET}, {35, ARROW_DOWN}};
static const struct {
  const gchar *name;
  const MotionKey *keys;
  guint count, settle;
} phases[] = {
    [PAN_SINGLE] = {"single", single, G_N_ELEMENTS(single), 550},
    [PAN_REVERSE] = {"repeat-reverse", rapid, G_N_ELEMENTS(rapid), 650},
    [PAN_REDUCED] = {"reduced", reduced, G_N_ELEMENTS(reduced), 350},
    [BUTTON_ZOOM] = {"zoom-out", zoom, G_N_ELEMENTS(zoom), 1300},
    [WHEEL_ZOOM] = {"wheel-reverse", gesture, G_N_ELEMENTS(gesture), 1300},
    [RESET_ZOOM] = {"zoom-reset", reset, G_N_ELEMENTS(reset), 1300},
    [REDUCED_ZOOM] = {"zoom-reduced", zoom, G_N_ELEMENTS(zoom), 600},
    [ZOOM_AND_SELECT] = {"zoom-and-select", follow, G_N_ELEMENTS(follow), 1300},
};

static GtkWidget *zoom_overlay(Motion *m) {
  // During a zoom, the top drawing area receives subsequent pointer events.
  GList *children = gtk_container_get_children(GTK_CONTAINER(gtk_widget_get_parent(m->scroll)));
  GtkWidget *preview = NULL;
  for (GList *item = children; item; item = item->next)
    if (GTK_IS_DRAWING_AREA(item->data) && gtk_widget_get_mapped(item->data))
      preview = item->data;
  g_list_free(children);
  return preview;
}

static guint toolbar_zoom(GtkWidget *window) {
  GtkWidget *bar = gtk_widget_get_parent(button(window, "Fit all"));
  GList *labels = gtk_container_get_children(GTK_CONTAINER(bar));
  guint zoom = 0;
  for (GList *item = labels; item; item = item->next) {
    if (!GTK_IS_LABEL(item->data))
      continue;
    gchar *suffix = NULL;
    guint64 value = g_ascii_strtoull(gtk_label_get_text(item->data), &suffix, 10);
    if (suffix && *suffix == '%')
      zoom = value;
  }
  g_list_free(labels);
  return zoom;
}

static void wheel(Motion *m, gdouble delta) {
  GtkWidget *target = zoom_overlay(m);
  if (!target)
    target = find(m->window, GTK_TYPE_LAYOUT, NULL, NULL);
  if (!target)
    fail("Cannot deliver wheel input to the visible map.");
  gint wx, wy, sx, sy;
  gdk_window_get_origin(gtk_widget_get_window(m->window), &wx, &wy);
  gtk_widget_translate_coordinates(m->scroll, m->window, 0, 0, &sx, &sy);
  GdkEvent *event = gdk_event_new(GDK_SCROLL);
  event->scroll.window = g_object_ref(gtk_widget_get_window(target));
  event->scroll.send_event = TRUE;
  event->scroll.direction = GDK_SCROLL_SMOOTH;
  event->scroll.state = GDK_CONTROL_MASK;
  event->scroll.delta_y = delta;
  event->scroll.x_root = wx + sx + .55 * gtk_adjustment_get_page_size(m->horizontal);
  event->scroll.y_root = wy + sy + .5 * gtk_adjustment_get_page_size(
      gtk_scrolled_window_get_vadjustment(GTK_SCROLLED_WINDOW(m->scroll)));
  GdkSeat *seat = gdk_display_get_default_seat(gdk_display_get_default());
  gdk_event_set_device(event, gdk_seat_get_pointer(seat));
  gtk_widget_event(target, event);
  gdk_event_free(event);
}

static void sample_border(Motion *m, gdouble *left, gdouble *width) {
  gint x, y;
  if (!gtk_widget_translate_coordinates(m->scroll, m->window, 1, 1, &x, &y))
    fail("Cannot locate the native scroll viewport.");
  gint w = gtk_widget_get_allocated_width(m->scroll) - 3;
  gint h = gtk_widget_get_allocated_height(m->scroll) - 3;
  GdkPixbuf *image = gdk_pixbuf_get_from_window(gtk_widget_get_window(m->window),
                                                x, y, w, h);
  if (!image)
    fail("Cannot sample the painted card outline.");
  gint iw = gdk_pixbuf_get_width(image), ih = gdk_pixbuf_get_height(image);
  gint ch = gdk_pixbuf_get_n_channels(image), stride = gdk_pixbuf_get_rowstride(image);
  const guchar *bytes = gdk_pixbuf_read_pixels(image);
  gint best = 0, start = 0;
  for (gint row = 0; row < ih; ++row) {
    const guchar *p = bytes + row * stride;
    gint run = 0;
    for (gint col = 0; col < iw; col += 2, p += 2 * ch) {
      // Bilinear scaling blends the blue stroke (#58a6ff) with the dark
      // canvas, especially on a 1x display. Follow its hue across that range.
      if (p[2] >= 110 && p[2] > p[1] + 30 && p[1] > p[0] + 28) {
        run += 2;
        if (run > best) { best = run; start = col + 2 - run; }
      } else {
        run = 0;
      }
    }
  }
  *left = best > 35 ? (gdouble)start * w / iw : -1;
  *width = (gdouble)best * w / iw;
  if (*left < 0 && m->missing_phase != m->phase + 1) {
    m->missing_phase = m->phase + 1;
    gchar *path = g_strdup_printf("%s/%s.missing.png", g_getenv("NATIVE_CAPTURES"),
                                  phases[m->phase].name);
    gdk_pixbuf_save(image, path, "png", NULL, NULL);
    g_free(path);
  }
  g_object_unref(image);
}

static void setup_zoom(Motion *m) {
  GtkWidget *card = find(m->layout, GTK_TYPE_WIDGET, NULL, "focused-card");
  GtkAllocation allocation;
  gtk_widget_get_allocation(card, &allocation);
  GtkAdjustment *vertical = gtk_scrolled_window_get_vadjustment(GTK_SCROLLED_WINDOW(m->scroll));
  gtk_adjustment_set_value(m->horizontal, allocation.x + allocation.width / 2.
                                             - gtk_adjustment_get_page_size(m->horizontal) / 2.);
  gtk_adjustment_set_value(vertical, allocation.y + allocation.height / 2.
                                         - gtk_adjustment_get_page_size(vertical) / 2.);
  g_object_set(gtk_settings_get_default(), "gtk-enable-animations", TRUE, NULL);
}

static gboolean sample_motion(gpointer data) {
  Motion *m = data;
  gint64 now = g_get_monotonic_time();
  gint elapsed = (now - m->since) / 1000;
  if (elapsed < 0)
    return G_SOURCE_CONTINUE;
  gdouble left, width;
  sample_border(m, &left, &width);
  g_string_append_printf(m->trace, "%s,%d,%u,%.1f,%.1f,%.1f,%.1f\n",
      phases[m->phase].name, elapsed, m->input, left, width,
      gtk_adjustment_get_value(m->horizontal), gtk_adjustment_get_page_size(m->horizontal));
  const MotionKey *keys = phases[m->phase].keys;
  if (m->input < phases[m->phase].count && elapsed >= (gint)keys[m->input].at) {
    guint key = keys[m->input++].key;
    if (key == ZOOM_OUT || key == RESET)
      gtk_button_clicked(GTK_BUTTON(button(m->window, key == RESET ? "100%" : "−")));
    else if (key == ARROW_DOWN)
      press_key(m->window, GDK_KEY_Down);
    else if (key == WHEEL_DOWN || key == WHEEL_UP)
      wheel(m, key == WHEEL_DOWN ? 3 : -3);
    else
      key_event(m->window, key, GDK_SHIFT_MASK);
  } else if (m->input == phases[m->phase].count &&
             elapsed >= (gint)phases[m->phase].settle) {
    GtkWidget *preview = zoom_overlay(m);
    if (preview && elapsed < 5000)
      return G_SOURCE_CONTINUE;
    if (preview)
      fail("Zoom did not return to interactive cards within five seconds.");
    m->layout = find(m->window, GTK_TYPE_LAYOUT, NULL, NULL);
    GtkWidget *card = m->layout ? find(m->layout, GTK_TYPE_WIDGET, NULL, "focused-card") : NULL;
    GtkWidget *label = card ? find(card, GTK_TYPE_LABEL, NULL, NULL) : NULL;
    gchar *path = label ? gtk_widget_get_tooltip_text(label) : NULL;
    gchar **paths = g_strsplit(g_getenv("NATIVE_NAVIGATION"), "\n", -1);
    const gchar *expected = m->phase == ZOOM_AND_SELECT ? g_getenv("NATIVE_NEXT_BACKEND") : paths[2];
    if (g_strcmp0(path, expected) != 0) {
      report("motion.csv", m->trace->str);
      screenshot(m->window, "motion-failed");
      fail(g_strdup_printf("%s: expected selected %s, found %s",
                           phases[m->phase].name, expected, path ? path : "(none)"));
    }
    // Prove that the painted outline belongs to the final, interactive
    // GTK widget, including after the preview owns the window for a while.
    gint card_x, card_y;
    gtk_widget_translate_coordinates(card, m->scroll, 0, 0, &card_x, &card_y);
    if (ABS(left - (card_x - 1)) > 3 || ABS(width - gtk_widget_get_allocated_width(card)) > 3) {
      if (elapsed < 5000) {
        g_free(path);
        g_strfreev(paths);
        return G_SOURCE_CONTINUE;
      }
      fail(g_strdup_printf("%s: painted card %.1f/%.1f differs from GTK card %d/%d",
                           phases[m->phase].name, left, width, card_x - 1,
                           gtk_widget_get_allocated_width(card)));
    }
    g_free(path);
    g_strfreev(paths);
    const guint expected_zoom[] = {100, 100, 100, 83, 66, 100, 83, 100};
    if (toolbar_zoom(m->window) != expected_zoom[m->phase])
      fail(g_strdup_printf("%s: expected %u%% zoom", phases[m->phase].name, expected_zoom[m->phase]));
    screenshot(m->window, phases[m->phase].name);
    if (++m->phase == G_N_ELEMENTS(phases)) {
      report("motion.csv", m->trace->str);
      g_string_free(m->trace, TRUE);
      g_free(m);
      finish();
      return G_SOURCE_REMOVE;
    }
    m->input = 0;
    if (m->phase == PAN_REDUCED || m->phase == REDUCED_ZOOM)
      g_object_set(gtk_settings_get_default(), "gtk-enable-animations", FALSE, NULL);
    if (m->phase == BUTTON_ZOOM)
      setup_zoom(m);
    if (m->phase == ZOOM_AND_SELECT)
      g_object_set(gtk_settings_get_default(), "gtk-enable-animations", TRUE, NULL);
    m->since = g_get_monotonic_time() + 150000;
  }
  return G_SOURCE_CONTINUE;
}

/* A repeating timeout whose capture outlasts its interval may starve GTK's
 * lower-priority redraws. Always allow the main loop to paint between reads. */
static gboolean next_sample(gpointer data) {
  if (sample_motion(data))
    g_timeout_add(7, next_sample, data);
  return G_SOURCE_REMOVE;
}

void start_motion(GtkWidget *window, GtkWidget *layout) {
  Motion *m = g_new0(Motion, 1);
  m->window = window;
  m->layout = layout;
  m->scroll = gtk_widget_get_ancestor(layout, GTK_TYPE_SCROLLED_WINDOW);
  m->horizontal = gtk_scrolled_window_get_hadjustment(GTK_SCROLLED_WINDOW(m->scroll));
  g_object_set(gtk_settings_get_default(), "gtk-enable-animations", TRUE, NULL);
  press_key(window, GDK_KEY_Right);
  press_key(window, GDK_KEY_Right);
  GtkWidget *card = find(layout, GTK_TYPE_WIDGET, NULL, "focused-card");
  if (!card)
    fail("Cannot position the first Backend file for native pan profiling.");
  GtkAllocation a;
  gtk_widget_get_allocation(card, &a);
  gtk_adjustment_set_value(m->horizontal, a.x - .55 * gtk_adjustment_get_page_size(m->horizontal));
  m->since = g_get_monotonic_time() + 200000;
  m->trace = g_string_new("phase,ms,inputs,left,width,camera,page\n");
  g_timeout_add(7, next_sample, m);
}
