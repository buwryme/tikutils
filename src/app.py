#!/usr/bin/env python3
"""
TikUtils — tiktok analytics & stream downloader app
MIT • by buwryme
"""
import sys
import os
import re
import random
import threading
import tempfile
import shutil
import logging
from pathlib import Path
from datetime import datetime

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk

# make sure the backend package next to this file is importable
sys.path.insert(0, str(Path(__file__).parent))
from backend import analyzer
from backend.patcher import ConfigManager, patch_video

APP_ID = "net.buwryy.TikUtils"


# read the version from the VERSION file shipped by setup.sh
def load_version():
    candidates = [
        Path(__file__).parent / "VERSION",
        Path(__file__).parent.parent / "VERSION",
    ]
    for candidate in candidates:
        try:
            return candidate.read_text().strip()
        except Exception:
            continue
    return "unknown"

VERSION = load_version()


# logging: info to the terminal, everything to a timestamped file
def setup_logging():
    logger = logging.getLogger("TikUtils")
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(
        "[TikUtils:%(levelname)s %(asctime)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(console)

    log_dir = Path.home() / ".local" / "share" / APP_ID / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"

    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(name)s - %(message)s"))
    logger.addHandler(file_handler)

    return logger

log = setup_logging()

class Log:
    @staticmethod
    def inf(msg): log.info(msg)
    @staticmethod
    def dbg(msg): log.debug(msg)
    @staticmethod
    def wrn(msg): log.warning(msg)
    @staticmethod
    def err(msg): log.error(msg)


# small shared helpers
def codec_name(vcodec):
    if 'avc1' in vcodec:
        return 'h264'
    if 'hev1' in vcodec or 'hvc1' in vcodec:
        return 'hevc'
    if 'bv1' in vcodec:
        return 'bvc2'
    return 'unknown'

def resolution_string(width, height, fps):
    if not width or not height:
        return "unknown"
    return f"{min(width, height)}p{int(fps) if fps else ''}"

def format_bytes(size):
    return f"{size / (1024 * 1024):.1f} MB" if size else "n/a"

def format_bitrate(bps):
    if not bps:
        return "n/a"
    kbps = bps / 1024
    return f"{kbps / 1024:.1f} MBps" if kbps > 1000 else f"{kbps:.0f} KBps"

def get_unique_streams(formats):
    seen = set()
    unique = []
    for f in formats:
        sig = f"{min(f.get('width', 0), f.get('height', 0))}p{f.get('fps', 0)}_{f.get('filesize', 0)}"
        if sig not in seen:
            seen.add(sig)
            unique.append(f)
    return unique

def make_save_name(username, suffix):
    # 10 digit random integer + poster username + quality tag
    clean = re.sub(r'[^A-Za-z0-9_.-]', '', username or 'user')
    return f"{random.randint(10 ** 9, (10 ** 10) - 1)}-{clean}-{suffix}.mp4"


class TikUtilsApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = TikUtilsWindow(application=self)
        win.present()


class TikUtilsWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_title("TikUtils")
        self.set_default_size(650, 850)

        self.config_manager = ConfigManager()

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        # navigation view gives us native slide-in pages and back buttons
        self.nav_view = Adw.NavigationView()
        self.toast_overlay.set_child(self.nav_view)

        self.welcome_page = WelcomePage(self)
        self.patcher_select_page = PatcherSelectPage(self)
        self.patcher_settings_page = PatcherSettingsPage(self)
        self.analyzer_input_page = AnalyzerInputPage(self)
        self.analyzer_results_page = AnalyzerResultsPage(self)

        self.nav_view.add(self.welcome_page)

        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self.on_about)
        self.add_action(about_action)

        # css for the short header progress bars, plus a hard cap on headerbar
        # height so the auto back button can never be stretched vertically
        css = b"""
        .header-progress {
            min-width: 80px;
            max-width: 100px;
        }

        headerbar {
            min-height: 48px;
            max-height: 48px;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def on_about(self, *args):
        Log.inf("opening about dialog")
        try:
            about = Adw.AboutDialog.new()
            about.set_application_name("TikUtils")
            about.set_application_icon(APP_ID)
            about.set_version(VERSION)
            about.set_developer_name("buwryme")
            about.set_developers(["buwryme"])
            about.set_license_type(Gtk.License.MIT_X11)
            about.set_comments("TikTok utilities app made with GTK4/Libadwaita")
            about.set_website("https://buwry.me")
            about.present(self)
        except AttributeError:
            about = Adw.AboutWindow.new()
            about.set_application_name("TikUtils")
            about.set_application_icon(APP_ID)
            about.set_version(VERSION)
            about.set_developer_name("buwryme")
            about.set_license_type(Gtk.License.MIT_X11)
            about.set_comments("TikTok utilities app made with GTK4/Libadwaita")
            about.set_website("https://github.com/buwryme/tikutils")
            about.present()

    def show_toast(self, msg):
        self.toast_overlay.add_toast(Adw.Toast.new(msg))

    def show_error_dialog(self, title, body):
        try:
            d = Adw.AlertDialog.new(title, body)
            d.add_response("ok", "OK")
            d.set_default_response("ok")
            d.set_close_response("ok")
            d.present(self)
        except AttributeError:
            d = Adw.MessageDialog.new(self)
            d.set_heading(title)
            d.set_body(body)
            d.add_response("ok", "OK")
            d.set_default_response("ok")
            d.set_close_response("ok")
            d.present()


class WelcomePage(Adw.NavigationPage):
    def __init__(self, window):
        super().__init__(title="TikUtils")
        self.window = window

        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar()
        menu = Gio.Menu()
        menu.append("About TikUtils", "win.about")
        popover = Gtk.PopoverMenu.new_from_model(menu)
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", popover=popover)
        menu_btn.set_valign(Gtk.Align.CENTER)
        header.pack_end(menu_btn)
        toolbar_view.add_top_bar(header)

        clamp = Adw.Clamp(maximum_size=420)
        clamp.set_vexpand(True)
        clamp.set_valign(Gtk.Align.CENTER)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_valign(Gtk.Align.CENTER)

        title_lbl = Gtk.Label(label="Welcome to TikUtils")
        title_lbl.add_css_class("title-1")
        title_lbl.set_halign(Gtk.Align.CENTER)
        box.append(title_lbl)

        # slightly smaller subtitle for visual depth
        sub_lbl = Gtk.Label(label="Choose an action")
        sub_lbl.set_css_classes(["title-4", "dim-label"])
        sub_lbl.set_halign(Gtk.Align.CENTER)
        box.append(sub_lbl)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        btn_box.set_margin_top(28)

        patch_btn = Gtk.Button(label="Patch a video for lossless upload")
        patch_btn.add_css_class("suggested-action")
        patch_btn.add_css_class("pill")
        patch_btn.set_tooltip_text("Patch your video to avoid server-side re-encoding when uploading to TikTok")
        patch_btn.connect("clicked", lambda b: self.window.nav_view.push(self.window.patcher_select_page))
        btn_box.append(patch_btn)

        analyze_btn = Gtk.Button(label="Analyze or download videos")
        analyze_btn.add_css_class("suggested-action")
        analyze_btn.add_css_class("pill")
        analyze_btn.set_tooltip_text("Analyze and/or download TikTok videos")
        analyze_btn.connect("clicked", lambda b: self.window.nav_view.push(self.window.analyzer_input_page))
        btn_box.append(analyze_btn)

        box.append(btn_box)
        clamp.set_child(box)
        toolbar_view.set_content(clamp)
        self.set_child(toolbar_view)


class PatcherSelectPage(Adw.NavigationPage):
    def __init__(self, window):
        super().__init__(title="Patch Video")
        self.window = window

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)

        icon = Gtk.Image.new_from_icon_name("video-x-generic-symbolic")
        icon.set_pixel_size(96)
        icon.set_halign(Gtk.Align.CENTER)
        box.append(icon)

        title = Gtk.Label(label="Select a video to patch")
        title.add_css_class("title-2")
        title.set_halign(Gtk.Align.CENTER)
        box.append(title)

        select_btn = Gtk.Button(label="Select video")
        select_btn.add_css_class("suggested-action")
        select_btn.add_css_class("pill")
        select_btn.set_halign(Gtk.Align.CENTER)
        select_btn.set_margin_top(12)
        select_btn.connect("clicked", self.on_select_video)
        box.append(select_btn)

        toolbar_view.set_content(box)
        self.set_child(toolbar_view)

    def on_select_video(self, btn):
        dialog = Gtk.FileDialog()
        dialog.set_title("Select Video to Patch")

        video_filter = Gtk.FileFilter()
        video_filter.set_name("Video files")
        video_filter.add_mime_type("video/*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(video_filter)
        dialog.set_filters(filters)

        dialog.open(self.window, None, self.on_file_selected)

    def on_file_selected(self, dialog, result):
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            # user closed the picker without choosing anything
            Log.wrn("video selection cancelled by user")
            self.window.show_toast("Video selection cancelled by user")
            return

        if not file:
            Log.wrn("video selection cancelled by user")
            self.window.show_toast("Video selection cancelled by user")
            return

        path = file.get_path()
        Log.inf(f"selected video for patching: {path}")
        self.window.show_toast(f"Video imported: {Path(path).name}")
        self.window.patcher_settings_page.set_target(path)
        self.window.nav_view.push(self.window.patcher_settings_page)


class PatcherSettingsPage(Adw.NavigationPage):
    def __init__(self, window):
        super().__init__(title="Patch Settings")
        self.window = window
        self.config_manager = window.config_manager
        self.original_config = self.config_manager.config.copy()
        self.target_path = None
        self.pulse_id = None

        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar()

        # patching activity bar, hidden unless a patch is running.
        # same class as the analyzer bar so both share the same width.
        self.progress_bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER)
        self.progress_bar.add_css_class("header-progress")
        self.progress_bar.set_visible(False)
        header.pack_end(self.progress_bar)

        toolbar_view.add_top_bar(header)

        prefs_page = Adw.PreferencesPage()

        # shows which file will be patched
        session_group = Adw.PreferencesGroup(title="Session")
        self.file_row = Adw.ActionRow(title="File")
        self.file_row.set_subtitle("no video selected")
        session_group.add(self.file_row)
        prefs_page.add(session_group)

        # metadata group
        meta_group = Adw.PreferencesGroup(title="Metadata")
        self.entries = {}
        fields = ["artist", "composer", "album", "comment", "copyright", "grouping"]
        for field in fields:
            row = Adw.ActionRow(title=field.capitalize())
            entry = Gtk.Entry(valign=Gtk.Align.CENTER)
            # fixed width so all fields line up symmetrically
            entry.set_size_request(260, -1)
            entry.set_text(self.config_manager.config.get(field, "buwryy"))
            entry.connect("changed", self.on_setting_changed)
            row.add_suffix(entry)
            row.set_activatable_widget(entry)
            self.entries[field] = entry
            meta_group.add(row)
        prefs_page.add(meta_group)

        # patcher options group
        patch_group = Adw.PreferencesGroup(title="Patcher Options")

        reenc_row = Adw.ActionRow(title="Re-encode Video")
        reenc_row.set_subtitle("HEVC, Main 10, 20K Bitrate...")
        self.reenc_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.reenc_switch.set_active(self.config_manager.config.get("re_encode", True))
        self.reenc_switch.connect("notify::active", self.on_setting_changed)
        reenc_row.add_suffix(self.reenc_switch)
        reenc_row.set_activatable_widget(self.reenc_switch)
        patch_group.add(reenc_row)

        bitrate_row = Adw.ActionRow(title="Bitrate")
        bitrate_row.set_subtitle("1.5k - 20k kbps")
        bitrate_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.bitrate_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1.5, 20, 0.5)
        self.bitrate_scale.set_draw_value(False)
        self.bitrate_scale.set_value(self.config_manager.config.get("avg_bitrate", 15))
        self.bitrate_scale.set_size_request(260, -1)
        self.bitrate_scale.set_hexpand(True)
        self.bitrate_label = Gtk.Label(label="15.0k")
        self.bitrate_label.set_size_request(40, -1)
        self.bitrate_label.set_halign(Gtk.Align.END)
        self.bitrate_label.add_css_class("numeric")
        self.bitrate_label.add_css_class("dim-label")
        self.bitrate_scale.connect("value-changed", self.on_bitrate_changed)
        bitrate_container.append(self.bitrate_scale)
        bitrate_container.append(self.bitrate_label)
        bitrate_row.add_suffix(bitrate_container)
        bitrate_row.set_activatable_widget(self.bitrate_scale)
        patch_group.add(bitrate_row)

        infl_row = Adw.ActionRow(title="Inflation Factor")
        infl_row.set_subtitle("Multiplier for audio sample table padding")
        self.infl_spin = Gtk.SpinButton.new_with_range(1, 100, 1)
        self.infl_spin.set_valign(Gtk.Align.CENTER)
        self.infl_spin.set_value(self.config_manager.config.get("inflation_rate", 10))
        self.infl_spin.connect("value-changed", self.on_setting_changed)
        infl_row.add_suffix(self.infl_spin)
        infl_row.set_activatable_widget(self.infl_spin)
        patch_group.add(infl_row)

        trail_row = Adw.ActionRow(title="Trailing Dummy Bytes")
        trail_row.set_subtitle("Junk data appended to confuse validators")
        self.trail_spin = Gtk.SpinButton.new_with_range(0, 1000000, 100)
        self.trail_spin.set_valign(Gtk.Align.CENTER)
        self.trail_spin.set_value(self.config_manager.config.get("trailing_bytes", 1024))
        self.trail_spin.connect("value-changed", self.on_setting_changed)
        trail_row.add_suffix(self.trail_spin)
        trail_row.set_activatable_widget(self.trail_spin)
        patch_group.add(trail_row)

        prefs_page.add(patch_group)
        toolbar_view.set_content(prefs_page)

        # bottom bar: save on the left, patch on the right
        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.END, spacing=12)
        bottom_bar.set_margin_start(12)
        bottom_bar.set_margin_end(12)
        bottom_bar.set_margin_bottom(12)

        self.save_btn = Gtk.Button(label="Save")
        self.save_btn.add_css_class("pill")
        self.save_btn.set_sensitive(False)
        self.save_btn.connect("clicked", self.on_save_clicked)
        bottom_bar.append(self.save_btn)

        self.patch_btn = Gtk.Button(label="Patch")
        self.patch_btn.add_css_class("suggested-action")
        self.patch_btn.add_css_class("pill")
        self.patch_btn.connect("clicked", self.on_patch_clicked)
        bottom_bar.append(self.patch_btn)

        toolbar_view.add_bottom_bar(bottom_bar)
        self.set_child(toolbar_view)

    def set_target(self, path):
        self.target_path = path
        self.file_row.set_subtitle(Path(path).name)

    def get_current_config(self):
        cfg = self.config_manager.config.copy()
        for field, entry in self.entries.items():
            cfg[field] = entry.get_text()
        cfg["re_encode"] = self.reenc_switch.get_active()
        cfg["avg_bitrate"] = self.bitrate_scale.get_value()
        cfg["inflation_rate"] = int(self.infl_spin.get_value())
        cfg["trailing_bytes"] = int(self.trail_spin.get_value())
        return cfg

    def on_bitrate_changed(self, scale):
        value = scale.get_value()
        self.bitrate_label.set_label(f"{value:.1f}k")
        GLib.idle_add(self.on_setting_changed)

    def on_setting_changed(self, *args):
        self.save_btn.set_sensitive(self.get_current_config() != self.original_config)

    def on_save_clicked(self, btn):
        current = self.get_current_config()
        self.config_manager.save(current)
        self.original_config = current.copy()
        self.save_btn.set_sensitive(False)
        self.window.show_toast("Settings saved to disk")
        Log.inf("patcher settings saved")

    # progress bar helpers. ffmpeg gives us no percentage, so we just
    # pulse the bar left to right every 150ms while the pipeline runs
    def start_pulse(self):
        self.progress_bar.set_visible(True)
        self.progress_bar.set_fraction(0.0)
        if self.pulse_id is None:
            self.pulse_id = GLib.timeout_add(150, self.on_pulse_tick)

    def on_pulse_tick(self):
        self.progress_bar.pulse()
        return True

    def stop_pulse(self):
        if self.pulse_id is not None:
            GLib.source_remove(self.pulse_id)
            self.pulse_id = None

    def hide_progress(self):
        self.stop_pulse()
        self.progress_bar.set_visible(False)
        self.progress_bar.set_fraction(0.0)

    # patch pipeline: work on a temp copy so the original is never touched,
    # then hand the finished file to the xdg save portal
    def on_patch_clicked(self, btn):
        if not self.target_path:
            self.window.show_toast("No video selected")
            return

        # read widgets on the main thread before hopping to the worker
        config = self.get_current_config()
        target = self.target_path

        self.patch_btn.set_sensitive(False)
        self.start_pulse()
        self.window.show_toast("Patching started...")
        threading.Thread(target=self.do_patch, args=(target, config), daemon=True).start()

    def do_patch(self, target, config):
        temp_dir = tempfile.mkdtemp(prefix="tikutils_patch_")
        try:
            src = Path(target)
            copy_path = os.path.join(temp_dir, "source" + src.suffix)
            shutil.copy2(target, copy_path)
            Log.inf(f"patching temp copy: {copy_path}")

            patch_video(copy_path, config)

            # the backend writes the encoded file next to its input
            if config.get("re_encode", True):
                result_path = os.path.join(temp_dir, "source_tiktok.mp4")
            else:
                result_path = copy_path

            if not os.path.exists(result_path) or os.path.getsize(result_path) == 0:
                raise RuntimeError("patched output missing")

            Log.inf("patch complete, opening save dialog")
            GLib.idle_add(self.on_patch_complete, result_path, temp_dir, src.stem)
        except Exception as e:
            Log.err(f"patching failed: {e}")
            GLib.idle_add(self.window.show_toast, f"Patch failed: {str(e)[:40]}")
            GLib.idle_add(self.on_patch_failed)
            shutil.rmtree(temp_dir, ignore_errors=True)

    def on_patch_failed(self):
        self.hide_progress()
        self.patch_btn.set_sensitive(True)

    def on_patch_complete(self, result_path, temp_dir, stem):
        # work is done, freeze the bar full until the dialog resolves
        self.stop_pulse()
        self.progress_bar.set_fraction(1.0)

        suggested = f"{stem}_tiktok.mp4"

        dialog = Gtk.FileDialog.new()
        dialog.set_title("Save Patched Video")
        dialog.set_initial_name(suggested)
        # the dialog parent must be a window, not a navigation page
        dialog.save(self.window, None, self.on_save_response, (result_path, temp_dir))

    def on_save_response(self, dialog, result, user_data):
        result_path, temp_dir = user_data
        try:
            file = dialog.save_finish(result)
            if file:
                dest_path = file.get_path()
                shutil.move(result_path, dest_path)
                Log.inf(f"saved: {dest_path}")
                self.window.show_toast("Saved")
            else:
                Log.wrn("save cancelled by user")
                self.window.show_toast("Save cancelled by user")
        except GLib.Error:
            Log.wrn("save cancelled by user")
            self.window.show_toast("Save cancelled by user")
        except Exception as e:
            Log.err(f"save failed: {e}")
            self.window.show_toast(f"Save failed: {str(e)[:30]}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.hide_progress()
            self.patch_btn.set_sensitive(True)


class AnalyzerInputPage(Adw.NavigationPage):
    def __init__(self, window):
        super().__init__(title="Analyze")
        self.window = window

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(Adw.HeaderBar())

        status_page = Adw.StatusPage(
            icon_name="network-transmit-symbolic",
            title="Analyze Video",
            description="Paste a TikTok URL to fetch streams and metadata")

        url_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        url_box.set_margin_top(24)
        url_box.set_halign(Gtk.Align.CENTER)
        url_box.set_size_request(400, -1)

        self.url_entry = Gtk.Entry(hexpand=True, placeholder_text="https://vm.tiktok.com/...")
        self.url_entry.connect("activate", self.on_fetch)
        url_box.append(self.url_entry)

        fetch_btn = Gtk.Button(label="Analyze")
        fetch_btn.add_css_class("suggested-action")
        fetch_btn.add_css_class("pill")
        fetch_btn.connect("clicked", self.on_fetch)
        url_box.append(fetch_btn)

        status_page.set_child(url_box)
        clamp = Adw.Clamp(maximum_size=600)
        clamp.set_child(status_page)

        center_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True, valign=Gtk.Align.CENTER)
        center_box.append(clamp)
        toolbar_view.set_content(center_box)
        self.set_child(toolbar_view)

    def on_fetch(self, widget):
        url = self.url_entry.get_text().strip()
        if not url:
            self.window.show_toast("Please enter a valid URL")
            return
        self.window.show_toast("Analyzing...")
        threading.Thread(target=self.fetch_data, args=(url,), daemon=True).start()

    def fetch_data(self, url):
        try:
            data = analyzer.fetch_metadata(url)
            data['_origin'] = analyzer.resolve_origin(url, data)
            GLib.idle_add(self.on_fetch_done, data)
        except Exception as e:
            Log.err(f"fetch failed: {e}")
            GLib.idle_add(self.window.show_toast, f"Error: {str(e)[:40]}")

    def on_fetch_done(self, data):
        results_page = self.window.analyzer_results_page
        results_page.populate(data)
        self.window.nav_view.push(results_page)


class AnalyzerResultsPage(Adw.NavigationPage):
    def __init__(self, window):
        super().__init__(title="Results")
        self.window = window
        self.current_info = None

        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar()

        # download progress, hidden unless something is actively downloading
        self.progress_bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER)
        self.progress_bar.add_css_class("header-progress")
        self.progress_bar.set_visible(False)
        header.pack_end(self.progress_bar)

        self.copy_btn = Gtk.Button(icon_name="edit-copy-symbolic")
        self.copy_btn.set_valign(Gtk.Align.CENTER)
        self.copy_btn.set_tooltip_text("Copy Video ID")
        self.copy_btn.connect("clicked", self.on_copy_id)
        header.pack_end(self.copy_btn)

        toolbar_view.add_top_bar(header)

        self.results_scrolled = Gtk.ScrolledWindow()
        self.results_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.results_box.set_margin_top(24)
        self.results_box.set_margin_bottom(24)
        self.results_box.set_margin_start(12)
        self.results_box.set_margin_end(12)

        clamp = Adw.Clamp(maximum_size=700)
        clamp.set_child(self.results_box)
        self.results_scrolled.set_child(clamp)
        toolbar_view.set_content(self.results_scrolled)
        self.set_child(toolbar_view)

    def on_copy_id(self, btn):
        if self.current_info:
            vid_id = self.current_info.get('id', '')
            Gdk.Display.get_default().get_clipboard().set(vid_id)
            Log.dbg(f"copied id {vid_id} to clipboard")
            self.window.show_toast(f"Copied ID: {vid_id}")

    def populate(self, info):
        self.current_info = info

        while child := self.results_box.get_first_child():
            self.results_box.remove(child)

        # details
        details = Adw.PreferencesGroup(title="Details")
        cap_row = Adw.ActionRow(title="Caption")
        cap_row.set_subtitle(info.get('description', 'No caption'))
        details.add(cap_row)

        aud_row = Adw.ActionRow(title="Audio")
        aud_row.set_subtitle(f"{info.get('track', 'original sound')} - {info.get('artist', '')}")
        details.add(aud_row)
        self.results_box.append(details)

        # statistics
        stats = Adw.PreferencesGroup(title="Statistics")
        stat_map = [
            ("Views", "view_count"),
            ("Likes", "like_count"),
            ("Comments", "comment_count"),
            ("Favorites", "bookmark_count"),
            ("Shares", "repost_count"),
        ]
        for name, key in stat_map:
            val = info.get(key, 0) or info.get(key.replace('_count', '') + '_count', 0) or 0
            row = Adw.ActionRow(title=name)
            lbl = Gtk.Label(label=f"{val:,}")
            lbl.add_css_class("numeric")
            lbl.add_css_class("dim-label")
            row.add_suffix(lbl)
            stats.add(row)
        self.results_box.append(stats)

        # video streams
        streams = Adw.PreferencesGroup(title="Video streams")

        # only show the origin row when a source url was actually resolved
        origin = info.get('_origin', {'type': None, 'value': None})
        if origin.get('type'):
            orig_row = Adw.ActionRow(title="Origin", subtitle="Source video")
            orig_btn = Gtk.Button(icon_name="folder-download-symbolic")
            orig_btn.add_css_class("flat")
            orig_btn.connect("clicked", self.on_download_origin)
            orig_row.add_suffix(orig_btn)
            orig_row.set_activatable_widget(orig_btn)
            streams.add(orig_row)

        formats = info.get('formats', [])
        video_formats = [f for f in formats if f.get('width', 0) > 0 and f.get('height', 0) > 0]

        for f in get_unique_streams(video_formats):
            width = f.get('width', 0)
            height = f.get('height', 0)
            fps = f.get('fps', 0)
            vcodec = codec_name(f.get('vcodec', 'unknown'))
            tbr = f.get('tbr') or 0

            row = Adw.ActionRow(title=f"{resolution_string(width, height, fps)} • {vcodec.upper()}")
            row.set_subtitle(f"{format_bytes(f.get('filesize', 0))} • {format_bitrate(tbr * 1024)}")

            dl_btn = Gtk.Button(icon_name="folder-download-symbolic")
            dl_btn.add_css_class("flat")
            suffix = f"{min(width, height)}p"
            dl_btn.connect("clicked", self.on_download_stream, f.get('format_id'), suffix)
            row.add_suffix(dl_btn)
            row.set_activatable_widget(dl_btn)
            streams.add(row)

        self.results_box.append(streams)

    # downloads
    def on_download_origin(self, btn):
        origin = (self.current_info or {}).get('_origin', {'type': None, 'value': None})
        if not origin.get('type'):
            Log.wrn("origin download not available for this video")
            self.window.show_toast("Origin not available for this video")
            return

        username = (self.current_info or {}).get('uploader', 'user')
        suggested = make_save_name(username, 'origin')

        if origin['type'] == 'direct':
            self.start_download(origin['value'], None, suggested, "Origin", direct=True)
        else:
            self.start_download((self.current_info or {}).get('webpage_url', ''),
                                origin['value'], suggested, "Origin")

    def on_download_stream(self, btn, format_id, suffix):
        if not self.current_info or not format_id:
            return
        suggested = make_save_name(self.current_info.get('uploader', 'user'), suffix)
        self.start_download(self.current_info.get('webpage_url', ''), format_id,
                            suggested, format_id)

    def start_download(self, url, format_selector, suggested_name, label, direct=False):
        self.progress_bar.set_visible(True)
        self.progress_bar.set_fraction(0.0)
        Log.inf(f"starting download: {label}")
        self.window.show_toast(f"Downloading {label}...")

        thread = threading.Thread(target=self.download_to_cache,
                                  args=(url, format_selector, suggested_name, label, direct),
                                  daemon=True)
        thread.start()

    def download_to_cache(self, url, format_selector, suggested_name, label, direct):
        temp_dir = tempfile.mkdtemp(prefix="tikutils_")
        temp_path = os.path.join(temp_dir, suggested_name)

        def update_progress(frac):
            GLib.idle_add(self.progress_bar.set_fraction, frac)

        try:
            if direct:
                analyzer.download_direct(url, temp_path, update_progress)
            else:
                analyzer.download_ytdlp(url, format_selector, temp_path, update_progress)

            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                Log.inf(f"download complete: {label}")
                GLib.idle_add(self.on_download_complete, temp_path, suggested_name, label)
            else:
                raise RuntimeError("empty file downloaded")
        except Exception as e:
            Log.err(f"download error ({label}): {e}")
            GLib.idle_add(self.window.show_toast, f"Error: {str(e)[:40]}")
            GLib.idle_add(self.hide_progress)
            shutil.rmtree(temp_dir, ignore_errors=True)

    def on_download_complete(self, temp_path, suggested_name, label):
        self.progress_bar.set_fraction(1.0)
        Log.inf(f"opening save dialog for {label}")

        dialog = Gtk.FileDialog.new()
        dialog.set_title("Save Video")
        dialog.set_initial_name(suggested_name)
        # the dialog parent must be a window, not a navigation page
        dialog.save(self.window, None, self.on_save_response, (temp_path, label))

    def on_save_response(self, dialog, result, user_data):
        temp_path, label = user_data
        try:
            file = dialog.save_finish(result)
            if file:
                dest_path = file.get_path()
                shutil.move(temp_path, dest_path)
                Log.inf(f"saved: {dest_path}")
                self.window.show_toast("Saved")
            else:
                Log.wrn(f"save cancelled by user: {label}")
                self.window.show_toast("Save cancelled by user")
        except GLib.Error:
            Log.wrn(f"save cancelled by user: {label}")
            self.window.show_toast("Save cancelled by user")
        except Exception as e:
            Log.err(f"save failed: {e}")
            self.window.show_toast(f"Save failed: {str(e)[:30]}")
        finally:
            shutil.rmtree(os.path.dirname(temp_path), ignore_errors=True)
            self.hide_progress()

    def hide_progress(self):
        self.progress_bar.set_visible(False)
        self.progress_bar.set_fraction(0.0)


if __name__ == "__main__":
    Log.inf(f"starting TikUtils {VERSION}")
    app = TikUtilsApp()
    sys.exit(app.run(sys.argv))
