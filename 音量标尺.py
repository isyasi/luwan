"""界面统一使用 0–100 音量刻度，内部仍保留实际数字音量。"""
from collections import deque
import statistics

def number(db):
    return round(max(0., min(100., (float(db) + 60) * 2)))

def decibels(value):
    return max(0., min(100., float(value))) / 2 - 60

class SpeechMinimum:
    def __init__(self):
        self.groups = deque(maxlen=300)
        self.pending = []

    def add(self, db, speech):
        if not speech:
            self.pending.clear()
            return
        self.pending.append(float(db))
        if len(self.pending) == 4:
            # ponytail: 约 0.13 秒人声的中值，防止单帧噪点或字尾衰减把建议拖到静音。
            self.groups.append(statistics.median(self.pending))
            self.pending.clear()

    @property
    def recommendation(self):
        return min(self.groups) if len(self.groups) >= 4 else None
