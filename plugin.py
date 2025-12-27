import os
import sys
import json
import gradio as gr
import subprocess
import shutil
from shared.utils.plugins import WAN2GPPlugin

MUSUBI_REPO_URL = "https://github.com/kohya-ss/musubi-tuner.git"
DEFAULT_INSTALL_DIR_NAME = "musubi-tuner"

class MusubiTrainingPlugin(WAN2GPPlugin):
    def __init__(self):
        super().__init__()
        self.name = "Musubi Tuner Training"
        self.version = "1.0.0"
        self.description = "Integrates Kohya-ss Musubi Tuner for Wan2.1 training directly into Wan2GP."
        self.config_file = os.path.join(os.path.dirname(__file__), "config.json")
        self.config = self.load_config()

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
        self.add_tab(
            tab_id="musubi_training",
            label="Training",
            component_constructor=self.create_ui,
            position=2 
        )

    def create_ui(self):
        path = self.config.get("install_path", "")
        is_installed = False
        
        if path and os.path.exists(os.path.join(path, "src", "musubi_tuner", "gui", "gui.py")):
            is_installed = True

        if is_installed:
            return self.render_musubi_ui(path)
        else:
            return self.render_installer_ui(path)

    def render_installer_ui(self, current_path):
        with gr.Blocks() as installer:
            gr.Markdown("## Musubi Tuner Installation")
            gr.Markdown("Musubi Tuner is required to enable training features. You can install it automatically or select an existing folder.")
            
            with gr.Row():
                path_input = gr.Textbox(
                    label="Installation Path (Existing or Target for new install)", 
                    value=current_path or os.path.join(os.path.dirname(__file__), DEFAULT_INSTALL_DIR_NAME),
                    scale=4
                )
                browse_btn = gr.Button("Save Path / Refresh", scale=1)

            with gr.Row():
                install_btn = gr.Button("Clone & Install Musubi Tuner", variant="primary")
                
            status_box = gr.Textbox(label="Status", interactive=False)

            def install_musubi(target_path):
                if not target_path:
                    return "Please specify a path."
                
                target_path = os.path.abspath(target_path)

                if not os.path.exists(os.path.join(target_path, ".git")):
                    try:
                        yield f"Cloning {MUSUBI_REPO_URL} into {target_path}..."
                        subprocess.check_call(["git", "clone", MUSUBI_REPO_URL, target_path])
                    except Exception as e:
                        yield f"Error cloning git repo: {e}"
                        return

                req_file = os.path.join(target_path, "requirements.txt")
                if os.path.exists(req_file):
                    try:
                        yield "Installing requirements (this may take a while)..."
                        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req_file])
                    except Exception as e:
                        yield f"Error installing requirements: {e}"
                        return
                
                self.save_config(target_path)
                yield "Installation Complete. Please restart Wan2GP to load the UI."

            def save_and_refresh(path):
                self.save_config(path)
                return "Path saved. If valid, restart Wan2GP to load the interface."

            install_btn.click(install_musubi, inputs=[path_input], outputs=[status_box])
            browse_btn.click(save_and_refresh, inputs=[path_input], outputs=[status_box])

        return installer

    def render_musubi_ui(self, musubi_path):
        src_path = os.path.join(musubi_path, "src")
        if src_path not in sys.path:
            sys.path.insert(0, src_path)

        original_cwd = os.getcwd()
        
        try:
            os.chdir(musubi_path)

            try:
                import musubi_tuner.gui.gui as musubi_gui
                from musubi_tuner.gui.i18n_data import I18N_DATA

                def forced_i18n(key):
                    return I18N_DATA.get("en", {}).get(key, key)

                musubi_gui.i18n = forced_i18n

            except ImportError as e:
                os.chdir(original_cwd)
                return gr.Markdown(f"## Error Loading Musubi\nCould not import musubi modules. Setup may be corrupt.\nError: {e}")

            try:
                demo = musubi_gui.construct_ui()
                
                gr.Markdown(f"--- \n*Musubi Tuner loaded from: {musubi_path}*")
                
            except Exception as e:
                import traceback
                trace = traceback.format_exc()
                gr.Markdown(f"## Runtime Error\nFailed to construct Musubi UI.\n```\n{trace}\n```")

        finally:
            os.chdir(original_cwd)
            
        return None 

    def on_tab_select(self, state):
        pass