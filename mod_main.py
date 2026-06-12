def process_menu(self, sub, req):
        try:
            arg = P.ModelSetting.to_dict()
            if arg is None:
                arg = {}
            for key, value in self.db_default.items():
                if key not in arg:
                    arg[key] = value

            host_url = req.host_url.rstrip('/')
            arg["api_m3u"] = f"{host_url}/{P.package_name}/api/m3u"
            
            return render_template(f"{P.package_name}_{self.name}_{sub}.html", arg=arg)
            
        except Exception as e:
            import traceback
            # 500 에러 대신 진짜 에러 원인을 화면에 텍스트로 출력합니다.
            error_msg = f"<h1>에러 원인 분석</h1><pre>{traceback.format_exc()}</pre>"
            P.logger.error(traceback.format_exc())
            return error_msg
