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

sys.path.insert(0, str(Path(__file__).parent))
from backend import analyzer
from backend.patcher import ConfigManager, patch_video

APP_ID = "net.buwryy.TikUtils"


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
    for f in sorted(formats, key=analyzer.stream_rank, reverse=True):
        sig = (f.get('width'), f.get('height'), f.get('fps'),
               f.get('tbr') or f.get('vbr') or 0, f.get('filesize'))
        if sig not in seen:
            seen.add(sig)
            unique.append(f)
    return unique

def make_save_name(username, suffix):
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

        css = b"""
        .header-progress {
            min-width: 80px;
            max-width: 100px;
        }
        .best-stream-pill {
            background-color: @accent_bg_color;
            color: @accent_fg_color;
            border-radius: 9999px;
            padding: 2px 8px;
            font-weight: bold;
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
            about.set_website("https://github.com/buwryme")
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
        self.progress_bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER)
        self.progress_bar.add_css_class("header-progress")
        self.progress_bar.set_visible(False)
        header.pack_end(self.progress_bar)
        toolbar_view.add_top_bar(header)

        prefs_page = Adw.PreferencesPage()

        # session group
        session_group = Adw.PreferencesGroup(title="Session")
        self.file_row = Adw.ActionRow(title="File")
        self.file_row.set_subtitle("no video selected")
        session_group.add(self.file_row)
        prefs_page.add(session_group)

        # metadata group — using EntryRow per HIG boxed list guidelines
        meta_group = Adw.PreferencesGroup(title="Metadata")

        self.encoder_row = Adw.EntryRow(title="Encoder")
        self.encoder_row.set_text(str(self.config_manager.config.get("encoder", "")))
        self.encoder_row.connect("changed", self.on_setting_changed)
        meta_group.add(self.encoder_row)

        self.comment_row = Adw.EntryRow(title="Comment (primary)")
        self.comment_row.set_text(str(self.config_manager.config.get("comment", "")))
        self.comment_row.connect("changed", self.on_setting_changed)
        meta_group.add(self.comment_row)

        self.comment_short_row = Adw.EntryRow(title="Comment (secondary)")
        self.comment_short_row.set_text(str(self.config_manager.config.get("comment_short", "")))
        self.comment_short_row.connect("changed", self.on_setting_changed)
        meta_group.add(self.comment_short_row)

        self.name_row = Adw.EntryRow(title="Name Box Payload")
        self.name_row.set_text(str(self.config_manager.config.get("name_box_payload", "")))
        self.name_row.connect("changed", self.on_setting_changed)
        meta_group.add(self.name_row)

        prefs_page.add(meta_group)

        # patcher options group
        patch_group = Adw.PreferencesGroup(title="Patcher Options")

        self.reenc_row = Adw.SwitchRow(title="Re-encode Video")
        self.reenc_row.set_active(self.config_manager.config.get("re_encode", True))
        self.reenc_row.connect("notify::active", self.on_reenc_toggled)
        patch_group.add(self.reenc_row)

        self.codec_row = Adw.ComboRow(title="Codec")
        codec_model = Gtk.StringList()
        codec_model.append("H.264")
        codec_model.append("H.265")
        self.codec_row.set_model(codec_model)
        active_codec = self.config_manager.config.get("codec", "h264")
        self.codec_row.set_selected(1 if active_codec == "h265" else 0)
        self.codec_row.connect("notify::selected", self.on_setting_changed)
        patch_group.add(self.codec_row)

        saved_crf = self.config_manager.config.get("crf", 18)
        self.crf_row = Adw.SpinRow(title="Quality (CRF)", adjustment=Gtk.Adjustment(
            value=saved_crf, lower=0, upper=51, step_increment=1))
        self.crf_row.set_subtitle("Lower is better quality/larger file")
        self.crf_row.connect("notify::value", self.on_setting_changed)
        patch_group.add(self.crf_row)

        self.infl_row = Adw.SpinRow(title="Inflation Factor", adjustment=Gtk.Adjustment(
            value=self.config_manager.config.get("inflation_rate", 10),
            lower=1, upper=100, step_increment=1))
        self.infl_row.set_subtitle("Multiplier for audio sample table padding")
        self.infl_row.connect("notify::value", self.on_setting_changed)
        patch_group.add(self.infl_row)

        self.trail_row = Adw.SpinRow(title="Trailing Dummy Bytes", adjustment=Gtk.Adjustment(
            value=self.config_manager.config.get("trailing_bytes", 33836),
            lower=0, upper=1000000, step_increment=100))
        self.trail_row.set_subtitle("Junk data appended to confuse validators")
        self.trail_row.connect("notify::value", self.on_setting_changed)
        patch_group.add(self.trail_row)

        prefs_page.add(patch_group)
        toolbar_view.set_content(prefs_page)

        # bottom bar with action buttons per HIG pill button guidance
        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.END, spacing=12)
        bottom_bar.set_margin_start(12)
        bottom_bar.set_margin_end(12)
        bottom_bar.set_margin_bottom(12)

        self.reset_btn = Gtk.Button(label="Reset")
        self.reset_btn.add_css_class("pill")
        self.reset_btn.add_css_class("destructive-action")
        self.reset_btn.connect("clicked", self.on_reset_clicked)
        bottom_bar.append(self.reset_btn)

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

        self.update_encoding_visibility()

    def set_target(self, path):
        self.target_path = path
        self.file_row.set_subtitle(Path(path).name)

    def get_current_config(self):
        cfg = self.config_manager.config.copy()
        cfg["encoder"] = self.encoder_row.get_text()
        cfg["comment"] = self.comment_row.get_text()
        cfg["comment_short"] = self.comment_short_row.get_text()
        cfg["name_box_payload"] = self.name_row.get_text()
        cfg["re_encode"] = self.reenc_row.get_active()
        cfg["codec"] = "h265" if self.codec_row.get_selected() == 1 else "h264"
        cfg["crf"] = int(self.crf_row.get_value())
        cfg["inflation_rate"] = int(self.infl_row.get_value())
        cfg["trailing_bytes"] = int(self.trail_row.get_value())
        return cfg

    def apply_config_to_widgets(self, cfg):
        self.encoder_row.set_text(str(cfg.get("encoder", "")))
        self.comment_row.set_text(str(cfg.get("comment", "")))
        self.comment_short_row.set_text(str(cfg.get("comment_short", "")))
        self.name_row.set_text(str(cfg.get("name_box_payload", "")))
        self.reenc_row.set_active(cfg.get("re_encode", True))
        self.codec_row.set_selected(1 if cfg.get("codec", "h264") == "h265" else 0)
        self.crf_row.set_value(cfg.get("crf", 18))
        self.infl_row.set_value(cfg.get("inflation_rate", 10))
        self.trail_row.set_value(cfg.get("trailing_bytes", 33836))
        self.update_encoding_visibility()

    def on_reenc_toggled(self, row, param):
        self.update_encoding_visibility()
        self.on_setting_changed()

    def update_encoding_visibility(self):
        is_enabled = self.reenc_row.get_active()
        self.codec_row.set_visible(is_enabled)
        self.crf_row.set_visible(is_enabled)

    def on_setting_changed(self, *args):
        self.save_btn.set_sensitive(self.get_current_config() != self.original_config)

    def on_reset_clicked(self, btn):
        defaults = self.config_manager.get_defaults()
        self.apply_config_to_widgets(defaults)
        self.on_setting_changed()
        self.window.show_toast("Settings reset to defaults")
        Log.inf("patcher settings reset to defaults")

    def on_save_clicked(self, btn):
        current = self.get_current_config()
        self.config_manager.save(current)
        self.original_config = current.copy()
        self.save_btn.set_sensitive(False)
        self.window.show_toast("Settings saved to disk")
        Log.inf("patcher settings saved")

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

    def on_patch_clicked(self, btn):
        if not self.target_path:
            self.window.show_toast("No video selected")
            return

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

            from backend.patcher import set_runtime_params
            set_runtime_params(
                config.get("inflation_rate", 10),
                config.get("dummy_sample_size", 8),
            )
            patch_video(copy_path, config)

            result_path = os.path.join(temp_dir, "source_tiktok.mp4")

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
        self.stop_pulse()
        self.progress_bar.set_fraction(1.0)

        suggested = f"{stem}_tiktok.mp4"

        dialog = Gtk.FileDialog.new()
        dialog.set_title("Save Patched Video")
        dialog.set_initial_name(suggested)
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
        self.progress_bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER)
        self.progress_bar.add_css_class("header-progress")
        self.progress_bar.set_visible(False)
        header.pack_end(self.progress_bar)

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

    @staticmethod
    def _add_row_icon(row, icon_name):
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(16)
        icon.add_css_class("dim-label")
        row.add_prefix(icon)

    @staticmethod
    def _section(title, icon_name):
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        heading.set_margin_start(6)

        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(18)
        icon.add_css_class("heading")
        heading.append(icon)

        label = Gtk.Label(label=title, xalign=0)
        label.add_css_class("heading")
        heading.append(label)
        section.append(heading)

        group = Adw.PreferencesGroup()
        section.append(group)
        return section, group

    def populate(self, info):
        self.current_info = info

        while child := self.results_box.get_first_child():
            self.results_box.remove(child)

        metadata = info.get('_tikutils') or analyzer.normalize_metadata(info)
        stats_section, stats = self._section("Statistics", "view-list-symbolic")
        stat_map = [
            ("Views", "views", "view-reveal-symbolic"),
            ("Likes", "likes", "star-new-symbolic"),
            ("Comments", "comments", "chat-message-new-symbolic"),
            ("Favorites", "favorites", "starred-symbolic"),
            ("Shares", "shares", "network-transmit-symbolic"),
        ]
        for name, key, icon_name in stat_map:
            val = metadata.get(key)
            row = Adw.ActionRow(title=name)
            self._add_row_icon(row, icon_name)
            lbl = Gtk.Label(label=f"{val:,}" if isinstance(val, (int, float)) else (str(val) if val else "Unavailable"))
            lbl.add_css_class("numeric")
            lbl.add_css_class("dim-label")
            row.add_suffix(lbl)
            stats.add(row)
        self.results_box.append(stats_section)

        details_section, details = self._section("Information", "dialog-information-symbolic")
        info_rows = [
            ("Video ID", metadata.get('video_id'), "video-x-generic-symbolic"),
            ("Upload source", metadata.get('upload_source'), "preferences-system-devices-symbolic"),
            ("VQScore", metadata.get('vq_score'), "document-properties-symbolic"),
            ("Creator region", metadata.get('region'), "mark-location-symbolic"),
            ("Shadowban", metadata.get('shadowban'), "security-high-symbolic"),
        ]
        for title, value, icon_name in info_rows:
            row = Adw.ActionRow(title=title)
            if title == "Upload source" and value:
                icon_name = ("phone-apple-iphone-symbolic" if str(value).startswith("Phone")
                             else "computer-symbolic" if str(value).startswith("Desktop")
                             else icon_name)
            self._add_row_icon(row, icon_name)
            if value is not None:
                subtitle = str(value)
            elif title == "Upload source":
                subtitle = "Could not inspect a TikTok MP4 stream"
            elif title == "VQScore":
                subtitle = "Undetermined"
            else:
                subtitle = "Unavailable from public video metadata"
            row.set_subtitle(subtitle)
            if title == "Video ID":
                copy_btn = Gtk.Button(icon_name="edit-copy-symbolic")
                copy_btn.add_css_class("flat")
                copy_btn.set_valign(Gtk.Align.CENTER)
                copy_btn.set_tooltip_text("Copy Video ID")
                copy_btn.connect("clicked", self.on_copy_id)
                row.add_suffix(copy_btn)
            details.add(row)
        caption = Adw.ActionRow(title="Caption")
        self._add_row_icon(caption, "text-x-generic-symbolic")
        caption_text = (info.get('description') or "No caption")
        caption_text = caption_text.replace("\r\n", "\n").replace("\r", "\n")
        caption.set_subtitle(caption_text)
        caption.set_subtitle_selectable(True)
        caption.set_subtitle_lines(max(2, caption_text.count("\n") + 1))
        details.add(caption)
        audio = Adw.ActionRow(title="Audio")
        self._add_row_icon(audio, "audio-x-generic-symbolic")
        audio.set_subtitle(f"{info.get('track') or 'original sound'} - {info.get('artist') or ''}")
        details.add(audio)
        self.results_box.append(details_section)

        quality_section, quality = self._section("Quality", "preferences-system-devices-symbolic")
        best = max((f for f in info.get('formats', []) if f.get('width') and f.get('height')),
                   key=analyzer.stream_rank, default=None)
        served = resolution_string(best.get('width'), best.get('height'), best.get('fps')) if best else "Unavailable"
        for title, value, icon_name in [
            ("Browser", metadata.get('browser_quality') or (f"Up to {served} (available browser stream; client not identified)" if best else served), "web-browser-symbolic"),
            ("Mobile", metadata.get('mobile_quality') or metadata.get('browser_quality') or served, "phone-apple-iphone-symbolic"),
            ("Original resolution", f"{metadata['original_width']}x{metadata['original_height']}" if metadata.get('original_width') and metadata.get('original_height') else "Unavailable", "video-display-symbolic"),
        ]:
            row = Adw.ActionRow(title=title)
            self._add_row_icon(row, icon_name)
            row.set_subtitle(value)
            quality.add(row)
        self.results_box.append(quality_section)

        categories_section, categories = self._section("Categories", "applications-games-symbolic")
        category_row = Adw.ActionRow(title="TikTok detected categories")
        self._add_row_icon(category_row, "view-list-symbolic")
        category_row.set_subtitle(", ".join(metadata.get('categories') or []) or "Unavailable from public metadata")
        categories.add(category_row)
        self.results_box.append(categories_section)

        streams_section, streams = self._section("Streams and downloads", "folder-download-symbolic")

        formats = info.get('formats', [])
        video_formats = [f for f in formats if f.get('width', 0) > 0 and f.get('height', 0) > 0]

        unique_streams = get_unique_streams(video_formats)
        best_stream = unique_streams[0] if unique_streams else None
        for f in unique_streams:
            width = f.get('width', 0)
            height = f.get('height', 0)
            fps = f.get('fps', 0)
            vcodec = codec_name(f.get('vcodec', 'unknown'))
            tbr = f.get('tbr') or 0

            row = Adw.ActionRow(title=f"{resolution_string(width, height, fps)} • {vcodec.upper()}")
            self._add_row_icon(row, "camera-video-symbolic")
            row.set_subtitle(f"{format_bytes(f.get('filesize', 0))} • {format_bitrate(tbr * 1000)}")

            if f is best_stream:
                best_pill = Gtk.Label(label="BEST")
                best_pill.add_css_class("caption")
                best_pill.add_css_class("best-stream-pill")
                best_pill.set_valign(Gtk.Align.CENTER)
                row.add_suffix(best_pill)

            dl_btn = Gtk.Button(icon_name="folder-download-symbolic")
            dl_btn.add_css_class("flat")
            suffix = f"{min(width, height)}p"
            dl_btn.connect("clicked", self.on_download_stream, f.get('format_id'), suffix)
            row.add_suffix(dl_btn)
            row.set_activatable_widget(dl_btn)
            streams.add(row)

        self.results_box.append(streams_section)

    def on_download_stream(self, btn, format_id, suffix):
        if not self.current_info or not format_id:
            return
        suggested = make_save_name(self.current_info.get('uploader', 'user'), suffix)
        stream = next((f for f in self.current_info.get('formats', [])
                       if f.get('format_id') == format_id), None)
        if stream and stream.get('url'):
            self.start_download(stream['url'], suggested, format_id)

    def start_download(self, url, suggested_name, label):
        self.progress_bar.set_visible(True)
        self.progress_bar.set_fraction(0.0)
        Log.inf(f"starting download: {label}")
        self.window.show_toast(f"Downloading {label}...")

        thread = threading.Thread(target=self.download_to_cache,
                                  args=(url, suggested_name, label),
                                  daemon=True)
        thread.start()

    def download_to_cache(self, url, suggested_name, label):
        temp_dir = tempfile.mkdtemp(prefix="tikutils_")
        temp_path = os.path.join(temp_dir, suggested_name)

        def update_progress(frac):
            GLib.idle_add(self.progress_bar.set_fraction, frac)

        try:
            analyzer.download_stream(url, temp_path, update_progress)

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
