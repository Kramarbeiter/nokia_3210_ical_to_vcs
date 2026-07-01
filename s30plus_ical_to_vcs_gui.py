import os
import re
import json
import sys
import ctypes
import tkinter as tk
from tkinter import filedialog, messagebox, Menu
from tkinterdnd2 import DND_FILES, TkinterDnD
from datetime import datetime, timedelta


def resource_path(relative_path):
    """Gets the absolute path to the resource, compatible with PyInstaller."""
    base_path = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base_path, relative_path)


def clean_text(text):
    """Replaces umlauts and removes special characters for the Nokia."""
    replacements = {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "Ä": "Ae",
        "Ö": "Oe",
        "Ü": "Ue",
        "ß": "ss",
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return re.sub(r"[^a-zA-Z0-9\s\.\!\?\-\:\(\)\,\/]", "", text).strip()


class ToolTip:
    """Creates a small hover window (tooltip) for GUI elements."""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tipwindow = None
        self.id = None
        self.x = self.y = 0
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)

    def enter(self, event=None):
        self.schedule()

    def leave(self, event=None):
        self.unschedule()
        self.hidetip()

    def schedule(self):
        self.unschedule()
        self.id = self.widget.after(500, self.showtip)

    def unschedule(self):
        id = self.id
        self.id = None
        if id:
            self.widget.after_cancel(id)

    def showtip(self, event=None):
        x = self.widget.winfo_rootx() + 25
        y = self.widget.winfo_rooty() + 20

        self.tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry("+%d+%d" % (x, y))
        label = tk.Label(
            tw,
            text=self.text,
            justify=tk.LEFT,
            background="#ffffe0",
            relief=tk.SOLID,
            borderwidth=1,
            font=("tahoma", 8, "normal"),
        )
        label.pack(ipadx=1)

    def hidetip(self):
        tw = self.tipwindow
        self.tipwindow = None
        if tw:
            tw.destroy()


class Event:
    def __init__(
        self,
        start="",
        end="",
        summary="",
        location="",
        description="",
        rrule="",
        uid="",
    ):
        start_raw = start.split("Z")[0].split("+")[0]
        end_raw = end.split("Z")[0].split("+")[0] if end else start_raw

        self.summary_clean = clean_text(summary)
        self.location_clean = clean_text(location)
        self.rrule_orig = rrule

        # Identify if the original strings were all-day (no 'T' present)
        is_all_day_start = "T" not in start_raw
        is_all_day_end = "T" not in end_raw

        d_start_str = start_raw[:8]
        t_start_str = (
            start_raw.split("T")[1] if not is_all_day_start else "000000"
        ).ljust(6, "0")[:6]

        d_end_str = end_raw[:8]
        t_end_str = (end_raw.split("T")[1] if not is_all_day_end else "000000").ljust(
            6, "0"
        )[:6]

        # Handle exclusive end date for all-day events (e.g. Google Calendar sets end to the next day)
        if is_all_day_start and is_all_day_end and d_end_str > d_start_str:
            try:
                end_dt = datetime.strptime(d_end_str, "%Y%m%d") - timedelta(days=1)
                d_end_str = end_dt.strftime("%Y%m%d")
            except ValueError:
                pass

        self.start = f"{d_start_str}T{t_start_str}"
        self.end_orig = f"{d_end_str}T{t_end_str}"

        self.time_suffix = ""

        # --- THE MULTI-DAY PATCH ---
        # Detects if the event spans across multiple calendar days
        if d_start_str != d_end_str:
            # If it has specific times, save them to be appended to the title safely later
            if not (is_all_day_start and is_all_day_end):
                self.time_suffix = f", {t_start_str[:2]}:{t_start_str[2:4]}-{t_end_str[:2]}:{t_end_str[2:4]}"

            # Force the event to start (and end) at midnight to act like a full-day event on the Nokia
            self.start = f"{d_start_str}T000000"
            self.end_orig = f"{d_end_str}T000000"

            # Turn it into a daily recurring event if it isn't a series already
            if not self.rrule_orig:
                self.rrule_orig = f"FREQ=DAILY;UNTIL={d_end_str}T000000"

        # Saves the unique ID. Creates a fallback hash if UID is missing in the file.
        self.uid = uid.strip() if uid else f"{self.start}-{self.summary_clean}"
        self.final_summary = ""

    def get_interval(self):
        if not self.rrule_orig:
            return 1
        match = re.search(r"INTERVAL=(\d+)", self.rrule_orig.upper())
        return int(match.group(1)) if match else 1

    def translate_and_build_summary(self):
        r = self.rrule_orig.upper()
        interval = self.get_interval()
        logic_str = ""

        # Round-up logic (adds a note to clarify the change)
        if r and interval > 1:
            try:
                start_dt = datetime.strptime(self.start[:8], "%Y%m%d")
                kw = start_dt.isocalendar()[1]
                unit = "W" if "WEEKLY" in r else "D"
                logic_str = f"({interval}{unit}-W{kw})"
            except ValueError:
                pass

        title = self.summary_clean
        location = self.location_clean

        # Combine protected suffixes (Times and Logic)
        logic_suffix = self.time_suffix
        if logic_str:
            logic_suffix += f" {logic_str}"

        avail_len = 40 - len(logic_suffix)
        loc_str = f", {location}" if location else ""

        # Smart truncation logic: Prioritize Location and Logic over full Title
        if len(title) + len(loc_str) <= avail_len:
            test_summary = title + loc_str + logic_suffix
        else:
            # If location is excessively long, cap it
            if len(loc_str) > 15:
                location = location[:12] + "." if len(location) > 12 else location
                loc_str = f", {location}" if location else ""

            title_max = avail_len - len(loc_str)
            if title_max < 5:
                title_max = avail_len
                loc_str = ""

            title = title[:title_max].strip()
            test_summary = title + loc_str + logic_suffix

        self.final_summary = test_summary

        prefix = ""
        if r:
            if "DAILY" in r:
                prefix = "D1"
            elif "WEEKLY" in r:
                prefix = "W1"
            elif "MONTHLY" in r:
                prefix = "MD1"
            elif "YEARLY" in r:
                prefix = "YD1"

        return f"{prefix} {self.start}" if prefix else ""

    def toVCS(self):
        rrule_nokia = self.translate_and_build_summary()

        nokia_end = self.end_orig
        if rrule_nokia:
            until_date = "20991231"
            match = re.search(r"UNTIL=([0-9]{8})", self.rrule_orig.upper())
            if match:
                until_date = match.group(1)

            end_time = self.end_orig[8:] if len(self.end_orig) > 8 else "T000000"
            nokia_end = f"{until_date}{end_time}"

        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:1.0",
            "BEGIN:VEVENT",
            f"SUMMARY;CHARSET=UTF-8:{self.final_summary}",
            f"DTSTART:{self.start}",
            f"DTEND:{nokia_end}",
        ]

        if rrule_nokia:
            lines.append(f"RRULE:{rrule_nokia}")

        lines.append(f"AALARM:{self.start};;;")
        lines.append("END:VEVENT")
        lines.append("END:VCALENDAR")

        return "\r\n".join(lines)

    def get_filename(self):
        clean_title = re.sub(r"[^a-zA-Z0-9]", "", self.summary_clean.replace(" ", "_"))
        return f"{clean_title[:15]}_{self.start[:15]}.vcs"


class Calendar:
    def __init__(self, file_path):
        self.events = []
        if not os.path.exists(file_path):
            return
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            tmp = {
                "start": "",
                "end": "",
                "summary": "",
                "location": "",
                "description": "",
                "rrule": "",
                "uid": "",
            }
            in_ev = False
            for line in f:
                line = line.strip()
                if line.startswith("BEGIN:VEVENT"):
                    in_ev = True
                elif line.startswith("END:VEVENT"):
                    self.events.append(Event(**tmp))
                    tmp = {
                        "start": "",
                        "end": "",
                        "summary": "",
                        "location": "",
                        "description": "",
                        "rrule": "",
                        "uid": "",
                    }
                    in_ev = False
                elif in_ev and ":" in line:
                    try:
                        k_f, v = line.split(":", 1)
                        k = k_f.split(";")[0]
                        if k == "DTSTART":
                            tmp["start"] = v
                        elif k == "DTEND":
                            tmp["end"] = v
                        elif k == "SUMMARY":
                            tmp["summary"] = v
                        elif k == "LOCATION":
                            tmp["location"] = v
                        elif k == "RRULE":
                            tmp["rrule"] = v
                        elif k == "UID":
                            tmp["uid"] = v
                    except (ValueError, IndexError):
                        continue
                    except Exception as e:
                        print(f"Unexpected error parsing line '{line}': {e}")
                        continue

    def scan(self, all_past=False):
        # Sorts everything chronologically first
        self.events.sort(key=lambda x: x.start)
        today = datetime.now().strftime("%Y%m%d")

        scenario2_events = []
        dead_past_events = []

        for e in self.events:
            is_scenario2 = False

            # Check if it is a future/today event or an ongoing series
            if e.start[:8] >= today:
                is_scenario2 = True
            elif e.rrule_orig:
                rrule_upper = e.rrule_orig.upper()
                match_until = re.search(r"UNTIL=([0-9]{8})", rrule_upper)
                match_count = re.search(r"COUNT=(\d+)", rrule_upper)

                if match_until:
                    if match_until.group(1) >= today:
                        is_scenario2 = True
                elif match_count:
                    # Approximation of the end date for COUNT-based series
                    count = int(match_count.group(1))
                    interval = e.get_interval()
                    freq_match = re.search(
                        r"FREQ=(DAILY|WEEKLY|MONTHLY|YEARLY)", rrule_upper
                    )
                    freq = freq_match.group(1) if freq_match else "DAILY"

                    days_mult = {
                        "DAILY": 1,
                        "WEEKLY": 7,
                        "MONTHLY": 31,
                        "YEARLY": 365,
                    }.get(freq, 1)
                    total_days = count * interval * days_mult

                    try:
                        start_dt = datetime.strptime(e.start[:8], "%Y%m%d")
                        end_dt = start_dt + timedelta(days=total_days)
                        if end_dt.strftime("%Y%m%d") >= today:
                            is_scenario2 = True
                    except ValueError:
                        is_scenario2 = True
                else:
                    # Infinite series without UNTIL or COUNT
                    is_scenario2 = True

            # Categorize events based on our check
            if is_scenario2:
                scenario2_events.append(e)
            else:
                dead_past_events.append(e)

        # If all_past is true, we prioritize scenario2 events, then append dead events to fill the quota
        if all_past:
            return scenario2_events + dead_past_events
        else:
            return scenario2_events


class NokiaConverterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Coca - S30+ iCal to VCS Converter")
        self.root.geometry("500x515")
        self.root.resizable(False, False)

        # --- Taskbar fix for Windows ---
        try:
            myappid = "nokia.s30plus.converter.1.0"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception:
            pass

        # Load the icon for the window
        icon_path = resource_path("./icon.ico")
        if os.path.exists(icon_path):
            try:
                self.root.iconbitmap(icon_path)
            except tk.TclError:
                # Fallback for Linux/macOS if .ico isn't supported natively by Tcl/Tk
                try:
                    img = tk.PhotoImage(file=icon_path)
                    self.root.iconphoto(True, img)
                except Exception:
                    pass  # Gracefully ignore if the format completely fails on this OS

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # --- Profile & State Variables ---
        self.file_paths = []
        self.config_path = os.path.join(
            os.path.expanduser("~"), ".s30_converter_cfg.json"
        )
        self.current_profile_path = None
        self.unsaved_profile_changes = False
        self.exported_uids = []
        self.last_profile_dir = ""
        self.last_ics_dir = ""  # Remembers the last used directory for .ics files

        self.max_events_var = tk.StringVar(value="0")
        self.out_dir_var = tk.StringVar(value=os.path.join(os.getcwd(), "vcs_files"))
        self.all_past_var = tk.BooleanVar(value=False)
        self.skip_dupes_var = tk.BooleanVar(value=True)

        # --- Menu Bar ---
        menubar = Menu(self.root)
        profile_menu = Menu(menubar, tearoff=0)
        profile_menu.add_command(label="New Profile", command=self.new_profile)
        profile_menu.add_separator()
        profile_menu.add_command(label="Load Profile", command=self.load_profile)
        profile_menu.add_command(label="Save Profile", command=self.save_profile)
        menubar.add_cascade(label="Profile", menu=profile_menu)
        self.root.config(menu=menubar)

        # --- Active Profile Label ---
        self.profile_label_var = tk.StringVar()
        self.update_profile_label()
        profile_lbl = tk.Label(
            self.root,
            textvariable=self.profile_label_var,
            font=("Arial", 9, "bold"),
            fg="#555555",
        )
        profile_lbl.pack(pady=(10, 0))

        self.load_settings()

        # --- Top Frame ---
        top_frame = tk.Frame(root)
        top_frame.pack(fill=tk.X, padx=20, pady=(10, 5))

        self.add_btn = tk.Button(
            top_frame, text="Add .ics", command=self.add_files, width=10
        )
        self.add_btn.pack(side=tk.LEFT)
        ToolTip(self.add_btn, "Select one or more .ics files from your PC.")

        tk.Label(top_frame, text="Selected Files:", font=("Arial", 9, "bold")).pack(
            side=tk.LEFT, padx=20
        )

        # --- Listbox Frame ---
        list_frame = tk.Frame(root)
        list_frame.pack(padx=20, pady=5, fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.listbox = tk.Listbox(
            list_frame,
            selectmode=tk.EXTENDED,
            yscrollcommand=scrollbar.set,
            font=("Arial", 10),
        )
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.listbox.yview)

        # Drag & Drop registration for the listbox
        self.listbox.drop_target_register(DND_FILES)  # type: ignore
        self.listbox.dnd_bind("<<Drop>>", self.drop_files)  # type: ignore

        self.listbox.bind("<Delete>", self.remove_selected)
        ToolTip(
            self.listbox,
            "Drag & Drop your .ics files here!\nMultiple selection enabled.\nPress 'Del' or right-click to remove files.",
        )

        self.context_menu = Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Delete", command=self.remove_selected)
        self.listbox.bind("<Button-3>", self.show_context_menu)

        # --- Settings Frame ---
        settings_frame = tk.Frame(root)
        settings_frame.pack(fill=tk.X, padx=20, pady=10)

        tk.Label(settings_frame, text="Amount of events to process per file:").grid(
            row=0, column=0, sticky="w", pady=2
        )
        self.max_events_entry = tk.Entry(
            settings_frame, textvariable=self.max_events_var, width=5
        )
        self.max_events_entry.grid(row=0, column=1, sticky="w", padx=5)
        ToolTip(
            self.max_events_entry,
            "Maximum number of events to process per file.\n'0' means: Process ALL events in the file.\nNote: Future and ongoing events are always prioritized over old past events.",
        )

        tk.Label(settings_frame, text="Output Folder:").grid(
            row=1, column=0, sticky="w", pady=5
        )
        folder_frame = tk.Frame(settings_frame)
        folder_frame.grid(row=1, column=1, sticky="w", padx=5)

        self.out_dir_entry = tk.Entry(
            folder_frame, textvariable=self.out_dir_var, width=30
        )
        self.out_dir_entry.pack(side=tk.LEFT)
        ToolTip(
            self.out_dir_entry,
            "The folder where the converted .vcs files will be saved.",
        )

        self.browse_btn = tk.Button(
            folder_frame, text="...", command=self.choose_folder, width=3
        )
        self.browse_btn.pack(side=tk.LEFT, padx=5)
        ToolTip(self.browse_btn, "Browse for output folder.")

        # --- Checkboxes with increased top padding (pady=(15, 2)) ---
        self.chk_past = tk.Checkbutton(
            settings_frame, text="Export past events", variable=self.all_past_var
        )
        self.chk_past.grid(row=2, column=0, columnspan=2, sticky="w", pady=(15, 2))
        ToolTip(
            self.chk_past,
            "If checked, past events will also be exported.\nOtherwise, only events from today onwards\n(incl. ongoing past series) are exported.",
        )

        self.chk_dupes = tk.Checkbutton(
            settings_frame,
            text="Skip already exported events of active loaded profile",
            variable=self.skip_dupes_var,
        )
        self.chk_dupes.grid(row=3, column=0, columnspan=2, sticky="w", pady=2)
        ToolTip(
            self.chk_dupes,
            "Uses the active Profile Memory to prevent creating duplicates. ",
        )

        # --- Convert Button ---
        self.convert_btn = tk.Button(
            root,
            text="Convert Files",
            command=self.process_files,
            bg="#4CAF50",
            fg="white",
            font=("Arial", 10, "bold"),
            padx=10,
            pady=5,
        )
        self.convert_btn.pack(pady=(5, 10))
        ToolTip(self.convert_btn, "Starts converting all files currently in the list.")

    def update_profile_label(self):
        status = "*" if self.unsaved_profile_changes else ""
        if self.current_profile_path:
            name = os.path.basename(self.current_profile_path)
            self.profile_label_var.set(f"Active Profile: {name}{status}")
        else:
            self.profile_label_var.set(f"Active Profile: Unsaved{status}")

    def load_settings(self):
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, "r") as f:
                    config = json.load(f)
                    if "max_events" in config:
                        self.max_events_var.set(config["max_events"])
                    if "out_dir" in config:
                        self.out_dir_var.set(config["out_dir"])
                    if "all_past" in config:
                        self.all_past_var.set(config["all_past"])
                    if "skip_dupes" in config:
                        self.skip_dupes_var.set(config["skip_dupes"])
                    if "last_profile_dir" in config:
                        self.last_profile_dir = config["last_profile_dir"]
                    if "last_ics_dir" in config:
                        self.last_ics_dir = config["last_ics_dir"]
                    if "last_profile_path" in config:
                        path = config["last_profile_path"]
                        if path and os.path.exists(path):
                            self._load_profile_data(path)
        except Exception:
            pass

    def save_settings(self):
        try:
            config = {
                "max_events": self.max_events_var.get(),
                "out_dir": self.out_dir_var.get(),
                "all_past": self.all_past_var.get(),
                "skip_dupes": self.skip_dupes_var.get(),
                "last_profile_path": getattr(self, "current_profile_path", None),
                "last_profile_dir": getattr(self, "last_profile_dir", ""),
                "last_ics_dir": getattr(self, "last_ics_dir", ""),
            }
            with open(self.config_path, "w") as f:
                json.dump(config, f)
        except Exception:
            pass

    def _load_profile_data(self, filepath):
        try:
            with open(filepath, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    self.exported_uids = data
                    self.current_profile_path = filepath
                    self.unsaved_profile_changes = False
                    self.update_profile_label()
                    return True
        except Exception:
            pass
        return False

    def new_profile(self):
        """Creates a new empty profile and loads it."""
        if self.unsaved_profile_changes:
            res = messagebox.askyesnocancel(
                "Unsaved Changes",
                "You have unsaved exports. Do you want to save before creating a new profile?",
            )
            if res is True:
                self.save_profile()
            elif res is None:
                return  # Aborts

        default_name = f"nokia_profile_{datetime.now().strftime('%Y%m%d')}.json"

        filepath = filedialog.asksaveasfilename(
            title="Create New Phone Profile",
            initialdir=self.last_profile_dir if self.last_profile_dir else None,
            defaultextension=".json",
            filetypes=[("JSON Profile", "*.json")],
            initialfile=default_name,
        )

        if filepath:
            try:
                self.last_profile_dir = os.path.dirname(filepath)
                self.exported_uids = []
                with open(filepath, "w") as f:
                    json.dump(self.exported_uids, f)

                self.current_profile_path = filepath
                self.unsaved_profile_changes = False
                self.update_profile_label()
                self.save_settings()
                messagebox.showinfo(
                    "Success", "New profile created and loaded successfully!"
                )
            except Exception as e:
                messagebox.showerror("Error", f"Could not create profile:\n{e}")

    def load_profile(self):
        """Manually loads a specific list of UIDs from a JSON file."""
        if self.unsaved_profile_changes:
            res = messagebox.askyesnocancel(
                "Unsaved Changes",
                "You have unsaved exports. Do you want to save before loading a new profile?",
            )
            if res is True:
                self.save_profile()
            elif res is None:
                return

        filepath = filedialog.askopenfilename(
            title="Load Phone Profile",
            initialdir=self.last_profile_dir if self.last_profile_dir else None,
            filetypes=[("JSON Profile", "*.json")],
        )

        if filepath:
            self.last_profile_dir = os.path.dirname(filepath)
            if self._load_profile_data(filepath):
                messagebox.showinfo(
                    "Success",
                    f"Profile loaded successfully!\n\n({len(self.exported_uids)} events in memory)",
                )
            else:
                messagebox.showerror(
                    "Error", "Invalid profile format. Expected a valid JSON list."
                )

    def save_profile(self):
        """Saves the current profile. Updates the timestamp in the filename automatically."""
        today_str = datetime.now().strftime("%Y%m%d")

        if self.current_profile_path:
            # Smart Save: Extract and update the date in the filename
            old_filepath = self.current_profile_path
            dir_name = os.path.dirname(old_filepath)
            base_name = os.path.basename(old_filepath)

            # Isolate base name without file extension
            name_without_ext = os.path.splitext(base_name)[0]

            # Trim a possible old date at the end (Regex looks for "_12345678" at the end)
            name_without_date = re.sub(r"_\d{8}$", "", name_without_ext)

            # Assemble new filename with current date
            new_filename = f"{name_without_date}_{today_str}.json"
            filepath = os.path.join(dir_name, new_filename)

            try:
                self.last_profile_dir = dir_name
                self.exported_uids = list(set(self.exported_uids))
                with open(filepath, "w") as f:
                    json.dump(self.exported_uids, f)

                # If the name changed (due to a new date), delete the old file
                if filepath != old_filepath and os.path.exists(old_filepath):
                    os.remove(old_filepath)

                self.current_profile_path = filepath
                self.unsaved_profile_changes = False
                self.update_profile_label()
                messagebox.showinfo(
                    "Success",
                    f"Profile automatically saved and updated to:\n{new_filename}",
                )
            except Exception as e:
                messagebox.showerror("Error", f"Could not auto-save profile:\n{e}")

        else:
            # If no profile has been loaded/created yet (Fallback)
            default_name = f"nokia_profile_{today_str}.json"
            filepath = filedialog.asksaveasfilename(
                title="Save Phone Profile",
                initialdir=self.last_profile_dir if self.last_profile_dir else None,
                defaultextension=".json",
                filetypes=[("JSON Profile", "*.json")],
                initialfile=default_name,
            )

            if filepath:
                try:
                    self.last_profile_dir = os.path.dirname(filepath)
                    self.exported_uids = list(set(self.exported_uids))
                    with open(filepath, "w") as f:
                        json.dump(self.exported_uids, f)

                    self.current_profile_path = filepath
                    self.unsaved_profile_changes = False
                    self.update_profile_label()
                    messagebox.showinfo("Success", "Profile saved successfully!")
                except Exception as e:
                    messagebox.showerror("Error", f"Could not save profile:\n{e}")

    def on_closing(self):
        if self.unsaved_profile_changes:
            res = messagebox.askyesnocancel(
                "Unsaved Changes",
                "You have exported new events that haven't been saved to your profile.\n\nDo you want to save your profile before closing?",
            )
            if res is True:
                self.save_profile()
            elif res is None:
                return  # Aborts closing if they hit cancel

        # We only save the UI settings and the path to the last loaded profile.
        # The memory list itself is deliberately wiped from RAM when closing.
        self.save_settings()
        self.root.destroy()

    def add_files(self):
        files = filedialog.askopenfilenames(
            title="Select .ics files",
            initialdir=self.last_ics_dir if self.last_ics_dir else None,
            filetypes=[("ICS files", "*.ics")],
        )
        if files:
            # Immediately save the folder of the last selected file
            self.last_ics_dir = os.path.dirname(files[0])
            self.save_settings()

            for f in files:
                if f not in self.file_paths:
                    self.file_paths.append(f)
                    self.listbox.insert(tk.END, os.path.basename(f))

    def drop_files(self, event):
        files = self.root.tk.splitlist(event.data)
        for f in files:
            if f.lower().endswith(".ics") and f not in self.file_paths:
                self.file_paths.append(f)
                self.listbox.insert(tk.END, os.path.basename(f))

    def remove_selected(self, event=None):
        selection = self.listbox.curselection()
        if not selection:
            return
        for i in reversed(selection):
            self.listbox.delete(i)
            del self.file_paths[i]

    def show_context_menu(self, event):
        try:
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(self.listbox.nearest(event.y))
            self.listbox.activate(self.listbox.nearest(event.y))
            self.context_menu.tk_popup(event.x_root, event.y_root)
        except Exception:
            pass
        finally:
            self.context_menu.grab_release()

    def choose_folder(self):
        folder = filedialog.askdirectory(
            title="Select Output Folder", initialdir=self.out_dir_var.get()
        )
        if folder:
            self.out_dir_var.set(folder)

    def process_files(self):
        if not self.file_paths:
            messagebox.showwarning(
                "No Files", "Please add at least one .ics file to the list."
            )
            return

        out_dir = self.out_dir_var.get()
        if not os.path.exists(out_dir):
            try:
                os.makedirs(out_dir)
            except Exception as e:
                messagebox.showerror(
                    "Error", f"Could not create output directory:\n{e}"
                )
                return

        try:
            max_limit = int(self.max_events_var.get())
        except ValueError:
            messagebox.showerror(
                "Invalid Input", "Amount of events must be a valid number."
            )
            return

        all_past = self.all_past_var.get()
        skip_dupes = self.skip_dupes_var.get()

        total_files = 0
        total_events = 0
        skipped_events = 0
        new_exports_added = False

        for file_path in self.file_paths:
            cal = Calendar(file_path)
            found_events = cal.scan(all_past=all_past)

            # --- Anti-Duplicate Filter ---
            if skip_dupes:
                filtered_events = []
                for e in found_events:
                    if e.uid in self.exported_uids:
                        skipped_events += 1
                    else:
                        filtered_events.append(e)
                found_events = filtered_events

            total_found = len(found_events)
            if total_found == 0:
                continue

            limit = max_limit if max_limit > 0 else total_found
            export_count = min(limit, total_found)

            for i in range(export_count):
                ev = found_events[i]
                vcs_text = ev.toVCS()
                path = os.path.join(out_dir, ev.get_filename())
                with open(path, "w", encoding="latin-1", errors="replace") as f:
                    f.write(vcs_text)

                total_events += 1
                self.exported_uids.append(ev.uid)
                new_exports_added = True

            total_files += 1

        if new_exports_added:
            self.unsaved_profile_changes = True
            self.update_profile_label()

        self.save_settings()

        msg = f"Done!\n\nProcessed {total_files} file(s).\nCreated {total_events} new .vcs files in:\n{out_dir}"
        if skip_dupes and skipped_events > 0:
            msg += f"\n\n(Skipped {skipped_events} events that were already exported previously)"

        messagebox.showinfo("Success", msg)


if __name__ == "__main__":
    # Use TkinterDnD instead of tk.Tk() for Drag & Drop support
    root = TkinterDnD.Tk()
    app = NokiaConverterApp(root)
    root.mainloop()
