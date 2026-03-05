import customtkinter as ctk
import os
import sys
import time
import threading
import pygame
import winreg
import json
import functools
import ctypes
from datetime import datetime
import pystray
from PIL import Image, ImageDraw

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")
TASKS_FILE = "tasks.json"

# ========================================================
# 1. 弹窗类：步骤 3/3 任务命名
# ========================================================
class TaskRenameDialog(ctk.CTkToplevel):
    def __init__(self, parent, default_name, callback):
        super().__init__(parent)
        self.callback = callback
        self.title("步骤 3/3: 任务命名")
        self.geometry("350x250")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.grab_set() 

        ctk.CTkLabel(self, text="为这个任务起个名字", font=ctk.CTkFont(size=18, weight="bold"), 
                     text_color="#1F6AA5").pack(pady=(25, 15))

        self.name_entry = ctk.CTkEntry(self, width=250, height=35, font=ctk.CTkFont(size=14))
        clean_name = os.path.splitext(default_name)[0]
        if len(clean_name) > 20: clean_name = clean_name[:20] + "..."
        self.name_entry.insert(0, clean_name)
        self.name_entry.pack(pady=10)
        
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(side="bottom", pady=20)

        self.btn_confirm = ctk.CTkButton(btn_frame, text="完成", command=self.on_confirm,
                                       width=120, height=40, corner_radius=20, 
                                       font=ctk.CTkFont(size=15, weight="bold"))
        self.btn_confirm.pack(side="left", padx=10)

        self.btn_cancel = ctk.CTkButton(btn_frame, text="取消", command=self.destroy,
                                      width=80, height=40, corner_radius=20,
                                      fg_color="transparent", border_width=2, text_color="gray")
        self.btn_cancel.pack(side="left", padx=10)
        
        self.name_entry.bind("<Return>", lambda event: self.on_confirm())
        self.name_entry.focus_set()

    def on_confirm(self):
        new_name = self.name_entry.get().strip()
        if not new_name:
            new_name = "未命名任务"
        self.callback(new_name)
        self.destroy()

# ========================================================
# 2. 弹窗类：步骤 2/3 选择星期
# ========================================================
class WeekdaySelectionDialog(ctk.CTkToplevel):
    def __init__(self, parent, time_str, song_count, callback, initial_selection=None):
        super().__init__(parent)
        self.callback = callback
        self.title("步骤 2/3: 选择播放日期")
        self.geometry("400x550") 
        self.resizable(False, False)
        self.attributes("-topmost", True) 
        self.grab_set() 
        
        ctk.CTkLabel(self, text=f"任务时间: {time_str}", font=ctk.CTkFont(size=22, weight="bold"), text_color="#1F6AA5").pack(pady=(30, 5))
        ctk.CTkLabel(self, text=f"包含歌曲数量: {song_count} 首", text_color="gray", font=ctk.CTkFont(size=12)).pack(pady=(0, 20))
        
        ctk.CTkFrame(self, height=2, fg_color="#E0E0E0").pack(fill="x", padx=30, pady=5)
        
        ctk.CTkLabel(self, text="请勾选需要播放的星期:", font=ctk.CTkFont(size=15, weight="bold")).pack(pady=15)

        self.checkboxes = []
        days = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        
        self.check_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.check_frame.pack(pady=5)

        for i, day in enumerate(days):
            is_checked = True
            if initial_selection is not None:
                is_checked = (i in initial_selection)
            
            var = ctk.BooleanVar(value=is_checked)
            cb = ctk.CTkCheckBox(self.check_frame, text=day, variable=var, font=ctk.CTkFont(size=14), 
                                 checkbox_width=24, checkbox_height=24, border_width=2)
            cb.grid(row=i, column=0, pady=8, padx=40, sticky="w")
            self.checkboxes.append(var)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(side="bottom", pady=30, fill="x", padx=40) 
        
        self.btn_confirm = ctk.CTkButton(btn_frame, text="下一步", command=self.on_confirm, 
                                       height=45, corner_radius=22, 
                                       font=ctk.CTkFont(size=16, weight="bold"),
                                       fg_color="#1F6AA5", hover_color="#144d7a")
        self.btn_confirm.pack(side="left", expand=True, fill="x", padx=(0, 10))
        
        self.btn_cancel = ctk.CTkButton(btn_frame, text="取消", command=self.destroy, 
                                      height=45, corner_radius=22, width=80,
                                      fg_color="transparent", border_width=2, border_color="gray", text_color="gray",
                                      hover_color="#EEEEEE")
        self.btn_cancel.pack(side="right", padx=(10, 0))

    def on_confirm(self):
        selected_indices = [i for i, var in enumerate(self.checkboxes) if var.get()]
        self.callback(selected_indices)
        self.destroy()

# ========================================================
# 3. 弹窗类：步骤 1/3 选择歌曲
# ========================================================
class MultiSongSelectDialog(ctk.CTkToplevel):
    def __init__(self, parent, all_music_files, callback, initial_selection=None):
        super().__init__(parent)
        self.all_music_files = all_music_files 
        self.callback = callback
        
        self.selected_files = list(initial_selection) if initial_selection else []

        self.title("步骤 1/3: 选择歌曲并排序")
        self.geometry("700x500")
        self.attributes("-topmost", True)
        self.grab_set()

        ctk.CTkLabel(self, text="请从左侧添加歌曲，在右侧调整播放顺序", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=10)

        content_frame = ctk.CTkFrame(self, fg_color="transparent")
        content_frame.pack(expand=True, fill="both", padx=10, pady=(0, 10))

        left_frame = ctk.CTkFrame(content_frame)
        left_frame.pack(side="left", fill="both", expand=True, padx=5)
        ctk.CTkLabel(left_frame, text="音乐库 (点击添加)", text_color="gray").pack(pady=5)
        
        self.scroll_library = ctk.CTkScrollableFrame(left_frame)
        self.scroll_library.pack(fill="both", expand=True, padx=5, pady=5)
        
        self.populate_library()

        right_frame = ctk.CTkFrame(content_frame)
        right_frame.pack(side="right", fill="both", expand=True, padx=5)
        ctk.CTkLabel(right_frame, text="播放顺序 (从上到下)", text_color="#1F6AA5", font=ctk.CTkFont(weight="bold")).pack(pady=5)
        
        self.scroll_playlist = ctk.CTkScrollableFrame(right_frame)
        self.scroll_playlist.pack(fill="both", expand=True, padx=5, pady=5)
        
        self.update_playlist_ui()

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(side="bottom", pady=15, fill="x", padx=20)
        
        self.status_lbl = ctk.CTkLabel(btn_frame, text="已选: 0 首", text_color="gray")
        self.status_lbl.pack(side="left", padx=10)

        ctk.CTkButton(btn_frame, text="下一步", command=self.on_next, width=120, height=40, font=ctk.CTkFont(weight="bold")).pack(side="right", padx=10)
        ctk.CTkButton(btn_frame, text="取消", command=self.destroy, fg_color="transparent", border_width=1, text_color="gray", width=80).pack(side="right", padx=10)

    def populate_library(self):
        for f_path in self.all_music_files:
            try:
                row = ctk.CTkFrame(self.scroll_library, fg_color="transparent")
                row.pack(fill="x", pady=2)
                
                name = os.path.basename(f_path)
                display_name = name if len(name) < 25 else name[:22] + "..."
                
                ctk.CTkLabel(row, text=display_name, anchor="w").pack(side="left", padx=5)
                
                add_cmd = functools.partial(self.add_song, f_path)
                
                ctk.CTkButton(row, text="+", width=30, height=24, 
                              command=add_cmd).pack(side="right", padx=5)
            except Exception:
                pass

    def add_song(self, path):
        try:
            self.selected_files.append(path)
            self.update_playlist_ui()
        except Exception:
            pass

    def remove_song(self, index):
        if 0 <= index < len(self.selected_files):
            del self.selected_files[index]
            self.update_playlist_ui()

    def move_up(self, index):
        if index > 0:
            self.selected_files[index], self.selected_files[index-1] = self.selected_files[index-1], self.selected_files[index]
            self.update_playlist_ui()

    def move_down(self, index):
        if index < len(self.selected_files) - 1:
            self.selected_files[index], self.selected_files[index+1] = self.selected_files[index+1], self.selected_files[index]
            self.update_playlist_ui()

    def update_playlist_ui(self):
        try:
            for widget in self.scroll_playlist.winfo_children():
                widget.destroy()
            
            self.status_lbl.configure(text=f"已选: {len(self.selected_files)} 首")

            for idx, f_path in enumerate(self.selected_files):
                row = ctk.CTkFrame(self.scroll_playlist)
                row.pack(fill="x", pady=2, padx=2)
                
                ctk.CTkLabel(row, text=f"{idx+1}.", width=20, text_color="gray").pack(side="left", padx=(5,0))
                
                name = os.path.basename(f_path)
                display_name = name if len(name) < 18 else name[:15] + "..."
                ctk.CTkLabel(row, text=display_name, anchor="w").pack(side="left", padx=5, expand=True, fill="x")

                if idx > 0:
                    cmd_up = functools.partial(self.move_up, idx)
                    ctk.CTkButton(row, text="↑", width=25, height=20, fg_color="#DDDDDD", text_color="black", hover_color="#BBBBBB",
                                  command=cmd_up).pack(side="left", padx=2)
                else:
                    ctk.CTkLabel(row, text="", width=29).pack(side="left")

                if idx < len(self.selected_files) - 1:
                    cmd_down = functools.partial(self.move_down, idx)
                    ctk.CTkButton(row, text="↓", width=25, height=20, fg_color="#DDDDDD", text_color="black", hover_color="#BBBBBB",
                                  command=cmd_down).pack(side="left", padx=2)
                else:
                    ctk.CTkLabel(row, text="", width=29).pack(side="left")

                cmd_del = functools.partial(self.remove_song, idx)
                ctk.CTkButton(row, text="✕", width=25, height=20, fg_color="transparent", text_color="red", hover_color="#FFEEEE",
                              command=cmd_del).pack(side="left", padx=(5, 5))
                              
        except Exception:
            pass

    def on_next(self):
        if not self.selected_files:
            return 
        self.callback(self.selected_files)
        self.destroy()

# ========================================================
# NEW: 弹窗类：步骤 0/3 设置时间和模式 (优化：动态隐藏结束时间)
# ========================================================
class TimeModeDialog(ctk.CTkToplevel):
    def __init__(self, parent, callback, initial_data=None):
        super().__init__(parent)
        self.callback = callback
        self.title("任务设置: 时间与模式")
        self.attributes("-topmost", True)
        self.grab_set()
        self.resizable(False, False)

        # 默认值
        init_start = ""
        init_mode = "song" # 'song' or 'duration'
        init_end = ""
        
        if initial_data:
            init_start = initial_data.get("time", "")
            init_mode = initial_data.get("mode", "song")
            init_end = initial_data.get("end_time", "")

        # 1. 开始时间
        ctk.CTkLabel(self, text="1. 设置开始时间", font=ctk.CTkFont(weight="bold")).pack(pady=(20, 5), anchor="w", padx=30)
        self.start_entry = ctk.CTkEntry(self, width=200, placeholder_text="例如 1300 或 13:00")
        self.start_entry.insert(0, init_start)
        self.start_entry.pack(pady=5)

        # 2. 播放模式
        ctk.CTkLabel(self, text="2. 选择播放模式", font=ctk.CTkFont(weight="bold")).pack(pady=(20, 5), anchor="w", padx=30)
        
        self.mode_var = ctk.StringVar(value=init_mode)
        
        self.radio_song = ctk.CTkRadioButton(self, text="模式一：固定曲目 (播完即止)", variable=self.mode_var, value="song", command=self.toggle_mode)
        self.radio_song.pack(pady=5, anchor="w", padx=50)
        
        self.radio_duration = ctk.CTkRadioButton(self, text="模式二：固定时长 (循环/截断)", variable=self.mode_var, value="duration", command=self.toggle_mode)
        self.radio_duration.pack(pady=5, anchor="w", padx=50)

        # 3. 结束时间 (仅模式二) - 使用 Frame 包裹以便整体隐藏
        self.end_time_frame = ctk.CTkFrame(self, fg_color="transparent")
        # 初始不 pack，由 toggle_mode 决定
        
        ctk.CTkLabel(self.end_time_frame, text="结束时间:", text_color="#1F6AA5", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(50, 10))
        self.end_entry = ctk.CTkEntry(self.end_time_frame, width=120, placeholder_text="例如 13:40")
        self.end_entry.insert(0, init_end)
        self.end_entry.pack(side="left")

        # 底部按钮
        self.btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.btn_frame.pack(side="bottom", pady=20)
        ctk.CTkButton(self.btn_frame, text="下一步", command=self.on_confirm, width=120).pack(side="left", padx=10)
        ctk.CTkButton(self.btn_frame, text="取消", command=self.destroy, fg_color="transparent", border_width=1, text_color="gray", width=80).pack(side="left", padx=10)

        # 初始化显示状态
        self.toggle_mode()

    def toggle_mode(self):
        if self.mode_var.get() == "duration":
            # 模式二：显示结束时间输入框，窗口变高
            self.end_time_frame.pack(pady=10, fill="x", before=self.btn_frame)
            self.geometry("400x420")
        else:
            # 模式一：隐藏结束时间输入框，窗口变矮
            self.end_time_frame.pack_forget()
            self.geometry("400x340")

    def parse_time(self, t_input):
        t_clean = t_input.strip().replace("：", ":").replace(" ", "")
        candidate = ""
        if ":" in t_clean:
            parts = t_clean.split(":")
            if len(parts) == 2: parts.append("00")
            if len(parts) == 3: candidate = f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:{parts[2].zfill(2)}"
        else:
            if len(t_clean) <= 2: candidate = f"{t_clean.zfill(2)}:00:00"
            elif len(t_clean) == 4: candidate = f"{t_clean[:2]}:{t_clean[2:]}:00"
            elif len(t_clean) == 6: candidate = f"{t_clean[:2]}:{t_clean[2:4]}:{t_clean[4:]}"
        
        if candidate:
            try:
                datetime.strptime(candidate, "%H:%M:%S")
                return candidate
            except ValueError:
                return None
        return None

    def show_alert(self, msg):
        top = ctk.CTkToplevel(self)
        top.geometry("250x150")
        top.attributes("-topmost", True)
        ctk.CTkLabel(top, text=msg, wraplength=220).pack(expand=True)
        ctk.CTkButton(top, text="OK", command=top.destroy).pack(pady=10)

    def on_confirm(self):
        start_t = self.parse_time(self.start_entry.get())
        if not start_t:
            self.show_alert("开始时间格式错误\n请输入如 1300 或 13:00")
            return
        
        mode = self.mode_var.get()
        end_t = ""
        
        if mode == "duration":
            end_t = self.parse_time(self.end_entry.get())
            if not end_t:
                self.show_alert("结束时间格式错误\n请输入如 1340 或 13:40")
                return
            if end_t <= start_t:
                self.show_alert("结束时间必须晚于开始时间")
                return
        else:
            # 模式一：强制清空结束时间，确保不保存脏数据
            end_t = ""

        self.callback({
            "time": start_t,
            "mode": mode,
            "end_time": end_t
        })
        self.destroy()

# ========================================================
# 4. 主程序逻辑
# ========================================================
class MusicSchedulerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("音乐定时播放系统 Pro")
        self.geometry("1000x750")
        
        if "--silent" in sys.argv:
            self.withdraw()
        else:
            self.deiconify()

        try:
            pygame.init()
            pygame.mixer.init()
            pygame.mixer.music.set_volume(1.0)
        except Exception as e:
            print(f"音频初始化警告: {e}")

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.tasks = []
        self.music_files = []
        
        # 播放状态变量
        self.playlist_queue = []      
        self.current_track_index = 0  
        self.is_playlist_active = False 
        self.current_task_name = "" 
        self.current_task_mode = "song" 
        self.current_task_end_time = "" 
        
        self.auto_start_var = ctk.BooleanVar(value=False)

        self.create_sidebar()
        self.create_main_area()
        self.create_footer()
        self.check_startup_status()
        self.load_music_files()
        self.load_tasks() # 加载已保存的任务
        
        self.setup_tray_icon()
        
        self.running = True
        self.timer_thread = threading.Thread(target=self.check_schedule_loop)
        self.timer_thread.daemon = True
        self.timer_thread.start()

        self.after(200, self.check_first_run)

    # --- 持久化存储 ---
    def save_tasks(self):
        try:
            with open(TASKS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.tasks, f, ensure_ascii=False, indent=2)
            self.status_label.configure(text="设置已保存", text_color="green")
        except Exception as e:
            self.status_label.configure(text=f"保存失败: {e}", text_color="red")

    def load_tasks(self):
        if os.path.exists(TASKS_FILE):
            try:
                with open(TASKS_FILE, 'r', encoding='utf-8') as f:
                    self.tasks = json.load(f)
                self.refresh_task_list()
            except Exception as e:
                print(f"读取任务失败: {e}")

    def show_error_alert(self, msg):
        err_win = ctk.CTkToplevel(self)
        err_win.title("操作提示")
        err_win.geometry("320x180")
        err_win.resizable(False, False)
        err_win.attributes("-topmost", True)
        err_win.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - 160
        y = self.winfo_y() + (self.winfo_height() // 2) - 90
        if x < 0: x = 100
        if y < 0: y = 100
        err_win.geometry(f"+{x}+{y}")
        
        err_win.grab_set()

        ctk.CTkLabel(err_win, text="⚠️", font=ctk.CTkFont(size=40)).pack(pady=(15, 0))
        ctk.CTkLabel(err_win, text=msg, font=ctk.CTkFont(size=14), text_color="#CC0000", 
                     wraplength=280, justify="center").pack(expand=True, padx=10, pady=5)
        
        ctk.CTkButton(err_win, text="确定", command=err_win.destroy, 
                      width=100, fg_color="#CC0000", hover_color="#990000").pack(pady=(0, 20))

    def create_tray_image(self):
        width = 64
        height = 64
        color_bg = "#1F6AA5" 
        color_fg = "white"   
        image = Image.new('RGB', (width, height), color_bg)
        d = ImageDraw.Draw(image)
        d.rectangle((16, 16, 48, 48), fill=color_fg)
        return image

    def setup_tray_icon(self):
        menu = (
            pystray.MenuItem('显示窗口', self.show_window_from_tray, default=True),
            pystray.MenuItem('退出程序', self.quit_app_from_tray)
        )
        icon_image = self.create_tray_image()
        self.tray_icon = pystray.Icon("MusicScheduler", icon_image, "定时播放器", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def show_window_from_tray(self, icon=None, item=None):
        self.after(0, self.deiconify)
        self.after(0, self.lift) 

    def quit_app_from_tray(self, icon=None, item=None):
        self.tray_icon.stop()
        self.on_real_close()

    def on_close(self):
        self.withdraw()

    def on_real_close(self):
        self.running = False
        try: pygame.mixer.quit()
        except: pass
        self.destroy()
        sys.exit()

    def create_sidebar(self):
        self.sidebar_frame = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(7, weight=1)

        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="定时播放器 Pro", font=ctk.CTkFont(size=20, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))

        self.sidebar_button_refresh = ctk.CTkButton(self.sidebar_frame, text="刷新音乐列表", command=self.load_music_files)
        self.sidebar_button_refresh.grid(row=1, column=0, padx=20, pady=10)
        
        self.help_btn = ctk.CTkButton(self.sidebar_frame, text="查看使用说明", fg_color="gray", 
                                      command=lambda: self.show_help_window(forced_countdown=False))
        self.help_btn.grid(row=2, column=0, padx=20, pady=5)

        self.label_settings = ctk.CTkLabel(self.sidebar_frame, text="系统设置", anchor="w")
        self.label_settings.grid(row=3, column=0, padx=20, pady=(10, 0))

        self.check_startup = ctk.CTkCheckBox(self.sidebar_frame, text="开机自启", variable=self.auto_start_var, command=self.toggle_startup)
        self.check_startup.grid(row=4, column=0, padx=20, pady=10, sticky="w")

        self.stop_btn = ctk.CTkButton(self.sidebar_frame, text="停止播放", fg_color="#FF4D4D", hover_color="#CC0000", text_color="white", command=self.stop_music)
        self.stop_btn.grid(row=7, column=0, padx=20, pady=20)

    def create_main_area(self):
        self.main_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, sticky="nsew")
        self.main_frame.grid_rowconfigure(1, weight=1)
        self.main_frame.grid_columnconfigure(0, weight=1)

        self.top_info_frame = ctk.CTkFrame(self.main_frame)
        self.top_info_frame.grid(row=0, column=0, padx=20, pady=20, sticky="ew")
        
        self.time_label = ctk.CTkLabel(self.top_info_frame, text="00:00:00", font=ctk.CTkFont(size=48, weight="bold"), text_color="#1F6AA5")
        self.time_label.pack(side="left", padx=20, pady=10)
        
        self.next_task_label = ctk.CTkLabel(self.top_info_frame, text="无任务", font=ctk.CTkFont(size=16), anchor="e")
        self.next_task_label.pack(side="right", padx=20, fill="x")

        self.tab_view = ctk.CTkTabview(self.main_frame)
        self.tab_view.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="nsew")
        
        self.tab_schedule = self.tab_view.add("任务列表")
        self.tab_music = self.tab_view.add("音乐库")
        self.setup_schedule_tab()
        self.setup_music_tab()

    def setup_schedule_tab(self):
        self.tab_schedule.grid_columnconfigure(0, weight=1)
        self.tab_schedule.grid_rowconfigure(1, weight=1) 

        input_frame = ctk.CTkFrame(self.tab_schedule)
        input_frame.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="ew")

        # 将原来的输入框逻辑替换为一个“创建新任务”的大按钮
        ctk.CTkLabel(input_frame, text="任务管理:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=20)
        
        create_task_btn = ctk.CTkButton(input_frame, text="+ 创建新任务", width=150, height=35,
                                        fg_color="#1F6AA5", font=ctk.CTkFont(weight="bold"),
                                        command=self.initiate_add_task_flow)
        create_task_btn.pack(side="left", padx=10, pady=15)
        
        ctk.CTkLabel(input_frame, text="支持: 固定歌曲播放 / 固定时长播放", text_color="gray", font=ctk.CTkFont(size=12)).pack(side="right", padx=20)

        self.schedule_scroll = ctk.CTkScrollableFrame(self.tab_schedule, label_text="已定任务 (自动保存)")
        self.schedule_scroll.grid(row=1, column=0, padx=10, pady=10, sticky="nsew")

    def setup_music_tab(self):
        self.tab_music.grid_columnconfigure(0, weight=1)
        self.music_list_scroll = ctk.CTkScrollableFrame(self.tab_music, label_text="发现的音频文件")
        self.music_list_scroll.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")

    def create_footer(self):
        self.footer_frame = ctk.CTkFrame(self, height=40, corner_radius=0)
        self.footer_frame.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.status_label = ctk.CTkLabel(self.footer_frame, text="就绪", text_color="gray")
        self.status_label.pack(side="left", padx=20)
        ctk.CTkLabel(self.footer_frame, text="v7.0 Pro", font=ctk.CTkFont(size=10)).pack(side="right", padx=20)
    
    def check_first_run(self):
        config_file = "config.json"
        if not os.path.exists(config_file):
            self.show_help_window(forced_countdown=True)
            try:
                with open(config_file, 'w') as f:
                    json.dump({"first_run": False}, f)
            except:
                pass

    def show_help_window(self, forced_countdown=False):
        help_win = ctk.CTkToplevel(self)
        help_win.title("使用说明")
        help_win.geometry("480x550") 
        help_win.attributes("-topmost", True)
        
        if forced_countdown:
            help_win.protocol("WM_DELETE_WINDOW", lambda: None)
        
        ctk.CTkLabel(help_win, text="使用说明", font=ctk.CTkFont(size=20, weight="bold"), text_color="#1F6AA5").pack(pady=(20, 10))
        
        scroll_help = ctk.CTkScrollableFrame(help_win, fg_color="transparent")
        scroll_help.pack(fill="both", expand=True, padx=20, pady=5)
        
        text_process = (
            "1. 【使用流程】\n"
            "   ① 将音频文件放入软件目录下的 mp3 或 changyong 文件夹\n"
            "   ② 点击左侧“刷新音乐列表”\n"
            "   ③ 输入时间，点击“开始创建任务”\n"
            "   ④ 按向导依次完成：选择歌曲 -> 设置星期 -> 设置任务名称\n\n"
            "2. 【修改任务】\n"
            "   在任务列表中点击“修改”按钮，可以重新设置该任务的\n"
            "   时间、歌曲、播放星期和名称。\n\n"
            "3. 【静默运行】\n"
            "   程序启动时会自动隐藏界面，仅在右下角托盘显示图标。\n"
            "   点击右上角关闭按钮，程序也会最小化到托盘继续运行。\n\n"
            "4. 【注意事项】\n"
            "   - 支持格式：mp3, flac, wav, ogg, m4a, wma, aac\n"
            "   - 时间示例：130000, 13:00:00, 13：00：00\n"
            "     (支持纯数字、英文冒号及中文冒号)\n"
            "   - 请务必禁用电脑自动休眠，以免影响播放"
        )
        ctk.CTkLabel(scroll_help, text=text_process, justify="left", font=ctk.CTkFont(size=14), anchor="w").pack(fill="x", pady=5)
        
        ctk.CTkLabel(scroll_help, text="   - 建议开启开机启动 (在左侧设置中勾选)", text_color="#FF4D4D", 
                     justify="left", font=ctk.CTkFont(size=14, weight="bold"), anchor="w").pack(fill="x")
        
        btn_text = "我知道了"
        btn_state = "normal"
        if forced_countdown:
            btn_text = "请阅读 (10s)"
            btn_state = "disabled"

        self.btn_know = ctk.CTkButton(help_win, text=btn_text, state=btn_state, command=help_win.destroy)
        self.btn_know.pack(pady=20)

        if forced_countdown:
            self.countdown_val = 10
            def update_countdown():
                self.countdown_val -= 1
                if self.countdown_val > 0:
                    self.btn_know.configure(text=f"请阅读 ({self.countdown_val}s)")
                    help_win.after(1000, update_countdown)
                else:
                    self.btn_know.configure(text="我知道了", state="normal")
                    help_win.protocol("WM_DELETE_WINDOW", help_win.destroy)
            help_win.after(1000, update_countdown)

    def show_startup_error(self, error_msg):
        err_win = ctk.CTkToplevel(self)
        err_win.title("设置失败")
        err_win.geometry("300x200")
        err_win.attributes("-topmost", True)
        ctk.CTkLabel(err_win, text="无法开启开机自启", font=ctk.CTkFont(size=16, weight="bold"), text_color="red").pack(pady=20)
        ctk.CTkLabel(err_win, text="可能被杀毒软件拦截\n请手动添加白名单", text_color="gray").pack()
        ctk.CTkButton(err_win, text="确定", width=80, command=err_win.destroy).pack(pady=20)

    def check_startup_status(self):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
            try:
                value, _ = winreg.QueryValueEx(key, "MusicSchedulerByStudent")
                expected_cmd = f'"{sys.executable}" --silent'
                if value == expected_cmd or value == sys.executable:
                    self.auto_start_var.set(True)
            except FileNotFoundError:
                self.auto_start_var.set(False)
            winreg.CloseKey(key)
        except Exception:
            self.auto_start_var.set(False)

    def toggle_startup(self):
        app_path = sys.executable 
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "MusicSchedulerByStudent"
        
        if not self.auto_start_var.get():
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
                winreg.DeleteValue(key, app_name)
                winreg.CloseKey(key)
                self.status_label.configure(text="已关闭开机自启", text_color="gray")
            except FileNotFoundError:
                pass
            except Exception as e:
                self.status_label.configure(text=f"关闭自启失败: {e}", text_color="red")
            return

        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
            cmd = f'"{app_path}" --silent'
            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
            self.status_label.configure(text="已开启开机自启", text_color="green")
                
        except Exception as e:
            self.auto_start_var.set(False)
            self.status_label.configure(text="设置自启失败", text_color="red")
            self.show_startup_error(str(e))

    def load_music_files(self):
        self.music_files = []
        directories = ["mp3", "changyong"]
        for widget in self.music_list_scroll.winfo_children():
            widget.destroy()
        found_files = []
        allowed_extensions = ('.mp3', '.flac', '.wav', '.ogg', '.m4a', '.wma', '.aac')
        
        for folder in directories:
            if not os.path.exists(folder):
                try: os.makedirs(folder)
                except: continue
            for file in os.listdir(folder):
                if file.lower().endswith(allowed_extensions):
                    full_path = os.path.abspath(os.path.join(folder, file))
                    found_files.append(full_path)
                    row_frame = ctk.CTkFrame(self.music_list_scroll)
                    row_frame.pack(fill="x", padx=5, pady=2)
                    ctk.CTkLabel(row_frame, text=f"[{folder}] {file}").pack(side="left", padx=10)
                    ctk.CTkButton(row_frame, text="播放", width=50, height=24, 
                                command=lambda p=full_path: self.play_single_file_manually(p)).pack(side="right", padx=5, pady=5)
        self.music_files = found_files
        self.status_label.configure(text=f"刷新成功，找到 {len(found_files)} 个音频文件", text_color="green")

    # --- 新的添加任务流程 ---
    def initiate_add_task_flow(self):
        if not self.music_files:
            self.show_error_alert("没有扫描到音频文件\n请先点击左侧【刷新音乐列表】")
            return
        # 步骤 0: 设置时间与模式
        TimeModeDialog(self, callback=self.step_1_songs)

    def step_1_songs(self, config_data):
        # config_data: {'time': '..', 'mode': '..', 'end_time': '..'}
        MultiSongSelectDialog(self, self.music_files, 
                              lambda files: self.step_2_weekdays(config_data, files))

    def step_2_weekdays(self, config_data, file_list):
        WeekdaySelectionDialog(self, config_data['time'], len(file_list),
                               lambda weekdays: self.step_3_rename(config_data, file_list, weekdays))

    def step_3_rename(self, config_data, file_list, weekdays):
        default_name = os.path.basename(file_list[0])
        if len(file_list) > 1:
            default_name += f" 等{len(file_list)}首"
        TaskRenameDialog(self, default_name,
                         lambda name: self.finalize_add_task(config_data, file_list, name, weekdays))

    def finalize_add_task(self, config, f_list, display_name, weekdays_indices):
        self.tasks.append({
            "time": config['time'], 
            "mode": config['mode'],
            "end_time": config['end_time'],
            "files": f_list, 
            "name": display_name,
            "weekdays": weekdays_indices 
        })
        self.save_tasks() # 保存
        self.refresh_task_list()
        self.status_label.configure(text=f"成功添加任务: {display_name}", text_color="green")

    # --- 新的修改任务流程 ---
    def start_modify_task(self, index):
        if not (0 <= index < len(self.tasks)): return
        
        task = self.tasks[index]
        self.status_label.configure(text=f"正在修改: {task['name']}", text_color="blue")
        
        # 步骤 0: 修改时间与模式
        initial_config = {
            "time": task['time'],
            "mode": task.get('mode', 'song'),
            "end_time": task.get('end_time', '')
        }
        
        TimeModeDialog(self, 
                       callback=lambda cfg: self.modify_step_1(index, cfg),
                       initial_data=initial_config)

    def modify_step_1(self, index, new_config):
        task = self.tasks[index]
        MultiSongSelectDialog(self, self.music_files,
                              lambda files: self.modify_step_2(index, new_config, files),
                              initial_selection=task['files'])

    def modify_step_2(self, index, new_config, new_files):
        task = self.tasks[index]
        WeekdaySelectionDialog(self, new_config['time'], len(new_files),
                               lambda weekdays: self.modify_step_3(index, new_config, new_files, weekdays),
                               initial_selection=task.get('weekdays', []))

    def modify_step_3(self, index, new_config, new_files, new_weekdays):
        task = self.tasks[index]
        TaskRenameDialog(self, task['name'],
                         lambda name: self.finalize_modify(index, new_config, new_files, new_weekdays, name))

    def finalize_modify(self, index, config, f_list, weekdays_indices, display_name):
        self.tasks[index]['time'] = config['time']
        self.tasks[index]['mode'] = config['mode']
        self.tasks[index]['end_time'] = config['end_time']
        self.tasks[index]['files'] = f_list
        self.tasks[index]['weekdays'] = weekdays_indices
        self.tasks[index]['name'] = display_name
        
        self.save_tasks() # 保存
        self.refresh_task_list()
        self.status_label.configure(text=f"任务修改成功: {display_name}", text_color="green")

    def refresh_task_list(self):
        for widget in self.schedule_scroll.winfo_children():
            widget.destroy()
        self.tasks.sort(key=lambda x: x["time"])
        
        week_map = ["一", "二", "三", "四", "五", "六", "日"]

        for idx, task in enumerate(self.tasks):
            f = ctk.CTkFrame(self.schedule_scroll)
            f.pack(fill="x", padx=5, pady=2)
            
            days_idx = task.get("weekdays", [])
            if len(days_idx) == 7: days_str = "每天"
            elif len(days_idx) == 0: days_str = "关"
            else:
                d_names = [week_map[i] for i in days_idx]
                days_str = f"周{','.join(d_names)}"
            
            # 显示时间段 (如果是模式二)
            mode = task.get("mode", "song")
            time_display = task["time"]
            if mode == "duration":
                time_display += f" - {task.get('end_time','?')}"
                mode_str = "[时长]"
            else:
                mode_str = "[曲目]"

            info_text = f"{time_display} {mode_str}\n{task['name']} ({len(task.get('files',[]))}首) {days_str}"
            
            lbl = ctk.CTkLabel(f, text=info_text, anchor="w", justify="left")
            lbl.pack(side="left", padx=10, pady=5)
            
            del_btn = ctk.CTkButton(f, text="删除", width=50, fg_color="#CC0000", hover_color="#AA0000", text_color="white",
                                  command=lambda i=idx: self.delete_task(i))
            del_btn.pack(side="right", padx=5, pady=5)
            
            edit_btn = ctk.CTkButton(f, text="修改", width=50, fg_color="#1F6AA5", hover_color="#144d7a",
                                   command=lambda i=idx: self.start_modify_task(i))
            edit_btn.pack(side="right", padx=5, pady=5)
        
        self.update_top_status()

    def delete_task(self, index):
        if 0 <= index < len(self.tasks):
            del self.tasks[index]
            self.save_tasks() # 保存
            self.refresh_task_list()
            self.update_top_status()

    def start_playlist(self, task):
        self.playlist_queue = task["files"]
        self.current_track_index = 0
        self.is_playlist_active = True
        self.current_task_name = task["name"]
        self.current_task_mode = task.get("mode", "song")
        self.current_task_end_time = task.get("end_time", "")
        
        self.play_next_in_queue()

    def play_next_in_queue(self):
        # 检查是否越界
        if self.current_track_index >= len(self.playlist_queue):
            # 列表播放结束
            if self.current_task_mode == "duration":
                # 模式二：循环播放
                self.current_track_index = 0
            else:
                # 模式一：结束
                self.stop_music()
                self.status_label.configure(text="任务播放完毕", text_color="green")
                return

        path = self.playlist_queue[self.current_track_index]
        success, msg = self.play_music_file(path)
        
        if success:
            self.current_track_index += 1
            self.update_top_status()
            self.status_label.configure(text=f"正在播放: {os.path.basename(path)}", text_color="#1F6AA5")
        else:
            # 播放失败跳过
            self.current_track_index += 1
            self.play_next_in_queue()

    def update_top_status(self):
        if self.is_playlist_active:
            mode_desc = "循环中" if self.current_task_mode == "duration" else "顺序播放"
            display_text = f"{self.current_task_name} - {mode_desc}"
            self.next_task_label.configure(text=display_text, text_color="#1F6AA5")
        else:
            if self.tasks:
                next_t = self.tasks[0]
                display_text = f"下次: {next_t['time']} {next_t['name']}"
                self.next_task_label.configure(text=display_text, text_color="gray")
            else:
                self.next_task_label.configure(text="下次播放: 无任务", text_color="gray")

    def play_single_file_manually(self, path):
        self.is_playlist_active = False 
        self.current_task_name = ""
        success, msg = self.play_music_file(path)
        if success:
            self.status_label.configure(text=f"手动播放: {os.path.basename(path)}", text_color="#1F6AA5")
            self.update_top_status()
        else:
            self.show_error_alert(f"播放失败: {msg}")

    def play_music_file(self, path):
        if not os.path.exists(path): return False, "文件不存在"
        try:
            if pygame.mixer.music.get_busy():
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
            file_obj = open(path, 'rb')
            pygame.mixer.music.load(file_obj)
            pygame.mixer.music.play()
            return True, ""
        except Exception as e:
            return False, str(e)

    def stop_music(self):
        try:
            self.is_playlist_active = False
            self.current_task_name = ""
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
            self.status_label.configure(text="播放已停止", text_color="gray")
            self.update_top_status()
        except:
            pass

    def check_schedule_loop(self):
        while self.running:
            now = datetime.now()
            current_time_str = now.strftime("%H:%M:%S")
            current_weekday = now.weekday()
            
            try: self.time_label.configure(text=current_time_str)
            except: pass
            
            # 1. 检查是否有任务需要开始
            for task in self.tasks:
                if task["time"] == current_time_str:
                    if current_weekday in task.get("weekdays", []):
                        self.start_playlist(task)
                        time.sleep(1.1) 
            
            # 2. 检查当前播放是否需要处理
            if self.is_playlist_active:
                # 模式二：检查结束时间，强制停止
                if self.current_task_mode == "duration" and self.current_task_end_time:
                    # 简单字符串比较 (在同一天内有效)
                    if current_time_str >= self.current_task_end_time:
                         self.stop_music()
                         self.status_label.configure(text="已达到设定结束时间", text_color="orange")

                # 如果还在播放状态，且音乐停止了（一首歌播完），切下一首
                if self.is_playlist_active and not pygame.mixer.music.get_busy():
                    self.play_next_in_queue()
            
            time.sleep(0.5)

if __name__ == "__main__":
    mutex_name = "Global_MusicScheduler_Instance_Lock"
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, mutex_name)
    last_error = kernel32.GetLastError()

    if last_error == 183: # ERROR_ALREADY_EXISTS
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, "音乐定时播放系统 Pro")
        if hwnd:
            user32.ShowWindow(hwnd, 9) 
            user32.SetForegroundWindow(hwnd)
        sys.exit(0)

    app = MusicSchedulerApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()