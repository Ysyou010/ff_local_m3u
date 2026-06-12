import os
import subprocess
import traceback
from base64 import urlsafe_b64encode, urlsafe_b64decode
from flask import Response, stream_with_context
from .setup import P

def _safe_b64encode(text):
    return urlsafe_b64encode(str(text).encode('utf-8')).decode('utf-8').rstrip('=')

def _safe_b64decode(text):
    padding = '=' * (-len(str(text)) % 4)
    return urlsafe_b64decode((str(text) + padding).encode('utf-8')).decode('utf-8')

def get_media_files():
    raw_text = P.ModelSetting.get("custom_file_list")
    file_list = []
    
    if not raw_text:
        return file_list

    # 줄바꿈 단위로 쪼개어 파일이 실제로 존재하는지 확인합니다.
    for line in raw_text.splitlines():
        path = line.strip()
        if not path:
            continue
        if os.path.isfile(path):
            file_list.append({
                "name": os.path.basename(path),
                "path": path
            })
            
    return file_list

def get_media_list(req):
    files = get_media_files()
    host_url = req.host_url.rstrip('/')
    result = []
    
    for idx, item in enumerate(files, 1):
        # 파일 전체 경로를 base64로 묶어서 재생 라우트로 넘깁니다.
        encoded_path = _safe_b64encode(item["path"])
        play_url = f"{host_url}/{P.package_name}/api/play/ffmpeg/{encoded_path}"
        
        result.append({
            "idx": idx,
            "name": item["name"],
            "path": item["path"],
            "url": play_url
        })
        
    return result

def make_m3u(req):
    files = get_media_files()
    host_url = req.host_url.rstrip('/')
    
    lines = ["#EXTM3U\n"]
    for index, item in enumerate(files, 1):
        encoded_path = _safe_b64encode(item["path"])
        play_url = f"{host_url}/{P.package_name}/api/play/ffmpeg/{encoded_path}"
        
        lines.append(f'#EXTINF:-1 tvg-name="{item["name"]}" tvg-chno="{index}",{item["name"]}\n{play_url}\n')
        
    return Response("".join(lines), content_type="audio/mpegurl; charset=utf-8")

def play_ffmpeg_copy(encoded_path):
    try:
        # 넘어온 전체 경로를 해독합니다.
        full_path = _safe_b64decode(encoded_path)
        
        if not os.path.isfile(full_path):
            return Response("File not found", status=404)

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
