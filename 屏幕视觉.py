"""屏幕视觉 v1：Latest-Frame 截图 → Flash 眼睛 → screen_state → ShouldSpeak。

旁路模块：失败全部自吞，不拖垮语音听环。可开关（语音设置.json vision_enabled）。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import socket
import threading
import time
import traceback
from pathlib import Path

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
_stop = threading.Event()


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


def capture_jpeg(max_width=1280, quality=55) -> bytes | None:
    try:
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


def call_eyes(jpeg: bytes, cfg: dict) -> dict:
    provider, key = _vision_key(cfg)
    if not key:
        return {'ok': False, 'error': 'no_vision_key', 'summary': '未配置视觉密钥'}
    url, model = _vision_endpoint(cfg, provider)
    encoded = 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode('ascii')
    payload = {
        'model': model,
        'messages': [{
            'role': 'user',
            'content': [
                {'type': 'image_url', 'image_url': {'url': encoded}},
                {'type': 'text', 'text': _EYES_PROMPT},
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
    worthy = bool(parsed.get('worthy'))
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
        # 状态文件很旧则不当忙
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


def proactive_speak(state: dict, cfg: dict) -> bool:
    """走现有 send → reply → TTS；全程异常自吞。"""
    if not _speak_lock.acquire(blocking=False):
        return False
    try:
        if is_dialogue_busy():
            return False
        sid = cfg.get('conversation_id') or ''
        if not sid:
            return False
        from 语音链路 import send, stream_reply, synthesize, play, reply_snapshot, state as set_listen_state, VOICES
        nudge = (
            '【视觉事件】我刚扫了一眼屏幕。'
            '若值得跟主人开口，请用一两句口语简短说；不值得就只回复一个句号。'
        )
        previous, _ = reply_snapshot(sid)
        set_listen_state('视觉主动开口：发送中', text=state.get('summary') or '')
        send(nudge, sid)
        voice = cfg.get('voice') or VOICES.get(cfg.get('tts_provider', ''), ['Cherry'])[0]
        speaker = cfg.get('speaker')
        spoken_len = 0
        for answer, finished in stream_reply(sid, previous):
            if is_dialogue_busy() and spoken_len == 0:
                # 用户突然开口则放弃本轮主动念
                break
            tail = (answer or '')[spoken_len:]
            if tail.strip() and tail.strip() not in ('。', '.', '…', '...'):
                set_listen_state('视觉主动开口：播报中', text=state.get('summary') or '')
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


def _capture_loop(interval: float):
    while not _stop.is_set():
        cfg = _load_config()
        if not cfg.get('vision_enabled'):
            _stop.wait(0.5)
            continue
        interval = max(0.4, float(cfg.get('vision_interval_sec', 1) or 1))
        jpeg = capture_jpeg()
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
                _stop.wait(0.8)
                continue
            interval = max(0.4, float(cfg.get('vision_interval_sec', 1) or 1))
            with _latest_lock:
                jpeg = _latest.get('jpeg')
                fp = _latest.get('fp') or ''
                cap_ts = float(_latest.get('ts') or 0)
            if not jpeg or not fp:
                _stop.wait(0.3)
                continue
            if fp == last_fp and last_ok_state:
                # 同画面：只刷新时间戳，不刷 API、不刷麦
                refreshed = dict(last_ok_state)
                refreshed['ts'] = time.time()
                write_screen_state(**{k: refreshed[k] for k in (
                    'ok', 'ts', 'hash', 'summary', 'detail', 'event_id', 'worthy'
                )})
                _stop.wait(0.5)
                continue
            # 视频等场景指纹会抖：眼睛 API 仍按间隔节流，始终只看最新槽
            now = time.time()
            if now - last_eyes_at < interval:
                _stop.wait(0.2)
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
                last_fp = fp  # 避免失败风暴里每帧重打 API；画面指纹变了才会再试
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
            # 附带字段写一次完整文件
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
        _stop.wait(0.2)


def run():
    lock = socket.socket()
    try:
        lock.bind(('127.0.0.1', VISION_LOCK_PORT))
    except OSError:
        return False
    cfg = _load_config()
    if not cfg.get('vision_enabled'):
        write_screen_state(ok=False, summary='视觉已关闭', detail='vision_enabled=false')
        # 仍保持进程：轮询开关，便于菜单启停共用一个常驻进程；若由壳按开关拉起也可
    try:
        threading.Thread(target=_capture_loop, args=(float(cfg.get('vision_interval_sec', 1) or 1),), daemon=True).start()
        threading.Thread(target=_analyze_loop, daemon=True).start()
        while not _stop.is_set():
            cfg = _load_config()
            if not cfg.get('vision_enabled'):
                # 关视觉：清空槽，空转
                with _latest_lock:
                    _latest['jpeg'] = None
                    _latest['fp'] = ''
                time.sleep(0.8)
                continue
            time.sleep(0.5)
    except Exception:
        write_screen_state(ok=False, summary='视觉进程退出', detail='fatal_swallowed')
    finally:
        _stop.set()
        try:
            lock.close()
        except OSError:
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
        # 绝对不把堆栈甩到无人看的 pythonw 控制台以外；写状态即可
        try:
            write_screen_state(ok=False, summary='视觉启动失败', detail='startup')
        except Exception:
            pass
