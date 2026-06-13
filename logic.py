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
        # 1. 로컬 파일 처리
        # ==========================================
        if not (full_path.startswith("http://") or full_path.startswith("https://")):
            if not os.path.isfile(full_path):
                P.logger.error(f"[재생 실패] 로컬 파일 없음: {full_path}")
                return Response(f"File not found: {full_path}", status=404)
            P.logger.info(f"[재생 시작] 로컬 파일 다이렉트 전송: {full_path}")
            return send_file(full_path, conditional=True)
            
        # ==========================================
        # 2. 유튜브 처리
        # ==========================================
        P.logger.info(f"[재생 요청] YouTube: {full_path} (요청 화질: {quality})")
        
        try:
            import yt_dlp
            import requests
            
            needs_ffmpeg = False
            
            # 🌟 [고화질 분기] 1080p 이상은 무조건 분리된 파일을 강제 합성해야 하므로 FFmpeg 가동
            if quality in ["1080p", "1440p", "2160p"]:
                needs_ffmpeg = True
                max_height = quality[:-1]
                # 안드로이드 충돌을 막기 위해 철저하게 H.264(avc1) 코덱만 뽑아옵니다.
                format_str = f'bestvideo[height<={max_height}][vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]'
            else:
                # 720p 이하는 합본 파일이 존재하므로 구간 탐색(앞뒤 넘김)을 위해 파이썬 프록시 준비
                if quality == "자동":
                    format_str = 'best[ext=mp4]/best'
                else:
                    max_height = quality[:-1]
                    format_str = f'best[height<={max_height}][ext=mp4]/best[ext=mp4]/best'
                    
            ydl_opts = {'format': format_str, 'quiet': True, 'noplaylist': True}
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(full_path, download=False)
                is_live = info.get('is_live', False)
                req_formats = info.get('requested_formats')
                stream_url = info.get('url') or info.get('manifest_url')
                
                # 라이브 방송은 탐색이 안 되므로 무조건 FFmpeg 중계 모드로 전환
                if is_live:
                    needs_ffmpeg = True
                    
                if not needs_ffmpeg:
                    # ==========================================
                    # [모드 A] 파이썬 투명 프록시 (VOD 720p 이하: 탐색 완벽 지원)
                    # ==========================================
                    P.logger.info(f"[재생 시작] YouTube VOD 투명 프록시 가동 (탐색 완벽 지원)")
                    req_headers = {}
                    if 'Range' in request.headers:
                        req_headers['Range'] = request.headers['Range']
                    
                    yt_resp = requests.request(method=request.method, url=stream_url, headers=req_headers, stream=True, allow_redirects=True)
                    
                    resp_headers = {}
                    for k in ['Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges']:
                        if k in yt_resp.headers:
                            resp_headers[k] = yt_resp.headers[k]
                    
                    if request.method == 'HEAD':
                        return Response(status=yt_resp.status_code, headers=resp_headers)
                        
                    def generate_vod():
                        for chunk in yt_resp.iter_content(chunk_size=1024 * 1024):
                            if chunk: 
                                yield chunk
                                
                    return Response(stream_with_context(generate_vod()), status=yt_resp.status_code, headers=resp_headers, direct_passthrough=True)

                else:
                    # ==========================================
                    # [모드 B] FFmpeg 실시간 합성 중계 (VOD 1080p 이상 또는 라이브)
                    # ==========================================
                    P.logger.info(f"[재생 시작] YouTube 1080p 고화질/라이브 FFmpeg 병합 가동")
                    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
                    
                    # 1080p 분리 파일인 경우
                    if req_formats and len(req_formats) == 2 and not is_live:
                        v_url = req_formats[0].get('url')
                        a_url = req_formats[1].get('url')
                        ua = req_formats[0].get('http_headers', {}).get('User-Agent', "Mozilla/5.0")
                        
                        cmd.extend(["-user_agent", ua, "-i", v_url, "-i", a_url])
                        cmd.extend(["-map", "0:v:0", "-map", "1:a:0"])
                    else:
                        ua = info.get('http_headers', {}).get('User-Agent', "Mozilla/5.0")
                        cmd.extend(["-user_agent", ua, "-i", stream_url])
                        cmd.extend(["-map", "0:v:0?", "-map", "0:a:0?"])
                        
                    cmd.extend([
                        "-c:v", "copy", "-c:a", "copy",
                        "-bsf:v", "h264_mp4toannexb", # avc1 코덱을 강제했으므로 이제 에러 없이 작동합니다.
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
                            P.logger.info("[재생 종료] FFmpeg 프로세스 해제")

                    return Response(generate_live(), mimetype="video/MP2T")
                    
        except Exception as e:
            P.logger.error(f"[재생 실패] 유튜브 에러: {e}")
            P.logger.error(traceback.format_exc())
            return Response(f"유튜브 에러: {e}", status=500)

    except Exception as e:
        P.logger.error(f"[재생 에러] {str(e)}")
        P.logger.error(traceback.format_exc())
        return Response("Playback Error", status=500)
