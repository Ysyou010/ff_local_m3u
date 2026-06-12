import os
import subprocess
import traceback
from base64 import urlsafe_b64encode, urlsafe_b64decode
from urllib.parse import urlencode
from flask import Response, stream_with_context
from framework import SystemModelSetting
from .setup import P

def get_apikey():
    try:
        if SystemModelSetting.get_bool("use_apikey"):
            return str(SystemModelSetting.get("apikey") or "").strip()
    except:
        pass
    return ""

def get_base_url(req):
    try:
        return req.url_root.rstrip("/")
    except:
        return ""

def get_api_url(req, endpoint, params=None):
    if params is None:
        params = {}
    apikey = get_apikey()
    if apikey:
        params['apikey'] = apikey
        
    base = get_base_url(req)
    query = urlencode(params)
    url = f"{base}/{P.package_name}/api/{endpoint}" if base else f"/{P.package_name}/api/{endpoint}"
    return f"{url}?{query}" if query else url

def _safe_b64encode(text):
    return urlsafe_b64encode(str(text).encode('utf-8')).decode('utf-8').rstrip('=')

def _safe_b64decode(text):
    padding = '=' * (-len(str(text)) % 4)
    return urlsafe_b64decode((str(text) + padding).encode('utf-8')).decode('utf-8')

def get_media_files():
    media_paths_raw = P.ModelSetting.get("media_path")
    if not media_paths_raw:
        return []
        
    # 엔터(줄바꿈) 단위로 경로들을 쪼개서 리스트로 만듭니다.
    media_paths = [p.strip() for p in media_paths_raw.split('\n') if p.strip()]
    
    ext_setting = P.ModelSetting.get("extensions")
    valid_exts = tuple([x.strip().lower() for x in ext_setting.split(",")])
    
    file_list = []
    
    for path in media_paths:
        # 1. 단일 파일인 경우
        if os.path.isfile(path):
            if path.lower().endswith(valid_exts):
                file_list.append(path.replace('\\', '/'))
            continue
            
        # 2. 잘못된 경로인 경우 (로그 출력)
        if not os.path.isdir(path):
            P.logger.error(f"[로컬 M3U] 올바르지 않은 경로입니다: {path}")
            continue

        # 3. 폴더인 경우 하위 모두 탐색
        for root, dirs, files in os.walk(path, followlinks=True):
            for file_name in files:
                if file_name.lower().endswith(valid_exts):
                    full_path = os.path.join(root, file_name)
                    file_list.append(full_path.replace('\\', '/'))
                    
    # 중복 제거 후 정렬
    return sorted(list(set(file_list)))

def get_media_list(req):
    files = get_media_files()
    result = []
    
    for idx, full_path in enumerate(files, 1):
        # 전체 절대경로를 통째로 인코딩합니다.
        encoded_name = _safe_b64encode(full_path)
        play_url = get_api_url(req, f"play/ffmpeg/{encoded_name}")
        
        # 화면에는 파일명과 폴더 구조를 보여주기 위해 가공
        display_name = os.path.basename(full_path)
        
        result.append({
            "idx": idx,
            "name": display_name,
            "url": play_url
        })
        
    return result

def make_m3u(req):
    files = get_media_files()
    lines = ["#EXTM3U\n"]
    
    for index, full_path in enumerate(files, 1):
        encoded_name = _safe_b64encode(full_path)
        play_url = get_api_url(req, f"play/ffmpeg/{encoded_name}")
        display_name = os.path.basename(full_path)
        
        lines.append(f'#EXTINF:-1 tvg-name="{display_name}" tvg-chno="{index}",{display_name}\n{play_url}\n')
        
    return Response("".join(lines), content_type="audio/mpegurl; charset=utf-8")

def play_ffmpeg_copy(encoded_name):
    try:
        # 인코딩된 것 자체가 완벽한 절대경로입니다.
        full_path = _safe_b64decode(encoded_name)
        
        if not os.path.isfile(full_path):
            return Response(f"File not found: {full_path}", status=404)

        cmd = [
            "ffmpeg", 
            "-hide_banner", 
            "-loglevel", "warning",
            "-re", 
            "-i", full_path,
            "-map", "0:v:0?", 
            "-map", "0:a:0?",
            "-c:v", "copy", 
            "-c:a", "copy",
            "-muxdelay", "0",
            "-f", "mpegts",
            "-"
        ]
        
        P.logger.info(f"FFmpeg Play Start: {full_path}")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        
        @stream_with_context
        def generate():
            try:
                while True:
                    chunk = proc.stdout.read(188 * 32)
                    if not chunk:
                        break
                    yield chunk
            finally:
                if proc.poll() is None:
                    proc.kill()
                P.logger.info(f"FFmpeg Play End: {full_path}")

        return Response(generate(), mimetype="video/MP2T")
        
    except Exception as e:
        P.logger.error(f"Exception: {str(e)}")
        P.logger.error(traceback.format_exc())
        return Response("Playback Error", status=500)
