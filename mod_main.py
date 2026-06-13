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

    # 🌟 [수정된 부분] 함수 이름을 process_command 에서 process_ajax 로 변경했습니다!
    def process_ajax(self, sub, req):
        try:
            if sub == "get_list":
                ret = {"ret": "success", "list": logic.get_media_list(req)}
                return jsonify(ret)
            
            return super(ModuleMain, self).process_ajax(sub, req)
                
        except Exception as e:
            P.logger.error(f"Exception:{str(e)}")
            P.logger.error(traceback.format_exc())
            return jsonify({"ret": "error", "msg": str(e)})

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
