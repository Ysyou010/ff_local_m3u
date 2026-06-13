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

# --- logic.py 기존 코드 유지 부분 생략 ---

def get_media_files(target_category=None):
    media_path_raw = P.ModelSetting.get("media_path")
    if not media_path_raw:
        return []
        
    ext_setting = P.ModelSetting.get("extensions")
    valid_exts = tuple([x.strip().lower() for x in ext_setting.split(",")])
    
    file_list = []
    paths = [p.strip() for p in media_path_raw.split('\n') if p.strip()]
    
    for line in paths:
        # 데이터 분리 (카테고리|제목|경로)
        parts = line.split('|')
        if len(parts) >= 3:
            category = parts[0].strip()
            title = parts[1].strip()
            path = parts.slice(2).join('|').strip() if hasattr(parts, 'slice') else '|'.join(parts[2:]).strip()
        elif len(parts) == 2:
            category = "기본"
            title = parts[0].strip()
            path = parts[1].strip()
        else:
            category = "기본"
            title = ""
            path = line.strip()
            
        if not title:
            title = "YouTube Stream" if path.startswith("http") else os.path.basename(path)

        # ★ 요청한 카테고리가 있고, 'all'이 아니며, 현재 줄의 카테고리와 다르면 건너뜀!
        if target_category and target_category != 'all' and category != target_category:
            continue
            
        if path.startswith("http://") or path.startswith("https://"):
            file_list.append({"category": category, "title": title, "path": path})
        elif os.path.isfile(path):
            if path.lower().endswith(valid_exts):
                file_list.append({"category": category, "title": title, "path": path.replace('\\', '/')})
        else:
            P.logger.error(f"[로컬 M3U] 파일이 없거나 폴더입니다: {path}")
            
    return file_list

def get_media_list(req):
    files = get_media_files('all') # 플러그인 자체 목록 화면에서는 무조건 전체 표시
    result = []
    for idx, item in enumerate(files, 1):
        encoded_name = _safe_b64encode(item['path'])
        play_url = get_api_url(req, "play", {"file": encoded_name})
        
        display_name = f"[{item['category']}] {item['title']}"
        result.append({
            "idx": idx,
            "name": display_name,
            "url": play_url
        })
    return result

def make_m3u(req):
    try:
        # URL에서 요청한 재생목록 ID(카테고리명)를 가져옴. 파라미터가 없으면 'all' (전체)
        target_category = req.args.get('id', 'all')
        
        # 해당 카테고리만 필터링해서 가져오기
        files = get_media_files(target_category)
        lines = ["#EXTM3U\n"]
        
        for index, item in enumerate(files, 1):
            encoded_name = _safe_b64encode(item['path'])
            play_url = get_api_url(req, "play", {"file": encoded_name})
            
            display_name = item['title']
            
            lines.append(f'#EXTINF:-1 tvg-name="{display_name}" tvg-chno="{index}",{display_name}\n{play_url}\n')
            
        return Response("".join(lines), content_type="audio/mpegurl; charset=utf-8")
    except Exception as e:
        P.logger.error(traceback.format_exc())
        return Response(f"make_m3u 생성 중 에러: {str(e)}", status=500)

# --- 이하 play_ffmpeg_copy 함수는 기존 코드 유지 ---

def play_ffmpeg_copy(encoded_name):
    try:
        P.logger.info("========== [재생 준비 단계] ==========")
        full_path = _safe_b64decode(encoded_name)
        P.logger.info(f"-> 최초 요청 경로: {full_path}")
        
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
        
        # 1. 유튜브 등 외부 스트림 처리
        if full_path.startswith("http://") or full_path.startswith("https://"):
            P.logger.info("-> 외부 스트림 감지. yt-dlp로 라이브/VOD 원본 주소 추출 시도")
            try:
                import yt_dlp
                ydl_opts = {'format': 'best', 'quiet': True, 'noplaylist': True}
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(full_path, download=False)
                    stream_url = info.get('url') or info.get('manifest_url')
                    
                    if stream_url:
                        P.logger.info("-> [성공] 스트림 주소 확보. FFmpeg 프록시 스트리밍을 시작합니다.")
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
            
            # 외부 스트림은 구간 탐색 없이 FFmpeg로 실시간 중계 (기존과 동일)
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

        # 2. 로컬 파일 처리 (★ 구간 탐색 완벽 지원 ★)
        else:
            if not os.path.isfile(full_path):
                P.logger.error(f"-> [실패] 실제 파일이 존재하지 않습니다!! (404 Error)")
                return Response(f"File not found: {full_path}", status=404)
            
            P.logger.info(f"-> [로컬 파일] 다이렉트 전송 시작 (구간 탐색/HTTP Range 지원)")
            # conditional=True 속성이 안드로이드/팟플레이어의 앞뒤 이동(Seek) 요청을 완벽하게 처리해 줍니다.
            return send_file(full_path, conditional=True)
            
    except Exception as e:
        P.logger.error(f"재생 처리 에러: {str(e)}")
        P.logger.error(traceback.format_exc())
        return Response("Playback Error", status=500)
