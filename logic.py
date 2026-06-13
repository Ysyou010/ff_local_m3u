import os
import subprocess
import traceback
import mimetypes
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

# 🌟 안드로이드 플레이어(ExoPlayer) 생존 필수: 확장자에 따른 명확한 MimeType 강제 지정 함수
def get_mime_type(file_path):
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        ext = os.path.splitext(file_path)[1].lower()
        mime_map = {
            '.mp4': 'video/mp4',
            '.mkv': 'video/x-matroska',
            '.ts': 'video/MP2T',
            '.avi': 'video/x-msvideo',
            '.m3u8': 'application/vnd.apple.mpegurl'
        }
        mime_type = mime_map.get(ext, 'video/mp4')
    return mime_type

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
        # 1. 로컬 파일: MimeType 명시 다이렉트 전송
        # ==========================================
        if not (full_path.startswith("http://") or full_path.startswith("https://")):
            if not os.path.isfile(full_path):
                P.logger.error(f"[재생 실패] 로컬 파일 없음: {full_path}")
                return Response(f"File not found: {full_path}", status=404)
            
            mime_type = get_mime_type(full_path)
            P.logger.info(f"[재생 시작] 로컬 파일 다이렉트 전송 (MIME 강제: {mime_type}): {full_path}")
            return send_file(full_path, mimetype=mime_type, conditional=True)
            
        # ==========================================
        # 2. 유튜브 처리
        # ==========================================
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
        P.logger.info(f"[재생 요청] YouTube: {full_path} (요청 화질: {quality})")
        
        try:
            import yt_dlp
            import requests
            
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
                
                if not stream_url:
                    return Response("스트림 주소 추출 실패", status=500)
                
                # 🌟 일반 영상(VOD): 완벽한 파이썬 Range 프록시 가동
                if not is_live:
                    P.logger.info(f"[재생 시작] YouTube VOD 파이썬 완벽 프록시 가동 (차단 우회/탐색 지원)")
                    
                    req_headers = {"User-Agent": user_agent}
                    range_header = request.headers.get('Range')
                    if range_header:
                        req_headers['Range'] = range_header
                    
                    # 안드로이드의 사전 찌르기(HEAD) 요청 고속 응답 처리
                    if request.method == 'HEAD':
                        yt_head = requests.head(stream_url, headers=req_headers, allow_redirects=True)
                        resp = Response(status=yt_head.status_code)
                        for key in ['Content-Length', 'Accept-Ranges', 'Content-Type']:
                            if key in yt_head.headers:
                                resp.headers[key] = yt_head.headers[key]
                        if 'Content-Type' not in resp.headers:
                            resp.headers['Content-Type'] = 'video/mp4'
                        return resp
                        
                    yt_req = requests.get(stream_url, headers=req_headers, stream=True, allow_redirects=True)
                    
                    def generate():
                        try:
                            for chunk in yt_req.iter_content(chunk_size=1024*1024):
                                if chunk:
                                    yield chunk
                        except Exception as e:
                            pass
                            
                    # 🌟 핵심: direct_passthrough=True를 통해 Framework의 Chunked 강제 개입을 차단합니다.
                    response = Response(stream_with_context(generate()), status=yt_req.status_code, direct_passthrough=True)
                    
                    # 길이 정보(Content-Length)를 빼앗기지 않고 안드로이드에게 고스란히 전달합니다.
                    for key in ['Content-Range', 'Content-Length', 'Accept-Ranges', 'Content-Type']:
                        if key in yt_req.headers:
                            response.headers[key] = yt_req.headers[key]
                    
                    if 'Content-Type' not in response.headers:
                        response.headers['Content-Type'] = 'video/mp4'
                        
                    return response
                
                # 🌟 라이브 방송(Live): 실시간 중계를 위해 기존처럼 FFmpeg 프록시 사용
                P.logger.info(f"[재생 시작] YouTube 라이브 방송 중계 가동")
                headers = info.get('http_headers', {})
                if headers and 'User-Agent' in headers:
                    user_agent = headers['User-Agent']
                    
                cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
                cmd.extend(["-user_agent", user_agent])
                cmd.extend([
                    "-i", stream_url,
                    "-map", "0:v:0?", "-map", "0:a:0?",
                    "-c:v", "copy", "-c:a", "copy",
                    "-muxdelay", "0", "-f", "mpegts", "-"
                ])
                
        except Exception as e:
            P.logger.error(f"[재생 실패] 유튜브 에러: {e}")
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
                P.logger.info("[재생 종료] YouTube 실시간 중계 종료")

        return Response(generate_live(), mimetype="video/MP2T")

    except Exception as e:
        P.logger.error(f"[재생 에러] {str(e)}")
        P.logger.error(traceback.format_exc())
        return Response("Playback Error", status=500)
