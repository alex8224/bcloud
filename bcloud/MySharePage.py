# Copyright (C) 2014 LiuLang <gsushzhsosgsu@gmail.com>
# Use of this source code is governed by GPLv3 license that can be found
# in http://www.gnu.org/licenses/gpl-3.0.html

import os
import time

from gi.repository import GdkPixbuf
from gi.repository import GLib
from gi.repository import GObject
from gi.repository import Gtk
from gi.repository import Pango

from bcloud import Config
_ = Config._
from bcloud import ErrorMsg
from bcloud.FolderBrowserDialog import FolderBrowserDialog
from bcloud import gutil
from bcloud.log import logger
from bcloud import pcs
from bcloud import util

(NAME_COL, URL_COL, MTIME_COL, SHARE_ID) = list(range(4))
REFRESH_ICON = 'view-refresh-symbolic'
ABORT_ICON = 'edit-delete-symbolic'
GO_ICON = 'go-next-symbolic'
ICON_SIZE = 24         # 60x37
LARGE_ICON_SIZE = 100  # 100x62


class MySharePage(Gtk.Box):

    icon_name = 'emblem-shared-symbolic'
    disname = _('My Shares')
    name = 'MySharePage'
    tooltip = _('My Shared files')
    first_run = True

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app

        self.curr_url = ''
        self.uk = ''
        self.shareid = ''
        self.page = 1  # 从1开始计数
        self.has_next = False
        self.dirname = ''

        if Config.GTK_GE_312:
            self.headerbar = Gtk.HeaderBar()
            self.headerbar.props.show_close_button = True
            self.headerbar.props.has_subtitle = False
            self.headerbar.set_title(self.disname)

            copy_button = Gtk.Button()
            copy_img = Gtk.Image.new_from_icon_name('edit-undo-symbolic',
                    Gtk.IconSize.SMALL_TOOLBAR)
            copy_button.set_image(copy_img)
            copy_button.set_tooltip_text(_('Copy ShortUrl'))
            copy_button.connect('clicked', self.on_copy_button_clicked)
            self.headerbar.pack_start(copy_button)

            reload_button = Gtk.Button()
            reload_img = Gtk.Image.new_from_icon_name('view-refresh-symbolic',
                    Gtk.IconSize.SMALL_TOOLBAR)
            reload_button.set_image(reload_img)
            reload_button.set_tooltip_text(_('Reload'))
            reload_button.connect('clicked', self.on_reload_button_clicked)
            self.headerbar.pack_start(reload_button)


            delete_button = Gtk.Button()
            delete_img = Gtk.Image.new_from_icon_name('list-remove-symbolic',
                    Gtk.IconSize.SMALL_TOOLBAR)
            delete_button.set_image(delete_img)
            delete_button.set_tooltip_text(_('Remove Share'))
            delete_button.set_tooltip_text(
                    _('Delete selected share in my share'))
            delete_button.connect('clicked', self.on_delete_button_clicked)
            self.headerbar.pack_start(delete_button)


            # show loading process
            self.loading_spin = Gtk.Spinner()
            self.headerbar.pack_end(self.loading_spin)
        else:
            control_box = Gtk.Box(spacing=0)
            control_box.props.margin_bottom = 10
            self.pack_start(control_box, False, False, 0)

            copy_button = Gtk.Button.new_with_label(_('Copy ShortUrl'))
            copy_button.connect('clicked', self.on_copy_button_clicked)
            control_box.pack_start(copy_button, False, False, 0)

            reload_button = Gtk.Button.new_with_label(_('Reload'))
            reload_button.connect('clicked', self.on_reload_button_clicked)
            control_box.pack_start(reload_button, False, False, 0)

            delete_button = Gtk.Button.new_with_label(_('Delete'))
            delete_button.set_tooltip_text(
                    _('Delete selected share permanently'))
            delete_button.connect('clicked', self.on_delete_button_clicked)
            control_box.pack_end(delete_button, False, False, 0)

            # show loading process
            self.loading_spin = Gtk.Spinner()
            self.loading_spin.props.margin_right = 5
            control_box.pack_end(self.loading_spin, False, False, 0)


        scrolled_win = Gtk.ScrolledWindow()
        self.pack_start(scrolled_win, True, True, 0)

        # checked, icon, large_icon, name, path, isdir, size, human-size,
        # mtime, human-mtime
        self.liststore = Gtk.ListStore(str, str, str, str)
        self.treeview = Gtk.TreeView(model=self.liststore)
        self.treeview.set_headers_clickable(True)
        # self.treeview.set_search_column(NAME_COL)
        self.treeview.props.has_tooltip = True
        self.treeview.connect('query-tooltip', self.on_treeview_query_tooltip)
        self.treeview.connect('row-activated', self.on_treeview_row_activated)
        self.treeview.get_vadjustment().connect('value-changed',
                                                self.on_treeview_scrolled)
        self.selection = self.treeview.get_selection()
        self.selection.set_mode(Gtk.SelectionMode.MULTIPLE)
        scrolled_win.add(self.treeview)

        name_cell = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END,
                                         ellipsize_set=True)
        name_col = Gtk.TreeViewColumn(_('Name'), name_cell, text=0)
        name_col.set_expand(True)
        name_col.set_resizable(True)
        self.treeview.append_column(name_col)
        name_col.set_sort_column_id(0)
        self.liststore.set_sort_func(0, gutil.tree_model_natsort)

        url_cell = Gtk.CellRendererText()
        url_col = Gtk.TreeViewColumn(_('ShortUrl'), url_cell, text=1)
        url_col.set_resizable(True)
        self.treeview.append_column(url_col)
        url_col.props.min_width = 145
        url_col.set_sort_column_id(1)

        mtime_cell = Gtk.CellRendererText()
        mtime_col = Gtk.TreeViewColumn(_('Modified'), mtime_cell,
                                       text=2)
        self.treeview.append_column(mtime_col)
        mtime_col.props.min_width = 100
        mtime_col.set_resizable(True)
        mtime_col.set_sort_column_id(2)

    def on_page_show(self):
        if Config.GTK_GE_312:
            self.scrolled_win.show_all()

    def check_first(self):
        if self.first_run:
            self.first_run = False
            self.show_all()
            self.load()

    def append_filelist(self, my_shares, error=None):
        self.loading_spin.stop()
        self.loading_spin.hide()

        if error or not my_shares:
            return
        for share in my_shares['list']:
            self.liststore.append([
                share['typicalPath'], 
                share['shortlink'],
                time.ctime(share['ctime']),
                str(share['shareId'])
                ])

    def load(self):
        self.loading_spin.start()
        self.loading_spin.show_all()
        self.liststore.clear()
        gutil.async_call(pcs.list_my_share, self.app.cookie, self.app.tokens, callback=self.append_filelist)

    def reload(self, *args, **kwds):
        pass

    def load_next(self):
        '''载入下一页'''
        self.page += 1
        self.load_url()

    def on_copy_button_clicked(self, button):

        selection = self.treeview.get_selection()
        model, tree_paths = selection.get_selected_rows()
        if not tree_paths:
            return
        shorturls = []
        for tree_path in tree_paths:
            shorturls.append(model[tree_path][URL_COL])
        self.app.update_clipboard('\n'.join(shorturls))

    def on_reload_button_clicked(self, button):
        self.load()

    def on_delete_button_clicked(self, button):

        def on_delete_callback(info, error=None):
            self.load()

        selection = self.treeview.get_selection()
        model, tree_paths = selection.get_selected_rows()
        if not tree_paths:
            return
        share_ids = []
        for tree_path in tree_paths:
            share_ids.append(model[tree_path][SHARE_ID])

        gutil.async_call(pcs.disable_share, self.app.cookie, self.app.tokens, 
                         share_ids, callback=on_delete_callback)

    def on_select_all_button_toggled(self, column):
        pass


    def on_treeview_query_tooltip(self, treeview, x, y, keyboard_mode, tooltip):
        pass
        # bx, by = treeview.convert_widget_to_bin_window_coords(x, y)
        # selected = treeview.get_path_at_pos(bx, by)
        # if not selected:
            # return
        # tree_path = selected[0]
        # if tree_path is None:
            # return

        # box = Gtk.Box(spacing=5, orientation=Gtk.Orientation.VERTICAL)
        # image = Gtk.Image.new_from_pixbuf(
                # self.liststore[tree_path][LARGE_ICON_COL])
        # image.props.xalign = 0
        # image.props.halign = Gtk.Align.START
        # box.pack_start(image, True, True, 0)
        # if self.liststore[tree_path][NAME_COL] == '..':
            # label = Gtk.Label(_('Go to parent directory: {0}').format(
                              # self.liststore[tree_path][PATH_COL]))
        # else:
            # label = Gtk.Label(self.liststore[tree_path][PATH_COL])
        # label.props.max_width_chars = 40
        # label.props.xalign = 0
        # label.props.halign = Gtk.Align.START
        # label.props.wrap_mode = Pango.WrapMode.CHAR
        # label.props.wrap = True
        # box.pack_start(label, False, False, 0)
        # tooltip.set_custom(box)
        # box.show_all()
        # return True

    def on_treeview_row_activated(self, treeview, tree_path, column):
        pass
        # if tree_path is None:
            # return

        # if self.liststore[tree_path][ISDIR_COL]:
            # dirname = self.liststore[tree_path][PATH_COL]
            # new_url = pcs.get_share_url_with_dirname(self.uk, self.shareid,
                                                     # dirname)
            # self.url_entry.set_text(new_url)
            # self.reload()

    def on_treeview_scrolled(self, adjustment):
        if gutil.reach_scrolled_bottom(adjustment) and self.has_next:
            self.load_next()
