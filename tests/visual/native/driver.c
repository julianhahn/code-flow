/* GTK's test module API lets both unmodified executables run exactly the same
 * events and read their own real window. No system-wide input permission. */
#include <gtk/gtk.h>
#include <stdlib.h>
#include <string.h>

static const gchar *destination;
static gint64 started;
static guint stage;
static guint overview_settle;
static gdouble initial_x, initial_y;
static const gdouble positions[][2] = {
    {750, 650}, {721, 629}, {692, 603}, {668, 585}, {640, 562}};

static void report(const gchar *filename, const gchar *contents) {
  gchar *path = g_build_filename(destination, filename, NULL);
  g_file_set_contents(path, contents, -1, NULL);
  g_free(path);
}

static void fail(const gchar *reason) {
  report("error", reason);
  exit(EXIT_FAILURE);
}

static void finish(void) {
  report("complete", "OK");
  gtk_main_quit();
}

static GtkWidget *review_window(void) {
  GList *windows = gtk_window_list_toplevels();
  GtkWidget *result = NULL;
  for (GList *w = windows; w && !result; w = w->next) {
    const gchar *name = gtk_window_get_title(GTK_WINDOW(w->data));
    if (g_strcmp0(name, "Code Flow — Review") == 0 &&
        gtk_widget_get_mapped(w->data))
      result = w->data;
  }
  g_list_free(windows);
  return result;
}

static GtkWidget *find(GtkWidget *root, GType type, const gchar *button_text,
                       const gchar *style) {
  if (!gtk_widget_get_mapped(root))
    return NULL;
  if (g_type_is_a(G_OBJECT_TYPE(root), type)) {
    const gchar *label =
        GTK_IS_BUTTON(root) ? gtk_button_get_label(GTK_BUTTON(root)) : NULL;
    if ((!button_text || g_strcmp0(label, button_text) == 0) &&
        (!style || gtk_style_context_has_class(
                       gtk_widget_get_style_context(root), style)))
      return root;
  }
  if (!GTK_IS_CONTAINER(root))
    return NULL;
  GList *children = gtk_container_get_children(GTK_CONTAINER(root));
  GtkWidget *result = NULL;
  for (GList *w = children; w && !result; w = w->next)
    result = find(w->data, type, button_text, style);
  g_list_free(children);
  return result;
}

static GtkWidget *button(GtkWidget *window, const gchar *text) {
  return find(window, GTK_TYPE_BUTTON, text, NULL);
}

static void screenshot(GtkWidget *window, const gchar *label) {
  GdkPixbuf *image =
      gdk_pixbuf_get_from_window(gtk_widget_get_window(window), 0, 0,
                                 gtk_widget_get_allocated_width(window),
                                 gtk_widget_get_allocated_height(window));
  if (!image)
    fail("The native window is unavailable for capture.");
  gchar *name = g_strdup_printf("%s/%s.png", destination, label);
  GError *error = NULL;
  if (!gdk_pixbuf_save(image, name, "png", &error, NULL))
    fail(error->message);
  g_free(name);
  g_object_unref(image);
}

static void drag_event(GtkWidget *layout, GdkEventType type, gdouble x,
                       gdouble y) {
  GdkEvent *event = gdk_event_new(type);
  event->any.window = g_object_ref(gtk_widget_get_window(layout));
  event->any.send_event = TRUE;
  if (type == GDK_MOTION_NOTIFY) {
    event->motion.x = event->motion.x_root = x;
    event->motion.y = event->motion.y_root = y;
    event->motion.state = GDK_BUTTON2_MASK;
  } else {
    event->button.button = 2;
    event->button.x = event->button.x_root = x;
    event->button.y = event->button.y_root = y;
  }
  GdkSeat *seat = gdk_display_get_default_seat(gdk_display_get_default());
  gdk_event_set_device(event, gdk_seat_get_pointer(seat));
  gtk_widget_event(layout, event);
  gdk_event_free(event);
}

static GdkPixbuf *sample(GtkWidget *window) {
  return gdk_pixbuf_get_from_window(gtk_widget_get_window(window), 100, 370,
                                    380, 240);
}

typedef struct {
  GtkWidget *window, *layout, *selected;
  GdkPixbuf *previous;
  gint64 start, last_change;
  guint inputs;
  GArray *intervals;
} Profile;

static gint compare_double(gconstpointer a, gconstpointer b) {
  const gdouble x = *(const gdouble *)a, y = *(const gdouble *)b;
  return (x > y) - (x < y);
}

static gboolean sample_pan(gpointer user_data) {
  Profile *profile = user_data;
  gint64 now = g_get_monotonic_time();
  gdouble seconds = (now - profile->start) / 1e6;
  gdouble x = 750 - 140 * seconds;
  gdouble y = 650 - 185 * seconds;
  if (seconds < 2) {
    drag_event(profile->layout, GDK_MOTION_NOTIFY, x, y);
    ++profile->inputs;
    GdkPixbuf *frame = sample(profile->window);
    if (!frame || gdk_pixbuf_get_byte_length(frame) !=
                      gdk_pixbuf_get_byte_length(profile->previous))
      fail("Lost the sampled area of the native window during the drag.");
    if (memcmp(gdk_pixbuf_read_pixels(frame),
               gdk_pixbuf_read_pixels(profile->previous),
               gdk_pixbuf_get_byte_length(frame)) != 0) {
      gdouble ms = (now - profile->last_change) / 1000.;
      g_array_append_val(profile->intervals, ms);
      profile->last_change = now;
    }
    g_object_unref(profile->previous);
    profile->previous = frame;
    return G_SOURCE_CONTINUE;
  }

  drag_event(profile->layout, GDK_BUTTON_RELEASE, x, y);
  if (find(profile->layout, GTK_TYPE_WIDGET, NULL, "focused-card") !=
      profile->selected)
    fail("The selected card changed or was rebuilt during the drag.");
  GArray *intervals = profile->intervals;
  if (intervals->len < 30)
    fail("Fewer than 30 distinct painted frames during the two-second drag.");
  g_array_sort(intervals, compare_double);
  gchar *summary = g_strdup_printf(
      "160 files, %u synthetic GTK motion events, %u distinct painted updates "
      "in two seconds. "
      "Intervals (ms): median %.1f, p95 %.1f, maximum %.1f. Selection and card "
      "identity retained.",
      profile->inputs, intervals->len,
      g_array_index(intervals, gdouble, intervals->len / 2),
      g_array_index(intervals, gdouble, (intervals->len - 1) * 95 / 100),
      g_array_index(intervals, gdouble, intervals->len - 1));
  report("pan.txt", summary);
  g_free(summary);
  g_object_unref(profile->previous);
  g_array_unref(intervals);
  g_free(profile);
  finish();
  return G_SOURCE_REMOVE;
}

static void start_profile(GtkWidget *window, GtkWidget *layout) {
  Profile *profile = g_new0(Profile, 1);
  profile->window = window;
  profile->layout = layout;
  profile->selected = find(layout, GTK_TYPE_WIDGET, NULL, "focused-card");
  profile->previous = sample(window);
  if (!profile->selected || !profile->previous)
    fail("Expected a selected card on a painted map.");
  profile->intervals = g_array_new(FALSE, FALSE, sizeof(gdouble));
  drag_event(layout, GDK_BUTTON_PRESS, 750, 650);
  profile->last_change = profile->start = g_get_monotonic_time();
  g_timeout_add(4, sample_pan, profile);
}

static gboolean advance(gpointer unused) {
  if (g_get_monotonic_time() - started > 60 * G_USEC_PER_SEC) {
    gchar *message = g_strdup_printf(
        "Timed out waiting for native GTK scenario stage %u", stage);
    fail(message);
  }
  GtkWidget *window = review_window();
  if (!window)
    return G_SOURCE_CONTINUE;
  GtkWidget *layout = find(window, GTK_TYPE_LAYOUT, NULL, NULL);

  switch (stage) {
  case 0:
    gtk_window_move(GTK_WINDOW(window), 55, 60);
    gtk_window_resize(GTK_WINDOW(window), 1350, 800);
    gtk_window_present(GTK_WINDOW(window));
    break;
  case 1: {
    GtkWidget *recent = button(window, "Pr · Review document publishing");
    if (!recent)
      return G_SOURCE_CONTINUE;
    screenshot(window, "dashboard");
    gtk_button_clicked(GTK_BUTTON(recent));
    break;
  }
  case 2: {
    GtkWidget *open = button(window, "Open diff map");
    if (!open || ++overview_settle < 3)
      return G_SOURCE_CONTINUE;
    screenshot(window, "overview");
    gtk_button_clicked(GTK_BUTTON(open));
    break;
  }
  case 3:
    if (!button(window, "Back to overview") || !layout)
      return G_SOURCE_CONTINUE;
    break;
  default: {
    if (!layout)
      fail("The visible map disappeared.");
    GtkScrolledWindow *scroll = GTK_SCROLLED_WINDOW(
        gtk_widget_get_ancestor(layout, GTK_TYPE_SCROLLED_WINDOW));
    GtkAdjustment *h = gtk_scrolled_window_get_hadjustment(scroll),
                  *v = gtk_scrolled_window_get_vadjustment(scroll);
    if (stage == 4) {
      screenshot(window, "map");
      if (g_strcmp0(g_getenv("NATIVE_STRESS"), "1") == 0) {
        start_profile(window, layout);
        return G_SOURCE_REMOVE;
      }
      initial_x = gtk_adjustment_get_value(h);
      initial_y = gtk_adjustment_get_value(v);
      drag_event(layout, GDK_BUTTON_PRESS, positions[0][0], positions[0][1]);
    } else if (stage <= 8) {
      guint i = stage - 4;
      if (i > 1) {
        if (ABS(gtk_adjustment_get_value(h) -
                (initial_x + positions[0][0] - positions[i - 1][0])) > .5 ||
            ABS(gtk_adjustment_get_value(v) -
                (initial_y + positions[0][1] - positions[i - 1][1])) > .5)
          fail("The camera did not follow the middle drag while the button was "
               "held.");
      }
      drag_event(layout, GDK_MOTION_NOTIFY, positions[i][0], positions[i][1]);
    } else if (stage == 9) {
      drag_event(layout, GDK_BUTTON_RELEASE, positions[4][0], positions[4][1]);
      screenshot(window, "pan");
      gtk_button_clicked(GTK_BUTTON(button(window, "Fit all")));
    } else if (stage == 10) {
      screenshot(window, "fit");
      gtk_button_clicked(GTK_BUTTON(button(window, "Hide specs")));
    } else if (stage == 11) {
      screenshot(window, "hide-specs");
      gtk_button_clicked(GTK_BUTTON(button(window, "100%")));
    } else if (stage == 12) {
      gtk_toggle_button_set_active(
          GTK_TOGGLE_BUTTON(button(window, "Viewed (V)")), TRUE);
    } else {
      screenshot(window, "viewed");
      finish();
      return G_SOURCE_REMOVE;
    }
  }
  }
  ++stage;
  return G_SOURCE_CONTINUE;
}

G_MODULE_EXPORT void gtk_module_init(gint *argc, gchar ***argv) {
  destination = g_getenv("NATIVE_CAPTURES");
  if (!destination)
    return;
  started = g_get_monotonic_time();
  g_timeout_add(750, advance, NULL);
}
