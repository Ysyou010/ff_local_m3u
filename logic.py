import os
import subprocess
import traceback
from base64 import urlsafe_b64encode, urlsafe_b64decode
from urllib.parse import urlencode
from flask import Response, stream_with_context, redirect, send_file
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
            
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
        
        if full_path.startswith("http://") or full_path.startswith("https://"):
            P.logger.info(f"[재생 요청] YouTube: {full_path} (화질: {quality})")
            try:
                import yt_dlp
                
                # 🌟 [수정 포인트] 1080p가 제대로 렌더링되도록 MP4(H.264) 및 M4A 포맷을 명시적으로 요구합니다.
                format_str = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
                if quality.endswith('p') and quality[:-1].isdigit():
                    max_height = quality[:-1]
                    format_str = f'bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/best[height<={max_height}][ext=mp4]/best'
                    
                ydl_opts = {'format': format_str, 'quiet': True, 'noplaylist': True}
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(full_path, download=False)
                    stream_url = info.get('url') or info.get('manifest_url')
                    is_live = info.get('is_live', False)
                    req_formats = info.get('requested_formats')
                    
                    # 🌟 Case 1: 비디오와 오디오가 분리된 고화질(1080p 등) VOD일 때 (FFmpeg 복합 프록시 가동)
                    if req_formats and len(req_formats) == 2 and not is_live:
                        P.logger.info(f"[재생 시작] YouTube 고화질 VOD 복합 스트림 프록시 중계 ({quality})")
                        video_url = req_formats[0].get('url')
                        audio_url = req_formats[1].get('url')
                        
                        headers = req_formats[0].get('http_headers', {})
                        if headers and 'User-Agent' in headers:
                            user_agent = headers['User-Agent']
                            
                        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
                        cmd.extend(["-user_agent", user_agent])
                        cmd.extend([
                            "-i", video_url,
                            "-i", audio_url,
                            "-map", "0:v:0", "-map", "1:a:0",
                            "-c:v", "copy", "-c:a", "copy",
                            "-bsf:v", "h264_mp4toannexb",  # 🌟 1080p H.264 화면 출력을 위한 필수 비트스트림 필터
                            "-fflags", "+genpts",          # 🌟 싱크 유지 플래그
                            "-muxdelay", "0", "-f", "mpegts", "-"
                        ])
                    
                    # 🌟 Case 2: 라이브 방송이거나 단일 스트림(720p 이하)일 때
                    else:
                        if not stream_url:
                            P.logger.error("[재생 실패] 유튜브 스트림 추출 실패")
                            return Response("유튜브 스트림 추출 실패", status=500)
                            
                        headers = info.get('http_headers', {})
                        if headers and 'User-Agent' in headers:
                            user_agent = headers['User-Agent']
                            
                        if not is_live:
                            # 720p 이하 단일 스트림은 플레이어가 직접 붙도록 리다이렉트 (앞뒤 탐색 지원)
                            P.logger.info(f"[재생 시작] YouTube 저화질 단일 스트림 VOD 리다이렉트 (탐색 활성화): {full_path}")
                            return redirect(stream_url, code=302)
                        
                        # 라이브 방송 중계
                        P.logger.info(f"[재생 시작] YouTube 라이브 프록시 중계 가동: {full_path}")
                        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
                        cmd.extend(["-user_agent", user_agent])
                        cmd.extend([
                            "-i", stream_url,
                            "-map", "0:v:0?", "-map", "0:a:0?",
                            "-c:v", "copy", "-c:a", "copy",
                            "-bsf:v", "h264_mp4toannexb",  # 🌟 라이브 방송 시에도 블랙스크린 방지 필터 적용
                            "-muxdelay", "0", "-f", "mpegts", "-"
                        ])
                        
            except Exception as e:
                P.logger.error(f"[재생 실패] 유튜브 에러: {e}")
                return Response(f"유튜브 에러: {e}", status=500)
            
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
                    P.logger.info("[재생 종료] YouTube 스트림 연결 해제")

            return Response(generate(), mimetype="video/MP2T")

        else:
            if not os.path.isfile(full_path):
                P.logger.error(f"[재생 실패] 로컬 파일 없음: {full_path}")
                return Response(f"File not found: {full_path}", status=404)
            
            P.logger.info(f"[재생 시작] 로컬 파일: {full_path}")
            return send_file(full_path, conditional=True)
            
    except Exception as e:
        P.logger.error(f"[재생 에러] {str(e)}")
        return Response("Playback Error", status=500)
