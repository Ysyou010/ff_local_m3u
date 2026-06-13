import os
import subprocess
import traceback
from base64 import urlsafe_b64encode, urlsafe_b64decode
from urllib.parse import urlencode
from flask import Response, stream_with_context, redirect, send_file, request
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

def get_media_files(target_category=None):
    try:
        media_path_raw = P.ModelSetting.get("media_path")
        if not media_path_raw:
            return []
            
        ext_setting = P.ModelSetting.get("extensions")
        valid_exts = tuple([x.strip().lower() for x in ext_setting.split(",")])
        
        file_list = []
        paths = [p.strip() for p in media_path_raw.split('\n') if p.strip()]
        
        for line in paths:
            parts = line.split('|')
            if len(parts) >= 4:
                category = parts[0].strip()
                title = parts[1].strip()
                quality = parts[2].strip()
                path = "|".join(parts[3:]).strip()
            elif len(parts) == 3:
                category = parts[0].strip()
                title = parts[1].strip()
                quality = "자동"
                path = "|".join(parts[2:]).strip()
            elif len(parts) == 2:
                category = "기본"
                title = parts[0].strip()
                quality = "자동"
                path = parts[1].strip()
            else:
                category = "기본"
                title = ""
                quality = "자동"
                path = line.strip()
                
            if not title:
                title = "YouTube Stream" if path.startswith("http") else os.path.basename(path)

            if target_category and target_category != 'all' and category != target_category:
                continue
                
            if path.startswith("http://") or path.startswith("https://"):
                file_list.append({"category": category, "title": title, "quality": quality, "path": path})
            elif os.path.isfile(path):
                if path.lower().endswith(valid_exts):
                    file_list.append({"category": category, "title": title, "quality": quality, "path": path.replace('\\', '/')})
                    
        return file_list
    except Exception as e:
        P.logger.error(traceback.format_exc())
        return []

def get_media_list(req):
    try:
        files = get_media_files('all')
        result = []
        for idx, item in enumerate(files, 1):
            encoded_payload = f"{item['quality']}||{item['path']}"
            encoded_name = _safe_b64encode(encoded_payload)
            play_url = get_api_url(req, "play", {"file": encoded_name})
            
            display_name = f"[{item['category']}] {item['title']}"
            result.append({
                "idx": idx,
                "name": display_name,
                "url": play_url
            })
        return result
    except Exception as e:
        P.logger.error(traceback.format_exc())
        raise e

def make_m3u(req):
    try:
        target_category = req.args.get('id', 'all')
        files = get_media_files(target_category)
        lines = ["#EXTM3U\n"]
        
        for index, item in enumerate(files, 1):
            encoded_payload = f"{item['quality']}||{item['path']}"
            encoded_name = _safe_b64encode(encoded_payload)
            play_url = get_api_url(req, "play", {"file": encoded_name})
            
            display_name = item['title']
            lines.append(f'#EXTINF:-1 tvg-name="{display_name}" tvg-chno="{index}",{display_name}\n{play_url}\n')
            
        return Response("".join(lines), content_type="audio/mpegurl; charset=utf-8")
    except Exception as e:
        P.logger.error(traceback.format_exc())
        return Response(f"make_m3u 에러: {str(e)}", status=500)

def play_ffmpeg_copy(encoded_name):
    try:
        full_str = _safe_b64decode(encoded_name)
        if "||" in full_str:
            quality, full_path = full_str.split("||", 1)
        else:
            quality = "자동"
            full_path = full_str
            
        # ==========================================
        # 1. 로컬 파일: 100% 순정 코드 (건드리지 않음)
        # ==========================================
        if not (full_path.startswith("http://") or full_path.startswith("https://")):
            if not os.path.isfile(full_path):
                P.logger.error(f"[재생 실패] 로컬 파일 없음: {full_path}")
                return Response(f"File not found: {full_path}", status=404)
                
            P.logger.info(f"[재생 시작] 로컬 파일 다이렉트: {full_path}")
            return send_file(full_path, conditional=True)
            
        # ==========================================
        # 2. 유튜브 처리
        # ==========================================
        P.logger.info(f"[재생 요청] YouTube: {full_path} (요청 화질: {quality})")
        
        try:
            import yt_dlp
            import requests
            
            # 합본 파일 중 최고 화질(최대 720p) 추출
            if quality == "자동":
                format_str = 'best[ext=mp4]/best'
            elif quality.endswith('p') and quality[:-1].isdigit():
                max_height = quality[:-1]
                format_str = f'best[height<={max_height}][ext=mp4]/best[ext=mp4]/best'
            else:
                format_str = 'best'
                
            ydl_opts = {'format': format_str, 'quiet': True, 'noplaylist': True}
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(full_path, download=False)
                is_live = info.get('is_live', False)
                stream_url = info.get('url') or info.get('manifest_url')
                user_agent = info.get('http_headers', {}).get('User-Agent', "Mozilla/5.0")
                
                if not stream_url:
                    return Response("스트림 주소 추출 실패", status=500)
                
                # 🌟 [VOD] 유튜브 원본 파이썬 중계 (구글 차단 회피 + 앞뒤 구간 탐색 지원)
                if not is_live:
                    P.logger.info(f"[재생 시작] YouTube VOD 서버 자체 중계 (탐색 지원)")
                    
                    req_headers = {"User-Agent": user_agent}
                    # 플레이어가 구간 이동(넘기기)을 요청하면, 그 요청을 구글에 그대로 전달
                    if request.headers.get('Range'):
                        req_headers['Range'] = request.headers.get('Range')
                        
                    r = requests.get(stream_url, headers=req_headers, stream=True)
                    
                    def generate():
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                yield chunk
                                
                    # 구글의 응답(정상 전송 또는 구간 전송 206)을 플레이어에게 그대로 패스
                    resp = Response(stream_with_context(generate()), status=r.status_code)
                    for k in ['Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges']:
                        if k in r.headers:
                            resp.headers[k] = r.headers[k]
                    return resp
                
                # 🌟 [LIVE] 라이브 방송은 FFmpeg로 파이프 전송
                P.logger.info(f"[재생 시작] YouTube 라이브 방송 FFmpeg 중계 가동")
                cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
                cmd.extend(["-user_agent", user_agent, "-i", stream_url])
                cmd.extend(["-c:v", "copy", "-c:a", "copy", "-muxdelay", "0", "-f", "mpegts", "-"])
                
        except Exception as e:
            P.logger.error(f"[재생 실패] 유튜브 에러: {e}")
            P.logger.error(traceback.format_exc())
            return Response(f"유튜브 에러: {e}", status=500)
        
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        
        @stream_with_context
        def generate_live():
            try:
                while True:
                    chunk = proc.stdout.read(188 * 32)
                    if not chunk:
                        break
                    yield chunk
            finally:
                if proc.poll() is None:
                    proc.kill()

        return Response(generate_live(), mimetype="video/MP2T")

    except Exception as e:
        P.logger.error(f"[재생 에러] {str(e)}")
        P.logger.error(traceback.format_exc())
        return Response("Playback Error", status=500)
