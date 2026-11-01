"""实时麦克风音量指示，音频仅用于绘图，不保存或发送。"""
import math
import queue
import tkinter as tk
import numpy as np
import sounddevice as sd
import threading
from 音量标尺 import number, SpeechMinimum

class MicrophoneMeter:
    def __init__(self, app, parent, value, scale=1):
        self.app, self.value = app, value
        self.stream = None
        self.samples = queue.Queue(maxsize=6)
        self.raw = 0.
        self.minimum = SpeechMinimum()
        self.recommended = None
        self.analysis_error = False
        self.analysis_stop = threading.Event()
        self.analysis_thread = None
        self.level = 0.0
        self.closed = False
        self.active = True  # False=空闲/失焦：停流+停画，降 CPU
        self.timer = None
        self.scale = scale
        self.canvas = tk.Canvas(parent, width=int(126*scale), height=int(25*scale), bg='#F3F8FC', highlightthickness=0)
        self.canvas.pack(side='right', padx=(8, 0))
        self.canvas.bind('<Destroy>', self.destroy)
        self.draw()

    def callback(self, audio, frames, timing, status):
        level = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
        try:
            self.raw = level
            self.samples.put_nowait(audio.copy())
        except queue.Full:
            pass

    def set_active(self, active):
        """聚焦/操作时 True；失焦或久无操作时 False，彻底停麦克风流与 after 绘制。"""
        active = bool(active)
        if self.closed:
            return
        if active == self.active and (bool(self.stream) == active or not active):
            # 已是目标态；若要激活但流没了则继续往下拉起
            if not active or self.stream is not None:
                self.active = active
                return
        self.active = active
        if active:
            self.start()
            if not self.timer:
                self.draw()
        else:
            if self.timer:
                try:
                    self.app.after_cancel(self.timer)
                except Exception:
                    pass
                self.timer = None
            self.stop()
            try:
                self.canvas.delete('all')
                for i in range(12):
                    x, y = (3 + i * 6) * self.scale, 12.5 * self.scale
                    self.canvas.create_line(x, y - self.scale, x, y + self.scale, fill='#A6B7C2', width=max(2, 2 * self.scale), capstyle='round')
                self.canvas.create_text(100 * self.scale, 12.5 * self.scale, text='暂停', fill='#91A4B0', font=('Microsoft YaHei UI', 9))
            except Exception:
                pass

    def start(self):
        self.stop()
        if self.closed or not self.active:
            return
        value = self.value.get()
        index = None if value == '系统默认' else int(value.split(':', 1)[0])
        try:
            rate = int(sd.query_devices(index, 'input')['default_samplerate'])
            self.minimum = SpeechMinimum()
            self.recommended = None
            self.analysis_error = False
            self.analysis_stop = threading.Event()
            self.analysis_thread = threading.Thread(target=self.analyze, args=(rate, self.analysis_stop), daemon=True)
            self.analysis_thread.start()
            self.stream = sd.InputStream(device=index, channels=1, samplerate=rate, blocksize=int(rate*.05), callback=self.callback)
            self.stream.start()
            self.canvas.configure(cursor='')
        except Exception:
            self.stop()
            self.canvas.configure(cursor='question_arrow')

    def stop(self):
        self.analysis_stop.set()
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self.level = 0
        self.raw = 0
        if self.analysis_thread and self.analysis_thread.is_alive():
            self.analysis_thread.join(timeout=.5)
        while not self.samples.empty():
            self.samples.get_nowait()

    def draw(self):
        if self.closed or not self.active:
            self.timer = None
            return
        raw = self.raw
        # ponytail: RMS 只显示声音强弱；是否为人声仍由监听链路的 Silero 判断。
        target = max(0., min(1., (20*math.log10(max(raw, 1e-8))+55)/45))
        self.level = max(target, self.level*.78)
        self.canvas.delete('all')
        db = max(-96., 20*math.log10(max(raw, 1e-8)))
        self.canvas.create_text(100*self.scale, 12.5*self.scale, text=str(number(db)) if self.stream else '—', fill='#738A9A', font=('Microsoft YaHei UI', 9))
        for i in range(12):
            weight = .3 + .7*math.sin((i+1)*math.pi/13)
            height = (2 + 20*self.level*weight) * self.scale
            x, y = (3+i*6)*self.scale, 12.5*self.scale
            self.canvas.create_line(x, y-height/2, x, y+height/2, fill='#328EAC' if self.stream else '#A6B7C2', width=max(2, 2*self.scale), capstyle='round')
        # 有声 150ms；近静音 400ms，进一步降空闲绘制频率
        delay = 150 if self.level > 0.05 else 400
        self.timer = self.app.after(delay, self.draw)

    def analyze(self, rate, stop):
        try:
            import onnxruntime as ort
            from pathlib import Path
            from scipy.signal import resample_poly
            import json
            model = Path.home() / '.dsh/local-plugins/dsh-voice-loop/models/silero_vad.onnx'
            vad = ort.InferenceSession(str(model), providers=['CPUExecutionProvider'])
            h = np.zeros((2, 1, 64), np.float32)
            c = h.copy()
            pending = np.zeros(0, np.float32)
            divisor = math.gcd(rate, 16000)
            while not stop.is_set():
                try:
                    audio = self.samples.get(timeout=.2).reshape(-1)
                except queue.Empty:
                    continue
                pending = np.concatenate((pending, resample_poly(audio, 16000//divisor, rate//divisor)))
                while len(pending) >= 512:
                    block, pending = pending[:512], pending[512:]
                    probability, h, c = vad.run(None, {'input': block[None, :].astype(np.float32), 'sr': np.array(16000, np.int64), 'h': h, 'c': c})
                    speaking = float(probability[0][0]) >= .5
                    # 不将桌宠自身正在播报的声音用于推荐门槛。
                    try:
                        status = json.loads(Path(__file__).with_name('监听状态.json').read_text(encoding='utf-8'))
                        if status.get('status') == '正在播报回复':
                            speaking = False
                    except (OSError, ValueError):
                        pass
                    db = 20*math.log10(max(float(np.sqrt(np.mean(block**2))), 1e-8))
                    self.minimum.add(db, speaking)
                    self.recommended = self.minimum.recommendation
        except Exception:
            self.analysis_error = True

    def reset_recommendation(self):
        self.minimum = SpeechMinimum()
        self.recommended = None

    def destroy(self, event=None):
        if event is not None and event.widget is not self.canvas:
            return
        self.closed = True
        if self.timer:
            self.app.after_cancel(self.timer)
        self.stop()
