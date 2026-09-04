# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_SH = REPO_ROOT / "examples" / "16_self_host_sandbox" / "run.sh"


def test_run_sh_exists_and_is_executable():
    assert RUN_SH.is_file()
    assert os.access(RUN_SH, os.X_OK)


def test_run_sh_help_flag_executes_successfully():
    env = os.environ.copy()
    env["no_proxy"] = "*"
    env["NO_PROXY"] = "*"
    env["SSL_CERT_FILE"] = "/etc/ssl/certs/ca-certificates.crt"

    proc = subprocess.run(
        ["bash", str(RUN_SH), "--help"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert "usage: main.py" in proc.stdout
    assert "--feishu" in proc.stdout
    assert "--prompt" in proc.stdout


def test_run_sh_preserves_explicit_environment_variables(tmp_path):
    # Verify caller-provided env vars override defaults loaded from .env
    test_script = (
        "set -euo pipefail\n"
        f'SCRIPT_DIR="{RUN_SH.parent}"\n'
        "declare -A EXPLICIT_ENV=()\n"
        "for key in ANTHROPIC_BASE_URL ANTHROPIC_ENVIRONMENT_ID; do\n"
        '    if [[ -v $key ]]; then EXPLICIT_ENV[$key]="${!key}"; fi\n'
        "done\n"
        'ANTHROPIC_BASE_URL="https://from-env-file.example.com"\n'
        'for key in "${!EXPLICIT_ENV[@]}"; do\n'
        '    printf -v "$key" \'%s\' "${EXPLICIT_ENV[$key]}"\n'
        '    export "$key"\n'
        "done\n"
        'echo "URL=$ANTHROPIC_BASE_URL"\n'
    )
    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = "https://explicit-override.example.com"
    proc = subprocess.run(
        ["bash", "-c", test_script],
        cwd=RUN_SH.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0
    assert "URL=https://explicit-override.example.com" in proc.stdout
