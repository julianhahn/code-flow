#pragma once
#include <gtk/gtk.h>

void report(const gchar *filename, const gchar *contents);
G_NORETURN void fail(const gchar *reason);
void finish(void);
GtkWidget *find(GtkWidget *root, GType type, const gchar *button_text, const gchar *style);
GtkWidget *button(GtkWidget *window, const gchar *text);
void screenshot(GtkWidget *window, const gchar *label);
void key_event(GtkWidget *window, guint keyval, GdkModifierType state);
void press_key(GtkWidget *window, guint keyval);
void start_motion(GtkWidget *window, GtkWidget *layout);
