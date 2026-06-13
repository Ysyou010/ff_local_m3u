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
            P.logger.info("[디버그 logic] 저장된 미디어 경로 데이터가 비어있습니다.")
            return []
            
        ext_setting = P.ModelSetting.get("extensions")
        valid_exts = tuple([x.strip().lower() for x in ext_setting.split(",")])
        
        file_list = []
        paths = [p.strip() for p in media_path_raw.split('\n') if p.strip()]
        
        P.logger.info(f"[디버그 logic] 총 {len(paths)}개의 원본 라인을 파싱하기 시작합니다.")
        
        for line in paths:
            parts = line.split('|')
            
            # 파이썬 표준 슬라이싱 기법으로 문법적 결함 완벽 방어 처리 완료
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
            else:
                P.logger.error(f"[로컬 M3U 예외] 실제 경로에 파일이 존재하지 않아 무시됨: {path}")
                
        return file_list
    except Exception as e:
        P.logger.error(f"[디버그 logic] get_media_files 처리 도중 크래시 발생!")
        P.logger.error(traceback.format_exc())
        return []

def get_media_list(req):
    try:
        P.logger.info("[디버그 logic] get_media_list 함수 진입")
        files = get_media_files('all')
        P.logger.info(f"[디버그 logic] get_media_files 수집 완료. 수량: {len(files)}")
        
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
        P.logger.error(f"[디버그 logic] get_media_list에서 가공 도중 치명적 에러 발생!")
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
        return Response(f"make_m3u 생성 중 에러: {str(e)}", status=500)

def play_ffmpeg_copy(encoded_name):
    try:
        P.logger.info("========== [재생 준비 단계] ==========")
        full_str = _safe_b64decode(encoded_name)
        
        if "||" in full_str:
            quality, full_path = full_str.split("||", 1)
        else:
            quality = "자동"
            full_path = full_str
            
        P.logger.info(f"-> 최초 요청 경로: {full_path} (요청 화질: {quality})")
        
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
        
        if full_path.startswith("http://") or full_path.startswith("https://"):
            P.logger.info("-> 외부 스트림 감지. yt-dlp로 라이브/VOD 원본 주소 추출 시도")
            try:
                import yt_dlp
                format_str = 'best'
                if quality.endswith('p') and quality[:-1].isdigit():
                    max_height = quality[:-1]
                    format_str = f'best[height<={max_height}]/best'
                    
                ydl_opts = {'format': format_str, 'quiet': True, 'noplaylist': True}
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(full_path, download=False)
                    stream_url = info.get('url') or info.get('manifest_url')
                    
                    if stream_url:
                        P.logger.info(f"-> [성공] {quality} 스트림 주소 확보. FFmpeg 프록시를 시작합니다.")
                        full_path = stream_url
                        
                        headers = info.get('http_headers', {})
                        if headers and 'User-Agent' in headers:
                            user_agent = headers['User-Agent']
                    else:
                        return Response("유튜브 스트림 추출 실패", status=500)
            except Exception as e:
                P.logger.error(f"-> [실패] 유튜브 추출 중 에러: {e}")
                P.logger.error(traceback.format_exc())
                return Response(f"유튜브 에러: {e}", status=500)
            
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
            cmd.extend(["-user_agent", user_agent])
            cmd.extend([
                "-i", full_path,
                "-map", "0:v:0?", "-map", "0:a:0?",
                "-c:v", "copy", "-c:a", "copy",
                "-muxdelay", "0", "-f", "mpegts", "-"
            ])
            
            P.logger.info(f"FFmpeg 명령어 실행 준비 완료")
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
                    P.logger.info(f"FFmpeg 전송 종료 (사용자 접속 해제)")

            return Response(generate(), mimetype="video/MP2T")

        else:
            if not os.path.isfile(full_path):
                P.logger.error(f"-> [실패] 실제 파일이 존재하지 않습니다!! (404 Error)")
                return Response(f"File not found: {full_path}", status=404)
            
            P.logger.info(f"-> [로컬 파일] 다이렉트 전송 시작 (구간 탐색/HTTP Range 지원)")
            return send_file(full_path, conditional=True)
            
    except Exception as e:
        P.logger.error(f"재생 처리 에러: {str(e)}")
        P.logger.error(traceback.format_exc())
        return Response("Playback Error", status=500)
