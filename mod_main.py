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
        # 1. DB 값을 가져옵니다.
        arg = P.ModelSetting.to_dict()
        
        # 2. DB 초기화 지연/누락으로 키가 없을 경우, 기본값을 안전하게 채워 넣습니다.
        if arg is None:
            arg = {}
        for key, value in self.db_default.items():
            if key not in arg:
                arg[key] = value

        # 3. M3U API 주소를 생성합니다. (명확한 request 객체 사용)
        host_url = request.host_url.rstrip('/')
        arg["api_m3u"] = f"{host_url}/{P.package_name}/api/m3u"
        
        # 4. 화면을 렌더링합니다.
        return render_template(f"{P.package_name}_{self.name}_{sub}.html", arg=arg)

    def process_command(self, command, arg1, arg2, arg3, req):
        ret = {"ret": "success"}
        try:
            if command == "get_list":
                ret["list"] = logic.get_media_list(req)
                
        except Exception as e:
            P.logger.error(f"Exception:{str(e)}")
            P.logger.error(traceback.format_exc())
            ret = {"ret": "error", "msg": str(e)}
            
        return jsonify(ret)

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
