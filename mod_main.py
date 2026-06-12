import traceback
from flask import Response, request, jsonify, render_template
from .setup import *
from . import logic

class ModuleMain(PluginModuleBase):
    def __init__(self, P):
        super(ModuleMain, self).__init__(P, name="main", first_menu="setting")
        self.db_default = {
            f"{self.name}_db_version": "1",
            "media_path": "/home/ysyou/docker/media",
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

            # logic.py의 범용 API URL 생성기를 사용하여 안전하게 주소 생성
            arg["api_m3u"] = logic.get_api_url(req, "m3u")
            
            return render_template(f"{P.package_name}_{self.name}_{sub}.html", arg=arg)
            
        except Exception as e:
            import traceback
            error_msg = f"<h1>에러 원인 분석</h1><pre>{traceback.format_exc()}</pre>"
            P.logger.error(traceback.format_exc())
            return error_msg

    def process_command(self, command, arg1, arg2, arg3, req):
        try:
            if command == "get_list":
                ret = {"ret": "success", "list": logic.get_media_list(req)}
                return jsonify(ret)
            
            # 프레임워크의 기본 명령어(설정 저장 등)를 정상적으로 처리하기 위해 부모 클래스 호출
            return super(ModuleMain, self).process_command(command, arg1, arg2, arg3, req)
                
        except Exception as e:
            P.logger.error(f"Exception:{str(e)}")
            P.logger.error(traceback.format_exc())
            return jsonify({"ret": "error", "msg": str(e)})

    def process_api(self, sub, req):
        try:
            if sub == "m3u":
                return logic.make_m3u(req)
            
            if sub.startswith("play/ffmpeg/"):
                encoded_name = sub.split("play/ffmpeg/")[1]
                return logic.play_ffmpeg_copy(encoded_name)
                
        except Exception as e:
            P.logger.error(f"Exception:{str(e)}")
            P.logger.error(traceback.format_exc())
            return Response(str(e), status=500, mimetype="text/plain")
