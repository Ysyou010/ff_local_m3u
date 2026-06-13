def play_ffmpeg_copy(encoded_name):
    try:
        P.logger.info("========== [재생 준비 단계] ==========")
        full_path = _safe_b64decode(encoded_name)
        P.logger.info(f"-> 최초 요청 경로: {full_path}")
        
        # 유튜브 우회를 위한 기본 브라우저 위장 설정
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
        
        # 1. 외부 스트림 (유튜브 VOD 및 라이브) 처리 로직
        if full_path.startswith("http://") or full_path.startswith("https://"):
            P.logger.info("-> 외부 스트림 감지. yt-dlp로 라이브/VOD 원본 주소 추출 시도")
            try:
                import yt_dlp
                # 'best' 옵션: 영상+음성이 합쳐진 최고 화질 선택 (라이브의 경우 M3U8 자동 선택)
                ydl_opts = {'format': 'best', 'quiet': True, 'noplaylist': True}
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(full_path, download=False)
                    # 라이브 스트림은 보통 manifest_url에, VOD는 url에 진짜 주소가 담깁니다.
                    stream_url = info.get('url') or info.get('manifest_url')
                    
                    if stream_url:
                        P.logger.info("-> [성공] 스트림 주소 확보. FFmpeg 프록시 스트리밍을 시작합니다.")
                        full_path = stream_url # ffmpeg가 읽을 입력 주소를 추출된 주소로 덮어치기
                        
                        # 403 차단 방지를 위해 yt-dlp가 사용한 진짜 User-Agent 훔쳐오기
                        headers = info.get('http_headers', {})
                        if headers and 'User-Agent' in headers:
                            user_agent = headers['User-Agent']
                    else:
                        return Response("유튜브 스트림 추출 실패", status=500)
            except Exception as e:
                P.logger.error(f"-> [실패] 유튜브 추출 중 에러: {e}")
                P.logger.error(traceback.format_exc())
                return Response(f"유튜브 에러: {e}", status=500)
        
        # 2. 로컬 파일인 경우 검증
        else:
            if not os.path.isfile(full_path):
                P.logger.error(f"-> [실패] 실제 파일이 존재하지 않습니다!! (404 Error)")
                return Response(f"File not found: {full_path}", status=404)

        # 3. FFmpeg 명령어 조립
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
        
        if not full_path.startswith("http"):
            # 로컬 파일은 속도 조절(-re) 필수
            cmd.append("-re")
        else:
            # 유튜브 등 외부 스트림은 차단 방지를 위해 위장 헤더 추가 (-re는 버퍼링 방지를 위해 생략)
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
        
    except Exception as e:
        P.logger.error(f"재생 처리 에러: {str(e)}")
        P.logger.error(traceback.format_exc())
        return Response("Playback Error", status=500)
