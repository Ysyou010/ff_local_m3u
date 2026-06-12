import traceback
from flask import Response, request, jsonify
from .setup import P
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
        P.logger.info("Local Media Plugin Loaded")

    def process_api(self, sub, req):
        try:
            if sub == "m3u":
                return logic.make_m3u(req)
            
            # /api/play/ffmpeg/<encoded_name> 라우트 처리
            if sub.startswith("play/ffmpeg/"):
                encoded_name = sub.split("play/ffmpeg/")[1]
                return logic.play_ffmpeg_copy(encoded_name)
                
        except Exception as e:
            P.logger.error(f"Exception:{str(e)}")
            P.logger.error(traceback.format_exc())
            return Response(str(e), status=500, mimetype="text/plain")

      def process_menu(self, sub, req):
        arg = P.ModelSetting.to_dict()
        
        # UI에 보여줄 M3U API 주소 생성
        host_url = req.host_url.rstrip('/')
        arg["api_m3u"] = f"{host_url}/{P.package_name}/api/m3u"
        
        if sub == "setting":
            return render_template(f"{P.package_name}_{self.name}_{sub}.html", arg=arg)
        return render_template(f"{P.package_name}_{self.name}_{sub}.html", arg=arg)
