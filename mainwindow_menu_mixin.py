from __future__ import annotations

from PyQt6.QtGui import QAction


class MainWindowMenuMixin:
    def _build_menu(self) -> None:
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("File")

        export_db_action = QAction("Export Tag Database...", self)
        export_db_action.triggered.connect(self._on_file_export_tag_database)
        file_menu.addAction(export_db_action)

        import_db_action = QAction("Import Tag Database...", self)
        import_db_action.triggered.connect(self._on_file_import_tag_database)
        file_menu.addAction(import_db_action)

        file_menu.addSeparator()

        rescan_action = QAction("Rescan", self)
        rescan_action.triggered.connect(self._rescan_library)
        file_menu.addAction(rescan_action)

        browse_action = QAction("Choose Samples Folder...", self)
        browse_action.triggered.connect(self._on_browse_folder)
        file_menu.addAction(browse_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        edit_menu = menu_bar.addMenu("Edit")

        rename_action = QAction("Rename", self)
        rename_action.triggered.connect(self._on_edit_rename)
        edit_menu.addAction(rename_action)
        self._rename_action = rename_action

        delete_action = QAction("Delete", self)
        delete_action.triggered.connect(self._on_edit_delete)
        edit_menu.addAction(delete_action)
        self._delete_action = delete_action

        edit_menu.addSeparator()

        copy_to_action = QAction("Copy-To", self)
        copy_to_action.triggered.connect(self._on_edit_copy_to)
        edit_menu.addAction(copy_to_action)

        move_to_action = QAction("Move-To", self)
        move_to_action.triggered.connect(self._on_edit_move_to)
        edit_menu.addAction(move_to_action)

        tools_menu = menu_bar.addMenu("Tools")
        options_action = QAction("Options", self)
        options_action.triggered.connect(self._on_open_options)
        tools_menu.addAction(options_action)
        edit_shortcuts_action = QAction("Edit Keyboard Shortcuts...", self)
        edit_shortcuts_action.triggered.connect(self._on_edit_keyboard_shortcuts)
        tools_menu.addAction(edit_shortcuts_action)
        tools_menu.addSeparator()
        update_from_folders_action = QAction("Update Categories from Folder Titles", self)
        update_from_folders_action.triggered.connect(self._on_tools_update_categories_from_folders)
        tools_menu.addAction(update_from_folders_action)
        find_duplicates_action = QAction("Find Duplicates", self)
        find_duplicates_action.triggered.connect(self._on_tools_find_duplicates)
        tools_menu.addAction(find_duplicates_action)
        clear_all_categories_action = QAction("Clear All Categories", self)
        clear_all_categories_action.triggered.connect(self._on_tools_clear_all_categories)
        tools_menu.addAction(clear_all_categories_action)
        tools_menu.addSeparator()
        self._auto_folder_tags_action = QAction("Auto-tag from Folders on Scan", self)
        self._auto_folder_tags_action.setCheckable(True)
        self._auto_folder_tags_action.setChecked(self._auto_folder_tags)
        self._auto_folder_tags_action.toggled.connect(self._on_auto_folder_tags_toggled)
        tools_menu.addAction(self._auto_folder_tags_action)

        self._watch_library_changes_action = QAction("Auto-Refresh on Library Changes", self)
        self._watch_library_changes_action.setCheckable(True)
        self._watch_library_changes_action.setChecked(self._watch_library_changes)
        self._watch_library_changes_action.toggled.connect(self._on_watch_library_changes_toggled)
        tools_menu.addAction(self._watch_library_changes_action)

        help_menu = menu_bar.addMenu("Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(self._on_help_about)
        help_menu.addAction(about_action)

        self._apply_keyboard_shortcuts_to_actions()


if __name__ == "__main__":
    print("This module is a helper and is not meant to be run directly.")
    print("Launch gui.py to start JingleAllTheDay.")
    raise SystemExit(1)
