import traceback
from flask import Response, request, jsonify, render_template
from .setup import *
from . import logic

class ModuleMain(PluginModuleBase):
    def __init__(self, P):
        super(ModuleMain, self).__init__(P, name="main", first_menu="setting")
        self.db_default = {
            f"{self.name}_db_version": "1",
            "media_path": "",
            "extensions": ".mp4,.mkv,.avi,.ts",
        }

    def plugin_load(self):
        P.logger.info("Local Media M3U Plugin Loaded")

    def process_menu(self, sub, req):
        try:
            arg = P.ModelSetting.to_dict()
            if arg is None:
                arg = {}
            for key, value in self.db_default.items():
                if key not in arg:
                    arg[key] = value

            arg["api_m3u"] = logic.get_api_url(req, "m3u")
            
            return render_template(f"{P.package_name}_{self.name}_{sub}.html", arg=arg)
            
        except Exception as e:
            import traceback
            error_msg = f"<h1>에러 원인 분석</h1><pre>{traceback.format_exc()}</pre>"
            P.logger.error(traceback.format_exc())
            return error_msg

    # 🌟 AJAX 디버깅 전용 상세 로그 심기
    def process_ajax(self, sub, req):
        try:
            P.logger.info(f"==================================================")
            P.logger.info(f"[디버그 로그] process_ajax 진입 완료 -> sub 값: {sub}")
            P.logger.info(f"==================================================")
            
            if sub == "get_list":
                P.logger.info("[디버그 로그] get_list 조건 일치. logic.get_media_list(req) 호출합니다.")
                list_data = logic.get_media_list(req)
                
                P.logger.info(f"[디버그 로그] 호출 결과 데이터 성공적 수신. 아이템 수: {len(list_data)}개")
                ret = {"ret": "success", "list": list_data}
                return jsonify(ret)
            
            P.logger.info(f"[디버그 로그] 알 수 없는 sub값 ({sub}) 처리 요청 상위 클래스로 위임")
            return super(ModuleMain, self).process_ajax(sub, req)
                
        except Exception as e:
            # 주방장 구역에서 터지면 원인을 추적해서 화면으로 강제 반환
            P.logger.error(f"[치명적 오류] process_ajax 실행 도중 크래시 발생!!")
            P.logger.error(traceback.format_exc())
            return jsonify({"ret": "error", "msg": f"서버 로직 크래시: {str(e)}\n{traceback.format_exc()}"})

    def process_api(self, sub, req):
        try:
            P.logger.info(f"========== [API 요청 수신] sub: {sub} ==========")
            
            if sub == "m3u" or sub.startswith("m3u"):
                ret = logic.make_m3u(req)
                if ret is None:
                    return Response("make_m3u 함수가 반환값이 없습니다.", status=500)
                return ret
            
            if sub == "play":
                encoded_name = req.args.get("file")
                if not encoded_name:
                    return Response("파일 파라미터(?file=)가 없습니다.", status=400)
                    
                ret = logic.play_ffmpeg_copy(encoded_name)
                if ret is None:
                    return Response("play_ffmpeg_copy 함수가 반환값이 없습니다.", status=500)
                return ret
                
            return Response(f"알 수 없는 API 요청입니다. (입력된 sub 값: {sub})", status=400)
            
        except Exception as e:
            P.logger.error(f"Exception:{str(e)}")
            P.logger.error(traceback.format_exc())
            return Response(str(e), status=500, mimetype="text/plain")
