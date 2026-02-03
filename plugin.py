import os
import sys
import json
import gradio as gr
import subprocess
import shutil
import traceback
import socket
import time
import atexit
import signal
from shared.utils.plugins import WAN2GPPlugin
from shared.utils.process_locks import acquire_GPU_ressources, release_GPU_ressources, any_GPU_process_running

MUSUBI_REPO_URL = "https://github.com/Tophness/musubi-tuner.git"
DEFAULT_INSTALL_DIR_NAME = "musubi-tuner"

def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

class MusubiTrainingPlugin(WAN2GPPlugin):
    def __init__(self):
        super().__init__()
        self.plugin_id = "musubi_training"
        self.config_file = os.path.join(os.path.dirname(__file__), "config.json")
        self.config = self.load_config()
        
        self.musubi_process = None
        self.musubi_port = None
        self.load_trigger = None 

        atexit.register(self.cleanup)

    def cleanup(self):
        if self.musubi_process:
            print(f"[Musubi] Killing background training server (PID: {self.musubi_process.pid})...")
            try:
                self.musubi_process.terminate()
                try:
                    self.musubi_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.musubi_process.kill()
            except Exception as e:
                print(f"[Musubi] Error cleanup: {e}")
            self.musubi_process = None

    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as f:
                    return json.load(f)
            except:
                pass
        local_install = os.path.join(os.path.dirname(__file__), DEFAULT_INSTALL_DIR_NAME)
        if os.path.exists(os.path.join(local_install, "src", "musubi_tuner", "gui", "gui.py")):
            return {"install_path": local_install}
        return {"install_path": ""}

    def save_config(self, path):
        self.config["install_path"] = path
        with open(self.config_file, "w") as f:
            json.dump(self.config, f, indent=4)

    def setup_ui(self):
        self.request_component("state")
        self.add_tab(
            tab_id="musubi_training",
            label="Training",
            component_constructor=self.create_ui,
            position=2 
        )

    def acquire_gpu(self, state):
        if any_GPU_process_running(state, self.plugin_id):
            pass
        acquire_GPU_ressources(state, self.plugin_id, self.name, gr=gr)

    def release_gpu(self, state):
        release_GPU_ressources(state, self.plugin_id)

    def _is_installed(self, path):
        return path and os.path.exists(os.path.join(path, "src", "musubi_tuner", "gui", "gui.py"))

    def create_ui(self):
        self.load_trigger = gr.State(False)
        self.on_tab_outputs = [self.load_trigger]

        current_path_val = self.config.get("install_path", "") or os.path.join(os.path.dirname(__file__), DEFAULT_INSTALL_DIR_NAME)
        path_state = gr.State(value=current_path_val)

        with gr.Column():
            interface_html = gr.HTML(value="<div style='padding:20px; text-align:center; color:gray'>Training interface will load when tab is selected...</div>")

            with gr.Accordion("Musubi Settings / Installation", open=not self._is_installed(current_path_val)) as settings_acc:
                with gr.Row():
                    path_input = gr.Textbox(label="Installation Path", value=current_path_val, scale=4)
                    save_path_btn = gr.Button("Save Path", scale=1)
                
                with gr.Row():
                    install_btn = gr.Button("Install / Reinstall / Update", variant="secondary", scale=1)
                    restart_btn = gr.Button("Restart Training Interface", variant="secondary", scale=1)
                
                status_box = gr.Textbox(label="System Log", interactive=False, lines=4)


        def update_path(new_path):
            self.save_config(new_path)
            is_valid = self._is_installed(new_path)
            msg = "Path saved." if is_valid else "Path saved (Warning: musubi-tuner not found at this location)."
            return new_path, msg, gr.Accordion(open=not is_valid)

        save_path_btn.click(
            update_path, inputs=[path_input], outputs=[path_state, status_box, settings_acc]
        )

        def install_musubi(target_path):
            if not target_path:
                yield "Please specify a path."
                return
            
            target_path = os.path.abspath(target_path)
            yield f"Starting operation on {target_path}..."

            try:
                if not os.path.exists(os.path.join(target_path, ".git")):
                    yield "Cloning repository..."
                    subprocess.check_call(["git", "clone", MUSUBI_REPO_URL, target_path])
                else:
                    yield "Repository exists. Pulling updates..."
                    subprocess.call(["git", "pull"], cwd=target_path)

                pyproject = os.path.join(target_path, "pyproject.toml")
                if os.path.exists(pyproject):
                    yield "Installing dependencies (pip install -e .)..."
                    subprocess.check_call([sys.executable, "-m", "pip", "install", "-e", "."], cwd=target_path)

                self.save_config(target_path)
                yield "Operation Complete. You may need to restart the interface."
            except Exception as e:
                yield f"Error: {e}"

        install_btn.click(
            install_musubi, inputs=[path_input], outputs=[status_box]
        ).success(
            fn=lambda p: p, inputs=[path_input], outputs=[path_state]
        )

        def launch_interface(triggered, path):
            if not triggered:
                return gr.update()
            
            if not self._is_installed(path):
                return gr.update(value="<div style='color:red; text-align:center; padding:20px'>Musubi Tuner not found at configured path. Please check settings below.</div>")

            if self.musubi_process:
                if self.musubi_process.poll() is None:
                    url = f"http://127.0.0.1:{self.musubi_port}"
                    return f'<iframe src="{url}" width="100%" height="1000px" style="border:none;"></iframe>'
                else:
                    self.musubi_process = None

            try:
                self.musubi_port = find_free_port()
                env = os.environ.copy()
                env["GRADIO_SERVER_PORT"] = str(self.musubi_port)
                env["PYTHONPATH"] = os.path.join(path, "src")
                env["GRADIO_SERVER_NAME"] = "127.0.0.1" 
                env["GRADIO_ANALYTICS_ENABLED"] = "False"
                
                cmd = [sys.executable, os.path.join(path, "src/musubi_tuner/gui/gui.py")]
                
                creationflags = 0
                if sys.platform == "win32":
                    creationflags = subprocess.CREATE_NO_WINDOW

                self.musubi_process = subprocess.Popen(
                    cmd, env=env, cwd=path, 
                    creationflags=creationflags
                )

                for _ in range(30):
                    time.sleep(0.5)
                    try:
                        with socket.create_connection(("127.0.0.1", self.musubi_port), timeout=1):
                            break
                    except:
                        pass
                
                url = f"http://127.0.0.1:{self.musubi_port}"
                return f'<iframe src="{url}" width="100%" height="1000px" style="border:none;"></iframe>'

            except Exception as e:
                traceback.print_exc()
                return f"<div style='color:red'>Error starting Musubi Tuner: {str(e)}</div>"

        gr.on(
            triggers=[self.load_trigger.change, restart_btn.click],
            fn=lambda: self.cleanup(),
            inputs=[], outputs=[]
        ).then(
            fn=launch_interface,
            inputs=[gr.State(True), path_state], # Force triggered=True for restart button
            outputs=[interface_html]
        )

    def on_tab_select(self, state):
        self.acquire_gpu(state)
        return True

    def on_tab_deselect(self, state):
        self.release_gpu(state)

