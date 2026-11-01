"""按服务商显示的音色设置对话框。"""
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from 云端音色 import MODELS, MODES, voices, create
from 密钥存储 import read as read_key, save as save_key

def open_dialog(parent, provider, saved, initial_key, on_save, speaker, host=None):
    if provider not in MODELS:
        messagebox.showinfo('本地音色', '当前本地服务使用已加载的派蒙音色；云端音色管理请先选择服务商。', parent=parent)
        return
    scale = parent.winfo_fpixels('1i') / 96
    if host is None:
        window = tk.Toplevel(parent)
        window.title(provider + ' · 音色设置')
        window.configure(bg='#F3F8FC')
        window.transient(parent)
        window.geometry(f'{int(620*scale)}x{int(620*scale)}')
        window.grab_set()
        panel = ttk.Frame(window, padding=16)
        panel.pack(fill='both', expand=True)
        action_host = panel
    else:
        window = parent
        footer = ttk.Frame(host, padding=(4, 6))
        footer.pack(side='bottom', fill='x')
        action_host = footer
        scroller = ttk.Frame(host)
        scroller.pack(fill='both', expand=True)
        canvas = tk.Canvas(scroller, bg='#F3F8FC', highlightthickness=0, height=int(200*scale))
        scrollbar = ttk.Scrollbar(scroller, orient='vertical', command=canvas.yview)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        panel = ttk.Frame(canvas, padding=(4, 4, 8, 4))
        item = canvas.create_window((0, 0), window=panel, anchor='nw')
        panel.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(item, width=event.width))
    ttk.Label(panel, text=provider + ' · 模型与音色', style='Subtitle.TLabel').pack(anchor='w', pady=(0, 6))
    values = {}
    widgets = {}
    defaults = {'model': MODELS[provider][0], 'mode': '系统音色', 'region': '北京', 'workspace': '', 'voice': 'Cherry' if provider == '阿里百炼' else ('冰糖' if provider == '小米 MiMo' else 'Chinese (Mandarin)_Sweet_Girl'), 'name': 'whalevoice', 'reference': '', 'instructions': '', 'preview': '你好，这是小鲸鱼的声音试听，希望今天能给你带来好心情。', 'speed': '1.2', 'volume': '1', 'pitch': '0', 'emotion': '自动'}
    frames = {}
    def row(key, title, choices=None, masked=False):
        frame = ttk.Frame(panel)
        frame.pack(fill='x', pady=4)
        frames[key] = frame
        ttk.Label(frame, text=title, width=13).pack(side='left')
        value = tk.StringVar(value=str(saved.get(key, defaults.get(key, ''))))
        values[key] = value
        widget = ttk.Combobox(frame, textvariable=value, values=choices, state='readonly') if choices else ttk.Entry(frame, textvariable=value, show='●' if masked else '')
        widget.pack(side='left', fill='x', expand=True)
        widgets[key] = widget
        return widget
    key_var = tk.StringVar(value=initial_key or read_key(provider))
    key_frame = ttk.Frame(panel)
    key_frame.pack(fill='x', pady=4)
    ttk.Label(key_frame, text='API Key', width=15).pack(side='left')
    ttk.Entry(key_frame, textvariable=key_var, show='●').pack(side='left', fill='x', expand=True)
    ttk.Label(panel, text='密钥由 Windows 加密，测试与自动对话共用。', style='Subtitle.TLabel', wraplength=int((390 if host is not None else 570)*scale)).pack(anchor='w')
    model_box = row('model', '合成模型', MODELS[provider])
    mode_box = row('mode', '音色来源', MODES if provider != '小米 MiMo' else [m for m in MODES if m != '已有音色 ID'])
    if provider == '阿里百炼':
        row('region', '服务地域', ['北京', '新加坡'])
        row('workspace', '业务空间 ID（可选）')
    voice_box = row('voice', '音色名称 / ID')
    name_box = row('name', '新音色名称 / ID')
    reference_box = row('reference', '参考音频')
    ttk.Button(frames['reference'], text='选择', command=lambda: values['reference'].set(filedialog.askopenfilename(parent=window, filetypes=[('参考音频', '*.wav *.mp3')]) or values['reference'].get())).pack(side='right', padx=(6, 0))
    row('instructions', '声音描述 / 风格')
    row('preview', '试听文字')
    if provider == '阿里百炼':
        row('speed', '语速', ['1', '1.2', '1.5'])
    if provider == 'MiniMax':
        row('speed', '语速', ['0.5', '0.8', '1', '1.2', '1.5', '2'])
        row('volume', '音量', ['0.5', '1', '1.5', '2'])
        row('pitch', '音调', ['-12', '-6', '-3', '0', '3', '6', '12'])
        row('emotion', '情绪', ['自动', 'happy', 'sad', 'angry', 'fearful', 'disgusted', 'surprised', 'neutral'])
    denoise = tk.BooleanVar(value=saved.get('denoise', False))
    normalize = tk.BooleanVar(value=saved.get('normalize', False))
    if provider == 'MiniMax':
        toggles = ttk.Frame(panel)
        toggles.pack(fill='x', pady=4)
        ttk.Checkbutton(toggles, text='克隆时降噪', variable=denoise).pack(side='left')
        ttk.Checkbutton(toggles, text='音量归一化', variable=normalize).pack(side='left', padx=12)
    note = tk.StringVar()
    ttk.Label(panel, textvariable=note, style='Subtitle.TLabel', wraplength=int((390 if host is not None else 570)*scale)).pack(fill='x', pady=8)
    def mode_changed(event=None):
        mode = values['mode'].get()
        frames['reference'].pack_forget()
        frames['name'].pack_forget()
        if mode == '参考音频克隆':
            frames['reference'].pack(fill='x', pady=4, before=frames['instructions'])
        if mode in ('参考音频克隆', '文字设计声音') and provider != '小米 MiMo':
            frames['name'].pack(fill='x', pady=4, before=frames['instructions'])
        if provider == '小米 MiMo':
            values['model'].set({'系统音色': 'mimo-v2.5-tts', '参考音频克隆': 'mimo-v2.5-tts-voiceclone', '文字设计声音': 'mimo-v2.5-tts-voicedesign'}[mode])
            note.set('克隆和设计在每次合成时使用参考音频或描述，无需创建音色号。')
        else:
            note.set('创建音色会上传所选录音或声音描述，并可能产生服务商费用。已有音色须匹配模型。')
        if provider == '阿里百炼' and mode in ('参考音频克隆', '文字设计声音') and values['model'].get() == 'qwen3-tts-flash':
            values['model'].set('qwen-audio-3.1-tts-flash')
    mode_box.bind('<<ComboboxSelected>>', mode_changed)
    mode_changed()
    status = tk.StringVar(value='选择设置后保存，或先生成一段试听。')
    ttk.Label(panel, textvariable=status, wraplength=int((390 if host is not None else 570)*scale)).pack(fill='x', pady=4)
    choices = tk.Listbox(panel, height=3, bg='#FCFEFF', fg='#24394B', relief='flat', exportselection=False)
    choices.pack(fill='x', pady=4)
    choices.bind('<<ListboxSelect>>', lambda event: values['voice'].set(choices.get(choices.curselection()[0])) if choices.curselection() else None)
    def options():
        result = {k: v.get().strip() for k, v in values.items()}
        result.update(denoise=denoise.get(), normalize=normalize.get())
        return result
    actions = []
    def run(action):
        option, key = options(), key_var.get().strip()
        output_device = speaker() if callable(speaker) else speaker
        for button in actions:
            button.configure(state='disabled')
        status.set('正在请求服务商…')
        def worker():
            try:
                if action == 'list':
                    result = voices(provider, option, key)
                    def show():
                        choices.delete(0, 'end')
                        for voice in result:
                            choices.insert('end', voice)
                        status.set(f'已获取 {len(result)} 个音色；点击可选择。')
                    window.after(0, show)
                elif action == 'create':
                    voice = create(provider, option, key)
                    def show_voice():
                        if voice:
                            values['voice'].set(voice)
                            values['mode'].set('已有音色 ID')
                            mode_changed()
                        status.set('音色准备完成，请试听后保存。')
                    window.after(0, show_voice)
                else:
                    from 语音链路 import synthesize, play
                    if not option.get('preview'):
                        raise ValueError('请填写试听文字')
                    audio = synthesize(provider, option['preview'], option['voice'], key, option)
                    play(audio, output_device)
                    window.after(0, lambda: status.set('试听音频播放完成。'))
            except Exception as error:
                message = str(error).replace(key, '***') if key else str(error)
                window.after(0, lambda message=message: status.set('失败：' + message))
            finally:
                window.after(0, lambda: [button.configure(state='normal') for button in actions])
        threading.Thread(target=worker, daemon=True).start()
    bar = ttk.Frame(action_host)
    bar.pack(fill='x', pady=8)
    for title, action in [('刷新音色', 'list'), ('创建 / 准备音色', 'create'), ('试听', 'test')]:
        button = ttk.Button(bar, text=title, command=lambda action=action: run(action))
        button.grid(row=0, column=len(actions), sticky='ew', padx=(0, 4))
        bar.columnconfigure(len(actions), weight=1)
        actions.append(button)
    def commit():
        try:
            option = options()
            if provider == '小米 MiMo' and option['mode'] == '参考音频克隆':
                from 云端音色 import reference
                reference(option['reference'])
            if option['mode'] in ('系统音色', '已有音色 ID') and not option.get('voice'):
                raise ValueError('请填写或选择音色 ID')
            if provider != '小米 MiMo' and option['mode'] in ('参考音频克隆', '文字设计声音'):
                raise ValueError('请先创建音色，再保存返回的音色 ID')
            save_key(provider, key_var.get())
            on_save(option)
            if host is None:
                window.destroy()
            else:
                status.set('已保存并使用，自动对话也会使用该音色。')
            return True
        except Exception as error:
            messagebox.showerror('无法保存', str(error), parent=window)
            return False
    ttk.Button(bar, text='保存并使用', style='Accent.TButton', command=commit).grid(row=1, column=0, columnspan=3, sticky='ew', pady=(6, 0))
    window.update_idletasks()
    # 内容高度由实际控件决定，避免高 DPI 下底部按钮被截断。
    if host is None:
        window.geometry(f'{int(620*scale)}x{min(window.winfo_screenheight()-100, panel.winfo_reqheight()+30)}')
    else:
        def wheel(event):
            canvas.yview_scroll(-int(event.delta/120), 'units')
            return 'break'
        def bind_wheel(widget):
            widget.bind('<MouseWheel>', wheel)
            for child in widget.winfo_children():
                bind_wheel(child)
        bind_wheel(scroller)
    return commit
