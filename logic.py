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

# 하위 폴더까지 모두 스캔하도록 업그레이드된 함수
# (위쪽 코드는 그대로 두시고, 이 함수만 교체해 주세요)
def get_media_files():
    media_path = P.ModelSetting.get("media_path")
    ext_setting = P.ModelSetting.get("extensions")
    valid_exts = tuple([x.strip().lower() for x in ext_setting.split(",")])
    
    file_list = []
    if not os.path.isdir(media_path):
        P.logger.error(f"[로컬 M3U] 폴더 경로를 찾을 수 없습니다: {media_path}")
        return file_list

    # followlinks=True 옵션을 추가하여 심볼릭 링크나 도커 마운트 폴더도 정상적으로 추적하도록 수정
    for root, dirs, files in os.walk(media_path, followlinks=True):
        for file_name in files:
            if file_name.lower().endswith(valid_exts):
                full_path = os.path.join(root, file_name)
                # 최상위 경로를 제외한 상대 경로 생성
                rel_path = os.path.relpath(full_path, media_path)
                # 윈도우 환경 대비 역슬래시를 슬래시로 변경
                rel_path = rel_path.replace('\\', '/')
                
                file_list.append(rel_path)
                
    return sorted(file_list)

def get_media_list(req):
    files = get_media_files()
    result = []
    
    for idx, file_path in enumerate(files, 1):
        encoded_name = _safe_b64encode(file_path)
        play_url = get_api_url(req, f"play/ffmpeg/{encoded_name}")
        
        result.append({
            "idx": idx,
            "name": file_path, # 파일명 대신 폴더가 포함된 상대경로를 표시
            "url": play_url
        })
        
    return result

def make_m3u(req):
    files = get_media_files()
    lines = ["#EXTM3U\n"]
    
    for index, file_path in enumerate(files, 1):
        encoded_name = _safe_b64encode(file_path)
        play_url = get_api_url(req, f"play/ffmpeg/{encoded_name}")
        
        lines.append(f'#EXTINF:-1 tvg-name="{file_path}" tvg-chno="{index}",{file_path}\n{play_url}\n')
        
    return Response("".join(lines), content_type="audio/mpegurl; charset=utf-8")

def play_ffmpeg_copy(encoded_name):
    try:
        rel_path = _safe_b64decode(encoded_name)
        media_path = P.ModelSetting.get("media_path")
        full_path = os.path.join(media_path, rel_path)
        
        if not os.path.isfile(full_path):
            return Response(f"File not found: {rel_path}", status=404)

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
        
        P.logger.info(f"FFmpeg Play Start: {rel_path}")
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
                P.logger.info(f"FFmpeg Play End: {rel_path}")

        return Response(generate(), mimetype="video/MP2T")
        
    except Exception as e:
        P.logger.error(f"Exception: {str(e)}")
        P.logger.error(traceback.format_exc())
        return Response("Playback Error", status=500)
