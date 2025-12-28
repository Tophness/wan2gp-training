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
        self.version = "1.1.2"
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
        current_path = self.config.get("install_path", "")
        path_state = gr.State(value=current_path)

        @gr.render(inputs=[path_state])
        def render_content(path):
            is_valid = False
            if path and os.path.isdir(path):
                if os.path.exists(os.path.join(path, "src", "musubi_tuner", "gui", "gui.py")):
                    is_valid = True
            
            if is_valid:
                self.render_musubi_ui(path, path_state)
            else:
                self.render_installer_ui(path, path_state)

    def render_installer_ui(self, current_path, path_state):
        with gr.Blocks() as installer:
            gr.Markdown("## Musubi Tuner Installation")
            gr.Markdown("Musubi Tuner is required to enable training features. You can install it automatically or select an existing folder.")
            
            with gr.Row():
                default_path = os.path.join(os.path.dirname(__file__), DEFAULT_INSTALL_DIR_NAME)
                path_input = gr.Textbox(
                    label="Installation Path (Existing or Target for new install)", 
                    value=current_path or default_path,
                    scale=4
                )
                save_refresh_btn = gr.Button("Save Path & Load", scale=1)

            with gr.Row():
                install_btn = gr.Button("Clone & Install Musubi Tuner", variant="primary")
                
            status_box = gr.Textbox(label="Status", interactive=False)

            def install_musubi(target_path):
                if not target_path:
                    yield "Please specify a path."
                    return
                
                target_path = os.path.abspath(target_path)

                if not os.path.exists(os.path.join(target_path, ".git")):
                    try:
                        yield f"Cloning {MUSUBI_REPO_URL} into {target_path}..."
                        subprocess.check_call(["git", "clone", MUSUBI_REPO_URL, target_path])
                    except Exception as e:
                        yield f"Error cloning git repo: {e}"
                        return

                pyproject_file = os.path.join(target_path, "pyproject.toml")
                if os.path.exists(pyproject_file):
                    try:
                        yield "Installing dependencies via 'pip install -e .' (this may take a while)..."
                        # Run pip install -e . inside the target directory
                        subprocess.check_call(
                            [sys.executable, "-m", "pip", "install", "-e", "."], 
                            cwd=target_path
                        )
                    except Exception as e:
                        yield f"Error installing dependencies: {e}"
                        return
                
                self.save_config(target_path)
                yield "Installation Complete. Loading interface..."

            def save_only(path):
                self.save_config(path)
                return "Path saved. Attempting to load..."

            install_btn.click(
                install_musubi, inputs=[path_input], outputs=[status_box]
            ).success(
                fn=lambda p: p, inputs=[path_input], outputs=[path_state]
            )

            save_refresh_btn.click(
                save_only, inputs=[path_input], outputs=[status_box]
            ).then(
                fn=lambda p: p, inputs=[path_input], outputs=[path_state]
            )

    def render_musubi_ui(self, musubi_path, path_state):

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
                gr.Markdown(f"## Error Loading Musubi\nCould not import musubi modules. Setup may be corrupt.\nError: {e}")
                return

            try:
                musubi_gui.construct_ui()

                gr.Markdown("---")
                with gr.Accordion("Musubi Installation Management", open=False):
                    with gr.Row(variant="panel"):
                        path_edit = gr.Textbox(label="Installation Path", value=musubi_path, scale=4)
                        update_path_btn = gr.Button("Save Path", scale=1)
                        git_update_btn = gr.Button("Update from GitHub", scale=1)
                    
                    update_log = gr.Textbox(label="Logs", visible=False, lines=5)

                    def do_update_path(new_path):
                        self.save_config(new_path)
                        return new_path

                    def do_git_update():
                        log = []
                        try:
                            log.append("Executing: git pull")
                            result = subprocess.run(
                                ["git", "pull"], 
                                cwd=musubi_path, 
                                capture_output=True, 
                                text=True
                            )
                            log.append(result.stdout)
                            if result.stderr:
                                log.append(f"STDERR: {result.stderr}")
                            
                            if result.returncode != 0:
                                log.append("Git pull failed.")
                                return "\n".join(log), gr.update(visible=True)

                            pyproject_path = os.path.join(musubi_path, "pyproject.toml")
                            if os.path.exists(pyproject_path):
                                log.append("\nUpdating dependencies (pip install -e .)...")
                                try:
                                    pip_res = subprocess.run(
                                        [sys.executable, "-m", "pip", "install", "-e", "."],
                                        cwd=musubi_path,
                                        capture_output=True,
                                        text=True
                                    )
                                    log.append(pip_res.stdout)
                                    if pip_res.returncode != 0:
                                        log.append(f"Pip error: {pip_res.stderr}")
                                except Exception as e:
                                    log.append(f"Pip exception: {e}")
                            
                            log.append("\nUpdate process finished.")

                        except Exception as e:
                            log.append(f"Critical Error: {str(e)}")
                        
                        return "\n".join(log), gr.update(visible=True)

                    update_path_btn.click(
                        do_update_path, inputs=[path_edit], outputs=[path_state]
                    )

                    git_update_btn.click(do_git_update, outputs=[update_log, update_log])
                
            except Exception as e:
                import traceback
                trace = traceback.format_exc()
                gr.Markdown(f"## Runtime Error\nFailed to construct Musubi UI.\n```\n{trace}\n```")

        finally:
            os.chdir(original_cwd)

    def on_tab_select(self, state):
        pass
