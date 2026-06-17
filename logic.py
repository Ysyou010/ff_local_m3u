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
        # 🌟 1순위: Flaskfarm 시스템에 설정된 DDNS 주소
        ddns = SystemModelSetting.get('ddns')
        if ddns and ddns.strip():
            return ddns.strip().rstrip("/")
    except Exception as e:
        P.logger.error(f"DDNS 주소 가져오기 실패: {str(e)}")
        pass

    try:
        # 🌟 2순위: 현재 접속 중인 주소
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
            
            ext = os.path.splitext(item['path'])[1] if not item['path'].startswith('http') else '.mkv'
            play_url = get_api_url(req, "play", {"file": encoded_name, "ext": ext})
            
            display_name = f"[{item['category']}] {item['title']}"
            result.append({
                "idx": idx,
                "name": display_name,
                "url": play_url,
                "raw_category": item['category'],
                "raw_title": item['title'],
                "raw_quality": item['quality'],
                "raw_path": item['path']
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
            
            ext = os.path.splitext(item['path'])[1] if not item['path'].startswith('http') else '.mkv'
            play_url = get_api_url(req, "play", {"file": encoded_name, "ext": ext})
            
            display_name = item['title']

            sub_url = ""
            if not item['path'].startswith('http'):
                base_path = os.path.splitext(item['path'])[0]
                
                possible_srts = [
                    base_path + ".srt",
                    base_path + ".ko.srt",
                    base_path + ".kr.srt",
                    base_path + ".kor.srt"
                ]
                
                for srt_path in possible_srts:
                    if os.path.isfile(srt_path):
                        encoded_sub = _safe_b64encode(srt_path)
                        sub_url = get_api_url(req, "subtitle", {"file": encoded_sub, "ext": ".srt"})
                        break 

            if sub_url:
                lines.append(f'#EXTINF:-1 tvg-name="{display_name}" tvg-chno="{index}" subtitle="{sub_url}",{display_name}\n')
                lines.append(f'#EXTVLCOPT:sub-file={sub_url}\n')
                lines.append(f'{play_url}\n')
            else:
                lines.append(f'#EXTINF:-1 tvg-name="{display_name}" tvg-chno="{index}",{display_name}\n')
                lines.append(f'{play_url}\n')
            
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
        
        cmd = []
        mimetype = "video/MP2T"

        # ==========================================
        # 1. 로컬 파일 처리
        # ==========================================
        if not (full_path.startswith("http://") or full_path.startswith("https://")):
            if not os.path.isfile(full_path):
                P.logger.error(f"[재생 실패] 로컬 파일 없음: {full_path}")
                return Response(f"File not found: {full_path}", status=404)
            
            # [ExoPlayer 호환성 수정] send_file 대신 완벽한 호환을 위해 TS 스트림으로 변환 송출
            P.logger.info(f"[재생 시작] 로컬 파일 IPTV TS 패키징 (ExoPlayer 대응): {full_path}")
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
            cmd.extend(["-re", "-i", full_path])
            cmd.extend([
                "-map", "0:v:0?", "-map", "0:a:0?",
                "-c:v", "copy", "-c:a", "copy",
                "-fflags", "+genpts",
                "-mpegts_flags", "resend_headers",
                "-pcr_period", "40",
                "-muxdelay", "0", "-f", "mpegts", "-"
            ])
            
        # ==========================================
        # 2. 유튜브 처리
        # ==========================================
        else:
            P.logger.info(f"[재생 요청] YouTube: {full_path} (요청 화질: {quality})")
            try:
                import yt_dlp
                
                ydl_opts = {
                    'quiet': True, 
                    'noplaylist': True,
                    'extractor_args': {'youtube': ['player_client=android,ios,tv,web']} 
                }
                
                if quality in ["720p", "480p", "360p", "240p", "144p"]:
                    ydl_opts['format'] = 'b'
                    max_height = quality[:-1]
                    ydl_opts['format_sort'] = [f'res:{max_height}']
                else:
                    ydl_opts['format'] = 'bv*+ba/b'
                    if quality != "자동" and quality.endswith('p') and quality[:-1].isdigit():
                        max_height = quality[:-1]
                        ydl_opts['format_sort'] = [f'res:{max_height}']
                
                # 중복 호출된 with 블록을 하나로 정리
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(full_path, download=False)
                    stream_url = info.get('url') or info.get('manifest_url')
                    is_live = info.get('is_live', False)
                    req_formats = info.get('requested_formats')
                    
                    # 1080p 고화질 (오디오/비디오 분리본) MKV 병합
                    if req_formats and len(req_formats) == 2 and not is_live:
                        video_url = req_formats[0].get('url')
                        audio_url = req_formats[1].get('url')
                        
                        headers = req_formats[0].get('http_headers', {})
                        if headers and 'User-Agent' in headers:
                            user_agent = headers['User-Agent']
                            
                        P.logger.info(f"[재생 시작] YouTube 고화질 VOD - MKV 수동 병합 중계")
                        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
                        cmd.extend(["-user_agent", user_agent])
                        cmd.extend([
                            "-i", video_url,
                            "-i", audio_url,
                            "-map", "0:v:0", "-map", "1:a:0",
                            "-c:v", "copy", "-c:a", "copy",
                            "-f", "matroska", "-"
                        ])
                        mimetype = "video/x-matroska"
                    
                    # 720p 이하 VOD 또는 라이브 방송 단일 스트림 처리
                    else:
                        if not stream_url:
                            P.logger.error("[재생 실패] 유튜브 스트림 추출 실패")
                            return Response("유튜브 스트림 추출 실패", status=500)
                            
                        headers = info.get('http_headers', {})
                        if headers and 'User-Agent' in headers:
                            user_agent = headers['User-Agent']
                            
                        # [ExoPlayer 호환성 수정] redirect 대신 TS 프록시 스트리밍으로 통일
                        P.logger.info(f"[재생 시작] YouTube 단일 스트림 TS 프록시 중계 (ExoPlayer 대응)")
                        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
                        cmd.extend(["-user_agent", user_agent])
                        cmd.extend([
                            "-i", stream_url,
                            "-map", "0:v:0?", "-map", "0:a:0?",
                            "-c:v", "copy", "-c:a", "copy",
                            "-fflags", "+genpts",
                            "-mpegts_flags", "resend_headers",
                            "-pcr_period", "40",
                            "-f", "mpegts", "-"
                        ])
                        mimetype = "video/MP2T"
                        
            except Exception as e:
                P.logger.error(f"[재생 실패] 유튜브 에러: {e}")
                P.logger.error(traceback.format_exc())
                return Response(f"유튜브 에러: {e}", status=500)
        
        # ==========================================
        # 3. 파이프라인 통합 실행
        # ==========================================
        # [ExoPlayer 호환성 수정] stderr=subprocess.DEVNULL 로 변경하여 버퍼 초과 데드락 방지
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        
        @stream_with_context
        def generate():
            try:
                while True:
                    chunk = proc.stdout.read(65536)
                    if not chunk:
                        break
                    yield chunk
            finally:
                if proc.poll() is None:
                    proc.kill()
                P.logger.info("[재생 종료] 스트림 연결 해제")

        response = Response(generate(), mimetype=mimetype)
        
        # [ExoPlayer 호환성 수정] 앱 접속 안정성을 위한 HTTP 응답 헤더 추가
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Connection'] = 'keep-alive'
        
        return response

    except Exception as e:
        P.logger.error(f"[재생 에러] {str(e)}")
        P.logger.error(traceback.format_exc())
        return Response("Playback Error", status=500)

def play_subtitle(encoded_name):
    try:
        full_path = _safe_b64decode(encoded_name)
        if not os.path.isfile(full_path):
            P.logger.error(f"[자막 실패] 파일 없음: {full_path}")
            return Response("Subtitle not found", status=404)
            
        P.logger.info(f"[자막 전송] {full_path}")
        return send_file(full_path, mimetype="text/plain", conditional=True)
        
    except Exception as e:
        P.logger.error(f"[자막 에러] {str(e)}")
        return Response("Subtitle Error", status=500)

def edit_media_item(idx, category, title, quality, path):
    try:
        media_path_raw = P.ModelSetting.get("media_path")
        lines = media_path_raw.split('\n')
        
        non_empty_count = 0
        for i, line in enumerate(lines):
            if line.strip():  
                non_empty_count += 1
                if non_empty_count == idx:
                    lines[i] = f"{category} | {title} | {quality} | {path}"
                    break
                    
        new_media_path = "\n".join(lines)
        P.ModelSetting.set("media_path", new_media_path)
        return True
    except Exception as e:
        P.logger.error(traceback.format_exc())
        return False
