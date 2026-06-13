import os
import subprocess
import traceback
import mimetypes
import time
from base64 import urlsafe_b64encode, urlsafe_b64decode
from urllib.parse import urlencode
from flask import Response, stream_with_context, redirect, send_file, request
from framework import SystemModelSetting
from .setup import P

# 🌟 유튜브 주소 캐싱 메모리 (2시간 동안 주소 기억)
YOUTUBE_STREAM_CACHE = {}

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
            
            ext = os.path.splitext(item['path'])[1] if not item['path'].startswith('http') else '.mp4'
            if not ext: ext = '.mp4'
            play_url = get_api_url(req, "play", {"file": encoded_name, "ext": ext})
            
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
            
            ext = os.path.splitext(item['path'])[1] if not item['path'].startswith('http') else '.mp4'
            if not ext: ext = '.mp4'
            play_url = get_api_url(req, "play", {"file": encoded_name, "ext": ext})
            
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
        # 1. 로컬 파일 처리: 안드로이드 무한로딩 원천 차단
        # ==========================================
        if not (full_path.startswith("http://") or full_path.startswith("https://")):
            if not os.path.isfile(full_path):
                P.logger.error(f"[재생 실패] 로컬 파일 없음: {full_path}")
                return Response(f"File not found: {full_path}", status=404)
            
            mime_type, _ = mimetypes.guess_type(full_path)
            if not mime_type: mime_type = "video/mp4"
            
            if request.method == 'HEAD':
                resp = Response(status=200, mimetype=mime_type)
                resp.headers['Accept-Ranges'] = 'bytes'
                resp.headers['Content-Length'] = str(os.path.getsize(full_path))
                return resp
                
            P.logger.info(f"[재생 시작] 로컬 파일 다이렉트 전송: {full_path}")
            return send_file(full_path, mimetype=mime_type, conditional=True)
            
        # ==========================================
        # 2. 유튜브 처리: 타임아웃 방어 및 고화질 캐싱
        # ==========================================
        P.logger.info(f"[재생 요청] YouTube: {full_path} (요청 화질: {quality})")
        
        # 🌟 [초고속 생존 신고] 안드로이드 IPTV 앱이 도망가지 못하게 0.01초 만에 응답
        if request.method == 'HEAD':
            P.logger.info(f" -> HEAD 요청 0.01초 패스 (앱 타임아웃 방어 성공)")
            resp = Response(status=200, mimetype="video/mp4")
            resp.headers['Accept-Ranges'] = 'bytes'
            resp.headers['Content-Length'] = '2000000000' # 가짜 전체 길이
            return resp
        
        try:
            import yt_dlp
            import requests
            
            cache_key = f"{full_path}_{quality}"
            current_time = time.time()
            info_data = None
            
            # 🌟 [캐싱 시스템] 2시간 이내에 찾았던 영상은 딜레이 0초로 우회
            if cache_key in YOUTUBE_STREAM_CACHE:
                cached_data, timestamp = YOUTUBE_STREAM_CACHE[cache_key]
                if current_time - timestamp < 7200:
                    P.logger.info(f" -> [캐시 적중] yt-dlp 추출 우회 (딜레이 0초)")
                    info_data = cached_data
            
            if not info_data:
                P.logger.info(f" -> [캐시 없음] 스트림 원본 추출 시작 (약 2초 소요)")
                needs_ffmpeg = False
                
                # 🌟 [화질 분기] 1080p 이상은 합성 모드(FFmpeg), 이하는 일반 모드
                if quality in ["1080p", "1440p", "2160p"]:
                    needs_ffmpeg = True
                    max_height = quality[:-1]
                    format_str = f'bestvideo[height<={max_height}][vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]'
                else:
                    if quality == "자동":
                        format_str = 'best[ext=mp4]/best'
                    else:
                        max_height = quality[:-1]
                        format_str = f'best[height<={max_height}][ext=mp4]/best[ext=mp4]/best'
                        
                ydl_opts = {'format': format_str, 'quiet': True, 'noplaylist': True}
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(full_path, download=False)
                    
                    info_data = {
                        'is_live': info.get('is_live', False),
                        'stream_url': info.get('url') or info.get('manifest_url'),
                        'req_formats_urls': [],
                        'user_agent': info.get('http_headers', {}).get('User-Agent', "Mozilla/5.0"),
                        'needs_ffmpeg': needs_ffmpeg
                    }
                    
                    req_formats = info.get('requested_formats')
                    if req_formats and len(req_formats) == 2:
                        info_data['req_formats_urls'] = [req_formats[0].get('url'), req_formats[1].get('url')]
                        info_data['user_agent'] = req_formats[0].get('http_headers', {}).get('User-Agent', "Mozilla/5.0")
                    
                    if info_data['is_live']:
                        info_data['needs_ffmpeg'] = True
                        
                    # 메모리에 정보 기억
                    YOUTUBE_STREAM_CACHE[cache_key] = (info_data, current_time)

            if not info_data['stream_url'] and not info_data['req_formats_urls']:
                return Response("스트림 주소 추출 실패", status=500)
            
            # ==========================================
            # [모드 A] 720p 이하 VOD: 앞뒤 구간 탐색 완벽 지원 프록시
            # ==========================================
            if not info_data['needs_ffmpeg'] and not info_data['is_live']:
                P.logger.info(f"[재생 시작] YouTube VOD 투명 프록시 가동 (탐색 지원)")
                req_headers = {"User-Agent": info_data['user_agent']}
                if 'Range' in request.headers:
                    req_headers['Range'] = request.headers['Range']
                
                yt_resp = requests.request(
                    method=request.method,
                    url=info_data['stream_url'],
                    headers=req_headers,
                    stream=True,
                    allow_redirects=True
                )
                
                resp_headers = {}
                for k in ['Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges']:
                    if k in yt_resp.headers:
                        resp_headers[k] = yt_resp.headers[k]
                
                if 'Content-Type' not in resp_headers:
                    resp_headers['Content-Type'] = 'video/mp4'
                    
                def generate_vod():
                    for chunk in yt_resp.iter_content(chunk_size=1024 * 1024):
                        if chunk: 
                            yield chunk
                            
                return Response(
                    stream_with_context(generate_vod()), 
                    status=yt_resp.status_code, 
                    headers=resp_headers, 
                    direct_passthrough=True
                )

            # ==========================================
            # [모드 B] 1080p 이상 / 라이브 방송: FFmpeg 실시간 중계
            # ==========================================
            else:
                P.logger.info(f"[재생 시작] YouTube 고화질/라이브 FFmpeg 병합 가동")
                cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
                
                if info_data['req_formats_urls'] and len(info_data['req_formats_urls']) == 2 and not info_data['is_live']:
                    cmd.extend(["-user_agent", info_data['user_agent'], "-i", info_data['req_formats_urls'][0], "-i", info_data['req_formats_urls'][1]])
                    cmd.extend(["-map", "0:v:0", "-map", "1:a:0"])
                else:
                    cmd.extend(["-user_agent", info_data['user_agent'], "-i", info_data['stream_url']])
                    cmd.extend(["-map", "0:v:0?", "-map", "0:a:0?"])
                    
                cmd.extend([
                    "-c:v", "copy", "-c:a", "copy",
                    "-bsf:v", "h264_mp4toannexb", 
                    "-fflags", "+genpts",
                    "-muxdelay", "0", "-f", "mpegts", "-"
                ])
                
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
                        P.logger.info("[재생 종료] FFmpeg 중계 해제")

                return Response(generate_live(), mimetype="video/MP2T")
                
        except Exception as e:
            P.logger.error(f"[재생 실패] 유튜브 에러: {e}")
            P.logger.error(traceback.format_exc())
            return Response(f"유튜브 에러: {e}", status=500)

    except Exception as e:
        P.logger.error(f"[재생 에러] {str(e)}")
        P.logger.error(traceback.format_exc())
        return Response("Playback Error", status=500)
