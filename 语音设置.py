"""桌宠语音设置；密钥通过环境变量提供，不写入设置文件。"""
import json
import ctypes
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
import sounddevice as sd
from 语音预设 import PRESETS, discover
from 语音链路 import VOICES, record, transcribe, synthesize, play, send
from 麦克风波纹 import MicrophoneMeter

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / '语音设置.json'

def _focus_existing_settings():
    """已有设置窗则前置并退出；用窗口标题匹配。"""
    user32 = ctypes.windll.user32
    found = ctypes.c_void_p()
    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_proc(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value == '桌宠语音设置':
            found.value = hwnd
            return False
        return True
    user32.EnumWindows(enum_proc, 0)
    if found.value:
        user32.ShowWindow(found.value, 9)  # SW_RESTORE
        user32.SetForegroundWindow(found.value)
        return True
    return False


def main():
    # 必须在创建任何窗口之前声明 DPI 感知，避免 Windows 拉伸位图。
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        ctypes.windll.user32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
        if not ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass
    # 单实例：端口锁；已开则聚焦旧窗并退出，避免多窗空转吃 CPU。
    import socket
    _settings_lock = socket.socket()
    try:
        _settings_lock.bind(('127.0.0.1', 19391))
    except OSError:
        try:
            _focus_existing_settings()
        except Exception:
            pass
        return
    config = json.loads(CONFIG.read_text(encoding='utf-8')) if CONFIG.exists() else {}
    app = tk.Tk()
    dpi = app.winfo_fpixels('1i')
    app.tk.call('tk', 'scaling', dpi / 72.0)
    ui_scale = max(1.0, dpi / 96.0)
    app.title('桌宠语音设置')
    app.configure(background='#F3F8FC')
    app.option_add('*Font', ('Microsoft YaHei UI', 10))
    app.option_add('*TCombobox*Listbox.background', '#FCFEFF')
    app.option_add('*TCombobox*Listbox.foreground', '#24394B')
    app.option_add('*TCombobox*Listbox.selectBackground', '#DCEFF8')
    app.option_add('*TCombobox*Listbox.selectForeground', '#24394B')
    style = ttk.Style(app)
    style.theme_use('clam')
    style.configure('.', font=('Microsoft YaHei UI', 10), background='#F3F8FC', foreground='#24394B')
    style.configure('TFrame', background='#F3F8FC')
    style.configure('TLabel', background='#F3F8FC', foreground='#24394B')
    style.configure('Title.TLabel', font=('Microsoft YaHei UI', 13, 'bold'), foreground='#245C76')
    style.configure('Subtitle.TLabel', foreground='#738A9A', font=('Microsoft YaHei UI', 9))
    style.configure('TLabelframe', background='#FCFEFF', bordercolor='#DCE9EF', relief='solid', borderwidth=1)
    style.configure('TLabelframe.Label', background='#FCFEFF', foreground='#245C76', font=('Microsoft YaHei UI', 11, 'bold'))
    style.configure('TButton', background='#EAF5FB', foreground='#245C76', bordercolor='#DCE9EF', lightcolor='#EAF5FB', darkcolor='#EAF5FB', padding=(10, 5), borderwidth=1)
    style.map('TButton', background=[('active', '#DCEFF8'), ('pressed', '#CBE5F1')], foreground=[('disabled', '#91A4B0')])
    style.configure('Accent.TButton', background='#328EAC', foreground='white', bordercolor='#328EAC')
    style.map('Accent.TButton', background=[('active', '#287C99'), ('pressed', '#226A83')], foreground=[('disabled', '#C5DCE5')])
    style.configure('TCombobox', fieldbackground='#FCFEFF', background='#EAF5FB', foreground='#24394B', arrowcolor='#328EAC', bordercolor='#DCE9EF', padding=4)
    style.map('TCombobox', fieldbackground=[('readonly', '#FCFEFF')], selectbackground=[('readonly', '#FCFEFF')], selectforeground=[('readonly', '#24394B')])
    style.configure('TEntry', fieldbackground='#FCFEFF', foreground='#24394B', bordercolor='#DCE9EF', padding=4)
    header = ttk.Frame(app, padding=(18, 12, 18, 4))
    header.pack(fill='x')
    ttk.Label(header, text='小鲸鱼 · 语音与对话', style='Title.TLabel').pack(anchor='w')
    app.geometry(f'{int(980 * ui_scale)}x{int(660 * ui_scale)}')
    app.minsize(int(940 * ui_scale), int(620 * ui_scale))
    body = ttk.Frame(app, padding=(10, 6, 10, 12))
    body.pack(fill='both', expand=True)
    left = ttk.Frame(body)
    left.pack(side='left', fill='both', expand=True, padx=(0, 10))
    right_column = ttk.Frame(body, width=int(440 * ui_scale))
    right_column.pack(side='right', fill='both')
    right_column.pack_propagate(False)
    right = ttk.LabelFrame(right_column, text='录音与识别 · 常驻', padding=8)
    right.pack(fill='x', pady=(0, 8))
    fields = {}
    profiles = dict(config.get('tts_profiles', {}))
    voice_refresh = [None]
    inline_commit = [None]
    devices = sd.query_devices()
    def row(key, title, choices=None):
        frame = ttk.Frame(left, padding=(6, 4))
        frame.pack(fill='x')
        ttk.Label(frame, text=title, width=13).pack(side='left')
        value = tk.StringVar(value=str(config.get(key, '')))
        widget = ttk.Combobox(frame, textvariable=value, values=choices, state='readonly') if choices is not None else ttk.Entry(frame, textvariable=value)
        widget.pack(side='left', fill='x', expand=True)
        fields[key] = value
        return widget
    for key, title, channel in [('microphone', '输入麦克风', 'max_input_channels'), ('speaker', '输出播放器', 'max_output_channels')]:
        choices = ['系统默认'] + [f'{i}: {d["name"]} [{sd.query_hostapis(d["hostapi"])["name"]}]' for i, d in enumerate(devices) if d[channel] > 0]
        device_box = row(key, title, choices)
        if fields[key].get() not in choices:
            fields[key].set('系统默认')
        if key == 'microphone':
            meter = MicrophoneMeter(app, device_box.master, fields[key], ui_scale)
            device_box.bind('<<ComboboxSelected>>', lambda event: meter.start())
            app.after(250, meter.start)
    interrupt_frame = ttk.LabelFrame(left, text='开口打断 · 音量门槛', padding=8)
    interrupt_frame.pack(fill='x', padx=6, pady=6)
    interrupt_db = tk.DoubleVar(value=max(-60., min(-10., float(config.get('interrupt_db', -30)))))
    interrupt_label = tk.StringVar(value=f'{interrupt_db.get():.0f} dBFS')
    threshold_row = ttk.Frame(interrupt_frame)
    threshold_row.pack(fill='x')
    ttk.Label(threshold_row, text='更容易打断', style='Subtitle.TLabel').pack(side='left')
    threshold_slider = ttk.Scale(threshold_row, from_=-60, to=-10, variable=interrupt_db)
    threshold_slider.pack(side='left', fill='x', expand=True, padx=8)
    ttk.Label(threshold_row, textvariable=interrupt_label, width=10).pack(side='right')
    ttk.Label(interrupt_frame, text='向右需要更大声。人声超过门槛约 0.1 秒才打断。\n麦克风波纹旁显示实时 dBFS；这是数字音量，非环境分贝。', style='Subtitle.TLabel', wraplength=int(460*ui_scale)).pack(anchor='w', pady=(5, 0))
    threshold_timer = [None]
    def persist_threshold():
        latest = json.loads(CONFIG.read_text(encoding='utf-8-sig')) if CONFIG.exists() else {}
        latest['interrupt_db'] = round(interrupt_db.get())
        temp = CONFIG.with_suffix('.threshold.tmp')
        temp.write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(CONFIG)
        threshold_timer[0] = None
    def threshold_changed(*args):
        interrupt_label.set(f'{interrupt_db.get():.0f} dBFS')
        if threshold_timer[0]:
            app.after_cancel(threshold_timer[0])
        threshold_timer[0] = app.after(250, persist_threshold)
    interrupt_db.trace_add('write', threshold_changed)
    sessions = {}
    bridge = {'base': ''}
    box = row('conversation', '绑定 DSH 对话', ['点击刷新查找对话'])
    status = tk.StringVar(value='DSH 地址与会话 ID 自动识别，无需填写。')
    ttk.Label(left, textvariable=status).pack()
    def refresh():
        import threading
        selected_id = sessions.get(fields['conversation'].get())
        try:
            latest_config = json.loads(CONFIG.read_text(encoding='utf-8-sig')) if CONFIG.exists() else {}
        except (OSError, ValueError):
            latest_config = {}
        selected_id = selected_id or latest_config.get('conversation_id')
        status.set('正在重新查找 DSH 与对话…')
        def worker():
            try:
                base, items = discover()
                def update():
                    bridge['base'] = base
                    sessions.clear()
                    for i, item in enumerate(items):
                        sessions[f'{i+1}. {item.get("title") or "未命名对话"}'] = item['id']
                    box['values'] = list(sessions)
                    matched = next((k for k,v in sessions.items() if v == selected_id), '')
                    fields['conversation'].set(matched or ('原绑定对话不在列表，请重新选择' if selected_id else '请选择要绑定的对话'))
                    if matched:
                        bind_conversation()
                    connection = '已连接 DSH' if base else '连接插件未加载'
                    status.set(f'{connection} · {len(sessions)} 个对话' + (' · 已保留绑定' if matched else ''))
                app.after(0, update)
            except Exception:
                app.after(0, lambda: status.set('未连上 DSH 会话桥；打开 DSH 后刷新。'))
        threading.Thread(target=worker, daemon=True).start()
    ttk.Button(left, text='刷新 DSH 对话', command=refresh).pack(pady=6)
    def bind_conversation(event=None):
        selected = fields['conversation'].get()
        if selected not in sessions:
            return
        try:
            latest = json.loads(CONFIG.read_text(encoding='utf-8-sig')) if CONFIG.exists() else {}
            latest.update(conversation=selected, conversation_id=sessions[selected], dsh_url=bridge['base'])
            temp = CONFIG.with_suffix('.binding.tmp')
            temp.write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(CONFIG)
            status.set('已绑定：' + selected)
        except (OSError, ValueError):
            status.set('绑定保存失败，请检查设置文件。')
    box.bind('<<ComboboxSelected>>', bind_conversation)
    for kind, title in [('asr', '语音识别 ASR'), ('tts', '语音合成 TTS')]:
        widget = row(kind+'_provider', title, list(PRESETS[kind]))
        if fields[kind+'_provider'].get() not in PRESETS[kind]:
            fields[kind+'_provider'].set(next(iter(PRESETS[kind])))
        hint = tk.StringVar()
        ttk.Label(left, textvariable=hint, style='Subtitle.TLabel', wraplength=int(480 * ui_scale)).pack(anchor='w', padx=6)
        def update(event=None, kind=kind, hint=hint):
            _, model = PRESETS[kind][fields[kind+'_provider'].get()]
            hint.set('默认模型：' + (model or '该服务商 ASR 尚未核实，暂不可用'))
            if kind == 'tts' and 'voice' in fields:
                update_voices()
        widget.bind('<<ComboboxSelected>>', update)
        update()
    voice_box = row('voice', 'TTS 音色', [])
    voice_box.master.pack_forget()
    def update_voices():
        values = VOICES[fields['tts_provider'].get()]
        voice_box['values'] = values
        saved_profile = profiles.get(fields['tts_provider'].get(), {})
        if saved_profile.get('voice'):
            fields['voice'].set(saved_profile['voice'])
        elif fields['voice'].get() not in values:
            fields['voice'].set(values[0])
        if voice_refresh[0]:
            voice_refresh[0]()
    update_voices()
    def open_voice_settings():
        from 音色设置 import open_dialog
        provider = fields['tts_provider'].get()
        for child in voice_mount.winfo_children():
            child.destroy()
        if provider not in ('阿里百炼', '小米 MiMo', 'MiniMax'):
            inline_commit[0] = None
            ttk.Label(voice_mount, text='当前使用：' + provider, wraplength=int(390*ui_scale)).pack(anchor='w', padx=8, pady=12)
            ttk.Label(voice_mount, text='使用上方文本框中的文字试听。', style='Subtitle.TLabel').pack(anchor='w', padx=8)
            ttk.Button(voice_mount, text='试听当前音色', command=lambda: test('tts')).pack(fill='x', padx=8, pady=10)
            return
        def apply_profile(option):
            profiles[provider] = option
            fields['voice'].set(option.get('voice', ''))
            current = json.loads(CONFIG.read_text(encoding='utf-8-sig')) if CONFIG.exists() else {}
            current['tts_profiles'] = profiles
            current.update(tts_provider=provider, voice=option.get('voice', ''), tts_model=option.get('model', ''))
            temp = CONFIG.with_suffix('.voice.tmp')
            temp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(CONFIG)
            result.set('音色与密钥已保存，自动对话也会使用。')
        inline_commit[0] = open_dialog(app, provider, profiles.get(provider, {}), api_key.get().strip(), apply_profile, lambda: fields['speaker'].get(), host=voice_mount)
    vision_frame = ttk.LabelFrame(left, text='屏幕视觉', padding=8)
    vision_frame.pack(fill='x', padx=6, pady=6)
    vision_enabled = tk.BooleanVar(value=bool(config.get('vision_enabled', False)))
    vision_interval = tk.StringVar(value=str(config.get('vision_interval_sec', 30)))
    vision_cooldown = tk.StringVar(value=str(config.get('vision_cooldown_sec', 8)))
    vision_model = tk.StringVar(value=str(config.get('vision_model', 'qwen3-vl-plus')))
    vision_hotkey = tk.StringVar(value=str(config.get('vision_hotkey', 'Ctrl+Alt+V') or 'Ctrl+Alt+V'))
    vision_gamepad = tk.StringVar(value=str(config.get('vision_gamepad_button', '无') or '无'))
    # 监视器 / 区域
    try:
        from 屏幕视觉 import list_monitors
        _monitors = list_monitors()
    except Exception:
        _monitors = [{'key': 'primary', 'label': '主显示器', 'primary': True}]
    _monitor_labels = [m.get('label') or m.get('key') for m in _monitors] + ['自定义区域']
    _label_to_key = {(m.get('label') or m.get('key')): (m.get('key') or 'primary') for m in _monitors}
    _key_to_label = {(m.get('key') or 'primary'): (m.get('label') or m.get('key')) for m in _monitors}
    _region = config.get('vision_region') if isinstance(config.get('vision_region'), dict) else {}
    _mode = str(config.get('vision_capture_mode') or 'monitor').strip().lower()
    _mon_key = str(config.get('vision_monitor') or 'primary').strip() or 'primary'
    if _mode == 'region':
        _src_default = '自定义区域'
    else:
        _src_default = _key_to_label.get(_mon_key) or _key_to_label.get('primary') or _monitor_labels[0]
    vision_source = tk.StringVar(value=_src_default)
    vision_region_l = tk.StringVar(value=str(_region.get('left', 0)))
    vision_region_t = tk.StringVar(value=str(_region.get('top', 0)))
    vision_region_w = tk.StringVar(value=str(_region.get('width', 1280)))
    vision_region_h = tk.StringVar(value=str(_region.get('height', 720)))
    vision_status = tk.StringVar(value='关闭时不截图；开启后只保留最新一帧。强制看屏：热键/手柄立刻截当前选区并开口。')
    ttk.Checkbutton(vision_frame, text='启用屏幕视觉（Latest-Frame → Flash 眼睛）', variable=vision_enabled).pack(anchor='w')
    vision_row = ttk.Frame(vision_frame)
    vision_row.pack(fill='x', pady=(6, 0))
    ttk.Label(vision_row, text='截图间隔(秒)').pack(side='left')
    vision_interval_box = ttk.Combobox(
        vision_row,
        textvariable=vision_interval,
        values=('0.5', '1', '2', '3', '5', '10', '30'),
        width=6,
    )
    vision_interval_box.pack(side='left', padx=(4, 12))
    ttk.Label(vision_row, text='开口冷却秒').pack(side='left')
    ttk.Entry(vision_row, textvariable=vision_cooldown, width=6).pack(side='left', padx=(4, 12))
    ttk.Label(vision_row, text='模型').pack(side='left')
    ttk.Entry(vision_row, textvariable=vision_model).pack(side='left', fill='x', expand=True, padx=(4, 0))
    vision_row2 = ttk.Frame(vision_frame)
    vision_row2.pack(fill='x', pady=(6, 0))
    ttk.Label(vision_row2, text='强制看屏热键').pack(side='left')
    ttk.Entry(vision_row2, textvariable=vision_hotkey, width=14).pack(side='left', padx=(4, 12))
    ttk.Label(vision_row2, text='手柄键').pack(side='left')
    ttk.Combobox(
        vision_row2,
        textvariable=vision_gamepad,
        values=('无', 'A', 'B', 'X', 'Y', 'LB', 'RB', 'START', 'BACK'),
        width=8,
    ).pack(side='left', padx=(4, 0))
    vision_row3 = ttk.Frame(vision_frame)
    vision_row3.pack(fill='x', pady=(6, 0))
    ttk.Label(vision_row3, text='截图范围').pack(side='left')
    vision_source_box = ttk.Combobox(
        vision_row3,
        textvariable=vision_source,
        values=tuple(_monitor_labels),
        state='readonly',
        width=28,
    )
    vision_source_box.pack(side='left', padx=(4, 0))
    region_row = ttk.Frame(vision_frame)
    ttk.Label(region_row, text='区域 L').pack(side='left')
    ttk.Entry(region_row, textvariable=vision_region_l, width=6).pack(side='left', padx=(2, 8))
    ttk.Label(region_row, text='T').pack(side='left')
    ttk.Entry(region_row, textvariable=vision_region_t, width=6).pack(side='left', padx=(2, 8))
    ttk.Label(region_row, text='W').pack(side='left')
    ttk.Entry(region_row, textvariable=vision_region_w, width=6).pack(side='left', padx=(2, 8))
    ttk.Label(region_row, text='H').pack(side='left')
    ttk.Entry(region_row, textvariable=vision_region_h, width=6).pack(side='left', padx=(2, 0))
    def _toggle_region_row(*_a):
        if vision_source.get() == '自定义区域':
            region_row.pack(fill='x', pady=(6, 0))
        else:
            region_row.pack_forget()
    vision_source.trace_add('write', _toggle_region_row)
    _toggle_region_row()
    ttk.Label(vision_frame, textvariable=vision_status, style='Subtitle.TLabel', wraplength=int(460 * ui_scale)).pack(anchor='w', pady=(6, 0))
    ttk.Label(
        vision_frame,
        text='热键默认 Ctrl+Alt+V；手柄按下边沿触发一次。范围对定时截图与强制看屏同时生效。密钥沿用云端 Key。',
        style='Subtitle.TLabel',
        wraplength=int(460 * ui_scale),
    ).pack(anchor='w', pady=(2, 0))
    after_ids = {'vision': None, 'listener': None, 'parent': None}
    def _cancel_afters():
        for key, aid in list(after_ids.items()):
            if aid is not None:
                try:
                    app.after_cancel(aid)
                except Exception:
                    pass
                after_ids[key] = None
    def poll_vision_status():
        try:
            if not app.winfo_exists():
                return
            sp = ROOT / 'screen_state.json'
            if not vision_enabled.get():
                vision_status.set('已关闭：不截图、不注入。')
                delay = 5000
            elif sp.exists():
                info = json.loads(sp.read_text(encoding='utf-8-sig'))
                if info.get('ok'):
                    vision_status.set('看到：' + str(info.get('summary') or '').replace('\n', ' ')[:80])
                else:
                    vision_status.set('视觉待命/失败：' + str(info.get('summary') or 'ok=false')[:60])
                # 开视觉时：失焦降到 5s，聚焦 2s
                try:
                    focused = bool(app.focus_displayof())
                except Exception:
                    focused = True
                delay = 2000 if focused else 5000
            else:
                vision_status.set('尚未产生 screen_state（请用右键菜单打开屏幕视觉）。')
                delay = 5000
        except (OSError, ValueError):
            vision_status.set('状态读取失败（已忽略）。')
            delay = 5000
        except tk.TclError:
            return
        after_ids['vision'] = app.after(delay, poll_vision_status)
    poll_vision_status()

    ttk.Label(left, text='右键开启监听后，识别文字会直接发送到绑定对话。', style='Subtitle.TLabel', wraplength=int(480 * ui_scale)).pack(anchor='w', padx=6, pady=(8, 3))
    ttk.Label(left, text='听完一句 → 发到 DSH → 等待回复 → 用选定音色播报。', style='Subtitle.TLabel', wraplength=int(480 * ui_scale)).pack(anchor='w', padx=6)
    ttk.Label(right, text='测试环境').pack(anchor='w')
    environment = tk.StringVar(value='本地')
    ttk.Combobox(right, textvariable=environment, values=['本地', '线上'], state='readonly').pack(fill='x', pady=6)
    api_key = tk.StringVar()
    ttk.Label(right, text='识别文字').pack(anchor='w', pady=(4, 0))
    test_text = tk.Text(right, height=3, wrap='word', background='#FCFEFF', foreground='#24394B', insertbackground='#328EAC', selectbackground='#DCEFF8', relief='flat', borderwidth=0, highlightthickness=1, highlightbackground='#DCE9EF', highlightcolor='#328EAC', padx=8, pady=6)
    test_text.insert('1.0', '你好，这是小鲸鱼的语音测试。')
    test_text.pack(fill='x', pady=6)
    result = tk.StringVar(value='选择左侧来源，点击测试。录音测试会采集 5 秒。')
    recognized_text = tk.StringVar(value='识别文字：等待你说话')
    buttons = []
    def test(action):
        import threading
        values = {k: v.get() for k, v in fields.items()}
        kind = 'asr' if action == 'asr' else 'tts'
        provider = values[kind+'_provider']
        online = provider in ('阿里百炼', '小米 MiMo', 'MiniMax') or provider.startswith('MiniMax')
        if action != 'send' and online != (environment.get() == '线上'):
            result.set('测试环境与左侧来源不一致，请选择对应的本地或线上来源。')
            return
        key = api_key.get().strip()
        text = test_text.get('1.0', 'end').strip()
        sid = sessions.get(values['conversation'])
        if action == 'tts' and not text:
            result.set('请先输入试听文字。')
            return
        if action == 'send' and (not text or not sid):
            result.set('请先选择对话并填写要发送的文字。')
            return
        for button in buttons:
            button.configure(state='disabled')
        meter.stop()
        result.set('正在录音，请说话（5 秒）…' if action == 'asr' else '正在测试…')
        def worker():
            try:
                if action == 'asr':
                    audio = record(values['microphone'])
                    app.after(0, lambda: result.set('录音结束，正在识别…'))
                    recognized = transcribe(provider, audio, key)
                    def show():
                        test_text.delete('1.0', 'end')
                        test_text.insert('1.0', recognized)
                        recognized_text.set('识别文字：' + recognized.replace('\n', ' '))
                        result.set('识别成功。可点击“发送到绑定对话”验证 DSH。')
                    app.after(0, show)
                elif action == 'tts':
                    audio = synthesize(provider, text, values['voice'], key, profiles.get(provider, {}))
                    play(audio, values['speaker'])
                    app.after(0, lambda: result.set('音频已合成并播放，请确认是否听到。'))
                else:
                    send(text, sid)
                    app.after(0, lambda: result.set('DSH 已接收这条消息。'))
            except Exception as error:
                message = '测试失败：' + str(error)
                if key:
                    message = message.replace(key, '***')
                app.after(0, lambda message=message: result.set(message))
            finally:
                def finished():
                    for b in buttons:
                        b.configure(state='normal')
                    meter.start()
                app.after(0, finished)
        threading.Thread(target=worker, daemon=True).start()
    record_actions = ttk.Frame(right)
    record_actions.pack(fill='x', pady=4)
    for label, action in [('录音 5 秒，测试识别', 'asr'), ('发送到绑定对话', 'send')]:
        button = ttk.Button(record_actions, text=label, command=lambda action=action: test(action))
        button.pack(side='left', fill='x', expand=True, padx=(0, 4))
        buttons.append(button)
    feedback = ttk.Frame(right, padding=(0, 8))
    feedback.pack(fill='x')
    ttk.Label(feedback, textvariable=result, style='Subtitle.TLabel', width=28, anchor='w').pack(fill='x')
    ttk.Label(feedback, textvariable=recognized_text, width=28, anchor='w').pack(fill='x', pady=(4, 0))


    last_listener_update = [0]
    def poll_listener():
        try:
            if not app.winfo_exists():
                return
            path = ROOT / '监听状态.json'
            if path.exists():
                info = json.loads(path.read_text(encoding='utf-8'))
                if info.get('time', 0) > last_listener_update[0]:
                    last_listener_update[0] = info.get('time', 0)
                    result.set(info.get('status', '').replace('\n', ' '))
                    if info.get('text'):
                        recognized_text.set('识别文字：' + info['text'].replace('\n', ' '))
                        test_text.delete('1.0', 'end')
                        test_text.insert('1.0', info['text'])
            try:
                focused = bool(app.focus_displayof())
            except Exception:
                focused = True
            delay = 1500 if focused else 5000
        except (OSError, ValueError):
            delay = 5000
        except tk.TclError:
            return
        after_ids['listener'] = app.after(delay, poll_listener)
    poll_listener()
    voice_mount = ttk.LabelFrame(right_column, text='音色设置与试听', padding=4)
    voice_mount.pack(fill='both', expand=True)
    voice_refresh[0] = open_voice_settings
    open_voice_settings()
    def save():
        if inline_commit[0] and inline_commit[0]() is False:
            return
        data = {key: value.get().strip() for key, value in fields.items()}
        if data['conversation'] not in sessions:
            messagebox.showerror('未绑定', '请刷新并选择一个 DSH 对话。')
            return
        if not PRESETS['asr'][data['asr_provider']][0]:
            messagebox.showerror('暂不可用', 'MiniMax ASR 尚未核实，请选择其他识别来源。')
            return
        data['conversation_id'] = sessions[data['conversation']]
        data['dsh_url'] = bridge['base']
        for kind in ('asr', 'tts'):
            data[kind+'_url'], data[kind+'_model'] = PRESETS[kind][data[kind+'_provider']]
        data['asr_key_file'] = r'C:\asr\api_key.txt'
        data['tts_profiles'] = profiles
        if api_key.get().strip():
            from 密钥存储 import save as save_key
            if data['tts_provider'] not in ('阿里百炼', '小米 MiMo', 'MiniMax'):
                messagebox.showerror('请选择服务商', '请先选择要保存此密钥的云端 TTS 服务商。')
                return
            save_key(data['tts_provider'], api_key.get())
        if profiles.get(data['tts_provider']):
            data['tts_model'] = profiles[data['tts_provider']]['model']
        latest = json.loads(CONFIG.read_text(encoding='utf-8-sig')) if CONFIG.exists() else {}
        data['vad_enabled'] = bool(latest.get('vad_enabled', False))
        data['interrupt_db'] = round(interrupt_db.get())
        data['vision_enabled'] = bool(vision_enabled.get())
        try:
            data['vision_interval_sec'] = max(0.4, float(vision_interval.get() or 30))
        except ValueError:
            data['vision_interval_sec'] = 30
        try:
            data['vision_cooldown_sec'] = max(1.0, float(vision_cooldown.get() or 8))
        except ValueError:
            data['vision_cooldown_sec'] = 8
        data['vision_model'] = (vision_model.get() or 'qwen3-vl-plus').strip()
        data['vision_provider'] = latest.get('vision_provider', '阿里百炼')
        data['vision_base_url'] = latest.get('vision_base_url', 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions')
        data['vision_hotkey'] = (vision_hotkey.get() or 'Ctrl+Alt+V').strip() or 'Ctrl+Alt+V'
        gp = (vision_gamepad.get() or '无').strip() or '无'
        data['vision_gamepad_button'] = '' if gp in ('无', 'NONE', 'none', 'off', '禁用') else gp.upper()
        src = vision_source.get() or '主显示器'
        if src == '自定义区域':
            data['vision_capture_mode'] = 'region'
            data['vision_monitor'] = latest.get('vision_monitor', 'primary') or 'primary'
            try:
                data['vision_region'] = {
                    'left': int(float(vision_region_l.get() or 0)),
                    'top': int(float(vision_region_t.get() or 0)),
                    'width': max(32, int(float(vision_region_w.get() or 1280))),
                    'height': max(32, int(float(vision_region_h.get() or 720))),
                }
            except ValueError:
                data['vision_region'] = {'left': 0, 'top': 0, 'width': 1280, 'height': 720}
        else:
            data['vision_capture_mode'] = 'monitor'
            data['vision_monitor'] = _label_to_key.get(src, 'primary')
            data['vision_region'] = None
        temporary = CONFIG.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(CONFIG)
        messagebox.showinfo('桌宠', '设置已保存。监听与屏幕视觉开关位于桌宠右键菜单。')
    ttk.Button(left, text='保存设置', style='Accent.TButton', command=save).pack(fill='x', padx=12, pady=12)
    refresh()
    def on_close():
        _cancel_afters()
        try:
            meter.stop()
        except Exception:
            pass
        try:
            meter.closed = True
            if getattr(meter, 'timer', None):
                app.after_cancel(meter.timer)
        except Exception:
            pass
        try:
            _settings_lock.close()
        except Exception:
            pass
        app.destroy()

    def _pet_shell_alive():
        """桌宠主窗是否还在（标题=桌宠）。"""
        user32 = ctypes.windll.user32
        found = ctypes.c_bool(False)
        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def enum_proc(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value == '桌宠':
                found.value = True
                return False
            return True
        user32.EnumWindows(enum_proc, 0)
        return bool(found.value)

    def poll_parent_alive():
        """主壳退出则关掉设置窗；失焦停麦指示，聚焦再开。"""
        try:
            if not app.winfo_exists():
                return
            if not _pet_shell_alive():
                on_close()
                return
            try:
                focused = bool(app.focus_displayof())
            except Exception:
                focused = False
            try:
                if focused:
                    if meter.stream is None and not meter.closed:
                        meter.start()
                else:
                    if meter.stream is not None:
                        meter.stop()
            except Exception:
                pass
            after_ids['parent'] = app.after(3000 if focused else 5000, poll_parent_alive)
        except tk.TclError:
            return
        except Exception:
            after_ids['parent'] = app.after(8000, poll_parent_alive)

    after_ids.setdefault('parent', None)
    poll_parent_alive()
    app.protocol('WM_DELETE_WINDOW', on_close)
    app.mainloop()

if __name__ == '__main__':
    main()
