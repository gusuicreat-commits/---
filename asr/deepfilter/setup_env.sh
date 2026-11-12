#!/bin/sh
set -eu
ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$ROOT_DIR"
if ! command -v uv >/dev/null 2>&1; then
    echo '找不到 uv，请先安装 uv 后重试' >&2
    exit 1
fi
if [ ! -d .venv-df ]; then
    uv venv --python 3.11 .venv-df
fi
uv pip install --python .venv-df/bin/python -r asr/deepfilter/requirements.txt
.venv-df/bin/python -c "from df.enhance import init_df; init_df()"
echo '降噪环境就绪'
