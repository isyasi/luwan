"""屏幕视觉 v1：Latest-Frame 截图 → Flash 眼睛 → screen_state → ShouldSpeak。

旁路模块：失败全部自吞，不拖垮语音听环。可开关（语音设置.json vision_enabled）。
支持：全局热键 / 手柄边沿强制看屏开口；多显示器或自定义区域截图。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import socket
from 单实例 import try_acquire, MUTEX_SCREEN_VISION, PORT_SCREEN_VISION
import threading
import time
import traceback
from pathlib import Path

import ctypes
import ctypes.wintypes as wintypes
import requests
from PIL import Image, ImageGrab

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / '语音设置.json'
LISTEN_STATE = ROOT / '监听状态.json'
SCREEN_STATE = ROOT / 'screen_state.json'
VISION_LOCK_PORT = 19390
FRESH_SEC = 5.0

# 单槽 Latest Frame Only（禁止队列）
_latest = {'jpeg': None, 'fp': '', 'ts': 0.0}
_latest_lock = threading.Lock()
_speak_lock = threading.Lock()
_last_spoken = {'event_id': '', 'at': 0.0}
_last_force_at = 0.0
_stop = threading.Event()

# XInput 按键名 → bitmask
_XINPUT_BUTTONS = {
    'DPAD_UP': 0x0001,
    'DPAD_DOWN': 0x0002,
    'DPAD_LEFT': 0x0004,
    'DPAD_RIGHT': 0x0008,
    'START': 0x0010,
    'BACK': 0x0020,
    'LS': 0x0040,
    'RS': 0x0080,
    'LB': 0x0100,
    'RB': 0x0200,
    'A': 0x1000,
    'B': 0x2000,
    'X': 0x4000,
    'Y': 0x8000,
}


def _http():
    s = requests.Session()
    s.trust_env = False
    return s


def _load_config():
    try:
        if CONFIG.exists():
            return json.loads(CONFIG.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        pass
    return {}


def _atomic_write(path: Path, obj: dict):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def write_screen_state(**fields):
    payload = {
        'ok': False,
        'ts': time.time(),
        'hash': '',
        'summary': '',
        'detail': '',
        'event_id': '',
        'worthy': False,
    }
    payload.update(fields)
    try:
        _atomic_write(SCREEN_STATE, payload)
    except OSError:
        pass
    return payload


def fingerprint(jpeg: bytes) -> str:
    """粗粒度画面指纹：缩略灰度，抗光标闪烁部分抖动。"""
    try:
        img = Image.open(io.BytesIO(jpeg)).convert('L').resize((48, 27), Image.Resampling.BILINEAR)
        return hashlib.md5(img.tobytes()).hexdigest()
    except Exception:
        return hashlib.md5(jpeg).hexdigest()


# ---------- 显示器 / 区域 ----------

class _RECT(ctypes.Structure):
    _fields_ = [
        ('left', ctypes.c_long),
        ('top', ctypes.c_long),
        ('right', ctypes.c_long),
        ('bottom', ctypes.c_long),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ('cbSize', ctypes.c_ulong),
        ('rcMonitor', _RECT),
        ('rcWork', _RECT),
        ('dwFlags', ctypes.c_ulong),
    ]


def list_monitors() -> list[dict]:
    """返回显示器列表：index/primary/left/top/width/height/label。失败给主屏兜底。"""
    monitors: list[dict] = []
    try:
        user32 = ctypes.windll.user32
        MonitorEnumProc = ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_RECT), ctypes.c_void_p
        )

        def _callback(hmonitor, _hdc, _lprect, _lparam):
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
                r = info.rcMonitor
                primary = bool(info.dwFlags & 1)
                monitors.append({
                    'handle': int(hmonitor),
                    'primary': primary,
                    'left': int(r.left),
                    'top': int(r.top),
                    'width': int(r.right - r.left),
                    'height': int(r.bottom - r.top),
                })
            return 1

        user32.EnumDisplayMonitors(0, 0, MonitorEnumProc(_callback), 0)
    except Exception:
        monitors = []
    if not monitors:
        try:
            user32 = ctypes.windll.user32
            w = int(user32.GetSystemMetrics(0))
            h = int(user32.GetSystemMetrics(1))
        except Exception:
            w, h = 1920, 1080
        monitors = [{'handle': 0, 'primary': True, 'left': 0, 'top': 0, 'width': w, 'height': h}]
    # 主屏优先编号
    monitors.sort(key=lambda m: (not m.get('primary'), m.get('left', 0), m.get('top', 0)))
    for i, m in enumerate(monitors):
        m['index'] = i
        tag = '主显示器' if m.get('primary') else f'显示器 {i + 1}'
        m['label'] = f"{tag} ({m['width']}x{m['height']})"
        m['key'] = 'primary' if m.get('primary') else str(i)
    return monitors


def resolve_capture_bbox(cfg: dict | None = None):
    """根据配置返回 ImageGrab bbox=(l,t,r,b)；None 表示主屏默认 grab。"""
    cfg = cfg if cfg is not None else _load_config()
    region = cfg.get('vision_region')
    mode = (cfg.get('vision_capture_mode') or '').strip().lower()
    if mode == 'region' or (isinstance(region, dict) and mode != 'monitor'):
        if isinstance(region, dict):
            try:
                left = int(region.get('left', 0))
                top = int(region.get('top', 0))
                width = int(region.get('width', 0))
                height = int(region.get('height', 0))
                if width >= 32 and height >= 32:
                    return (left, top, left + width, top + height)
            except (TypeError, ValueError):
                pass
    monitors = list_monitors()
    want = str(cfg.get('vision_monitor') or 'primary').strip().lower()
    chosen = None
    if want in ('primary', 'main', ''):
        chosen = next((m for m in monitors if m.get('primary')), monitors[0])
    else:
        try:
            idx = int(want)
            for m in monitors:
                if m.get('index') == idx:
                    chosen = m
                    break
        except ValueError:
            for m in monitors:
                if m.get('key') == want or m.get('label') == want:
                    chosen = m
                    break
    if not chosen:
        chosen = next((m for m in monitors if m.get('primary')), monitors[0])
    # 仅一块屏且就是主屏原点时，交给默认 grab
    if chosen.get('primary') and chosen.get('left') == 0 and chosen.get('top') == 0 and len(monitors) == 1:
        return None
    return (
        int(chosen['left']),
        int(chosen['top']),
        int(chosen['left'] + chosen['width']),
        int(chosen['top'] + chosen['height']),
    )


def capture_jpeg(max_width=960, quality=40, bbox=None) -> bytes | None:
    try:
        if bbox:
            img = ImageGrab.grab(bbox=bbox, all_screens=True)
        else:
            img = ImageGrab.grab()
        if img.mode != 'RGB':
            img = img.convert('RGB')
        w, h = img.size
        if w > max_width:
            nh = max(1, int(h * max_width / w))
            img = img.resize((max_width, nh), Image.Resampling.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=quality, optimize=True)
        return buf.getvalue()
    except Exception:
        return None


def _vision_key(cfg: dict) -> tuple[str, str]:
    """返回 (provider, key)。优先设置里的 vision_provider，否则 DeepSeek → 阿里百炼。"""
    provider = (cfg.get('vision_provider') or '').strip()
    order = [provider] if provider else []
    for name in ('DeepSeek', '阿里百炼', 'deepseek'):
        if name not in order:
            order.append(name)
    from 密钥存储 import read
    for name in order:
        if not name:
            continue
        key = ''
        env_map = {
            'DeepSeek': 'DEEPSEEK_API_KEY',
            'deepseek': 'DEEPSEEK_API_KEY',
            '阿里百炼': 'DASHSCOPE_API_KEY',
        }
        env = env_map.get(name)
        if env:
            key = os.environ.get(env, '')
        if not key:
            try:
                key = read(name) or ''
            except Exception:
                key = ''
        if key:
            return name, key
    return provider or '阿里百炼', ''


def _vision_endpoint(cfg: dict, provider: str) -> tuple[str, str]:
    base = (cfg.get('vision_base_url') or '').strip()
    model = (cfg.get('vision_model') or '').strip()
    if not base:
        if provider in ('DeepSeek', 'deepseek'):
            base = 'https://api.deepseek.com/chat/completions'
        else:
            base = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
    if not model:
        if provider in ('DeepSeek', 'deepseek'):
            model = 'deepseek-chat'
        else:
            model = 'qwen3-vl-plus'
    return base, model


_EYES_PROMPT = (
    '你是桌宠的眼睛，只描述当前屏幕，不要聊天、不要给建议。'
    '用简体中文，严格输出一行 JSON（不要 markdown）：'
    '{"scene":"场景","focus":"焦点窗口或区域","text_points":["可见文字要点最多3条"],'
    '"change":"none|window|text|media|dialog|other","worthy":true或false,'
    '"summary":"不超过40字的中文摘要"}。'
    'worthy=true 仅当变化值得桌宠主动开口（新弹窗、明显新内容、错误提示等）；'
    '时钟秒数、光标闪烁、视频帧抖动不要 worthy。'
)

_FORCE_EYES_PROMPT = (
    '你是桌宠的眼睛。主人刚刚按下「看一眼」热键，请认真描述当前屏幕。'
    '用简体中文，严格输出一行 JSON（不要 markdown）：'
    '{"scene":"场景","focus":"焦点窗口或区域","text_points":["可见文字要点最多3条"],'
    '"change":"other","worthy":true,'
    '"summary":"不超过40字的中文摘要，要让桌宠能据此开口"}。'
    'worthy 必须为 true。'
)


def call_eyes(jpeg: bytes, cfg: dict, force: bool = False) -> dict:
    provider, key = _vision_key(cfg)
    if not key:
        return {'ok': False, 'error': 'no_vision_key', 'summary': '未配置视觉密钥'}
    url, model = _vision_endpoint(cfg, provider)
    encoded = 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode('ascii')
    prompt = _FORCE_EYES_PROMPT if force else _EYES_PROMPT
    payload = {
        'model': model,
        'messages': [{
            'role': 'user',
            'content': [
                {'type': 'image_url', 'image_url': {'url': encoded}},
                {'type': 'text', 'text': prompt},
            ],
        }],
        'stream': False,
        'temperature': 0.2,
        'max_tokens': 280,
    }
    headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
    with _http() as s:
        r = s.post(url, headers=headers, json=payload, timeout=(6, 45))
    if not r.ok:
        return {'ok': False, 'error': f'http_{r.status_code}', 'summary': '视觉接口暂时不可用'}
    data = r.json()
    content = ''
    try:
        content = data['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError):
        return {'ok': False, 'error': 'bad_response', 'summary': '视觉返回无法解析'}
    if isinstance(content, list):
        content = ''.join(
            part.get('text', '') if isinstance(part, dict) else str(part) for part in content
        )
    text = (content or '').strip()
    if text.startswith('```'):
        text = text.strip('`')
        if text.startswith('json'):
            text = text[4:].strip()
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        return {'ok': False, 'error': 'no_json', 'summary': '眼睛未输出结构化结果', 'raw': text[:200]}
    try:
        parsed = json.loads(text[start:end + 1])
    except ValueError:
        return {'ok': False, 'error': 'json_parse', 'summary': '眼睛 JSON 解析失败'}
    summary = str(parsed.get('summary') or parsed.get('scene') or '').strip()[:80]
    focus = str(parsed.get('focus') or '').strip()[:60]
    points = parsed.get('text_points') or []
    if not isinstance(points, list):
        points = [str(points)]
    points = [str(p).strip() for p in points if str(p).strip()][:3]
    change = str(parsed.get('change') or 'other').strip()[:32]
    worthy = True if force else bool(parsed.get('worthy'))
    detail = ' | '.join(x for x in [focus, '；'.join(points)] if x)[:200]
    return {
        'ok': True,
        'summary': summary or (focus or '屏幕有内容'),
        'detail': detail,
        'scene': str(parsed.get('scene') or '')[:60],
        'focus': focus,
        'text_points': points,
        'change': change,
        'worthy': worthy,
        'provider': provider,
        'model': model,
    }


def is_dialogue_busy() -> bool:
    """与用户对话冲突时跳过主动开口。"""
    markers = (
        '正在识别', '正在发送', '等待回复', '正在合成', '正在播报',
        '人声音量', '听到你说话，等待', '已打断',
    )
    try:
        if not LISTEN_STATE.exists():
            return False
        info = json.loads(LISTEN_STATE.read_text(encoding='utf-8-sig'))
        status = str(info.get('status') or '')
        if any(m in status for m in markers):
            return True
        ts = float(info.get('time') or 0)
        if ts and (time.time() - ts) > 120:
            return False
    except (OSError, ValueError, TypeError):
        return False
    return False


def should_speak(state: dict, cfg: dict) -> bool:
    if not state.get('ok'):
        return False
    if not state.get('worthy'):
        return False
    event_id = str(state.get('event_id') or '')
    if not event_id:
        return False
    cooldown = float(cfg.get('vision_cooldown_sec', 8) or 8)
    now = time.time()
    if event_id == _last_spoken.get('event_id'):
        return False
    if now - float(_last_spoken.get('at') or 0) < cooldown:
        return False
    if is_dialogue_busy():
        return False
    return True


def proactive_speak(state: dict, cfg: dict, force: bool = False) -> bool:
    """走现有 send → reply → TTS；全程异常自吞。force 绕过 worthy/冷却/去重。"""
    if not _speak_lock.acquire(blocking=False):
        return False
    try:
        if is_dialogue_busy():
            return False
        sid = cfg.get('conversation_id') or ''
        if not sid:
            return False
        from 语音链路 import send, stream_reply, synthesize, play, reply_snapshot, state as set_listen_state, VOICES
        if force:
            nudge = (
                '【视觉强制】主人刚按了看屏热键/手柄，请根据当前屏幕用一两句口语直接开口回应，'
                '不要只回句号，不要说「我看不见」。'
            )
        else:
            nudge = (
                '【视觉事件】我刚扫了一眼屏幕。'
                '若值得跟主人开口，请用一两句口语简短说；不值得就只回复一个句号。'
            )
        previous, _ = reply_snapshot(sid)
        set_listen_state(
            '视觉强制开口：发送中' if force else '视觉主动开口：发送中',
            text=state.get('summary') or '',
        )
        send(nudge, sid)
        voice = cfg.get('voice') or VOICES.get(cfg.get('tts_provider', ''), ['Cherry'])[0]
        speaker = cfg.get('speaker')
        spoken_len = 0
        for answer, finished in stream_reply(sid, previous):
            if (not force) and is_dialogue_busy() and spoken_len == 0:
                break
            tail = (answer or '')[spoken_len:]
            if tail.strip() and tail.strip() not in ('。', '.', '…', '...'):
                set_listen_state(
                    '视觉强制开口：播报中' if force else '视觉主动开口：播报中',
                    text=state.get('summary') or '',
                )
                audio = synthesize(cfg.get('tts_provider') or '阿里百炼', tail, voice)
                if not play(audio, speaker):
                    break
                spoken_len = len(answer)
            if finished:
                break
        set_listen_state('正在听你说话' if cfg.get('vad_enabled') else '视觉待命')
        _last_spoken['event_id'] = str(state.get('event_id') or '')
        _last_spoken['at'] = time.time()
        return spoken_len > 0
    except Exception:
        try:
            from 语音链路 import state as set_listen_state
            set_listen_state('视觉主动开口失败（已忽略）')
        except Exception:
            pass
        return False
    finally:
        _speak_lock.release()


def force_look_and_speak() -> bool:
    """热键/手柄：立刻截当前选中区域 → 眼睛 → 必须主动开口（绕过该次 worthy/冷却/去重）。"""
    global _last_force_at
    try:
        now = time.time()
        if now - _last_force_at < 0.8:
            return False
        _last_force_at = now
        cfg = _load_config()
        if not cfg.get('vision_enabled'):
            return False
        bbox = resolve_capture_bbox(cfg)
        jpeg = capture_jpeg(bbox=bbox)
        if not jpeg:
            write_screen_state(ok=False, summary='强制看屏：截图失败', detail='capture')
            return False
        fp = fingerprint(jpeg)
        with _latest_lock:
            _latest['jpeg'] = jpeg
            _latest['fp'] = fp
            _latest['ts'] = time.time()
        eyes = call_eyes(jpeg, cfg, force=True)
        if not eyes.get('ok'):
            write_screen_state(
                ok=False,
                hash=fp,
                summary=eyes.get('summary') or '强制看屏：眼睛失败',
                detail=str(eyes.get('error') or ''),
                event_id='',
                worthy=False,
            )
            return False
        event_id = f"force-{int(time.time() * 1000)}"
        state = write_screen_state(
            ok=True,
            ts=time.time(),
            hash=fp,
            summary=eyes.get('summary') or '',
            detail=eyes.get('detail') or '',
            event_id=event_id,
            worthy=True,
            force=True,
            scene=eyes.get('scene') or '',
            focus=eyes.get('focus') or '',
            change=eyes.get('change') or 'other',
            model=eyes.get('model') or '',
        )
        # 强制路径：不经 should_speak
        ok = proactive_speak(state, cfg, force=True)
        try:
            state['spoke'] = bool(ok)
            _atomic_write(SCREEN_STATE, state)
        except OSError:
            pass
        return ok
    except Exception:
        try:
            write_screen_state(ok=False, summary='强制看屏异常', detail='swallowed')
        except Exception:
            pass
        return False


# ---------- 热键 / 手柄 ----------

_VK_NAME = {
    'ctrl': 0x11, 'control': 0x11,
    'alt': 0x12, 'menu': 0x12,
    'shift': 0x10,
    'win': 0x5B, 'super': 0x5B,
}


def parse_hotkey(spec: str) -> dict:
    """'Ctrl+Alt+V' → {'mods':[0x11,0x12], 'vk':0x56}。"""
    parts = [p.strip() for p in (spec or '').replace('-', '+').split('+') if p.strip()]
    mods = []
    vk = None
    for p in parts:
        low = p.lower()
        if low in _VK_NAME:
            mods.append(_VK_NAME[low])
            continue
        if len(p) == 1:
            ch = p.upper()
            if 'A' <= ch <= 'Z':
                vk = ord(ch)
            elif '0' <= ch <= '9':
                vk = ord(ch)
            continue
        if low.startswith('f') and low[1:].isdigit():
            n = int(low[1:])
            if 1 <= n <= 24:
                vk = 0x70 + n - 1
            continue
        # 常见别名
        aliases = {'space': 0x20, 'tab': 0x09, 'esc': 0x1B, 'escape': 0x1B, 'enter': 0x0D, 'return': 0x0D}
        if low in aliases:
            vk = aliases[low]
    if vk is None:
        vk = ord('V')
        mods = [_VK_NAME['ctrl'], _VK_NAME['alt']]
    return {'mods': mods, 'vk': vk}


def hotkey_is_down(parsed: dict) -> bool:
    user32 = ctypes.windll.user32
    if not parsed or not parsed.get('vk'):
        return False
    # 高位表示按下
    if not (user32.GetAsyncKeyState(int(parsed['vk'])) & 0x8000):
        return False
    for mod in parsed.get('mods') or []:
        if not (user32.GetAsyncKeyState(int(mod)) & 0x8000):
            return False
    return True


def _load_xinput():
    for name in ('xinput1_4.dll', 'xinput1_3.dll', 'xinput9_1_0.dll'):
        try:
            return ctypes.WinDLL(name)
        except OSError:
            continue
    return None


class _XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ('wButtons', ctypes.c_ushort),
        ('bLeftTrigger', ctypes.c_ubyte),
        ('bRightTrigger', ctypes.c_ubyte),
        ('sThumbLX', ctypes.c_short),
        ('sThumbLY', ctypes.c_short),
        ('sThumbRX', ctypes.c_short),
        ('sThumbRY', ctypes.c_short),
    ]


class _XINPUT_STATE(ctypes.Structure):
    _fields_ = [
        ('dwPacketNumber', ctypes.c_ulong),
        ('Gamepad', _XINPUT_GAMEPAD),
    ]


_xinput = None
_xinput_tried = False


def gamepad_buttons_mask() -> int:
    """任意已连接手柄的按钮掩码按位或；失败返回 0。"""
    global _xinput, _xinput_tried
    if not _xinput_tried:
        _xinput_tried = True
        _xinput = _load_xinput()
    if not _xinput:
        return 0
    mask = 0
    state = _XINPUT_STATE()
    for i in range(4):
        try:
            if _xinput.XInputGetState(i, ctypes.byref(state)) == 0:
                mask |= int(state.Gamepad.wButtons)
        except Exception:
            continue
    return mask


def gamepad_button_down(name: str) -> bool:
    key = (name or '').strip().upper()
    if not key or key in ('NONE', 'OFF', '无', '禁用'):
        return False
    bit = _XINPUT_BUTTONS.get(key)
    if not bit:
        return False
    return bool(gamepad_buttons_mask() & bit)


def _input_loop():
    """全局热键 + 手柄边沿触发；视觉关闭时停轮询。"""
    prev_hk = False
    prev_pad = False
    parsed = parse_hotkey('Ctrl+Alt+V')
    last_spec = ''
    while not _stop.is_set():
        try:
            cfg = _load_config()
            if not cfg.get('vision_enabled'):
                prev_hk = False
                prev_pad = False
                _stop.wait(1.0)
                continue
            spec = str(cfg.get('vision_hotkey') or 'Ctrl+Alt+V').strip() or 'Ctrl+Alt+V'
            if spec != last_spec:
                parsed = parse_hotkey(spec)
                last_spec = spec
                prev_hk = False
            down = hotkey_is_down(parsed)
            if down and not prev_hk:
                threading.Thread(target=force_look_and_speak, daemon=True).start()
            prev_hk = down

            btn = str(cfg.get('vision_gamepad_button') or '').strip()
            if btn and btn.upper() not in ('NONE', 'OFF', '无', '禁用', ''):
                pad = gamepad_button_down(btn)
                if pad and not prev_pad:
                    threading.Thread(target=force_look_and_speak, daemon=True).start()
                prev_pad = pad
            else:
                prev_pad = False
            _stop.wait(0.07)
        except Exception:
            _stop.wait(1.0)


def _capture_loop(interval: float):
    while not _stop.is_set():
        cfg = _load_config()
        if not cfg.get('vision_enabled'):
            _stop.wait(2.0)
            continue
        interval = max(0.4, float(cfg.get('vision_interval_sec', 30) or 30))
        bbox = resolve_capture_bbox(cfg)
        jpeg = capture_jpeg(bbox=bbox)
        if jpeg:
            fp = fingerprint(jpeg)
            with _latest_lock:
                _latest['jpeg'] = jpeg
                _latest['fp'] = fp
                _latest['ts'] = time.time()
        _stop.wait(interval)


def _analyze_loop():
    last_fp = ''
    last_ok_state = None
    last_eyes_at = 0.0
    while not _stop.is_set():
        try:
            cfg = _load_config()
            if not cfg.get('vision_enabled'):
                write_screen_state(ok=False, summary='视觉已关闭', detail='vision_enabled=false')
                last_fp = ''
                _stop.wait(2.0)
                continue
            interval = max(0.4, float(cfg.get('vision_interval_sec', 30) or 30))
            with _latest_lock:
                jpeg = _latest.get('jpeg')
                fp = _latest.get('fp') or ''
                cap_ts = float(_latest.get('ts') or 0)
            if not jpeg or not fp:
                _stop.wait(0.3)
                continue
            if fp == last_fp and last_ok_state:
                refreshed = dict(last_ok_state)
                refreshed['ts'] = time.time()
                write_screen_state(**{k: refreshed[k] for k in (
                    'ok', 'ts', 'hash', 'summary', 'detail', 'event_id', 'worthy'
                )})
                _stop.wait(min(2.0, max(0.8, interval * 0.25)))
                continue
            now = time.time()
            if now - last_eyes_at < interval:
                _stop.wait(0.4)
                continue
            last_eyes_at = now
            eyes = call_eyes(jpeg, cfg)
            if not eyes.get('ok'):
                write_screen_state(
                    ok=False,
                    hash=fp,
                    summary=eyes.get('summary') or '视觉失败',
                    detail=str(eyes.get('error') or ''),
                    event_id='',
                    worthy=False,
                )
                last_fp = fp
                _stop.wait(1.0)
                continue
            event_id = hashlib.md5(
                (fp + '|' + eyes.get('summary', '') + '|' + eyes.get('change', '')).encode('utf-8')
            ).hexdigest()[:16]
            state = write_screen_state(
                ok=True,
                ts=time.time(),
                hash=fp,
                summary=eyes.get('summary') or '',
                detail=eyes.get('detail') or '',
                event_id=event_id,
                worthy=bool(eyes.get('worthy')),
            )
            state.update({
                'scene': eyes.get('scene') or '',
                'focus': eyes.get('focus') or '',
                'change': eyes.get('change') or '',
                'captured_at': cap_ts,
                'model': eyes.get('model') or '',
            })
            try:
                _atomic_write(SCREEN_STATE, state)
            except OSError:
                pass
            last_fp = fp
            last_ok_state = state
            if should_speak(state, cfg):
                threading.Thread(target=proactive_speak, args=(state, cfg), daemon=True).start()
        except Exception:
            try:
                write_screen_state(ok=False, summary='视觉内部异常', detail='swallowed')
            except Exception:
                pass
            _stop.wait(1.0)
            continue
        _stop.wait(0.5)


def run():
    # 单实例：Mutex Local\XiaoJingYu_ScreenVision + 端口 19390
    lock = try_acquire(MUTEX_SCREEN_VISION, PORT_SCREEN_VISION)
    if lock is None:
        return False
    cfg = _load_config()
    if not cfg.get('vision_enabled'):
        write_screen_state(ok=False, summary='视觉已关闭', detail='vision_enabled=false')
    try:
        threading.Thread(target=_capture_loop, args=(float(cfg.get('vision_interval_sec', 30) or 30),), daemon=True).start()
        threading.Thread(target=_analyze_loop, daemon=True).start()
        threading.Thread(target=_input_loop, daemon=True).start()
        while not _stop.is_set():
            cfg = _load_config()
            if not cfg.get('vision_enabled'):
                with _latest_lock:
                    _latest['jpeg'] = None
                    _latest['fp'] = ''
                time.sleep(2.0)
                continue
            time.sleep(1.0)
    except Exception:
        write_screen_state(ok=False, summary='视觉进程退出', detail='fatal_swallowed')
    finally:
        _stop.set()
        try:
            lock.release()
        except Exception:
            pass
    return True


def format_context_block(max_age=FRESH_SEC) -> str:
    """供语音链路兜底注入；bridge 优先。失败返回空串。"""
    try:
        if not SCREEN_STATE.exists():
            return ''
        data = json.loads(SCREEN_STATE.read_text(encoding='utf-8-sig'))
        if not data.get('ok'):
            return ''
        ts = float(data.get('ts') or 0)
        if ts and (time.time() - ts) > max_age:
            return ''
        summary = str(data.get('summary') or '').strip()
        detail = str(data.get('detail') or '').strip()
        if not summary:
            return ''
        body = summary if not detail else f'{summary}\n{detail}'
        return '[CURRENT_SCREEN_STATE]\n' + body[:400]
    except Exception:
        return ''


if __name__ == '__main__':
    try:
        run()
    except Exception:
        try:
            write_screen_state(ok=False, summary='视觉启动失败', detail='startup')
        except Exception:
            pass
