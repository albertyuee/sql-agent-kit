#!/usr/bin/env bash
# ─────────────────────────────────────────────
#  sql-agent-kit  一键启动脚本 (macOS / Linux)
# ─────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

echo ""
echo -e "${CYAN}╔══════════════════════════════════════╗${NC}"
echo -e "${CYAN}║       sql-agent-kit  启动脚本        ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════╝${NC}"
echo ""

# ── 1. 检查 Python ──────────────────────────────
info "检查 Python 版本..."
if ! command -v python3 &>/dev/null; then
    error "未找到 python3，请先安装 Python 3.10+"
fi
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    error "需要 Python 3.10+，当前版本 $PY_VER"
fi
ok "Python $PY_VER"

# ── 2. 检查 Node.js ─────────────────────────────
info "检查 Node.js 版本..."
if ! command -v node &>/dev/null; then
    error "未找到 node，请先安装 Node.js 18+"
fi
NODE_VER=$(node -e "process.stdout.write(process.version.slice(1))")
NODE_MAJOR=$(echo "$NODE_VER" | cut -d. -f1)
if [ "$NODE_MAJOR" -lt 18 ]; then
    error "需要 Node.js 18+，当前版本 $NODE_VER"
fi
ok "Node.js $NODE_VER"

# ── 3. 检查 .env ────────────────────────────────
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        warn ".env 不存在，已从 .env.example 复制，请填写配置后重新运行"
        cp .env.example .env
        exit 1
    else
        error ".env 文件不存在，请先创建并填写数据库和 LLM 配置"
    fi
fi
ok ".env 已就绪"

# ── 4. Python 虚拟环境 ──────────────────────────
if [ ! -d ".venv" ]; then
    info "创建 Python 虚拟环境 .venv ..."
    python3 -m venv .venv
    ok "虚拟环境已创建"
fi

# 激活虚拟环境
source .venv/bin/activate
ok "虚拟环境已激活"

# ── 5. 安装 Python 依赖 ─────────────────────────
info "安装 Python 依赖（requirements-backend.txt）..."
python -m pip install --quiet --upgrade pip -i https://mirrors.aliyun.com/pypi/simple
python -m pip install --progress-bar on -r requirements-backend.txt -i https://mirrors.aliyun.com/pypi/simple
ok "Python 依赖已安装"

# ── 6. 安装前端依赖 ─────────────────────────────
info "安装前端依赖（npm install）..."
cd frontend
if [ ! -d "node_modules" ]; then
    npm install --silent
else
    # 只在 package.json 比 node_modules 新时才重新安装
    if [ "package.json" -nt "node_modules/.package-lock.json" ] 2>/dev/null; then
        npm install --silent
    fi
fi
ok "前端依赖已安装"

# ── 7. 构建前端 ─────────────────────────────────
info "构建前端（npm run build）..."
npm run build
ok "前端构建完成 → backend/static/"
cd ..

# ── 8. 创建必要目录 ─────────────────────────────
mkdir -p logs data config

# ── 9. 准备数据库 ───────────────────────────────
# 只在「.env 里是 sqlite 且库文件还不存在」时建示例库。
# MySQL / PostgreSQL 一律不碰：init_db.sql 里有 DROP TABLE，
# 自动对着用户已有的库跑是破坏性的，必须由人显式发起。
info "检查数据库..."
python scripts/init_db.py --dialect sqlite --auto

# ── 10. 启动后端 ────────────────────────────────
PORT=${PORT:-8000}
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  启动成功！访问地址：http://localhost:${PORT}${NC}"
echo -e "${GREEN}  按 Ctrl+C 停止服务${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

exec uvicorn backend.main:app --host 0.0.0.0 --port "$PORT" --reload
