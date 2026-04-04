from __future__ import annotations

import subprocess

from gui.config import ModelCommand


class CommandModelAdapter:
    def __init__(self, model_cfg: ModelCommand):
        self.model_cfg = model_cfg

    def generate(self, prompt: str, cwd: str | None = None) -> str:
        args = []
        prompt_embedded = False
        for arg in self.model_cfg.args:
            if "{prompt}" in arg:
                prompt_embedded = True
                args.append(arg.replace("{prompt}", prompt))
            else:
                args.append(arg)

        cmd = [self.model_cfg.command] + args

        try:
            completed = subprocess.run(
                cmd,
                input=None if prompt_embedded else prompt,
                text=True,
                capture_output=True,
                cwd=cwd,
                timeout=self.model_cfg.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Model command not found: {self.model_cfg.command}. "
                "Install/configure the CLI first."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Model command timed out after {self.model_cfg.timeout_seconds}s"
            ) from exc

        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(f"Model command failed (exit {completed.returncode}): {err}")

        output = completed.stdout.strip()
        if not output:
            output = completed.stderr.strip()
        if not output:
            raise RuntimeError("Model command returned empty output.")

        return output
