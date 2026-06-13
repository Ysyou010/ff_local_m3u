import os
import subprocess
import traceback
from base64 import urlsafe_b64encode, urlsafe_b64decode
from urllib.parse import urlencode
from flask import Response, stream_with_context, redirect
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
    media_path_raw = P.ModelSetting.get("media_path")
    if not media_path_raw:
        return []
        
    ext_setting = P.ModelSetting.get("extensions")
    valid_exts = tuple([x.strip().lower() for x in ext_setting.split(",")])
    
    file_list = []
    paths = [p.strip() for p in media_path_raw.split('\n') if p.strip()]
    
    for path in paths:
        # 🌟 업그레이드: http 로 시작하는 유튜브/외부 주소는 무조건 통과!
        if path.startswith("http://") or path.startswith("https://"):
            file_list.append(path)
        elif os.path.isfile(path):
            if path.lower().endswith(valid_exts):
                file_list.append(path.replace('\\', '/'))
        else:
            P.logger.error(f"[로컬 M3U] 파일이 존재하지 않거나 폴더 경로입니다 (무시됨): {path}")
            
    return file_list

def get_media_list(req):
    files = get_media_files()
    result = []
    
    for idx, full_path in enumerate(files, 1):
        encoded_name = _safe_b64encode(full_path)
        play_url = get_api_url(req, "play", {"file": encoded_name})
        
        # 주소 형태인 경우 파일명이 없으므로 보기 좋게 라벨링 처리
        if full_path.startswith("http"):
            display_name = f"YouTube Stream [{idx}]"
        else:
            display_name = os.path.basename(full_path)
        
        result.append({
            "idx": idx,
            "name": display_name,
            "url": play_url
        })
        
    return result

def make_m3u(req):
    try:
        files = get_media_files()
        lines = ["#EXTM3U\n"]
        
        for index, full_path in enumerate(files, 1):
            encoded_name = _safe_b64encode(full_path)
            play_url = get_api_url(req, "play", {"file": encoded_name})
            
            if full_path.startswith("http"):
                display_name = f"YouTube Stream [{index}]"
            else:
                display_name = os.path.basename(full_path)
            
            lines.append(f'#EXTINF:-1 tvg-name="{display_name}" tvg-chno="{index}",{display_name}\n{play_url}\n')
            
        return Response("".join(lines), content_type="audio/mpegurl; charset=utf-8")
    except Exception as e:
        P.logger.error(traceback.format_exc())
        return Response(f"make_m3u 생성 중 에러: {str(e)}", status=500)

def play_ffmpeg_copy(encoded_name):
    try:
        P.logger.info("========== [재생 준비 단계] ==========")
        full_path = _safe_b64decode(encoded_name)
        P.logger.info(f"-> 암호 해독된 최종 재생 시도 경로: {full_path}")
        
        # 🌟 업그레이드: 유튜브/외부 주소 처리 로직 (yt-dlp 활용)
        if full_path.startswith("http://") or full_path.startswith("https://"):
            P.logger.info("-> 외부 스트리밍 주소가 감지되었습니다. yt-dlp로 원본 추출을 시도합니다.")
            try:
                import yt_dlp
                # 라이브 스트림과 VOD 모두 안정적으로 가져오는 포맷 설정
                ydl_opts = {
                    'format': 'best/best[ext=mp4]', 
                    'quiet': True, 
                    'noplaylist': True
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(full_path, download=False)
                    stream_url = info.get('url', None)
                    
                    if stream_url:
                        P.logger.info("-> [성공] 스트림 주소 추출 완료! 플레이어를 리다이렉트합니다.")
                        # 앱이나 플레이어에게 진짜 스트림 주소로 가라고 302 신호를 보냅니다.
                        return redirect(stream_url, code=302)
                    else:
                        P.logger.error("-> [실패] 스트림 주소를 추출하지 못했습니다.")
                        return Response("유튜브 스트림 추출 실패", status=500)
                        
            except ImportError:
                P.logger.error("-> [실패] yt-dlp 모듈이 설치되어 있지 않습니다.")
                return Response("yt-dlp 모듈이 설치되어 있지 않습니다. 도커 컨테이너에 pip install yt-dlp를 실행하세요.", status=500)
            except Exception as e:
                P.logger.error(f"-> [실패] 유튜브 추출 중 에러: {e}")
                P.logger.error(traceback.format_exc())
                return Response(f"유튜브 추출 중 에러: {e}", status=500)

        # 이하 기존 로컬 파일 FFmpeg 처리 로직 동일
        if not os.path.isfile(full_path):
            P.logger.error(f"-> [실패] 실제 파일이 존재하지 않습니다!! (404 Error)")
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
        
        P.logger.info(f"FFmpeg 전송 시작: {full_path}")
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
                P.logger.info(f"FFmpeg 전송 종료: {full_path}")

        return Response(generate(), mimetype="video/MP2T")
        
    except Exception as e:
        P.logger.error(f"재생 처리 중 에러 발생: {str(e)}")
        P.logger.error(traceback.format_exc())
        return Response("Playback Error", status=500)
